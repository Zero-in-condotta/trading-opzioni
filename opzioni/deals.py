"""Nome della strategia complessiva e massimo profitto / massima perdita di un gruppo di gambe (SOLA LETTURA).

Gamba: dict con symbol, expiry (AAAAMMGG), strike, right (P/C), position, avgCost (per contratto, moltiplicatore
incluso, come lo da' IBKR), mult, currency, e opzionalmente iv e und (prezzo del sottostante).

Massimo profitto / perdita:
  - gambe con la stessa scadenza: payoff esatto a scadenza meno il costo pagato (o piu' il credito incassato);
    se il payoff continua a salire (o scendere) oltre l'ultimo strike, il valore e' "illimitato".
  - scadenze diverse (calendar): stima alla scadenza piu' vicina, con le gambe lunghe rivalutate con
    Black-Scholes alla loro IV attuale. E' una stima, segnalata come tale.
  - sottostanti diversi nello stesso gruppo (protezioni): si calcola per sottostante e si sommano gli estremi."""
from __future__ import annotations
import datetime as dt
from math import log, sqrt, exp
from statistics import NormalDist

N = NormalDist().cdf


def bs(S, K, T, s, cp, r=0.02):
    if T <= 0 or s <= 0 or S <= 0:
        return max(S - K, 0) if cp == "C" else max(K - S, 0)
    d1 = (log(S / K) + (r + .5 * s * s) * T) / (s * sqrt(T)); d2 = d1 - s * sqrt(T)
    return S * N(d1) - K * exp(-r * T) * N(d2) if cp == "C" else K * exp(-r * T) * N(-d2) - S * N(-d1)


def implied_vol(price, S, K, T, cp):
    if not price or not S or T <= 0:
        return None
    lo, hi = 0.01, 3.0
    for _ in range(60):
        m = (lo + hi) / 2
        lo, hi = (lo, m) if bs(S, K, T, m, cp) > price else (m, hi)
    return (lo + hi) / 2


def _date(e):
    return dt.datetime.strptime(e, "%Y%m%d").date()


def name(legs):
    """Nome della strategia complessiva, dalla forma delle gambe."""
    opts = [l for l in legs if l.get("right") in ("P", "C")]
    if not opts or len(opts) != len(legs):
        return "Combinazione"
    syms = {l["symbol"] for l in opts}
    if len(syms) > 1:
        return "Protezioni di coda (put lunghe)" if all(l["right"] == "P" and l["position"] > 0 for l in opts) else "Combinazione su più sottostanti"
    exps = {l["expiry"] for l in opts}; rights = {l["right"] for l in opts}
    longs = [l for l in opts if l["position"] > 0]; shorts = [l for l in opts if l["position"] < 0]
    if len(opts) == 1:
        l = opts[0]
        return f"{'Put' if l['right'] == 'P' else 'Call'} {'lunga' if l['position'] > 0 else 'venduta scoperta'}"
    if len(exps) == 2 and len(opts) == 2 and len({l['strike'] for l in opts}) == 1 and longs and shorts:
        return f"Calendar {'put' if opts[0]['right'] == 'P' else 'call'}"
    if len(exps) == 2 and len(opts) == 4 and rights == {"P", "C"}:
        return "Double calendar"
    if len(exps) == 1 and rights == {"C"} and longs and shorts:
        ql = sum(l["position"] for l in longs); qs = -sum(l["position"] for l in shorts)
        if ql == 2 * qs and max(l["strike"] for l in longs) < min(l["strike"] for l in shorts):
            return "ZEBRA (sostituto dell'azione, rapporto 2:1)"
    if len(exps) == 1 and len(opts) == 2 and len(rights) == 1 and longs and shorts and longs[0]["position"] == -shorts[0]["position"]:
        lk, sk = longs[0]["strike"], shorts[0]["strike"]
        if opts[0]["right"] == "P":
            return "Bull put spread (a credito)" if sk > lk else "Bear put spread (a debito)"
        return "Bull call spread (a debito)" if lk < sk else "Bear call spread (a credito)"
    if len(exps) == 1 and len(opts) == 4 and rights == {"P", "C"}:
        return "Iron condor / iron butterfly"
    return "Combinazione"


def _pl_at(S, legs, T0):
    """P/L totale (valuta del gruppo) se il sottostante vale S alla data T0 (scadenza piu' vicina)."""
    tot = 0.0
    for l in legs:
        K, cp, q, m = l["strike"], l["right"], l["position"], l["mult"]
        rest = (_date(l["expiry"]) - T0).days / 365
        v = bs(S, K, rest, l.get("iv") or 0, cp) if rest > 0 else (max(S - K, 0) if cp == "C" else max(K - S, 0))
        tot += q * m * v
    return tot - sum(l["position"] * l["avgCost"] for l in legs)


