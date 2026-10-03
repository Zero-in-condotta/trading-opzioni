"""Valore atteso statistico: previsione di volatilita' HAR e bootstrap dei rendimenti storici.

Sostituisce la log-normale piatta come unico metro. Due misure indipendenti sullo stesso storico
(l'indice padre per i mini-indici):
  * HAR-RV: previsione della volatilita' realizzata nell'orizzonte della struttura da una
    regressione sulle varianze giornaliera, settimanale e mensile (Corsi 2009), al posto del
    massimo delle ultime tre finestre.
  * BOOTSTRAP: tutti i rendimenti storici sovrapposti a quell'orizzonte (anni di crisi inclusi:
    2008, 2018, 2020, 2022, 2024) come distribuzione del sottostante a scadenza. Si toglie la
    deriva (martingala al tasso 2%) per isolare coda e volatilita' dal premio azionario.
Il valore atteso 'prudente' di una struttura e' il minore dei due.
Nessuna rete se i dati sono gia' in memoria; la serie si scarica una volta per processo.
"""
from __future__ import annotations
import datetime as dt
import math

import numpy as np

R_FREE = 0.02
_SERIE: dict[str, np.ndarray] = {}
_CACHE_RET: dict[tuple, np.ndarray] = {}


def serie(ticker, anni=25):
    """Chiusure giornaliere (numpy), il piu' possibile fino a `anni` anni; vuoto se non disponibili."""
    if ticker in _SERIE:
        return _SERIE[ticker]
    try:
        import yfinance as yf
        h = yf.download(ticker, period=f"{anni}y", auto_adjust=True, progress=False)["Close"].squeeze()
        c = np.asarray(h.dropna().values, dtype=float)
    except Exception:
        c = np.array([])
    c = c[np.isfinite(c) & (c > 0)]
    _SERIE[ticker] = c
    return c


def vol_har(ticker, dte, anni=12):
    """Volatilita' annualizzata prevista per i prossimi `dte` giorni di calendario (HAR su r^2)."""
    c = serie(ticker)
    if len(c) < 400:
        return None
    c = c[-(anni * 252 + 60):]
    r = np.diff(np.log(c))
    v = r * r
    H = max(2, int(round(dte * 252 / 365)))
    n = len(v)
    cs = np.concatenate([[0.0], np.cumsum(v)])
    def media(a, b):                               # media di v[a:b]
        return (cs[b] - cs[a]) / (b - a)
    xs, ys = [], []
    for t in range(21, n - H):
        xs.append((v[t], media(t - 4, t + 1), media(t - 21, t + 1)))
        ys.append(media(t + 1, t + 1 + H))
    if len(xs) < 300:
        return None
    X = np.column_stack([np.ones(len(xs)), np.array(xs)])
    beta, *_ = np.linalg.lstsq(X, np.array(ys), rcond=None)
    t = n - 1
    x_ora = np.array([1.0, v[t], media(t - 4, t + 1), media(t - 21, t + 1)])
    var_d = float(x_ora @ beta)
    # un modello lineare puo' dare un valore negativo o fuori scala: si tiene dentro un intervallo ragionevole
    ref = float(np.sqrt(media(n - 60, n) * 252))
    vol = math.sqrt(max(var_d, 1e-8) * 252)
    return float(min(max(vol, 0.5 * ref, 0.05), 2.5 * ref + 0.05))


def rendimenti(ticker, dte):
    """Log-rendimenti storici sovrapposti a `dte` giorni, corretti a martingala al 2%."""
    c = serie(ticker)
    if len(c) < 800:
        return np.array([])
    H = max(2, int(round(dte * 252 / 365)))
    k = (ticker, H)
    if k in _CACHE_RET:
        return _CACHE_RET[k]
    lr = np.log(c[H:] / c[:-H])
    T = dte / 365
    shift = R_FREE * T - math.log(float(np.mean(np.exp(lr))))     # E[S_T] = S0 * exp(r T)
    out = lr + shift
    if len(out) > 4000:                                           # tiene la distribuzione, riduce il costo
        out = out[np.linspace(0, len(out) - 1, 4000).astype(int)]
    _CACHE_RET[k] = out
    return out


def _ncdf(x):
    """Phi(x) per array, approssimazione di Abramowitz-Stegun (errore < 1e-7)."""
    t = 1.0 / (1.0 + 0.2316419 * np.abs(x))
    d = 0.3989423 * np.exp(-x * x / 2.0)
    p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
    return np.where(x >= 0, 1.0 - p, p)


def _bs(S, K, T, s, cp, r=R_FREE):
    S = np.asarray(S, dtype=float)
    if T <= 0 or not s:
        return np.maximum(S - K, 0.0) if cp == "C" else np.maximum(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * s * s) * T) / (s * math.sqrt(T))
    d2 = d1 - s * math.sqrt(T)
    if cp == "C":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def _data(e):
    return dt.datetime.strptime(e, "%Y%m%d").date()


