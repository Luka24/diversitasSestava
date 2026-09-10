"""Preveri, da racun v dashboardu drzi.

    python scripts/preveri.py

Osem preverb racuna in ena preverba izrisa. Zadnja pozene celo stran brezglavo
in ujame napake, ki jih racun ne more: manjkajoco knjiznico za izris, napacen
tip stolpca, zlom v Plotly.
"""
import sys
import warnings
from dataclasses import replace
from pathlib import Path

warnings.filterwarnings("ignore")
KOREN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOREN))

import numpy as np
import pandas as pd

from ui import dashboard as D
from model.config import LeanConfig
from model.strategy import position, run_strategy, traded_fraction
from model.warmup import trim_warmup


def racun() -> list[tuple[str, bool]]:
    vsi = tuple(D.VIRI)
    CENE = D._cene.__wrapped__(vsi)
    SIG = D._signali.__wrapped__(vsi, CENE)
    so = SIG["HYPE"][0].index[0]
    UT = {"BTC": .50, "ETH": .10, "SOL": .10, "LINK": .10, "BNB": .10,
          "SESTO": .10}
    kon = CENE["BTC"].index[-1]
    idx = CENE["BTC"].loc[pd.Timestamp("2021-01-01", tz="UTC"):kon].index
    out = []

    r, prov, konc, pot = D._knjiga(idx, CENE, SIG, UT, 30, True, so, 1)
    m = D._metrike(r)
    out.append(("koncna vrednost = 100 x (1 + skupaj)",
                abs(konc - 100 * (1 + m["skupaj"] / 100)) < 1e-6))

    _, p0, k0, _ = D._knjiga(idx, CENE, SIG, UT, 0, True, so, 1)
    out.append(("brez provizij je koncna vrednost visja", k0 > konc))
    out.append(("pri 0 bp so provizije nic", sum(p0.values()) < 1e-9))

    _, pu, _, _ = D._knjiga(idx, CENE, SIG, UT, 30, False, so)
    out.append(("brez uravnavanja ni stroska uravnavanja",
                pu["uravnavanje"] < 1e-9))

    cfg = replace(LeanConfig(), symbol_map=D._symbol_map())
    _, _, kb, _ = D._knjiga(idx, CENE, SIG, {"BTC": 1.0}, 30, False, None)
    df = trim_warmup(run_strategy(CENE["BTC"], config=cfg).df)
    p = position(df, cfg).reindex(idx)
    t = traded_fraction(df, cfg).reindex(idx).fillna(0)
    ret = CENE["BTC"]["close"].pct_change().reindex(idx).fillna(0.0)
    v = 100.0
    for i in range(len(idx)):
        v *= 1 + p.iloc[i] * ret.iloc[i] - t.iloc[i] * 0.003
    out.append(("knjiga z enim sredstvom = neposreden izracun",
                abs(kb - v) < 0.01))

    pod = D._podvodni(pot)
    out.append(("podvodni graf ni nikoli nad niclo", pod.max() <= 1e-9))
    pad = D._najhujsi_padci(idx, pot)
    out.append(("najhujsi padec se ujema z MaxDD",
                abs(float(pad["globina"].iloc[0]) - m["maxdd"]) < 0.1))
    faze = D._datiraj(CENE["BTC"]["close"])
    out.append(("datiranje faz da izmenjujoce se faze",
                all(faze[i][2] != faze[i + 1][2] for i in range(len(faze) - 1))))
    return out