def extremes(legs):
    """Ritorna (max_profitto, max_perdita, stimato) in valuta; None = illimitato."""
    by_sym = {}
    for l in legs:
        by_sym.setdefault(l["symbol"], []).append(l)
    mx_tot, mn_tot, est = 0.0, 0.0, False
    for sym, ls in by_sym.items():
        T0 = min(_date(l["expiry"]) for l in ls)
        multi = len({l["expiry"] for l in ls}) > 1
        if multi and any(not l.get("iv") for l in ls if _date(l["expiry"]) > T0):
            return "n/d", "n/d", True
        est = est or multi
        kmax = max(l["strike"] for l in ls)
        grid = sorted({0.0, *[l["strike"] for l in ls], *[kmax * i / 400 for i in range(1, 1201)]})
        vals = [_pl_at(S, ls, T0) for S in grid]
        # pendenza oltre l'ultimo strike: call nette comprate -> profitto illimitato, vendute -> perdita illimitata
        slope = sum(l["position"] * l["mult"] for l in ls if l["right"] == "C" and _date(l["expiry"]) == T0) if not multi else 0
        mx = None if slope > 0 else max(vals)
        mn = None if slope < 0 else min(vals)
        mx_tot = None if (mx is None or mx_tot is None) else mx_tot + mx
        mn_tot = None if (mn is None or mn_tot is None) else mn_tot + mn
    return mx_tot, mn_tot, est


def pl_al_livello(legs):
    """P/L a scadenza se il sottostante resta al livello di ADESSO.

    La scadenza di riferimento e' la piu' vicina (per sottostante, come in `extremes`): le gambe
    con scadenza successiva vengono rivalutate con Black-Scholes alla loro volatilita implicita
    attuale. Risponde alla domanda "se il mercato si fermasse qui, questa posizione scadrebbe in
    utile o in perdita?" e NON tiene conto di quanto il sottostante possa ancora muoversi.
    Ritorna None quando manca il prezzo del sottostante o la IV di una gamba lunga."""
    by_sym = {}
    for l in legs:
        by_sym.setdefault(l["symbol"], []).append(l)
    tot = 0.0
    for sym, ls in by_sym.items():
        S = next((l.get("und") for l in ls if l.get("und")), None)
        if not S or S <= 0:
            return None
        T0 = min(_date(l["expiry"]) for l in ls)
        if any(not l.get("iv") for l in ls if _date(l["expiry"]) > T0):
            return None
        tot += _pl_at(S, ls, T0)
    return tot


def _densita(legs, r, n, iv, vol):
    """(griglia, cdf, scadenza di riferimento) per integrare sulla distribuzione del sottostante.

    `iv` e' la volatilita' di riferimento della posizione; `vol` la sostituisce SOLO nella
    densita', non nella valutazione delle gambe. La distinzione conta: le gambe con scadenza
    lontana vanno sempre prezzate alla loro implicita (e' il loro prezzo di mercato), mentre
    la probabilita' dei movimenti futuri si vuole poter calcolare con una volatilita' attesa
    diversa da quella implicita."""
    if len({l["symbol"] for l in legs}) != 1:
        return None
    S = next((l.get("und") for l in legs if l.get("und")), None)
    if not S or S <= 0:
        return None
    T0 = min(_date(l["expiry"]) for l in legs)
    T = (T0 - dt.date.today()).days / 365
    if T <= 0:
        return None
    s = vol or iv
    if not s:
        con_iv = [l for l in legs if l.get("iv")]
        if not con_iv:
            return None
        s = min(con_iv, key=lambda l: abs(l["strike"] - S))["iv"]
    if not s or s <= 0:
        return None
    sq, mu = s * sqrt(T), (r - 0.5 * s * s) * T
    cdf = lambda K: 0.0 if K <= 0 else N((log(K / S) - mu) / sq)
    lo, hi = S * 0.02, S * 4.0
    passo = (hi / lo) ** (1 / n)
    try:
        bordi = [k for _, bes, _ in breakevens(legs) for k in bes]
    except Exception:
        bordi = []
    grid = sorted({*(l["strike"] for l in legs), *bordi,
                   *(lo * passo ** i for i in range(n + 1))})
    return grid, cdf, T0


def misure(legs, r=0.02, n=1200, iv=None, vol=None):
    """(valore atteso, probabilita' di chiudere in utile) in un solo passaggio.

    Le due grandezze condividono griglia e densita': calcolarle insieme costa la meta'."""
    d = _densita(legs, r, n, iv, vol)
    if d is None:
        return None, None
    grid, cdf, T0 = d
    ev, p, prev = 0.0, 0.0, cdf(grid[0])
    for a, b in zip(grid, grid[1:]):
        c = cdf(b)
        pl = _pl_at((a + b) / 2, legs, T0)
        ev += pl * (c - prev)
        if pl > 0:
            p += c - prev
        prev = c
    for S_, w in ((grid[0] / 2, cdf(grid[0])), (grid[-1] * 1.5, 1 - cdf(grid[-1]))):
        pl = _pl_at(S_, legs, T0)
        ev += pl * w
        if pl > 0:
            p += w
    return ev, max(0.0, min(1.0, p))


