"""Legge le posizioni aperte e le quotazioni collegate (SOLA LETTURA).

Uso:  uv run check_position.py --symbol SPY --port 7496
Non stampa il numero di conto.
"""
from __future__ import annotations

import argparse
import math
import sys

from ib_async import Stock

from ib_common import PORTS, clean, connect, setup_logging


def fmt(x, nd=2):
    x = clean(x)
    return "n/d" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=PORTS["tws-live"])
    p.add_argument("--client-id", type=int, default=103)
    p.add_argument("--market-data-type", type=int, default=1)
    args = p.parse_args()
    log = setup_logging("check_position")
    ib = connect(args.host, args.port, args.client_id)
    try:
        ib.reqMarketDataType(args.market_data_type)
        pos = [q for q in ib.positions() if q.contract.symbol == args.symbol]
        if not pos:
            log.info("Nessuna posizione su %s", args.symbol)
        for q in pos:
            c = q.contract
            log.info("POSIZIONE: %s %s %s %s %s x%s, costo medio per contratto %.2f (moltiplicatore %s)",
                     c.symbol, c.secType, c.lastTradeDateOrContractMonth, c.strike, c.right,
                     q.position, q.avgCost, c.multiplier)
            ib.qualifyContracts(c)
            t = ib.reqMktData(c, "100,101,106", False, False)
            u = Stock(args.symbol, "SMART", "USD"); ib.qualifyContracts(u)
            tu = ib.reqMktData(u, "", False, False)
            ib.sleep(6)
            g = t.modelGreeks
            log.info("Opzione: bid %s ask %s last %s | IV %s delta %s gamma %s vega %s theta %s | prezzo modello %s",
                     fmt(t.bid), fmt(t.ask), fmt(t.last), fmt(g.impliedVol if g else None, 3), fmt(g.delta if g else None, 3),
                     fmt(g.gamma if g else None, 4), fmt(g.vega if g else None, 3), fmt(g.theta if g else None, 3),
                     fmt(g.optPrice if g else None))
            log.info("Sottostante %s: last %s bid %s ask %s chiusura %s", args.symbol, fmt(tu.last), fmt(tu.bid), fmt(tu.ask), fmt(tu.close))
            log.info("Open interest put %s call %s, volume %s", fmt(t.putOpenInterest, 0), fmt(t.callOpenInterest, 0), fmt(t.volume, 0))
            ib.cancelMktData(c); ib.cancelMktData(u)
        # riepilogo del conto: solo grandezze aggregate
        want = {"NetLiquidation", "MaintMarginReq", "InitMarginReq", "ExcessLiquidity", "AvailableFunds", "GrossPositionValue", "TotalCashValue", "Cushion"}
        for a in ib.accountSummary():
            if a.tag in want:
                log.info("CONTO %s = %s %s", a.tag, a.value, a.currency)
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