def stikalo() -> list[tuple[str, bool]]:
    """Knjizno stikalo na BTC dela to, kar trdi.

    Dve trditvi, ki ju je treba preveriti loceno: da se ob medvedjem BTC res
    NE zgodi nic (knjiga stoji, tudi ce imajo druga sredstva svoj signal), in
    da preklop ni zastonj -- da se placa.
    """
    vsi = tuple(D.VIRI)
    CENE = D._cene.__wrapped__(vsi)
    SIG = D._signali.__wrapped__(vsi, CENE)
    idx = CENE["BTC"].loc["2021-06-01":].index
    UT = {"BTC": 0.5, "ETH": 0.2, "SOL": 0.15, "LINK": 0.15}
    so = SIG["HYPE"][0].index[0]
    r_brez, _, k_brez, _ = D._knjiga(idx, CENE, SIG, UT, 30, False, so,
                                     stikalo=False)
    r_s, prov_s, k_s, _ = D._knjiga(idx, CENE, SIG, UT, 30, False, so,
                                    stikalo=True)
    btc_bear = (SIG["BTC"][0].reindex(idx) <= 0.5).fillna(False).to_numpy()
    # Dni, ko je BTC medvedji IN je bil ze vceraj -- torej brez dneva preklopa.
    mirujoc = btc_bear & np.r_[False, btc_bear[:-1]]
    # Prva razlicica je tu trdila, da se knjiga ob medvedjem BTC NE premakne.
    # To je bilo napacno vedenje: trajno dno se ne proda nikoli, tudi ko knjizno
    # stikalo proda vse ostalo, zato tistih 5 % se naprej niha s ceno.
    #
    # Druga razlicica je nato trdila, da je gibanje `le drobec` tistega brez
    # stikala, in postavila mejo 25 %. Meritev jo je ovrgla: izmerjeno je 78 %,
    # ker so na medvedje dni tudi ostala sredstva ze skoraj pri svojem dnu
    # (izpostavljenost 3 do 11 %). Meja je bila ugibanje, ne trditev o kodi.
    #
    # Zato se zdaj preverja MEHANIZEM neposredno: pri ENEM samem sredstvu in
    # brez provizij mora biti dnevni donos knjige ob izklopljenem stikalu
    # natanko `dno x donos sredstva`. To je definicija in ne dopusca meje.
    r1, _, _, _ = D._knjiga(idx, CENE, SIG, {"ETH": 1.0}, 0, False, so,
                            stikalo=True)
    ret_eth = CENE["ETH"]["close"].pct_change().reindex(idx).fillna(0.0).to_numpy()
    prica = D.DNO * ret_eth[mirujoc]
    dobljeno = np.asarray(r1)[mirujoc]
    out = [("stikalo kaj spremeni", abs(k_s - k_brez) > 1e-6),
           ("ob izklopu drzi natanko dno x donos",
            bool(np.allclose(dobljeno, prica, atol=1e-12))),
           ("dno se ob izklopu se vedno giblje", bool(np.abs(dobljeno).max() > 0)),
           ("preklop ni zastonj", prov_s["signali"] > 0),
           ("brez stikala se nic ne spremeni",
            abs(D._knjiga(idx, CENE, SIG, UT, 30, False, so)[2] - k_brez) < 1e-9)]
    return out


