"""Test offline: nessun TWS, nessuna rete. Verifica che i calcoli statistici coincidano con quelli di riferimento, che lo
scanner costruisca tutte le famiglie di strutture su un mercato simulato e che i filtri di presentazione funzionino.
Uso (dalla radice del repository):  uv run python tests/test_offline.py"""
import datetime as dt
import os
import sys
from types import SimpleNamespace as NS

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "opzioni"))
sys.path.insert(0, AQUI)

import deals          # noqa: E402
import occ_render     # noqa: E402
import scan_vrp as sv  # noqa: E402
import stat_ev        # noqa: E402
from offline_market import FakeIB  # noqa: E402

fallimenti = []


def controlla(nome, condizione, dettaglio=""):
    print(("  ok   " if condizione else "  FAIL ") + nome + (f"  [{dettaglio}]" if dettaglio else ""))
    if not condizione:
        fallimenti.append(nome)


def gamba(strike, tipo, pos, scad, iv, costo):
    return dict(symbol="XSP", expiry=scad, strike=float(strike), right=tipo, position=pos, avgCost=costo, mult=100, iv=iv, und=772.0)


print("1. valore atteso vettoriale contro il calcolo di riferimento (deals.misure)")
f1 = (dt.date.today() + dt.timedelta(days=49)).strftime("%Y%m%d")
f2 = (dt.date.today() + dt.timedelta(days=77)).strftime("%Y%m%d")
casi = {
    "put spread": [gamba(740, "P", -1, f1, .17, 900.), gamba(730, "P", 1, f1, .18, 800.)],
    "calendar": [gamba(760, "P", -1, f1, .15, 900.), gamba(760, "P", 1, f2, .156, 1270.)],
}
for nome, legs in casi.items():
    o = stat_ev.EvLN(legs)
    for vol in (0.12, 0.17, 0.25):
        a = deals.misure(legs, iv=0.17, vol=vol, n=1200)
        b = o.ev(vol)
        scarto = abs(a[0] - b[0]) / max(abs(a[0]), 1.0)
        controlla(f"{nome} vol {vol:.2f}", scarto < 0.02, f"deals {a[0]:+.1f} vettoriale {b[0]:+.1f}")
    v0, verso = o.vol_pareggio()
    controlla(f"{nome}: volatilita' di pareggio trovata", v0 is not None and verso in ("sotto", "sopra"), f"{v0} {verso}")

print("2. scanner su mercato simulato (finestra 25-120 giorni, solo mensili)")
a = NS(width=None, banda=None, dte=(25, 120), delta=(0.08, 0.32), weekly=False)
righe = []
res = sv.analizza(FakeIB(), "XSP", a, lambda d: 0.16, lambda d: 0.16, on_rows=lambda rows, **k: righe.extend(rows), cfg=sv.config_sottostante("XSP"))
tipi = {r["tipo"] for r in res}
controlla("righe prodotte", len(res) > 500, f"{len(res)}")
controlla("le righe arrivano anche a flusso", len(righe) == len(res))
for t in ("bull put", "bear call", "iron condor", "calendar put", "bwb put", "put backspread 1x2", "ratio put 1x2"):
    controlla(f"famiglia {t}", t in tipi)

print("3. filtri di presentazione")
stato = dict(nav=149000.0, fx={"EUR": 1.0, "USD": 1.12}, conc={}, posizioni=[],
             protezioni=[dict(sym="XSP", strike=630.0, right="P", qty=1.0, mult=100.0, prezzo_ora=5.0, cur="USD")])
pos = []
for i, r in enumerate(res):
    if r.get("ev_rv") is not None:
        r2 = dict(r, id=f"XSP-{i}", ev_vol=r["ev_rv"], ev_st=None)
        pos.append(r2)
vis, nasc = occ_render.filtra(pos, stato, rigidi=True, ordine="er")
controlla("i put ratio non superano il tetto del 2% NAV", all(not o["tipo"].startswith("ratio put") for o in vis))
if vis:
    controlla("scheda HTML generata", len(occ_render.li_html(vis[0], stato, "oggi")) > 500)
rp = next((o for o in pos if o["tipo"].startswith("ratio put") and o.get("pl20") is not None), None)
if rp:
    controlla("rischio effettivo del put ratio inferiore al teorico", occ_render.rischio_eff(rp, stato) < rp["rischio"])
else:
    print("  (nessun put ratio con P/L a -20%: ok senza il gancio statistico)")

print()
if fallimenti:
    print("FALLITI:", ", ".join(fallimenti))
    sys.exit(1)
print("TUTTI I TEST OFFLINE SUPERATI")
