"""Sestava proti samemu BTC, z izbirnim datumom vstopa.

    streamlit run app.py

Racun vodi DENAR po nalozbah, ne donosov. Vsaka nalozba je znesek, ki raste in
manjsa, provizije se odbijejo od zneska, metrike pa se izracunajo sele iz poti
skupne vrednosti. To je ista koda, s katero je bila tabela v porocilu
preverjena, in ne tista, ki je strosek uravnavanja odstela od donosa, ne pa
tudi od nalozb.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from model.config import LeanConfig
from model.strategy import position, run_strategy, traded_fraction
from model.data_source import DEFAULT_SYMBOL_MAP, fetch_candles
from model.warmup import trim_warmup

PPY = 365
DNO = LeanConfig().bear_alloc_pct / 100.0   # trajno dno, se nikoli ne proda

# Vsako sredstvo ima tocno en vir in nadomestnega ni. Nadomestni vir bi tiho
# spremenil vsako izracunano stevilko, zato stran raje pove, da nima podatkov.
#
# Binancea tu ni, ceprav je bila raziskava narejena z njim. Iz Streamlit Clouda
# vrne HTTP 451, ker zavraca IP naslove podatkovnih centrov. BNB in XRP zato
# prideta z Yahooja, ki ju pokriva v celoti. Razlika je merjena in majhna:
# na BNB se pozicija ujema na 99,0 % od 3217 skupnih dni, Sortino 1,22 proti
# 1,25. Podrobno v README.
#
# Coinbase ne kotira BNB pred oktobrom 2025, XRP pa je bil pri njem med 2021 in
# 2023 umaknjen, torej bi manjkala prav leta, ki nas zanimajo.
VIRI = {
    "BTC": "coinbase",
    "ETH": "coinbase",
    "SOL": "coinbase",
    "LINK": "coinbase",
    "BNB": "yahoo",
    "XRP": "yahoo",
    "HYPE": "hyperliquid",
    "SPY": "yahoo",
}


def _symbol_map() -> dict:
    sm = dict(DEFAULT_SYMBOL_MAP)
    sm.setdefault("BNB", {})
    sm["BNB"] = dict(sm["BNB"], yahoo="BNB-USD")
    sm["HYPE"] = {"hyperliquid": "HYPE"}
    return sm


st.set_page_config(page_title="Sestava proti BTC", layout="wide")


class NapakaCen(Exception):
    """Vsi viri, ki so odpovedali, in vsi, ki so uspeli, v enem kosu.

    Zakaj lasten tip in ne preprost `raise`. Streamlit Cloud pri NEUJETI
    izjemi sporocilo zamenja z `The original error message is redacted to
    prevent data leaks`. Prav to sporocilo pa je edino, kar pove, KATERO
    sredstvo in KATERI vir sta odpovedala -- `fetch_candles` ga skrbno
    sestavi, uporabnik pa ga v oblaku ne vidi nikoli.

    Zato se izjema ujame v `main()` in izrise z `st.error`. Izrisano besedilo
    Streamlit ne cenzurira; cenzurira le izjeme, ki uidejo do njega.
    """

    def __init__(self, napake: list[tuple[str, str, str]], uspeli: list[str]):
        self.napake = napake
        self.uspeli = uspeli
        super().__init__("; ".join(f"{s} ({v})" for s, v, _ in napake))


@st.cache_data(ttl=3600, show_spinner=False)
def _cene(simboli: tuple[str, ...]) -> dict:
    """Cene za vsa sredstva. Poskusi VSA, tudi ce prvo odpove.

    Prej je prva napaka ustavila zanko, zato je stran povedala le za eno
    sredstvo -- naslednja osvezitev pa je pokazala naslednje. Ker so viri trije
    razlicni, je bilo tako nemogoce lociti `en vir je padel` od `oblak nima
    dostopa nikamor`. En sam ogled strani mora povedati celotno sliko.
    """
    cfg = replace(LeanConfig(), symbol_map=_symbol_map())
    out: dict = {}
    napake: list[tuple[str, str, str]] = []
    for s in simboli:
        try:
            out[s] = fetch_candles(s, "1d", bars=5000, config=cfg,
                                   prefer=VIRI[s], strict=True)
        except Exception as e:                                    # noqa: BLE001
            napake.append((s, VIRI[s], f"{type(e).__name__}: {e}"))
    if napake:
        raise NapakaCen(napake, sorted(out))
    return out


def _izpisi_napako_cen(e: NapakaCen) -> None:
    """Izrise, kaj je odpovedalo. Nadomestnega vira NE predlaga in ne uporabi:
    stran ima za vsako sredstvo natanko en vir prav zato, da se izracunane
    stevilke ne spremenijo tiho."""
    st.error(
        f"Podatki niso dosegljivi za {len(e.napake)} od "
        f"{len(e.napake) + len(e.uspeli)} sredstev. Strani ni mogoce izracunati."
    )
    st.table(pd.DataFrame(
        [{"sredstvo": s, "vir": v, "odgovor": (n[:160] + " ...") if len(n) > 160 else n}
         for s, v, n in e.napake]))
    if e.uspeli:
        st.caption("Uspesno naloženo: " + ", ".join(e.uspeli))
    with st.expander("Celotna sporočila napak"):
        for s, v, n in e.napake:
            st.code(f"{s}  <-  {v}\n{n}", language="text")
    st.caption(
        "Kaj to obicajno pomeni. HTTP 429 ali 401 z Yahooja: Yahoo je zavrnil "
        "IP naslov podatkovnega centra -- praviloma mine samo od sebe, pomaga "
        "osvezitev cez nekaj minut. HTTP 451: vir ne streze temu okolju "
        "(Binance to pocne za oblacne IP naslove; zato ga ta stran ne uporablja). "
        "ConnectionError ali Timeout: prehodna omrezna napaka, poskusite znova."
    )
    if st.button("Poskusi znova"):
        _cene.clear()
        st.rerun()


@st.cache_data(ttl=3600, show_spinner=False)
def _signali(simboli: tuple[str, ...], _cene_d: dict) -> dict:
    cfg = replace(LeanConfig(), symbol_map=_symbol_map())
    out = {}
    for s in simboli:
        if s == "SPY":
            continue                # samo primerjava, strategija ga ne trguje
        df = trim_warmup(run_strategy(_cene_d[s], config=cfg).df)
        out[s] = (position(df, cfg), traded_fraction(df, cfg).fillna(0.0))
    return out


def _vklop_stikala(idx, SIG, stikalo: bool):
    """Kdaj je knjiga v trgu po knjiznem stikalu. `None`, ce stikala ni."""
    if not stikalo or "BTC" not in SIG:
        return None
    return (SIG["BTC"][0].reindex(idx) > 0.5).fillna(False)


def _poz_sredstva(k, idx, SIG, sesto_od, vklop=None):
    """Pozicija enega sredstva, KAKRSNA JE V KNJIGI.

    Ce je knjizno stikalo vklopljeno, mora tudi ta serija to pokazati -- sicer
    stran na zavihku o sredstvih trdi, da je sredstvo v trgu, medtem ko ga je
    stikalo ze prodalo do dna. Prav to neskladje je bilo opazeno: graf
    izpostavljenosti se ob vklopu stikala ni spremenil.
    """
    if k == "SESTO":
        p = pd.concat([SIG["XRP"][0].reindex(idx[idx < sesto_od]),
                       SIG["HYPE"][0].reindex(idx[idx >= sesto_od])])
    else:
        p = SIG[k][0].reindex(idx)
    if vklop is not None:
        p = p.where(vklop.reindex(p.index).fillna(False), DNO)
    return p


def _metrike(r: np.ndarray) -> dict:
    eq = np.cumprod(1 + r)
    dd = eq / np.maximum.accumulate(eq) - 1
    vol = r.std() * np.sqrt(PPY)
    dn = np.sqrt(np.mean(np.minimum(r, 0.0) ** 2)) * np.sqrt(PPY)
    return {
        "skupaj": (eq[-1] - 1) * 100,
        "letno": (eq[-1] ** (PPY / len(r)) - 1) * 100,
        "vol": vol * 100,
        "sharpe": r.mean() * PPY / vol if vol > 0 else float("nan"),
        "sortino": r.mean() * PPY / dn if dn > 0 else float("nan"),
        "maxdd": dd.min() * 100,
    }


def _knjiga(idx, CENE, SIG, utezi, bps, uravnavaj, sesto_od, vsak_n_mesecev=1,
            stikalo=False):
    """Vsaka nalozba je znesek. Vrne pot vrednosti in razclenjene provizije.

    `stikalo` je KNJIZNO STIKALO NA BITCOINU. Ko je vklopljeno in je BTC-jev
    signal MEDVEDJI, gre CELA knjiga v gotovino -- tudi sredstva, katerih lastni
    signal je se vedno bikovski. Ko BTC znova postane bikovski, se knjiga vrne v
    to, kar takrat pravi signal vsakega sredstva posebej.

    Ni novega parametra: uporablja signal, ki v strategiji ze obstaja, samo na
    ravni knjige namesto vsakega sredstva zase.

    TRAJNO DNO OSTANE. Ko stikalo proda, ostane v vsakem sredstvu obicajnih
    5 % -- isto dno, ki ga strategija ne proda nikoli. Stikalo torej proda vse
    NAD dnom, ne cisto vsega.

    Stroski preklopa se obracunajo. Na dan preklopa se placa celoten premik
    pozicije (prodaja do dna oziroma nakup nazaj); na ostale dni velja obicajni
    obrat strategije, in ko je knjiga zunaj, se ne trguje, ker dno miruje.
    """
    E = {k: 100.0 * w for k, w in utezi.items()}
    zgod = [sum(E.values())]
    prov = {"signali": 0.0, "uravnavanje": 0.0, "zamenjava": 0.0}
    # BTC je bikovski, ko je njegova pozicija nad trajnim dnom (dno je 5 %).
    btc_bull = None
    if stikalo and "BTC" in SIG:
        btc_bull = (SIG["BTC"][0].reindex(idx) > 0.5).fillna(False).to_numpy()
    prej_vklop = True
    prej_poz: dict = {}
    ima_sesto = "SESTO" in utezi
    prej6 = None
    if ima_sesto:
        prej6 = "HYPE" if (sesto_od is not None and idx[0] >= sesto_od) else "XRP"
    _s = pd.Series(idx, index=idx)
    _mes = sorted(_s.groupby([idx.year, idx.month]).last())
    konci = set(_mes[vsak_n_mesecev - 1::vsak_n_mesecev])
    P = {s: (SIG[s][0].reindex(idx), SIG[s][1].reindex(idx).fillna(0.0)) for s in SIG}
    RT = {s: CENE[s]["close"].pct_change().reindex(idx).fillna(0.0) for s in CENE}

    for i, t in enumerate(idx):
        vklop = True if btc_bull is None else bool(btc_bull[i])
        preklop = (vklop != prej_vklop)
        s6 = None
        if ima_sesto:
            s6 = "HYPE" if (sesto_od is not None and t >= sesto_od) else "XRP"
            if s6 != prej6:
                c = E["SESTO"] * 2 * bps / 10000
                E["SESTO"] -= c
                prov["zamenjava"] += c
                prej6 = s6
        for k in utezi:
            s = s6 if k == "SESTO" else k
            p = P[s][0].iloc[i]
            if pd.isna(p):
                continue                       # sredstvo se nima signala, nalozba caka
            # Provizija je odsteta ZNOTRAJ dnevnega faktorja, ne pred njim, torej
            # E x (1 + p*r - t*f) in ne (E - E*t*f) x (1 + p*r). Razlika je le
            # krizni clen t*f*p*r, a to je oblika, ki jo uporablja shared/costs.py
            # in z njo so izracunane vse dosedanje tabele. Kontrola "sam BTC prek
            # knjige proti neposrednemu izracunu" je prej odstopala za 0,07 %.
            tr = P[s][1].iloc[i]
            if btc_bull is not None:
                # Ob izklopljenem stikalu ostane TRAJNO DNO, ne nic. Dno je
                # del strategije in se ne proda nikoli -- tudi ko knjizno
                # stikalo proda vse ostalo. Tistih 5 % zato se naprej niha s
                # ceno sredstva.
                p_uc = p if vklop else DNO
                if preklop:
                    # Na dan preklopa se placa celoten premik -- prodaja vsega
                    # ali nakup nazaj -- namesto obicajnega obrata strategije.
                    tr = abs(p_uc - prej_poz.get(k, 0.0))
                elif not vklop:
                    tr = 0.0            # zunaj trga se ne trguje
                prej_poz[k] = p_uc
                p = p_uc
            prov["signali"] += E[k] * tr * bps / 10000
            E[k] *= 1 + p * RT[s].iloc[i] - tr * bps / 10000
        if uravnavaj and (t in konci) and i < len(idx) - 1:
            sk = sum(E.values())
            c = sum(abs(E[k] - utezi[k] * sk) for k in utezi) * bps / 10000
            prov["uravnavanje"] += c
            sk -= c
            E = {k: utezi[k] * sk for k in utezi}
        prej_vklop = vklop
        zgod.append(sum(E.values()))

    v = np.array(zgod)
    return v[1:] / v[:-1] - 1, prov, v[-1], v


def _kupi_drzi(idx, CENE, utezi, sesto_od):
    """Kupis na dan vstopa in se nikoli ne dotaknes. Brez trgovanja, brez
    uravnavanja, brez provizij po zacetnem nakupu. Sredstvo, ki na dan vstopa
    se ne obstaja, pocaka v gotovini in se kupi na svoj prvi dan."""
    E, zac_cena = {}, {}
    for k, w in utezi.items():
        s = ("HYPE" if (sesto_od is not None and idx[0] >= sesto_od) else "XRP") if k == "SESTO" else k
        E[k] = 100.0 * w
        zac_cena[k] = (s, None)
    zgod = [sum(E.values())]
    for i, t in enumerate(idx):
        for k in list(E):
            s, kupljeno_po = zac_cena[k]
            if k == "SESTO" and sesto_od is not None and t >= sesto_od and s == "XRP":
                s = "HYPE"                      # sesto mesto preide na HYPE
                zac_cena[k] = (s, None)
                kupljeno_po = None
            c = CENE[s]["close"]
            if t not in c.index or pd.isna(c.loc[t]):
                continue                        # se ne obstaja, ceka v gotovini
            if kupljeno_po is None:
                zac_cena[k] = (s, float(c.loc[t]))
                continue                        # kupimo na zakljucek, rast sele jutri
            E[k] = 100.0 * utezi[k] * float(c.loc[t]) / kupljeno_po
        zgod.append(sum(E.values()))
    v = np.array(zgod)
    return v[1:] / v[:-1] - 1, v


def _eno_kupi_drzi(idx, cene):
    """Kupi in drzi eno samo sredstvo, poravnano na kripto koledar.

    Delnice se ne trgujejo ob vikendih in praznikih, zato se cena za te dni
    prenese z zadnjega trgovalnega dne. Polnjenje mora teci CEZ zdruzeni indeks,
    sicer ostane prvi dan prazen, kadar je vstop na dan, ko borza ni delala.
    Prvi januar 2021 je bil tak dan in cela krivulja je bila NaN.
    """
    c = cene["close"]
    c = c.reindex(c.index.union(idx)).ffill().reindex(idx)
    if pd.isna(c.iloc[0]):
        return np.zeros(len(idx)), np.full(len(idx) + 1, 100.0)
    c = c / c.iloc[0] * 100.0
    v = np.concatenate([[100.0], c.to_numpy(float)])
    return v[1:] / v[:-1] - 1, v


# ── barve ────────────────────────────────────────────────────────────────────
# Grafi so na BELI podlagi, zato so odtenki temnejsi od tistih na lean strani,
# ki riše na crno. Svetla oranzna in svetlo vijolicna sta na belem neberljivi.
COL_BULL, COL_BEAR = "#0f8a6a", "#d92d3a"
COL_BLUE, COL_ORANGE, COL_SPX = "#1e56d6", "#d97706", "#b45309"
COL_BG, COL_TEXT, COL_DIM, COL_MREZA = "#ffffff", "#111827", "#6b7280", "#e5e7eb"
# Ena barva na strategijo, ista na vseh grafih in v vseh tabelah.
BARVA = {
    "uravnavana": COL_BLUE, "puščena": COL_ORANGE, "BTC strategija": COL_BULL,
    "sestava B&H": "#7e57c2", "BTC B&H": "#00897b", "S&P 500": COL_SPX,
}
# Kupi in drzi so ob odprtju SKRITI, da so vidne tri trgovane krivulje. Kliknes
# jih v legendi, kadar jih hoces.
PRIVZETO_SKRITO = {"sestava B&H", "BTC B&H", "S&P 500"}


def _postavi(fig, visina=340, naslov=""):
    fig.update_layout(
        height=visina, template="plotly_white", paper_bgcolor=COL_BG, plot_bgcolor=COL_BG,
        margin=dict(l=0, r=0, t=60 if naslov else 16, b=0),
        legend=dict(
            orientation="h", y=1.16, x=0, xanchor="left", yanchor="bottom",
            font=dict(size=12, color=COL_TEXT),
            bgcolor="rgba(255,255,255,0.95)", bordercolor=COL_MREZA, borderwidth=1,
            itemsizing="constant", itemwidth=42, tracegroupgap=6,
        ),
        font=dict(color=COL_TEXT, size=12),
        hoverlabel=dict(bgcolor="#ffffff", font=dict(color=COL_TEXT, size=12),
                        bordercolor=COL_MREZA),
    )
    if naslov:
        fig.update_layout(title=dict(
            text=f'<span style="color:{COL_TEXT};font-size:12px;text-transform:uppercase;'
                 f'letter-spacing:1px">{naslov}</span>', x=0.01, y=0.97, yanchor="top"))
    fig.update_xaxes(gridcolor=COL_MREZA, zeroline=False, linecolor=COL_MREZA,
                     tickfont=dict(color=COL_DIM))
    fig.update_yaxes(gridcolor=COL_MREZA, zeroline=False, linecolor=COL_MREZA,
                     tickfont=dict(color=COL_DIM))
    return fig


def _podvodni(pot: np.ndarray) -> np.ndarray:
    """Koliko odstotkov pod prejsnjim vrhom si vsak dan."""
    return (pot / np.maximum.accumulate(pot) - 1) * 100


def _datiraj(cena: pd.Series, k: int = 90, min_faza: int = 120,
             min_ampl: float = 0.25) -> list[tuple]:
    """Datiraj faze rasti in padca po Bry-Boschan, razlicica Pagan-Sossounov.

    Akademski standard za dolocanje ciklov. Postopek: najdi lokalne vrhove in
    dna v oknu +/- k dni, vsili izmenjavanje vrh-dno-vrh, nato odstrani faze,
    ki so prekratke ali premajhne. Rezultat je objektiven in ponovljiv, za
    razliko od meje tipa "cena nad 200-dnevnim povprecjem", ki se krizajo sem
    in tja in stejejo kratek prehod enako kot dvoletni medvedji trg.

    Vrne [(od, do, "rast" ali "padec"), ...].
    """
    if len(cena) < 2 * k + min_faza:
        return []
    x = np.log(cena.to_numpy(float))
    n = len(x)
    tocke = []
    for i in range(k, n - k):
        okno = x[i - k:i + k + 1]
        if x[i] == okno.max():
            tocke.append((i, "V"))
        elif x[i] == okno.min():
            tocke.append((i, "D"))

    ocisceno = []
    for i, t in tocke:
        if ocisceno and ocisceno[-1][1] == t:
            j = ocisceno[-1][0]
            if (x[i] > x[j]) if t == "V" else (x[i] < x[j]):
                ocisceno[-1] = (i, t)
            continue
        ocisceno.append((i, t))

    spremenjeno = True
    while spremenjeno and len(ocisceno) > 2:
        spremenjeno = False
        for a in range(len(ocisceno) - 1):
            i, j = ocisceno[a][0], ocisceno[a + 1][0]
            if (j - i) < min_faza or abs(x[j] - x[i]) < np.log(1 + min_ampl):
                del ocisceno[a + 1]
                if a + 1 < len(ocisceno) and ocisceno[a][1] == ocisceno[a + 1][1]:
                    del ocisceno[a + 1]
                spremenjeno = True
                break

    return [(cena.index[ocisceno[a][0]], cena.index[ocisceno[a + 1][0]],
             "rast" if ocisceno[a][1] == "D" else "padec")
            for a in range(len(ocisceno) - 1)]


def _najhujsi_padci(idx, pot: np.ndarray, n: int = 5) -> pd.DataFrame:
    """Prvih n padcev, vsak z vrhom, dnom, okrevanjem in trajanjem.

    Alokatorji berejo stolpec 'dni do okrevanja' bolj kot samo globino: padec
    -29 %, iz katerega si okreval v treh mesecih, je nekaj drugega kot enak
    padec, ki je trajal dve leti.
    """
    d = pd.Series(pot[1:], index=idx)
    vrh = d.cummax()
    pod = d < vrh
    epizode, i = [], 0
    a = pod.to_numpy()
    while i < len(a):
        if not a[i]:
            i += 1
            continue
        j = i
        while j < len(a) and a[j]:
            j += 1
        odsek = d.iloc[i:j]
        vrh_v = float(vrh.iloc[i])
        dno_i = int(odsek.values.argmin())
        epizode.append({
            "globina": (float(odsek.iloc[dno_i]) / vrh_v - 1) * 100,
            "vrh": d.index[i - 1].date() if i else d.index[0].date(),
            "dno": odsek.index[dno_i].date(),
            "okrevanje": d.index[j].date() if j < len(a) else None,
            "dni skupaj": (d.index[min(j, len(a) - 1)] - d.index[max(i - 1, 0)]).days,
            "dni do okrevanja": ((d.index[j] - odsek.index[dno_i]).days
                                 if j < len(a) else None),
        })
        i = j
    if not epizode:
        return pd.DataFrame()
    t = pd.DataFrame(epizode).sort_values("globina").head(n).reset_index(drop=True)
    t.index = [f"{i+1}." for i in range(len(t))]
    # Oba v niz. Mesani tipi, torej datum in beseda v istem stolpcu, sesujejo
    # pretvorbo v Arrow, ki jo Streamlit uporablja za prikaz tabel.
    t["okrevanje"] = [("še traja" if pd.isna(v) else str(v)) for v in t["okrevanje"]]
    t["dni do okrevanja"] = [("še traja" if pd.isna(v) else "%d" % v)
                              for v in t["dni do okrevanja"]]
    return t


def _kotalec(r: np.ndarray, idx, okno: int = 365):
    """Kotaleci se Sharpe in beta do BTC."""
    s = pd.Series(r, index=idx)
    m = s.rolling(okno).mean() * PPY
    v = s.rolling(okno).std() * np.sqrt(PPY)
    return (m / v).dropna()


def _mesecna_karta(r: np.ndarray, idx) -> go.Figure:
    s = pd.Series(r, index=idx)
    mes = s.resample("ME").apply(lambda x: (1 + x).prod() - 1) * 100
    leta = sorted(mes.index.year.unique())
    oznake = ["jan", "feb", "mar", "apr", "maj", "jun",
              "jul", "avg", "sep", "okt", "nov", "dec"]
    z, txt, letno = [], [], []
    for y in leta:
        vr, vt = [], []
        for m in range(1, 13):
            v = mes[(mes.index.year == y) & (mes.index.month == m)]
            if len(v):
                vr.append(float(v.iloc[0])); vt.append(f"{float(v.iloc[0]):+.1f}")
            else:
                vr.append(None); vt.append("")
        z.append(vr); txt.append(vt)
        lr = s[s.index.year == y]
        letno.append(((1 + lr).prod() - 1) * 100)
    plosc = [v for vrsta in z for v in vrsta if v is not None]
    zmax = max(abs(v) for v in plosc) if plosc else 10
    for i, y in enumerate(leta):
        z[i].append(letno[i]); txt[i].append(f"{letno[i]:+.0f}")
    fig = go.Figure(go.Heatmap(
        z=z, x=oznake + ["LETO"], y=[str(y) for y in leta], text=txt,
        texttemplate="%{text}", textfont=dict(size=10, color=COL_TEXT),
        colorscale=[[0, COL_BEAR], [0.5, "#ffffff"], [1, COL_BULL]],
        zmin=-zmax, zmax=zmax, showscale=False,
        hovertemplate="%{y} %{x}: %{text} %<extra></extra>"))
    _postavi(fig, visina=max(190, len(leta) * 34 + 90),
             naslov="Mesečni donosi sestave, v odstotkih")
    fig.update_layout(yaxis=dict(autorange="reversed"))
    fig.update_xaxes(side="top", tickfont=dict(color=COL_DIM, size=9))
    fig.update_yaxes(tickfont=dict(color=COL_DIM, size=10))
    return fig


def main() -> None:
    st.title("Sestava proti samemu BTC")
    st.caption(
        "Uteži se postavijo na dan vstopa. Vsako sredstvo nato trguje po svojem "
        "signalu, neodvisno od ostalih. Provizije se obračunajo po dejanski "
        "vrednosti naložbe tistega dne, ne po številu poslov."
    )

    with st.sidebar:
        st.header("Nastavitve")
        vsi = ["BTC", "ETH", "SOL", "LINK", "BNB", "HYPE", "XRP", "SPY"]
        try:
            CENE = _cene(tuple(vsi))
        except NapakaCen as e:
            napaka = e
        else:
            napaka = None
    if napaka is not None:
        # Izpis mora biti v glavnem delu strani, ne v stranski vrstici: tam bi
        # bil ozek in bi ga bilo treba odpreti, da se sploh vidi, da je padlo.
        _izpisi_napako_cen(napaka)
        st.stop()

    with st.sidebar:
        SIG = _signali(tuple(vsi), CENE)
        kon_max = min(d.index[-1] for k, d in CENE.items() if k != "SPY").date()
        zac_min = SIG["BTC"][0].index[0].date()

        c1, c2 = st.columns(2)
        vstop = c1.date_input("Vstop", value=pd.Timestamp("2021-01-01").date(),
                              min_value=zac_min, max_value=kon_max - pd.Timedelta(days=120))
        izstop = c2.date_input("Izstop", value=kon_max,
                               min_value=zac_min, max_value=kon_max)

        st.divider()
        bps = st.slider("Provizija in zdrs, bazičnih točk na stran", 0, 60, 30, 5,
                        help="30 = 0,30 % na stran, torej 0,60 % na cel obrat")
        # Prej je tu stal gumb za vklop uravnavanja, ki ni delal nicesar: obe
        # vrstici sta se racunali s trdo vpisanima True in False. Zdaj izbira
        # POGOSTOST, ki dejansko doloca prvo vrstico, druga pa je vedno "nikoli".
        POGOSTOST = {"mesečno": 1, "četrtletno": 3, "polletno": 6, "letno": 12}
        pog_ime = st.selectbox("Kako pogosto uravnavati nazaj na ciljne uteži",
                               list(POGOSTOST), index=0,
                               help="Druga vrstica v tabeli je vedno različica "
                                    "brez uravnavanja, da imaš primerjavo.")
        pog = POGOSTOST[pog_ime]

        st.divider()
        stikalo = st.checkbox(
            "Knjižno stikalo na bitcoinu", value=False,
            help="Ko je BTC-jev signal medvedji, gre CELA knjiga v gotovino — "
                 "tudi sredstva, katerih lastni signal je še vedno bikovski. "
                 "Ne uvaja nobene nove nastavitve, uporabi obstoječi signal na "
                 "ravni knjige. Izmerjeno 2021–2026: največji padec se zmanjša "
                 "s 35 % na 23 %, končni znesek pa se skoraj ne spremeni. "
                 "Cena: leta 2024 je s tem izpadlo 24 dni, ko je knjiga "
                 "pridobila 14 %.")
        if stikalo:
            st.caption("Stikalo velja za obe vrstici knjige, ne za primerjavo "
                       "s samim BTC in ne za kupi-in-drži.")

        st.divider()
        st.caption("Uteži v odstotkih, skupaj naj bo 100")
        w_btc = st.number_input("BTC", 0, 100, 50, 5)
        w_eth = st.number_input("ETH", 0, 100, 10, 5)
        st.caption("SOL")
        w_sol = st.number_input("SOL", 0, 100, 10, 5, label_visibility="collapsed")
        w_link = st.number_input("LINK", 0, 100, 10, 5)
        w_bnb = st.number_input("BNB", 0, 100, 10, 5)
        w_6 = st.number_input("Šesto mesto: XRP, nato HYPE", 0, 100, 10, 5)

    utezi = {"BTC": w_btc, "ETH": w_eth, "SOL": w_sol,
             "LINK": w_link, "BNB": w_bnb, "SESTO": w_6}
    utezi = {k: v / 100.0 for k, v in utezi.items() if v > 0}
    vsota = sum(utezi.values())
    if abs(vsota - 1.0) > 1e-9:
        st.warning(f"Uteži se seštejejo v {vsota*100:.0f} %, ne 100 %. "
                   f"Preračunano sorazmerno.")
        utezi = {k: v / vsota for k, v in utezi.items()}

    zac = pd.Timestamp(vstop, tz="UTC")
    kon = pd.Timestamp(izstop, tz="UTC")
    idx = CENE["BTC"].loc[zac:kon].index
    if len(idx) < 60:
        st.error("Izbrano obdobje je prekratko, izberi vsaj dva meseca.")
        st.stop()

    sesto_od = SIG["HYPE"][0].index[0]
    r_ura, prov_ura, konc_ura, pot_ura = _knjiga(idx, CENE, SIG, utezi, bps, True, sesto_od, pog,
                                                 stikalo=stikalo)
    r_pus, prov_pus, konc_pus, pot_pus = _knjiga(idx, CENE, SIG, utezi, bps, False, sesto_od,
                                                 stikalo=stikalo)
    r_btc, prov_btc, konc_btc, pot_btc = _knjiga(idx, CENE, SIG, {"BTC": 1.0}, bps, False, None)
    IME_URA = f"sestava, uravnavana {pog_ime}"
    r_kd, pot_kd = _kupi_drzi(idx, CENE, utezi, sesto_od)
    r_bh, pot_bh = _eno_kupi_drzi(idx, CENE["BTC"])
    r_spy, pot_spy = _eno_kupi_drzi(idx, CENE["SPY"])

    st.subheader(f"{idx[0].date()} do {idx[-1].date()}, {len(idx)} dni")
    st.caption(f"Provizija in zdrs {bps/100:.2f} % na stran. Uravnavanje {pog_ime}. "
               f"Vsako sredstvo trguje po svojem signalu.")
    if idx[-1].date() >= pd.Timestamp.now(tz="UTC").date():
        st.caption("Zadnji dan je današnji in še ni zaključen, zato se te številke med "
                   "dnevom premikajo. Za stabilen izpis izberi izstop na včeraj.")

    VRSTE = [(IME_URA, r_ura, pot_ura, "uravnavana"),
             ("sestava, puščena", r_pus, pot_pus, "puščena"),
             ("sam BTC, strategija", r_btc, pot_btc, "BTC strategija"),
             ("sestava, kupi in drži", r_kd, pot_kd, "sestava B&H"),
             ("sam BTC, kupi in drži", r_bh, pot_bh, "BTC B&H"),
             ("S&P 500, kupi in drži", r_spy, pot_spy, "S&P 500")]

    vrstice = []
    for ime, r, pot, kljuc in VRSTE:
        m = _metrike(r)
        m["calmar"] = m["letno"] / abs(m["maxdd"]) if m["maxdd"] else float("nan")
        vrstice.append(m)
    tab = pd.DataFrame(vrstice, index=[v[0] for v in VRSTE])
    st.dataframe(
        tab.style.format({"skupaj": "{:.0f} %", "letno": "{:.1f} %", "vol": "{:.0f} %",
                          "sharpe": "{:.2f}", "sortino": "{:.2f}", "maxdd": "{:.0f} %",
                          "calmar": "{:.2f}"})
           .highlight_max(subset=["skupaj", "letno", "sharpe", "sortino", "maxdd", "calmar"],
                          props="background-color:#c6f6d5; color:#111; font-weight:700"),
        width="stretch")
    st.caption(
        "**vol** je letna volatilnost, torej kako močno vrednost niha. Izračuna se kot "
        "standardni odklon dnevnih donosov, pomnožen s korenom iz 365.\n\n"
        "Primer pri 50 %, brez trenda: od 10.000 EUR se jih v dveh letih od treh znajde "
        "med približno **6.100 in 16.500 EUR**. Pas ni simetričen, ker se cene množijo, "
        "ne seštevajo. Simetrična sta razpolovitev in podvojitev, ne minus in plus "
        "petdeset odstotkov. Pri majhni volatilnosti je razlika zanemarljiva, pri 50 % "
        "pa ne.\n\n"
        "Sama po sebi ni dobra ali slaba, je pa imenovalec Sharpa: isti donos pri nižji "
        "volatilnosti pomeni višji Sharpe.\n\n"
        "**calmar** je letni donos deljen z največjim padcem.")

    t1, t2, t3, t4, t5 = st.tabs(
        ["Krivulja in padci", "Skozi čas", "Sredstva", "Občutljivost na vstop", "Stroški"])

    with t1:
        x = [idx[0] - pd.Timedelta(days=1)] + list(idx)
        fig = go.Figure()
        for ime, r, pot, kljuc in VRSTE:
            trdna = kljuc in ("uravnavana", "puščena", "BTC strategija")
            fig.add_trace(go.Scatter(
                x=x, y=pot, name=ime,
                visible=("legendonly" if kljuc in PRIVZETO_SKRITO else True),
                line=dict(color=BARVA[kljuc], width=2.4 if trdna else 1.8,
                          dash="solid" if trdna else "dash"),
                hovertemplate=ime + ": %{y:.0f}<extra></extra>"))
        _postavi(fig, 420, "Vrednost 100 vloženih enot")
        st.plotly_chart(fig, width="stretch")
        st.caption("Kupi in drži so ob odprtju skriti. Klikni jih v legendi, da se "
                   "prikažejo, ali klikni katero drugo, da jo skriješ.")

        st.markdown("**Koliko pod prejšnjim vrhom**")
        st.caption("Vse črte naenkrat so neberljive, zato je ob odprtju prižgana samo "
                   "uravnavana sestava. Ostale prižgeš s klikom v legendi. Polnilo se "
                   "nariše le, kadar je prižgana ena sama črta. Časovna os je ista kot "
                   "zgoraj, zato lahko potegneš navpičnico skozi oba grafa.")
        prikazi = st.multiselect(
            "Katere črte narisati", [v[0] for v in VRSTE],
            default=[IME_URA], label_visibility="collapsed")
        fig2 = go.Figure()
        for ime, r, pot, kljuc in VRSTE:
            if ime not in prikazi:
                continue
            fig2.add_trace(go.Scatter(
                x=x, y=_podvodni(pot), name=ime, showlegend=True,
                fill="tozeroy" if len(prikazi) == 1 else None,
                line=dict(color=BARVA[kljuc], width=1.8),
                hovertemplate=ime + ": %{y:.1f} %<extra></extra>"))
        if not prikazi:
            st.info("Izberi vsaj eno črto.")
        else:
            _postavi(fig2, 280, "")
            fig2.update_xaxes(range=[x[0], x[-1]])
            fig2.update_layout(legend=dict(orientation="h", yanchor="bottom",
                                           y=1.02, xanchor="left", x=0))
            st.plotly_chart(fig2, width="stretch")

        st.markdown("**Najhujši padci sestave**")
        pad = _najhujsi_padci(idx, pot_ura)
        if not pad.empty:
            st.dataframe(pad.style.format({"globina": "{:.1f} %"}), width="stretch")
            st.caption("Stolpec **dni do okrevanja** je tisti, ki ga institucionalni "
                       "vlagatelji berejo najprej. Globina pove, kako hudo je bilo, ta pa "
                       "kako dolgo.")

    with t2:
        st.plotly_chart(_mesecna_karta(r_ura, idx), width="stretch")

        if len(idx) > 400:
            fig = go.Figure()
            for ime, r, pot, kljuc in VRSTE[:3]:
                ks = _kotalec(r, idx)
                fig.add_trace(go.Scatter(x=ks.index, y=ks.values, name=ime,
                                         line=dict(color=BARVA[kljuc], width=1.8)))
            fig.add_hline(y=0, line=dict(color=COL_DIM, width=1, dash="dot"))
            _postavi(fig, 300, "Kotaleči se Sharpe, okno 12 mesecev")
            st.plotly_chart(fig, width="stretch")

            s_str = pd.Series(r_ura, index=idx)
            s_btc = pd.Series(r_bh, index=idx)
            bet = (s_str.rolling(365).cov(s_btc) / s_btc.rolling(365).var()).dropna()
            fig = go.Figure(go.Scatter(x=bet.index, y=bet.values, name="beta",
                                       line=dict(color=BARVA["uravnavana"], width=1.8)))
            fig.add_hline(y=1, line=dict(color=COL_BEAR, width=1, dash="dot"))
            _postavi(fig, 260, "Kotaleča se beta sestave do BTC, okno 12 mesecev")
            st.plotly_chart(fig, width="stretch")
            st.caption("Rdeča črta je beta 1,0, torej isto gibanje kot BTC. Nižje pomeni, "
                       "da sestava ni le BTC v drugi obleki.")
        else:
            st.info("Za kotaleče se mere rabiš vsaj 400 dni obdobja.")

        fig = go.Figure(go.Histogram(x=r_ura * 100, nbinsx=80,
                                     marker=dict(color=BARVA["uravnavana"])))
        _postavi(fig, 260, "Porazdelitev dnevnih donosov sestave, v odstotkih")
        st.plotly_chart(fig, width="stretch")

    with t3:
        st.markdown("**Kaj je počela vsaka naložba**")
        zad = {}
        vklop = _vklop_stikala(idx, SIG, stikalo)
        for k in utezi:
            p = _poz_sredstva(k, idx, SIG, sesto_od, vklop)
            if k == "SESTO":
                ime = "6. mesto"
                sred = ("XRP do %s, nato HYPE" % sesto_od.date()
                        if idx[0] < sesto_od <= idx[-1]
                        else ("HYPE" if idx[0] >= sesto_od else "XRP"))
            else:
                ime, sred = k, k
            zad[ime] = {
                "sredstvo": sred,
                "ciljna utež": f"{utezi[k]*100:.0f} %",
                "dni v trgu": f"{float((p > 0.5).mean()*100):.0f} %",
                "stanje danes": ("V TRGU" if (not pd.isna(p.iloc[-1]) and p.iloc[-1] > 0.5)
                                 else "zunaj"),
            }
        st.dataframe(pd.DataFrame(zad).T, width="stretch")

        st.markdown("**Kdo je zaslužil**")
        prisp = {}
        for k in utezi:
            r1, _, k1, _ = _knjiga(idx, CENE, SIG, {k: 1.0}, bps, False,
                                   sesto_od if k == "SESTO" else None,
                                   stikalo=stikalo)
            ime = "6. mesto" if k == "SESTO" else k
            prisp[ime] = {"sam, cel kapital": (k1 / 100 - 1) * 100,
                          "utež v knjigi": utezi[k] * 100,
                          "približen prispevek": (k1 / 100 - 1) * 100 * utezi[k]}
        pr = pd.DataFrame(prisp).T.sort_values("približen prispevek", ascending=False)
        st.dataframe(pr.style.format({"sam, cel kapital": "{:+.0f} %",
                                      "utež v knjigi": "{:.0f} %",
                                      "približen prispevek": "{:+.1f} točk"}),
                     width="stretch")
        st.caption("Prvi stolpec je donos sredstva, če bi vanj vložil ves kapital. Zadnji je "
                   "ta donos, pomnožen z utežjo, torej groba ocena prispevka h knjigi. Ni "
                   "natančna razgradnja, ker se uteži med potjo premikajo.")

        st.markdown("**Izpostavljenost skozi čas**")
        fig = go.Figure()
        for k in utezi:
            p = _poz_sredstva(k, idx, SIG, sesto_od, vklop)
            ime = "6. mesto" if k == "SESTO" else k
            fig.add_trace(go.Scatter(x=idx, y=(p.fillna(0) * utezi[k] * 100), name=ime,
                                     stackgroup="one", line=dict(width=0.5)))
        _postavi(fig, 300, "Koliko odstotkov kapitala je bilo v trgu")
        st.plotly_chart(fig, width="stretch")

        st.markdown("**Korelacije dnevnih donosov sredstev**")
        RR = {}
        for k in utezi:
            s = ("HYPE" if idx[-1] >= sesto_od else "XRP") if k == "SESTO" else k
            RR["6. mesto" if k == "SESTO" else k] = CENE[s]["close"].pct_change().reindex(idx)
        km = pd.DataFrame(RR).dropna().corr()
        # Plotly namesto Styler.background_gradient, ki potrebuje matplotlib.
        # Tega v okolju ni in stran je zaradi njega padla.
        fig = go.Figure(go.Heatmap(
            z=km.values, x=list(km.columns), y=list(km.index),
            text=[[f"{v:.2f}" for v in vrsta] for vrsta in km.values],
            texttemplate="%{text}", textfont=dict(size=11, color=COL_TEXT),
            colorscale=[[0, "#ffffff"], [0.5, "#f4a6a0"], [1, COL_BEAR]],
            zmin=0, zmax=1, showscale=False,
            hovertemplate="%{y} proti %{x}: %{text}<extra></extra>"))
        _postavi(fig, visina=max(200, len(km) * 42 + 60))
        fig.update_layout(yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, width="stretch")
        st.caption("Rdeče pomeni, da se sredstvi gibljeta skupaj. Pri kriptu so vrednosti "
                   "običajno med 0,6 in 0,9, kar pomeni, da razpršitev prinese manj, kot bi "
                   "človek pričakoval.")

    with t4:
        st.markdown("**Kaj bi dobil pri drugem datumu vstopa**")
        st.caption("Vsak možen vstop na 30 dni, vsi do istega izstopa. To vnaprej odgovori "
                   "na očitek, da je bil izbran ugoden začetek.")
        kand = [t for t in idx[::30] if (idx[-1] - t).days > 200]
        vrs = []
        for t0 in kand:
            i2 = idx[idx >= t0]
            rr, _, _, _ = _knjiga(i2, CENE, SIG, utezi, bps, True, sesto_od, pog,
                                  stikalo=stikalo)
            rb, _, _, _ = _knjiga(i2, CENE, SIG, {"BTC": 1.0}, bps, False, None)
            ms, mb = _metrike(rr), _metrike(rb)
            vrs.append({"vstop": t0.date(), "sestava Sharpe": ms["sharpe"],
                        "BTC Sharpe": mb["sharpe"], "sestava letno": ms["letno"],
                        "sestava MaxDD": ms["maxdd"]})
        ob = pd.DataFrame(vrs)
        if len(ob):
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=ob["vstop"], y=ob["sestava Sharpe"], name="sestava",
                                     line=dict(color=BARVA["uravnavana"], width=2)))
            fig.add_trace(go.Scatter(x=ob["vstop"], y=ob["BTC Sharpe"], name="sam BTC",
                                     line=dict(color=BARVA["BTC strategija"], width=2)))
            _postavi(fig, 320, "Sharpe glede na datum vstopa")
            st.plotly_chart(fig, width="stretch")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("mediana sestave", f"{ob['sestava Sharpe'].median():.2f}")
            c2.metric("najslabši vstop", f"{ob['sestava Sharpe'].min():.2f}")
            c3.metric("najboljši vstop", f"{ob['sestava Sharpe'].max():.2f}")
            c4.metric("prekaša BTC",
                      f"{float((ob['sestava Sharpe'] > ob['BTC Sharpe']).mean()*100):.0f} % vstopov")
            st.dataframe(ob.style.format({"sestava Sharpe": "{:.2f}", "BTC Sharpe": "{:.2f}",
                                          "sestava letno": "{:.1f} %",
                                          "sestava MaxDD": "{:.0f} %"}),
                         width="stretch", height=280)

    with t5:
        st.markdown("**Kam gre 100 vloženih enot**")
        vrst = []
        for ime, prov, konc in ((IME_URA, prov_ura, konc_ura),
                                ("sestava, puščena", prov_pus, konc_pus),
                                ("sam BTC, strategija", prov_btc, konc_btc)):
            sk = sum(prov.values())
            vrst.append({"signali": prov["signali"], "uravnavanje": prov["uravnavanje"],
                         "zamenjava": prov["zamenjava"], "skupaj": sk,
                         "končna vrednost": konc, "delež končne": sk / konc * 100})
        st.dataframe(
            pd.DataFrame(vrst, index=[IME_URA, "sestava, puščena", "sam BTC, strategija"])
              .style.format({"signali": "{:.1f}", "uravnavanje": "{:.1f}",
                             "zamenjava": "{:.1f}", "skupaj": "{:.1f}",
                             "končna vrednost": "{:.0f}", "delež končne": "{:.1f} %"}),
            width="stretch")
        st.markdown(
            "**signali** so provizije od vstopov in izstopov strategije po vsakem sredstvu "
            "posebej.\n\n"
            "**uravnavanje** so provizije od vračanja na ciljne uteži. Zaračuna se samo "
            "tisto, kar se dejansko premakne: če BTC zdrsne s 50 % na 53 %, plačaš od tistih "
            "treh odstotnih točk, ne od celega portfelja.\n\n"
            "**zamenjava** je enkratna menjava XRP v HYPE na dan, ko HYPE dobi prvi signal.\n\n"
            "**delež končne** pove, koliko odstotkov končne vrednosti so pojedle provizije. "
            "Pravi ekonomski strošek je večji, ker zgodaj plačana provizija ne raste več s "
            "portfeljem.")

        st.divider()
        st.markdown("**Koliko je rezultat odvisen od predpostavke o proviziji**")
        st.caption("0,30 % na stran je predpostavka, ne izmerjena vrednost. Ta tabela "
                   "pove, koliko se izid premakne, če je resnica drugje.")
        vrst = []
        for b in (0, 10, 20, 30, 40, 60):
            rb_, pv_, kc_, _ = _knjiga(idx, CENE, SIG, utezi, b, True, sesto_od, pog,
                                       stikalo=stikalo)
            mb = _metrike(rb_)
            vrst.append({"provizija na stran": f"{b/100:.2f} %", "Sharpe": mb["sharpe"],
                         "Sortino": mb["sortino"], "letno": mb["letno"],
                         "končna vrednost": kc_, "provizije skupaj": sum(pv_.values())})
        t = pd.DataFrame(vrst).set_index("provizija na stran")
        st.dataframe(t.style.format({"Sharpe": "{:.2f}", "Sortino": "{:.2f}",
                                     "letno": "{:.1f} %", "končna vrednost": "{:.0f}",
                                     "provizije skupaj": "{:.1f}"}), width="stretch")
        d0 = t["Sharpe"].iloc[0] - t["Sharpe"].loc["0.60 %"]
        st.caption(f"Med brezplačnim trgovanjem in 0,60 % na stran je razlika "
                   f"{d0:.2f} Sharpa. Manjša ko je ta številka, manj je rezultat "
                   f"odvisen od nečesa, česar ne poznamo.")

        st.divider()
        st.markdown("**Provizije po fazah cikla**")
        st.caption("Faze so datirane na BTC po algoritmu Bry-Boschan v različici "
                   "Pagan-Sossounov, ki je akademski standard: lokalni vrhovi in dna v "
                   "oknu 90 dni, najkrajša faza 120 dni, najmanjša amplituda 25 %. "
                   "Meja ni ročno izbrana.")
        faze = _datiraj(CENE["BTC"]["close"].loc[idx[0]:idx[-1]])
        if len(faze) < 2:
            st.info("Izbrano obdobje je prekratko za datiranje faz. Razširi ga na "
                    "vsaj dve leti.")
        else:
            vrst = []
            for od, do, vr in faze:
                w = idx[(idx >= od) & (idx <= do)]
                if len(w) < 30:
                    continue
                rr, pp, kk, _ = _knjiga(w, CENE, SIG, utezi, bps, True, sesto_od, pog,
                                        stikalo=stikalo)
                bh = float(CENE["BTC"]["close"].loc[do] /
                           CENE["BTC"]["close"].loc[od] - 1) * 100
                vrst.append({"od": str(od.date()), "do": str(do.date()),
                             "vrsta": "rast" if vr == "rast" else "padec",
                             "dni": len(w), "BTC kupi in drži": bh,
                             "sestava": (kk / 100 - 1) * 100,
                             "provizije": sum(pp.values())})
            f = pd.DataFrame(vrst)
            st.dataframe(f.style.format({"BTC kupi in drži": "{:+.0f} %",
                                         "sestava": "{:+.0f} %",
                                         "provizije": "{:.2f}"}),
                         width="stretch", hide_index=True)
            r_ = f[f["vrsta"] == "rast"]
            p_ = f[f["vrsta"] == "padec"]
            if len(r_) and len(p_):
                c1, c2, c3 = st.columns(3)
                c1.metric("povprečje v rasti", f"{r_['sestava'].mean():+.0f} %",
                          f"BTC {r_['BTC kupi in drži'].mean():+.0f} %")
                c2.metric("povprečje v padcu", f"{p_['sestava'].mean():+.0f} %",
                          f"BTC {p_['BTC kupi in drži'].mean():+.0f} %")
                c3.metric("provizije padec proti rasti",
                          f"{p_['provizije'].mean() / r_['provizije'].mean():.2f}x"
                          if r_["provizije"].mean() > 0 else "n/a")
                st.caption(
                    "Zadnja številka je pomembnejša, kot izgleda. Strategija naj v "
                    "padajočih fazah trguje MANJ, ker izstopi in ostane zunaj. Če se "
                    "razmerje približa ena, pomeni, da plačuje provizije prav tam, kjer "
                    "ni donosa, ki bi jih pokril.")


if __name__ == "__main__":
    main()
