"""Raccoglie una fotografia della catena di opzioni (SOLA LETTURA) e la salva in Parquet.

Non invia ordini. Per ogni scadenza con DTE fra --min-dte e --max-dte e per gli strike
entro +-width dal prezzo del sottostante salva bid, ask, ultimo, dimensioni, volume,
open interest e greche del modello IBKR (IV, delta, gamma, vega, theta).

I dati di mercato vengono richiesti a blocchi (--batch) per rispettare il limite di
linee simultanee di IBKR (di solito 100 per conti piccoli).

Uso:
    uv run collect_chain.py --underlying XSP --dry-run            # conta i contratti, nessun dato
    uv run collect_chain.py --underlying XSP --market-data-type 3 # dati ritardati (paper)
    uv run collect_chain.py --underlying SPX --trading-classes SPX,SPXW --strike-step 5

Output: data/raw/<SIMBOLO>/<AAAA-MM-GG>/chain_<HHMMSS>.parquet
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from math import isnan

import pandas as pd
from ib_async import Option

from ib_common import DATA, PORTS, clean, connect, index_contract, setup_logging, spot_of

TRADING_CLASSES = {"XSP": ["XSP", "XSPW"], "SPX": ["SPX", "SPXW"]}


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def build_contracts(ib, und, spot, args, log):
    chains = ib.reqSecDefOptParams(und.symbol, "", und.secType, und.conId)
    wanted = args.trading_classes or TRADING_CLASSES.get(und.symbol, [und.symbol])
    today = date.today()
    lo, hi = spot * (1 - args.width), spot * (1 + args.width)
    out = []
    for ch in chains:
        if ch.tradingClass not in wanted:
            continue
        if ch.exchange not in ("SMART", "CBOE"):
            continue
        for exp in sorted(ch.expirations):
            dte = (datetime.strptime(exp, "%Y%m%d").date() - today).days
            if not (args.min_dte <= dte <= args.max_dte):
                continue
            for k in sorted(ch.strikes):
                if not (lo <= k <= hi):
                    continue
                if args.strike_step and (k % args.strike_step) != 0:
                    continue
                for right in ("P", "C"):
                    out.append(Option(und.symbol, exp, k, right, "SMART",
                                      tradingClass=ch.tradingClass, currency="USD"))
    log.info("Contratti candidati: %d (classi %s, DTE %d-%d, strike %.0f-%.0f)",
             len(out), wanted, args.min_dte, args.max_dte, lo, hi)
    return out


def row_of(ticker, und_symbol, spot, ts):
    c = ticker.contract
    g = ticker.modelGreeks
    oi = clean(ticker.callOpenInterest if c.right == "C" else ticker.putOpenInterest)
    exp = datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date()
    return {
        "ts": ts, "underlying": und_symbol, "spot": spot,
        "expiry": exp, "dte": (exp - ts.date()).days,
        "right": c.right, "strike": c.strike, "trading_class": c.tradingClass, "con_id": c.conId,
        "bid": clean(ticker.bid), "ask": clean(ticker.ask), "last": clean(ticker.last),
        "bid_size": clean(ticker.bidSize), "ask_size": clean(ticker.askSize),
        "volume": clean(ticker.volume), "open_interest": oi,
        "iv": clean(g.impliedVol) if g else None, "delta": clean(g.delta) if g else None,
        "gamma": clean(g.gamma) if g else None, "vega": clean(g.vega) if g else None,
        "theta": clean(g.theta) if g else None, "und_price": clean(g.undPrice) if g else None,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--underlying", default="XSP", choices=["XSP", "SPX", "VIX"])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=PORTS["tws-paper"])
    p.add_argument("--client-id", type=int, default=102)
    p.add_argument("--market-data-type", type=int, default=1)
    p.add_argument("--min-dte", type=int, default=7)
    p.add_argument("--max-dte", type=int, default=100)
    p.add_argument("--width", type=float, default=0.12, help="ampiezza degli strike, es. 0.12 = +-12%%")
    p.add_argument("--strike-step", type=float, default=0, help="tieni solo strike multipli di questo passo (0 = tutti)")
    p.add_argument("--trading-classes", type=lambda s: s.split(","), default=None)
    p.add_argument("--batch", type=int, default=80, help="contratti richiesti per volta (< limite linee)")
    p.add_argument("--wait", type=float, default=6.0, help="secondi di attesa per ogni blocco")
    p.add_argument("--dry-run", action="store_true", help="non richiede dati di mercato: conta e basta")
    args = p.parse_args()
    log = setup_logging("collect_chain")

    try:
        ib = connect(args.host, args.port, args.client_id)
    except Exception as exc:  # noqa: BLE001
        log.error("Connessione fallita su %s:%s (%s)", args.host, args.port, exc)
        return 1

    try:
        ib.reqMarketDataType(args.market_data_type)
        und = index_contract(args.underlying)
        ib.qualifyContracts(und)
        spot = spot_of(ib, und)
        if not spot:
            log.error("Prezzo del sottostante non disponibile: mercato chiuso o dati non abilitati")
            return 2
        log.info("%s = %.2f", args.underlying, spot)

        contracts = build_contracts(ib, und, spot, args, log)
        if args.dry_run or not contracts:
            log.info("Dry run: nessun dato di mercato richiesto.")
            return 0

        # Toglie i doppioni (la stessa scadenza può comparire in due classi di negoziazione).
        seen, uniq = set(), []
        for c in contracts:
            k = (c.lastTradeDateOrContractMonth, c.strike, c.right)
            if k not in seen:
                seen.add(k); uniq.append(c)
        contracts = uniq
        # Toglie i contratti inesistenti (blocchi da 50 per non superare il limite di richieste).
        valid = []
        for blk in chunks(contracts, 50):
            valid.extend(c for c in ib.qualifyContracts(*blk) if c and c.conId)
        log.info("Contratti validi: %d", len(valid))

        ts = datetime.now()
        rows = []
        for i, blk in enumerate(chunks(valid, args.batch), 1):
            tickers = [ib.reqMktData(c, "100,101", False, False) for c in blk]
            ib.sleep(args.wait)
            for t in tickers:
                rows.append(row_of(t, args.underlying, spot, ts))
            for c in blk:
                ib.cancelMktData(c)
            log.info("Blocco %d/%d completato (%d righe)", i, -(-len(valid) // args.batch), len(rows))

        df = pd.DataFrame(rows).drop_duplicates(subset=["con_id"]).reset_index(drop=True)
        # Righe senza quotazione (bid e ask assenti) le teniamo: dicono che la catena era vuota.
        outdir = DATA / "raw" / args.underlying / ts.strftime("%Y-%m-%d")
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / f"chain_{ts.strftime('%H%M%S')}.parquet"
        df.to_parquet(path, index=False)
        quoted = int(((df["bid"].notna()) & (df["ask"].notna())).sum())
        log.info("Salvato %s: %d righe, %d con bid e ask", path, len(df), quoted)
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
