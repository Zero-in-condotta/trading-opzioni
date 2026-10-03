"""Funzioni comuni. SOLA LETTURA: in questo progetto non si inviano ordini.

La connessione usa readonly=True: anche se per errore un giorno si chiamasse
placeOrder, l'API in sola lettura lo rifiuterebbe.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from ib_async import IB, Contract, Index

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LOGS = ROOT / "logs"

# Porte predefinite: TWS reale 7496, TWS paper 7497, Gateway reale 4001, Gateway paper 4002.
PORTS = {"tws-live": 7496, "tws-paper": 7497, "gw-live": 4001, "gw-paper": 4002}


def setup_logging(name: str) -> logging.Logger:
    LOGS.mkdir(exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fh = logging.FileHandler(LOGS / f"{name}.log")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


def connect(host: str, port: int, client_id: int, timeout: float = 15.0) -> IB:
    ib = IB()
    ib.connect(host, port, clientId=client_id, readonly=True, timeout=timeout)
    return ib


def index_contract(symbol: str) -> Contract:
    """Indici CBOE: SPX, XSP, VIX (sono tutti indici CBOE)."""
    return Index(symbol, "CBOE")


def clean(x):
    """IBKR usa nan e -1 per 'assente': convertiamo in None.
    Il -1 arriva a volte come int puro (non float), va controllato comunque
    (bug trovato il 22/09/2026: un bid -1 intero passava indenne e falsava un credito)."""
    if x is None:
        return None
    try:
        if isinstance(x, (int, float)) and (x == -1 or (isinstance(x, float) and math.isnan(x))):
            return None
    except TypeError:
        pass
    return x


def spot_of(ib: IB, contract: Contract, wait: float = 4.0):
    """Ultimo prezzo disponibile (last, altrimenti mid, altrimenti chiusura precedente)."""
    t = ib.reqMktData(contract, "", False, False)
    ib.sleep(wait)
    price = None
    for candidate in (t.last, t.marketPrice(), t.close):
        c = clean(candidate)
        if c is not None and not (isinstance(c, float) and math.isnan(c)) and c > 0:
            price = float(c)
            break
    ib.cancelMktData(contract)
    return price
