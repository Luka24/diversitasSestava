"""Preveri, ali so vsi viri cen dosegljivi.

    python scripts/preveri_vire.py

Pozeni to najprej, ce dashboard javi, da nima podatkov. Vsako sredstvo ima
tocno en vir in nadomestnega ni, ker bi tiho spremenil vse izracunane stevilke.
"""
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model.config import LeanConfig
from model.data_source import fetch_candles
from ui.dashboard import VIRI, _symbol_map


def main() -> int:
    cfg = replace(LeanConfig(), symbol_map=_symbol_map())
    napak = 0
    print("%-6s%-14s%8s  %s" % ("simbol", "vir", "vrstic", "obdobje"))
    for s, vir in VIRI.items():
        try:
            d = fetch_candles(s, "1d", bars=5000, config=cfg, prefer=vir,
                              strict=True)
            print("%-6s%-14s%8d  %s do %s"
                  % (s, vir, len(d), d.index[0].date(), d.index[-1].date()))
        except Exception as e:
            napak += 1
            print("%-6s%-14s   NAPAKA  %s" % (s, vir, str(e)[:70]))
    print()
    if napak:
        print("nedosegljivih virov: %d" % napak)
        return 1
    print("vsi viri so dosegljivi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