def pl_array(legs, S):
    """P/L totale della struttura (valuta del contratto) per un array di prezzi S alla scadenza piu'
    vicina; le gambe piu' lunghe sono rivalutate con Black-Scholes alla loro IV (come in deals)."""
    S = np.asarray(S, dtype=float)
    T0 = min(_data(l["expiry"]) for l in legs)
    tot = np.zeros_like(S)
    costo = 0.0
    for l in legs:
        rest = (_data(l["expiry"]) - T0).days / 365
        v = _bs(S, l["strike"], rest, l.get("iv") or 0, l["right"])
        tot = tot + l["position"] * l["mult"] * v
        costo += l["position"] * l["avgCost"]
    return tot - costo


def ev_storico(legs, ticker):
    """(valore atteso, probabilita' di utile, 5o percentile del P/L) sulla distribuzione storica,
    oppure None se la serie non c'e'. `legs` nel formato di deals/gamba (con und e iv)."""
    spot = next((l.get("und") for l in legs if l.get("und")), None)
    if not spot:
        return None
    T0 = min(_data(l["expiry"]) for l in legs)
    dte = (T0 - dt.date.today()).days
    if dte < 1:
        return None
    lr = rendimenti(ticker, dte)
    if not len(lr):
        return None
    pl = pl_array(legs, spot * np.exp(lr))
    return float(pl.mean()), float((pl > 0).mean()), float(np.percentile(pl, 5))


def pl_scenario(legs, spot, dm):
    """P/L della struttura alla scadenza piu' vicina se il sottostante varia di dm (es. -0,20)."""
    return float(pl_array(legs, np.array([spot * (1 + dm)]))[0])


# ---------------- valore atteso log-normale, vettoriale (sostituisce deals.misure nello scanner) ----------------
_EDGES = np.linspace(-1.6, 1.0, 1501)             # log(S/S0): da 0,20x a 2,7x il prezzo
_MID = (_EDGES[:-1] + _EDGES[1:]) / 2


class EvLN:
    """Valore atteso e probabilita' di utile alla scadenza piu' vicina con densita' log-normale di
    volatilita' `vol`. Il P/L della struttura si calcola UNA volta; poi ogni volatilita' e' solo un
    prodotto scalare: trovare la volatilita' di pareggio costa microsecondi invece di un secondo.
    Le gambe piu' lunghe si prezzano alla loro IV (come in deals). Stessa convenzione di deals.misure:
    deriva r = 2%, code sotto 0,2x e sopra 2,7x assegnate a un prezzo limite."""

    def __init__(self, legs):
        self.spot = next((l.get("und") for l in legs if l.get("und")), None)
        T0 = min(_data(l["expiry"]) for l in legs)
        self.T = (T0 - dt.date.today()).days / 365
        self.ok = bool(self.spot and self.T > 0)
        if self.ok:
            self.pl = pl_array(legs, self.spot * np.exp(_MID))
            self.pl_lo = float(pl_array(legs, np.array([self.spot * math.exp(_EDGES[0]) * 0.5]))[0])
            self.pl_hi = float(pl_array(legs, np.array([self.spot * math.exp(_EDGES[-1]) * 1.5]))[0])

    def ev(self, vol):
        if not self.ok or not vol or vol <= 0:
            return None, None
        s = vol * math.sqrt(self.T)
        mu = (R_FREE - 0.5 * vol * vol) * self.T
        cdf = _ncdf((_EDGES - mu) / s)
        p = np.diff(cdf)
        ev = float(p @ self.pl + cdf[0] * self.pl_lo + (1 - cdf[-1]) * self.pl_hi)
        prob = float(p[self.pl > 0].sum() + (cdf[0] if self.pl_lo > 0 else 0) + ((1 - cdf[-1]) if self.pl_hi > 0 else 0))
        return ev, max(0.0, min(1.0, prob))

    def vol_pareggio(self, lo=0.02, hi=1.0, giri=40):
        """(volatilita', verso): quella alla quale il valore atteso va a zero. verso 'sotto' = si guadagna se
        la volatilita' realizzata resta sotto quel valore (chi vende premio); 'sopra' = il contrario."""
        fa, fb = self.ev(lo)[0], self.ev(hi)[0]
        if fa is None or fb is None or (fa > 0) == (fb > 0):
            return None, None
        verso = "sotto" if fa > 0 else "sopra"
        for _ in range(giri):
            m = (lo + hi) / 2
            fm = self.ev(m)[0]
            if (fm > 0) == (fa > 0):
                lo = m
            else:
                hi = m
        return (lo + hi) / 2, verso
