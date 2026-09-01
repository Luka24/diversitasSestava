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


def izris() -> list[tuple[str, bool]]:
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(KOREN / "app.py"), default_timeout=300)
    at.run()
    if at.exception:
        for e in at.exception:
            print("   NAPAKA NA STRANI: %s" % str(e.value)[:300])
        return [("stran se izrise brez napake", False)]
    return [("stran se izrise brez napake", True),
            ("ima tabele", len(at.dataframe) >= 3),
            ("ima zavihke", len(at.tabs) >= 5)]


def main() -> int:
    vse = []
    print("RACUN")
    for ime, ok in racun():
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
