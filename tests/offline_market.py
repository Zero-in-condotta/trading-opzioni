"""Mercato simulato per provare lo scanner SENZA TWS: una catena di opzioni con prezzi Black-Scholes, skew e struttura a termine.
Espone la stessa interfaccia minima di ib_async.IB usata da scan_vrp.py (qualifyContracts, reqSecDefOptParams,
reqContractDetails, reqMktData, sleep, cancelMktData)."""
import datetime as dt
from math import log, sqrt
from statistics import NormalDist
from types import SimpleNamespace as NS

import numpy as np
from ib_async import Option

N = NormalDist().cdf
SPOT = 772.0


class Tk:
    def __init__(self, **k):
        self.__dict__.update(dict(bid=None, ask=None, last=None, close=None, modelGreeks=None,
                                  callOpenInterest=500.0, putOpenInterest=500.0, volume=100))
        self.__dict__.update(k)

    def marketPrice(self):
        return self.last


class FakeIB:
    """Finta connessione: sottostante a SPOT, venerdi' come scadenze, volatilita' con skew a sinistra."""

    def __init__(self, spot=SPOT):
        self.n = 1000
        self.spot = spot

    def sleep(self, x=0): pass
    def cancelMktData(self, c): pass
    def disconnect(self): pass
    def reqMarketDataType(self, t): pass

    def qualifyContracts(self, *cs):
        for c in cs:
            c.conId = c.conId or 1
        return list(cs)

    def reqSecDefOptParams(self, sym, fut, st, cid):
        oggi = dt.date.today()
        return [NS(expirations=sorted({(oggi + dt.timedelta(days=d)).strftime("%Y%m%d")
                                       for d in range(7, 260) if (oggi + dt.timedelta(days=d)).weekday() == 4}))]

    def reqContractDetails(self, c):
        out = []
        for k in range(int(self.spot * 0.8), int(self.spot * 1.2)):
            self.n += 1
            o = Option(c.symbol, c.lastTradeDateOrContractMonth, float(k), c.right, c.exchange, currency=c.currency, multiplier="100")
            o.conId = self.n
            out.append(NS(contract=o))
        return out

    def reqMktData(self, c, *a):
        if c.secType == "IND":
            return Tk(last=self.spot, close=self.spot)
        import deals
        T = (dt.datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date() - dt.date.today()).days / 365
        m = log(c.strike / self.spot)
        iv = max(0.13 + (-0.5 * m if m < 0 else -0.15 * m) + 0.02 * np.sqrt(0.1 / max(T, 0.02)), 0.08)
        p = float(deals.bs(self.spot, c.strike, T, iv, c.right))
        d1 = (log(self.spot / c.strike) + (0.02 + 0.5 * iv * iv) * T) / (iv * sqrt(T))
        dl = N(d1) if c.right == "C" else N(d1) - 1
        sp = max(0.05, p * 0.012)
        return Tk(bid=round(p - sp / 2, 2), ask=round(p + sp / 2, 2), last=p,
                  modelGreeks=NS(impliedVol=iv, delta=dl, optPrice=p, undPrice=self.spot))