def napake() -> list[tuple[str, bool]]:
    """Kaj se zgodi, ko vir odpove.

    Te preverbe so nastale iz napake, ki jo je stran v oblaku javila kot
    `The original error message is redacted to prevent data leaks` -- torej
    brez ene same koristne besede o tem, kateri vir je padel. Preverjata se
    dve stvari: da 429 pocakamo (ker pomeni `pocasneje`, ne `ne`), in da
    odpoved nasteje VSA sredstva, ne le prvega.
    """
    import time as _t

    from model import data_source as DS

    out = []
    pravi_get, pravi_sleep = DS.requests.get, DS.time.sleep
    cakanja: list[float] = []
    DS.time.sleep = lambda s: cakanja.append(s)

    class Odgovor:
        def __init__(self, code, headers=None):
            self.status_code, self.headers, self.text = code, headers or {}, ""

    try:
        # 1) 429 se pocaka in nato uspe
        zap = [Odgovor(429), Odgovor(429), Odgovor(200)]
        klici = []
        DS.requests.get = lambda *a, **k: (klici.append(1), zap[len(klici) - 1])[1]
        cakanja.clear()
        r = DS._get_pocakaj_na_429("http://x", params={})
        out.append(("429 se pocaka in konca z 200",
                    r.status_code == 200 and len(klici) == 3))
        out.append(("cakanje med poskusi narasca",
                    cakanja == sorted(cakanja) and len(cakanja) == 2))

        # 2) 451 se NE ponavlja -- cakanje kraja ne spremeni
        klici.clear()
        DS.requests.get = lambda *a, **k: (klici.append(1), Odgovor(451))[1]
        r = DS._get_pocakaj_na_429("http://x", params={})
        out.append(("451 se ne ponavlja", r.status_code == 451 and len(klici) == 1))

        # 3) Retry-After prevlada, ce je daljsi od nase lestvice
        klici.clear()
        zap2 = [Odgovor(429, {"Retry-After": "12"}), Odgovor(200)]
        DS.requests.get = lambda *a, **k: (klici.append(1), zap2[len(klici) - 1])[1]
        cakanja.clear()
        DS._get_pocakaj_na_429("http://x", params={})
        out.append(("Retry-After se uposteva", cakanja == [12.0]))

        # 4) vztrajen 429 se vrne klicatelju, da ta izpise svoje sporocilo
        klici.clear()
        DS.requests.get = lambda *a, **k: (klici.append(1), Odgovor(429))[1]
        r = DS._get_pocakaj_na_429("http://x", params={})
        out.append(("vztrajen 429 se vrne klicatelju",
                    r.status_code == 429
                    and len(klici) == len(DS._RATE_LIMIT_WAITS) + 1))
    finally:
        DS.requests.get, DS.time.sleep = pravi_get, pravi_sleep

    # 5) odpoved nasteje VSA padla sredstva, ne le prvega
    pravi_fetch = D.fetch_candles
    try:
        def pade_razen_btc(sym, *a, **k):
            if sym == "BTC":
                return pd.DataFrame({"close": [1.0]},
                                    index=pd.to_datetime(["2020-01-01"]))
            raise RuntimeError(f"HTTP 429 za {sym}")
        D.fetch_candles = pade_razen_btc
        try:
            D._cene.__wrapped__(("BTC", "ETH", "SOL"))
            ujeto = None
        except D.NapakaCen as e:
            ujeto = e
        out.append(("odpoved nasteje vsa padla sredstva",
                    ujeto is not None and len(ujeto.napake) == 2))
        out.append(("odpoved pove tudi, kaj je uspelo",
                    ujeto is not None and ujeto.uspeli == ["BTC"]))
        out.append(("odpoved imenuje vir vsakega sredstva",
                    ujeto is not None
                    and all(v == D.VIRI[s] for s, v, _ in ujeto.napake)))
    finally:
        D.fetch_candles = pravi_fetch
    return out


def izris() -> list[tuple[str, bool]]:
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(KOREN / "app.py"), default_timeout=300)
    at.run()
    if at.exception:
        for e in at.exception:
            print("   NAPAKA NA STRANI: %s" % str(e.value)[:300])
        return [("stran se izrise brez napake", False)]
    out = [("stran se izrise brez napake", True),
           ("ima tabele", len(at.dataframe) >= 3),
           ("ima zavihke", len(at.tabs) >= 5)]

    # Stikalo je privzeto IZKLOPLJENO, zato zgornji zagon njegove poti v
    # vmesniku sploh ne obisce. Racunske preverbe jo sicer pokrivajo, izris pa
    # ne -- in prav tam se skrijejo napake, ki jih racun ne more ujeti.
    kljukice = [c for c in at.sidebar.checkbox
                if "stikalo" in (c.label or "").lower()]
    if not kljukice:
        return out + [("kljukica za stikalo obstaja", False)]
    out.append(("kljukica za stikalo obstaja", True))
    at2 = kljukice[0].set_value(True).run(timeout=300)
    if at2.exception:
        for e in at2.exception:
            print("   NAPAKA S STIKALOM: %s" % str(e.value)[:300])
        return out + [("stran se izrise tudi s stikalom", False)]
    return out + [("stran se izrise tudi s stikalom", True),
                  ("s stikalom ima tabele", len(at2.dataframe) >= 3)]


def main() -> int:
    vse = []
    print("RACUN")
    for ime, ok in racun():
        print("   %-46s %s" % (ime, "OK" if ok else "NAPAKA"))
        vse.append(ok)
    print("\nKNJIZNO STIKALO")
    for ime, ok in stikalo():
        print("   %-46s %s" % (ime, "OK" if ok else "NAPAKA"))
        vse.append(ok)
    print("\nODPOVED VIRA")
    for ime, ok in napake():
        print("   %-46s %s" % (ime, "OK" if ok else "NAPAKA"))
        vse.append(ok)
    print("\nIZRIS")
    for ime, ok in izris():
        print("   %-46s %s" % (ime, "OK" if ok else "NAPAKA"))
        vse.append(ok)
    print()
    if all(vse):
        print("vseh %d preverb je uspelo" % len(vse))
        return 0
    print("NEUSPESNIH: %d od %d" % (sum(1 for x in vse if not x), len(vse)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
