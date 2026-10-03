"""Verifica la connessione all'API di IBKR (sola lettura) e legge SPX, XSP e VIX.

Uso:
    uv run check_connection.py                    # TWS paper, porta 7497
    uv run check_connection.py --port 7496        # TWS reale
    uv run check_connection.py --market-data-type 3   # dati ritardati
"""
from __future__ import annotations

import argparse
import sys

from ib_common import PORTS, connect, index_contract, setup_logging, spot_of


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=PORTS["tws-paper"])
    p.add_argument("--client-id", type=int, default=101)
    p.add_argument("--market-data-type", type=int, default=1,
                   help="1 = tempo reale, 2 = congelati, 3 = ritardati, 4 = ritardati congelati")
    args = p.parse_args()
    log = setup_logging("check_connection")

    try:
        ib = connect(args.host, args.port, args.client_id)
    except Exception as exc:  # noqa: BLE001
        log.error("Connessione fallita su %s:%s (%s). Controlla che TWS sia acceso e che "
                  "l'API sia abilitata (Configurazione globale > API > Impostazioni).",
                  args.host, args.port, exc)
        return 1

    try:
        log.info("Connesso. Versione server: %s", ib.client.serverVersion())
        log.info("Ora del server IBKR: %s", ib.reqCurrentTime())
        ib.reqMarketDataType(args.market_data_type)
        for sym in ("SPX", "XSP", "VIX"):
            c = index_contract(sym)
            ib.qualifyContracts(c)
            log.info("%s: conId=%s, prezzo=%s", sym, c.conId, spot_of(ib, c))
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
