"""Bozza di data/portfolio_map.json dalle posizioni reali del conto (SOLA LETTURA).

Serve quando si ricostruisce l'ambiente su un nuovo computer: le posizioni stanno su IBKR, ma il RAGGRUPPAMENTO delle gambe
in strutture (bull put, condor, calendar...) esiste solo nella mappa locale. Qui si crea un gruppo per ogni coppia
(sottostante, scadenza piu' vicina del lotto): e' solo il punto di partenza, poi si riunisce a mano (o con Claude)
in strutture vere e si danno nomi. Se due strutture condividono una gamba, aggiungere 'qty' (vedi config.example).

Uso:  uv run mappa_da_posizioni.py            -> scrive data/portfolio_map.draft.json e stampa il riepilogo
Non stampa il numero di conto e non invia ordini.
"""
from __future__ import annotations
import json
from collections import defaultdict

from ib_common import DATA, connect

ib = connect("127.0.0.1", 7496, 112)
try:
    ib.sleep(3)
    gruppi = defaultdict(list)
    azioni = []
    for p in ib.portfolio():
        c = p.contract
        if c.secType != "OPT":
            azioni.append(f"{c.symbol} {c.secType} x{p.position:g} (non opzione: da mappare a mano se serve)")
            continue
        gruppi[(c.symbol, c.lastTradeDateOrContractMonth)].append(
            dict(symbol=c.symbol, expiry=c.lastTradeDateOrContractMonth, strike=float(c.strike), right=c.right, _posizione=p.position))
    out = {"groups": []}
    for (sym, exp), legs in sorted(gruppi.items()):
        out["groups"].append({
            "id": f"{sym.lower()}_{exp}", "nome": f"{sym} - da classificare ({exp})",
            "note": "BOZZA automatica: riunire le gambe in strutture vere e dare un nome",
            "legs": [{k: v for k, v in l.items() if k != "_posizione"} for l in legs]})
        print(f"{sym} {exp}: " + ", ".join(f"{l['right']}{l['strike']:g} x{l['_posizione']:+g}" for l in legs))
    DATA.mkdir(exist_ok=True)
    (DATA / "portfolio_map.draft.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    for a in azioni:
        print("AVVISO:", a)
    print(f"\nScritta la bozza con {len(out['groups'])} gruppi in data/portfolio_map.draft.json: va rivista e rinominata in portfolio_map.json")
finally:
    ib.disconnect()