def valore_atteso(legs, r=0.02, n=1200, iv=None, vol=None):
    """Valore atteso del P/L a scadenza, integrando il payoff sulla densita' log-normale.

    **Con quale volatilita' si calcola cambia tutto.** Se si usa la volatilita implicita il
    risultato e' zero per costruzione (meno il costo di esecuzione): e' esattamente quello che
    il mercato sta prezzando. Il vantaggio del venditore di premio, se c'e', sta nella
    differenza fra implicita e realizzata (la VRP), quindi per misurare l'edge si passa `vol`
    con la **volatilita realizzata attesa**, piu' bassa dell'implicita.
    Vedi Risorse/Come si calcola se un trade ha edge nel vault."""
    d = _densita(legs, r, n, iv, vol)
    if d is None:
        return None
    grid, cdf, T0 = d
    ev, prev = 0.0, cdf(grid[0])
    for a, b in zip(grid, grid[1:]):
        c = cdf(b)
        ev += _pl_at((a + b) / 2, legs, T0) * (c - prev)
        prev = c
    ev += _pl_at(grid[0] / 2, legs, T0) * cdf(grid[0])
    ev += _pl_at(grid[-1] * 1.5, legs, T0) * (1 - cdf(grid[-1]))
    return ev


def prob_utile(legs, r=0.02, n=1200, iv=None, vol=None):
    """Probabilita' che la struttura chiuda IN UTILE alla scadenza piu' vicina.

    Si integra la densita' log-normale sulle zone in cui il payoff e' positivo, quindi funziona
    su qualunque forma (verticale, condor, butterfly, calendar) senza casi particolari.

    ATTENZIONE a come si legge: con la volatilita implicita e' la probabilita' RISK-NEUTRAL,
    quella scontata nei prezzi delle opzioni, non una previsione. Siccome l'implicita e' di
    norma piu' alta della volatilita' che poi si realizza, quella stima e' PRUDENTE per chi
    vende premio: la probabilita' vera tende a essere un po' piu' alta.
    Ritorna None quando non e' calcolabile (piu' sottostanti, prezzo o IV assenti, scadenza oggi).
    """
    d = _densita(legs, r, n, iv, vol)
    if d is None:
        return None
    grid, cdf, T0 = d
    p, prev = 0.0, cdf(grid[0])
    for a, b in zip(grid, grid[1:]):
        c = cdf(b)
        if _pl_at((a + b) / 2, legs, T0) > 0:
            p += c - prev
        prev = c
    if _pl_at(grid[0] / 2, legs, T0) > 0:
        p += cdf(grid[0])
    if _pl_at(grid[-1] * 1.5, legs, T0) > 0:
        p += 1 - cdf(grid[-1])
    return max(0.0, min(1.0, p))


def breakevens(legs):
    """Punti di pareggio a scadenza (alla scadenza piu' vicina se le scadenze sono diverse), per sottostante.
    Ritorna lista di (simbolo, [pareggi], direzione): 'su' = guadagno sopra, 'giu' = guadagno sotto,
    'tra' = guadagno tra i due valori, 'fuori' = guadagno fuori dall'intervallo, None se non determinabile."""
    by_sym = {}
    for l in legs:
        by_sym.setdefault(l["symbol"], []).append(l)
    out = []
    for sym, ls in by_sym.items():
        T0 = min(_date(l["expiry"]) for l in ls)
        if len({l["expiry"] for l in ls}) > 1 and any(not l.get("iv") for l in ls if _date(l["expiry"]) > T0):
            out.append((sym, [], None)); continue
        kmax = max(l["strike"] for l in ls)
        grid = sorted({*[l["strike"] for l in ls], *[kmax * i / 2000 for i in range(1, 6001)]})
        vals = [_pl_at(S, ls, T0) for S in grid]
        bes = []
        for (s0, v0), (s1, v1) in zip(zip(grid, vals), zip(grid[1:], vals[1:])):
            if (v0 < 0) != (v1 < 0) and v1 != v0:
                bes.append(s0 + (s1 - s0) * (-v0) / (v1 - v0))
        if not bes:
            out.append((sym, [], "sempre" if vals[len(vals) // 2] >= 0 else "mai")); continue
        hi_side = vals[-1] >= 0; lo_side = vals[0] >= 0
        if len(bes) == 1:
            d = "su" if hi_side else "giu"
        else:
            bes = [bes[0], bes[-1]]
            d = "tra" if not hi_side and not lo_side else "fuori"
        out.append((sym, bes, d))
    return out
