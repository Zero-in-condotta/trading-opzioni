"""Scansione del premio di volatilita' su ESTX50 (SOLA LETTURA, nessun ordine).

Domanda a cui risponde: fra le strutture a credito e rischio definito costruibili adesso,
quali hanno un vantaggio REALE, e quanto margine di sicurezza hanno?

Il criterio non e' "valore atteso zero": con la volatilita' IMPLICITA il valore atteso e' zero
per costruzione su qualunque opzione prezzata al giusto, perche' e' esattamente cio' che il
mercato prezza. Il vantaggio del venditore di premio, se c'e', sta nella differenza fra la
volatilita' implicita e quella che poi si realizza (la VRP).

Per ogni candidato si calcolano quindi tre numeri:
  1. EV alla IV, a prezzi ESEGUIBILI -> misura quanto si regala in spread denaro-lettera.
     Su un trade prezzato bene e liquido deve essere vicino a zero; molto negativo = stai
     pagando troppo per entrare, e nessuna VRP lo recupera.
  2. RV DI PAREGGIO -> la volatilita' realizzata alla quale il valore atteso va a zero.
     E' il numero che conta: non e' un parametro scelto, e' una proprieta' del trade.
  3. Margine = RV di pareggio meno la RV effettivamente realizzata dall'indice (misurata
     dallo storico). Piu' e' alto, piu' il trade regge un errore di stima.

La regola del Piano Finale chiede margine >= +2 punti, spread <= 10%, open interest >= 100.

Uso: uv run --with yfinance scan_vrp.py            (ESTX50, scadenze 25-70 giorni)
     uv run --with yfinance scan_vrp.py --dte 20 80 --width 50 100
"""
from __future__ import annotations

import argparse
import datetime as dt
import math

import deals
import stat_ev
from ib_common import connect, clean
from ib_async import Index, Option, Stock

# (borsa, valuta, ticker storico, larghezze delle ali, ampiezza della banda di strike)
# Le larghezze DEVONO essere per sottostante: ESTX50 sta a ~6.200 con strike da 25, XSP a ~766
# con strike da 1. Usare 50/100 su XSP significherebbe ali larghe il 7-13% dell'indice, cioe'
# strutture che non c'entrano niente con quelle costruite su ESTX50.
MERCATI = {
    "ESTX50": ("EUREX", "EUR", "^STOXX50E", [50, 100], 0.13),
    "SPX": ("CBOE", "USD", "^GSPC", [50, 100], 0.12),
    "XSP": ("CBOE", "USD", "^GSPC", [5, 10], 0.10),
    "RUT": ("CBOE", "USD", "^RUT", [25, 50], 0.12),
}
# Mini-indici: un indice che vale una frazione di un indice piu' grande (XSP = SPX/10, XND = NDX/100,
# MRUT = RUT/10, DJX = DJIA/100) e ha lo stesso andamento. REGOLA (decisione dell'utente): la
# volatilita' STORICA si prende dall'indice PADRE (e' la stessa, e per i mini spesso Yahoo non
# ha la serie); la CATENA, i prezzi e il sottostante delle opzioni si leggono dall'indice vero.
PADRE = {"XSP": "SPX", "XND": "NDX", "MRUT": "RUT", "DJX": "DJI"}
STORICO_INDICE = {"SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT", "DJI": "^DJI"}
# Borsa su cui sta l'INDICE sottostante, quando diversa da quella delle opzioni (CBOE)
ESCH_INDICE = {"XND": "NASDAQ", "NDX": "NASDAQ", "MRUT": "RUSSELL", "RUT": "RUSSELL"}
MERCATI.update({
    "NDX": ("CBOE", "USD", STORICO_INDICE["NDX"], [100, 200], 0.12),
    "XND": ("CBOE", "USD", STORICO_INDICE[PADRE["XND"]], [2, 5], 0.10),
    "MRUT": ("CBOE", "USD", STORICO_INDICE[PADRE["MRUT"]], [2.5, 5], 0.12),
    "DJX": ("CBOE", "USD", STORICO_INDICE["DJI"], [5, 10], 0.10),
})


def config_sottostante(sym):
    """(exch, cur, ticker storico, larghezze, banda, tipo). Un simbolo non fra gli indici noti
    si tratta come azione USA su SMART; le larghezze si scelgono sul prezzo (None qui)."""
    sym = sym.upper()
    if sym in MERCATI:
        return (*MERCATI[sym], "IND")
    return ("SMART", "USD", sym, None, 0.20, "STK")


def larghezze_per_prezzo(p):
    if p < 20:
        return [1.0, 2.5]
    if p < 60:
        return [2.5, 5.0, 10.0]
    if p < 150:
        return [5.0, 10.0, 20.0]
    if p < 400:
        return [10.0, 20.0, 40.0]
    return [20.0, 40.0, 80.0]


# Hook opzionale (impostato da scan_ticker): data la lista delle gambe restituisce un dict con le misure
# statistiche (valore atteso storico, probabilita', 5o percentile, P/L a -20%) da aggiungere alla riga.
EV_HOOK = None


def _att(r, legs):
    if EV_HOOK:
        try:
            res = EV_HOOK(legs)
        except Exception:
            res = None
        if res:
            r.update(res)
    return r


SYM = "ESTX50"
EXCH = "EUREX"
CUR = "EUR"
TICKER_STORICO = "^STOXX50E"


# ---------------------------------------------------------------- volatilita' storica

def rv_storiche(giorni=(20, 60, 120)):
    return rv_storiche_di(TICKER_STORICO, giorni)


def _rv_storiche_vecchia(giorni=(20, 60, 120)):
    """Volatilita' realizzata annualizzata su piu' finestre, dai prezzi di chiusura."""
    try:
        import yfinance as yf
        h = yf.Ticker(TICKER_STORICO).history(period="1y")["Close"].dropna()
        r = [math.log(b / a) for a, b in zip(h.values, h.values[1:])]
        out = {}
        for n in giorni:
            if len(r) >= n:
                camp = r[-n:]
                m = sum(camp) / len(camp)
                var = sum((x - m) ** 2 for x in camp) / (len(camp) - 1)
                out[n] = math.sqrt(var * 252)
        return out
    except Exception as e:
        print(f"  (storico non disponibile: {e})")
        return {}


# ---------------------------------------------------------------- catena

def quota(ib, contratti, wait=8, blocco=60):
    """Quotazioni e greche a blocchi: IBKR limita le linee dati simultanee."""
    out = {}
    for i in range(0, len(contratti), blocco):
        parte = contratti[i:i + blocco]
        ts = [(c, ib.reqMktData(c, "101", False, False)) for c in parte]
        ib.sleep(wait)
        for c, t in ts:
            b, a = clean(t.bid), clean(t.ask)
            g = t.modelGreeks
            oi = clean(t.callOpenInterest if c.right == "C" else t.putOpenInterest)
            out[c.conId] = dict(bid=b, ask=a, delta=g.delta if g else None,
                                iv=g.impliedVol if g else None,
                                oi=None if oi is None or oi != oi else oi)
            ib.cancelMktData(c)
    return out


def scadenze(ib, und, dte_min, dte_max, sym=None, weekly=False, weekly_max=45):
    """Scadenze nella finestra. Le mensili (terzo venerdi') sempre; le weekly solo se richieste ed entro
    `weekly_max` giorni: l'open interest sulle weekly e' spesso sottile (ESTX50, XSP), quindi si tengono
    solo se il filtro sull'open interest le lascia passare, non per principio."""
    exps = set()
    for ch in ib.reqSecDefOptParams(sym or SYM, "", und.secType, und.conId):
        exps |= set(ch.expirations)
    oggi = dt.date.today()
    out = []
    for e in sorted(exps):
        d = dt.datetime.strptime(e, "%Y%m%d").date()
        dd = (d - oggi).days
        if not (dte_min <= dd <= dte_max):
            continue
        mensile = 15 <= d.day <= 21 and d.weekday() == 4
        if mensile or (weekly and dd <= weekly_max and d.weekday() == 4):     # weekly = solo i venerdi'
            out.append((e, dd))
    return out[:18]


def catena(ib, expiry, right, sym=None, exch=None, cur=None):
    c = Option(sym or SYM, expiry, 0, right, exch or EXCH, currency=cur or CUR)
    try:
        return sorted(({cd.contract.strike: cd.contract
                        for cd in ib.reqContractDetails(c)
                        if (exch or EXCH) != "SMART" or cd.contract.tradingClass == (sym or SYM)}).items())
    except Exception:
        return []


# ---------------------------------------------------------------- misure

def gamba(c, px, pos, spot, mult, sym=None, cur=None):
    """Gamba nel formato di deals, al prezzo ESEGUIBILE (vendi al denaro, compra alla lettera)."""
    p = px["bid"] if pos < 0 else px["ask"]
    return dict(symbol=sym or SYM, secType="OPT", expiry=c.lastTradeDateOrContractMonth,
                strike=c.strike, right=c.right, position=pos, avgCost=p * mult,
                mult=mult, currency=cur or CUR, iv=px.get("iv"), und=spot, delta=px.get("delta"),
                oi=px.get("oi"))


def rv_di_pareggio(legs, iv_rif, lo=0.02, hi=1.0, giri=22, n=500):
    """(volatilita' di pareggio, verso) oppure (None, None).

    verso 'sotto' = si guadagna se la volatilita' realizzata resta SOTTO quel valore: e' il caso
    di chi vende premio. verso 'sopra' = il contrario, tipico di una struttura lunga di
    volatilita' come un calendar. Senza distinguere i due versi un calendar risulterebbe
    semplicemente "non calcolabile"."""
    f = lambda v: deals.valore_atteso(legs, iv=iv_rif, vol=v, n=n)
    try:
        fa, fb = f(lo), f(hi)
    except Exception:
        return None, None
    if fa is None or fb is None or (fa > 0) == (fb > 0):
        return None, None          # nessun cambio di segno nell'intervallo
    verso = "sotto" if fa > 0 else "sopra"
    for _ in range(giri):
        m = (lo + hi) / 2
        fm = f(m)
        if (fm > 0) == (fa > 0):
            lo = m
        else:
            hi = m
    return (lo + hi) / 2, verso


def oi_minimo(legs):
    """Open interest della gamba piu' sottile (None se nessuna lo riporta)."""
    v = [l.get("oi") for l in legs if l.get("oi") is not None]
    return min(v) if v else None


def forward_factor(legs):
    """FF = IV corta / volatilita' forward fra le due scadenze - 1, per un calendar.
    Positivo = la corta costa piu' del periodo successivo (il Piano chiede >= +0,20)."""
    scad = sorted({l["expiry"] for l in legs})
    if len(scad) != 2:
        return None
    oggi = dt.date.today()
    T = [(dt.datetime.strptime(e, "%Y%m%d").date() - oggi).days / 365 for e in scad]
    iv = [next((l.get("iv") for l in legs if l["expiry"] == e), None) for e in scad]
    if not all(iv) or T[1] <= T[0] or T[0] <= 0:
        return None
    var = (iv[1] ** 2 * T[1] - iv[0] ** 2 * T[0]) / (T[1] - T[0])
    return iv[0] / math.sqrt(var) - 1 if var > 0 else None


def liquidita(px_list):
    """(spread peggiore in % del prezzo medio) fra le gambe."""
    peggio = 0.0
    for px in px_list:
        b, a = px.get("bid"), px.get("ask")
        if b is None or a is None or (a + b) <= 0:
            return None
        peggio = max(peggio, (a - b) / ((a + b) / 2) * 100)
    return peggio


# ---------------------------------------------------------------- scansione

# Ticker storico (yfinance) per gli indici; per qualunque altro simbolo (azioni, ETF) si usa il simbolo stesso.
class _Storici(dict):
    def __missing__(self, k):
        return k


STORICI = _Storici({"ESTX50": "^STOXX50E", "XSP": "^GSPC", "SPX": "^GSPC", "RUT": "^RUT", "NDX": "^NDX"})


def posizioni_aperte():
    """RV di pareggio su ogni posizione aperta: quanta volatilita' in meno (o in piu') serve
    perche' la posizione abbia valore atteso positivo, contro quella che il sottostante
    realizza davvero. Risponde alla domanda lasciata aperta il 02/10: l'edge c'e' o no."""
    import dashboard, actions
    ib = connect("127.0.0.1", 7496, 197)
    try:
        ib.reqMarketDataType(1)
        groups, unmapped, account, *_ = dashboard.build_data(ib)
    finally:
        ib.disconnect()
    fx = (account.get("_fx") or {})
    print("=== RV DI PAREGGIO SULLE POSIZIONI APERTE ===")
    print(f"{dt.datetime.now():%d/%m/%Y %H:%M}\n")
    cache, misurate = {}, {}
    print(f"{'posizione':44s} {'IV':>6s} {'RV0':>7s} {'verso':>6s} {'RV reale':>9s} {'margine':>8s} {'EV':>8s}")
    print("-" * 94)
    for gid, g in groups.items():
        legs = actions._opt_legs(g)
        if not legs:
            continue
        simboli = {l["symbol"] for l in legs}
        if len(simboli) > 1:
            print(f"{g['nome'][:44]:44s} {'piu sottostanti: non calcolabile':>50s}")
            continue
        sym = simboli.pop()
        ivr = actions._iv_rif(legs)
        if not ivr:
            print(f"{g['nome'][:44]:44s} {'volatilita implicita assente':>50s}")
            continue
        if sym not in cache:
            cache[sym] = rv_storiche_di(STORICI.get(sym, sym))
        rvs = cache[sym]
        misurate[sym] = max(rvs.values()) if rvs else None
        rv_ref = max(rvs.values()) if rvs else None
        rv0, verso = rv_di_pareggio(legs, ivr, n=500)
        ev = deals.valore_atteso(legs, iv=ivr, vol=rv_ref, n=600) if rv_ref else None
        cur = legs[0]["currency"]
        if rv0 is None:
            print(f"{g['nome'][:44]:44s} {ivr*100:>5.1f}% {'n/d':>7s} {'-':>6s} "
                  f"{(f'{rv_ref*100:.1f}%' if rv_ref else 'n/d'):>9s} {'-':>8s} "
                  f"{(f'{ev:+,.0f}' if ev is not None else '-'):>8s}")
            continue
        # margine positivo = la volatilita' reale sta dalla parte giusta del pareggio
        marg = (rv0 - rv_ref) * 100 if verso == "sotto" else (rv_ref - rv0) * 100
        print(f"{g['nome'][:44]:44s} {ivr*100:>5.1f}% {rv0*100:>6.1f}% {verso:>6s} "
              f"{(f'{rv_ref*100:.1f}%' if rv_ref else 'n/d'):>9s} "
              f"{(f'{marg:+.1f}' if rv_ref else 'n/d'):>8s} "
              f"{(f'{ev:+,.0f} {cur}' if ev is not None else '-'):>8s}")
    print("\nCome si legge:")
    print("  RV0      volatilita' realizzata di pareggio: il valore atteso e' zero li'.")
    print("  verso    'sotto' = guadagni se la volatilita' resta SOTTO quel valore (venditore di")
    print("           premio). 'sopra' = il contrario, struttura lunga di volatilita'.")
    print("  RV reale la piu' alta fra RV20/60/120 del sottostante, cioe' la stima prudente.")
    print("  margine  punti di volatilita' dalla parte giusta. Il Piano ne chiede almeno 2.")
    print("  EV       valore atteso con la volatilita' reale al posto dell'implicita.")
    print("\nStessa avvertenza della scansione: log-normale piatta, coda sinistra sottostimata.")
    print("Sulle strutture che vendono put lontane dal prezzo il margine e' soprastimato.")
    f = salva_rv(misurate)
    print(f"\nVolatilita' misurate salvate in {f}: da qui in avanti la dashboard usa queste")
    print("invece della stima a percentuale dell'implicita.")


def salva_rv(misure_per_simbolo):
    """Scrive la volatilita' realizzata misurata dove actions.py la puo' leggere.

    La dashboard non ha yfinance fra le dipendenze e non deve dipendere dalla rete a ogni
    giro: la misura la fa questo strumento, quando lo lanci, e la lascia in un file."""
    import json
    from ib_common import DATA
    f = DATA / "rv_storiche.json"
    try:
        vecchio = json.loads(f.read_text()).get("rv", {})
    except Exception:
        vecchio = {}
    vecchio.update({k: v for k, v in misure_per_simbolo.items() if v})
    f.write_text(json.dumps({"aggiornato": dt.date.today().isoformat(), "rv": vecchio,
                             "nota": "volatilita' realizzata annualizzata, la piu' alta fra "
                                     "RV20/60/120 (stima prudente). Scritta da scan_vrp.py."},
                            indent=1))
    return f


def rv_storiche_di(ticker, giorni=(20, 60, 120)):
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="1y")["Close"].dropna()
        r = [math.log(b / a) for a, b in zip(h.values, h.values[1:])]
        out = {}
        for n in giorni:
            if len(r) >= n:
                c = r[-n:]
                m = sum(c) / len(c)
                out[n] = math.sqrt(sum((x - m) ** 2 for x in c) / (len(c) - 1) * 252)
        return out
    except Exception:
        return {}


def ordine_testo(legs):
    """L'ordine esatto da inserire, dalle gambe della struttura."""
    parti = []
    for l in sorted(legs, key=lambda x: (x["right"], x["strike"])):
        v = "VENDI" if l["position"] < 0 else "COMPRA"
        parti.append(f"{v} {abs(l['position']):g} {l['right']} {l['strike']:g} {l['expiry']}")
    return " + ".join(parti)


def spot_da_parita(ib, sym, exch, cur, und, a):
    """Prezzo del sottostante dalla catena: S = K + C - P allo strike dove C e P si equivalgono.
    Serve quando l'indice non ha quotazione in diretta. Due passate: la prima parte dallo strike
    centrale della lista (le catene sono fitte attorno al denaro), la seconda dalla stima."""
    sc = scadenze(ib, und, *a.dte, sym=sym)
    if not sc:
        return None
    exp = sc[0][0]
    calls = dict(catena(ib, exp, "C", sym, exch, cur))
    puts = dict(catena(ib, exp, "P", sym, exch, cur))
    ks = sorted(k for k in calls if k in puts)
    if len(ks) < 5:
        return None
    centro = ks[len(ks) // 2]
    stima = None
    for _ in range(2):
        vic = sorted(ks, key=lambda k: abs(k - centro))[:9]
        px = quota(ib, [calls[k] for k in vic] + [puts[k] for k in vic], wait=6)
        e = []
        for k in vic:
            qc, qp = px.get(calls[k].conId), px.get(puts[k].conId)
            if qc and qp and all(v is not None for v in (qc["bid"], qc["ask"], qp["bid"], qp["ask"])) \
                    and qc["bid"] > 0 and qp["bid"] > 0:
                cm, pm = (qc["bid"] + qc["ask"]) / 2, (qp["bid"] + qp["ask"]) / 2
                e.append((abs(cm - pm), k + cm - pm))
        if not e:
            return None
        e.sort()
        stima = sum(x for _, x in e[:3]) / len(e[:3])
        centro = min(ks, key=lambda k: abs(k - stima))
    return stima


def analizza(ib, sym, a, rv_ref, rv_lo=None, on_rows=None, cfg=None):
    """Tutti i candidati a rischio definito su un sottostante (indice o azione USA).

    Con `on_rows` le righe di ogni scadenza vengono consegnate appena calcolate, cosi' una
    pagina puo' mostrarle man mano; il valore di ritorno e' comunque la lista completa."""
    exch, cur, _, larghezze, banda_m, tipo = cfg or (*MERCATI[sym], "IND")
    banda = a.banda if a.banda else banda_m
    und = Stock(sym, "SMART", cur) if tipo == "STK" else Index(sym, ESCH_INDICE.get(sym, exch))
    ib.qualifyContracts(und)
    t = ib.reqMktData(und, "", False, False)
    ib.sleep(4)
    spot = next((v for v in (clean(t.last), clean(t.close)) if v), None)
    ib.cancelMktData(und)
    if not spot and tipo == "IND":
        # indice senza dati in diretta (es. XND: nessun abbonamento Nasdaq): lo si ricava dalla
        # catena delle opzioni con la put-call parity
        spot = spot_da_parita(ib, sym, exch, cur, und, a)
        if spot:
            print(f"  {sym}: sottostante non quotato, ricavato dalla put-call parity: {spot:,.2f}")
    if not spot:
        print(f"  {sym}: prezzo non disponibile")
        if on_rows:
            on_rows([], messaggio=f"{sym}: prezzo del sottostante non disponibile")
        return []
    larghezze = a.width or larghezze or larghezze_per_prezzo(spot)
    print(f"  {sym} {spot:,.2f}")
    righe, verticali, per_exp, prec = [], {}, {}, None
    emesse = [0]

    def emetti():
        """Consegna le righe calcolate dall'ultima consegna (a flusso, per la pagina)."""
        if on_rows and len(righe) > emesse[0]:
            on_rows(righe[emesse[0]:])
            emesse[0] = len(righe)
    def condor_exp(exp_, rv_x):
        out = []
        # iron condor: put spread + call spread, stessa scadenza e larghezza, accoppiati al delta.
        # A scadenza puo' perdere un solo lato: il rischio e' quello di una gamba, il credito e' la
        # somma. Paga molto di piu' per unita' di rischio, ma essendo corto di volatilita' su
        # entrambi i lati ha una volatilita' di pareggio piu' bassa, quindi meno margine.
        for (exp, w), gruppo in sorted(verticali.items()):
            if exp != exp_:
                continue
            put = [r for r in gruppo if r["tipo"] == "bull put"]
            call = [r for r in gruppo if r["tipo"] == "bear call"]
            if not put or not call:
                continue
            for bersaglio in (0.10, 0.15, 0.20, 0.25, 0.30):
                p_ = min(put, key=lambda r: abs(r["delta"] - bersaglio))
                c_ = min(call, key=lambda r: abs(r["delta"] - bersaglio))
                if abs(p_["delta"] - bersaglio) > 0.05 or abs(c_["delta"] - bersaglio) > 0.05:
                    continue
                legs = p_["legs"] + c_["legs"]
                mult, credito = p_["mult"], p_["credito"] + c_["credito"]
                _o = stat_ev.EvLN(legs)
                ev_iv, prob = _o.ev(p_["iv_atm"])
                if prob is None:
                    continue
                ev_rv = _o.ev(rv_x)[0] if rv_x else None
                rv0, _v = _o.vol_pareggio()
                sp = [x for x in (p_["spread"], c_["spread"]) if x is not None]
                r_c = (dict(sym=sym, cur=p_["cur"], spot=spot, exp=exp, dte=p_["dte"],
                                  tipo="iron condor", ks=p_["ks"], kl=c_["ks"], w=w,
                                  delta=(p_["delta"] + c_["delta"]) / 2, iv=p_["iv_atm"],
                                  credito=credito, rischio=w * mult - credito,
                                  ev_iv=ev_iv, ev_rv=ev_rv, prob=prob, rv0=rv0, rvref=rv_x,
                                  spread=max(sp) if sp else None, oi_min=oi_minimo(legs),
                                  ordine=ordine_testo(legs)))
                out.append(_att(r_c, legs))

        return out

    if on_rows:
        on_rows([], info=dict(spot=spot, cur=cur, sym=sym, tipo=tipo))
    for exp, dte in scadenze(ib, und, *a.dte, sym=sym, weekly=getattr(a, "weekly", False)):
        # volatilita' attesa per l'orizzonte di QUESTA scadenza (previsione HAR) o scalare (scansione programmata)
        rv_hi_e = rv_ref(dte) if callable(rv_ref) else rv_ref
        rv_lo_e = rv_lo(dte) if callable(rv_lo) else rv_lo
        if on_rows:
            on_rows([], messaggio=f"{sym} {exp[6:8]}/{exp[4:6]}/{exp[:4]} ({dte} gg): leggo la catena")
        ks_p = dict(catena(ib, exp, "P", sym, exch, cur))
        ks_c = dict(catena(ib, exp, "C", sym, exch, cur))
        if not ks_p or not ks_c:
            continue
        mult = float(next(iter(ks_p.values())).multiplier or 100)
        extra = max(larghezze)
        dentro = lambda k: spot * (1 - banda) - extra <= k <= spot * (1 + banda) + extra
        cs = [c for k, c in ks_p.items() if dentro(k)] + [c for k, c in ks_c.items() if dentro(k)]
        px = quota(ib, cs)
        print(f"    {exp} ({dte} gg): {len(cs)} contratti")
        iv_atm = None
        vicini = [(abs(k - spot), px.get(c.conId)) for k, c in ks_p.items()
                  if px.get(c.conId) and px[c.conId].get("iv")]
        if vicini:
            iv_atm = min(vicini, key=lambda x: x[0])[1]["iv"]
        per_exp[exp] = dict(dte=dte, ks_p=ks_p, ks_c=ks_c, px=px, mult=mult,
                            iv_atm=iv_atm)
        for right in ("P", "C"):
            ks = ks_p if right == "P" else ks_c
            for k, c in sorted(ks.items()):
                q = px.get(c.conId)
                if not q or q.get("bid") is None or q.get("delta") is None or not q.get("iv"):
                    continue
                if not (a.delta[0] <= abs(q["delta"]) <= a.delta[1]):
                    continue
                if not (spot * (1 - banda) <= k <= spot * (1 + banda)):
                    continue
                for w in larghezze:
                    k_l = k - w if right == "P" else k + w
                    c_l = ks.get(k_l)
                    if c_l is None or c_l.conId not in px:
                        continue
                    q_l = px[c_l.conId]
                    if q_l.get("ask") is None:
                        continue
                    legs = [gamba(c, q, -1, spot, mult, sym, cur),
                            gamba(c_l, q_l, +1, spot, mult, sym, cur)]
                    credito = (q["bid"] - q_l["ask"]) * mult
                    if credito <= 0:
                        continue
                    _o = stat_ev.EvLN(legs)
                    ev_iv, prob = _o.ev(q["iv"])
                    if prob is None:
                        continue
                    ev_rv = _o.ev(rv_hi_e)[0] if rv_hi_e else None
                    rv0, _v = _o.vol_pareggio()
                    r = dict(sym=sym, cur=cur, spot=spot, exp=exp, dte=dte,
                             tipo=("bull put" if right == "P" else "bear call"),
                             ks=k, kl=k_l, w=w, delta=abs(q["delta"]), iv=q["iv"],
                             credito=credito, rischio=w * mult - credito,
                             ev_iv=ev_iv, ev_rv=ev_rv, prob=prob, rv0=rv0, rvref=rv_hi_e,
                             spread=liquidita([q, q_l]), oi_min=oi_minimo(legs),
                             ordine=ordine_testo(legs))
                    _att(r, legs)
                    righe.append(r)
                    verticali.setdefault((exp, w), []).append(
                        dict(r, legs=legs, mult=mult, iv_atm=iv_atm or q["iv"]))
            emetti()
        # fine scadenza: condor, strutture extra (con la scadenza precedente per i calendar) e consegna
        righe += condor_exp(exp, rv_hi_e)
        emetti()
        righe += strutture_extra(sym, cur, spot, per_exp, larghezze, rv_hi_e, rv_lo_e, solo={exp})
        prec = exp
        emetti()
    return righe


# ---------------------------------------------------------------- altre famiglie

def _riga_extra(sym, cur, spot, exp, dte, tipo, legs, iv_ref, rv_hi, rv_lo, ks, kl, w, delta,
                spread, n=300):
    """Riga per una struttura qualunque (credito o debito). Rischio e profitto massimi vengono
    da deals.extremes sui prezzi eseguibili; la volatilita' di riferimento per il valore atteso
    dipende dal verso: chi e' corto di volatilita' (butterfly, calendar) si misura con la RV piu'
    alta fra le finestre, chi e' lungo (verticali a debito) con la piu' bassa."""
    mx, mn, _est = deals.extremes(legs)
    if not isinstance(mx, (int, float)) or not isinstance(mn, (int, float)) or mn >= 0 or mx <= 0:
        return None
    cash = -sum(l["position"] * l["avgCost"] for l in legs)      # >0 credito, <0 debito
    if tipo.startswith("calendar") and cash < 0:
        mn = -abs(cash)       # la perdita massima di un calendar e' il debito (a S=0 la griglia la gonfia)
    _o = stat_ev.EvLN(legs)
    rv0, verso = _o.vol_pareggio()
    verso = verso or "sotto"
    rv_rif = rv_hi if verso == "sotto" else rv_lo
    ev_iv, prob = _o.ev(iv_ref)
    ev_rv = _o.ev(rv_rif)[0] if rv_rif else None
    if ev_rv is None or prob is None:
        return None
    # Un verticale a debito e' una scommessa direzionale prezzata con lo skew: se il suo valore
    # atteso e' positivo GIA' alla volatilita' implicita, il vantaggio e' un artefatto del modello
    # a volatilita' piatta (le gambe hanno implicite diverse), non un'occasione. Scartato.
    if tipo in ("bull call", "bear put") and (ev_iv is None or ev_iv > 0):
        return None
    return _att(dict(sym=sym, cur=cur, spot=spot, exp=exp, dte=dte, tipo=tipo, ks=ks, kl=kl, w=w,
                delta=delta, iv=iv_ref, credito=max(cash, 0.0), importo=abs(cash),
                lato="credito" if cash > 0 else "debito", rischio=-mn, profitto=mx,
                ev_iv=ev_iv, ev_rv=ev_rv, prob=prob, rv0=rv0, verso=verso, rvref=rv_rif,
                spread=liquidita([l["_px"] for l in legs]), oi_min=oi_minimo(legs),
                ff=forward_factor(legs) if tipo.startswith("calendar") else None,
                ordine=ordine_testo(legs)), legs)


def strutture_extra(sym, cur, spot, per_exp, larghezze, rv_hi, rv_lo, solo=None):
    """Iron butterfly, verticali a debito (bull call / bear put) e calendar su strike vicini."""
    out = []
    esp = sorted(per_exp)

    def L(c, px, pos, mult):
        g = gamba(c, px, pos, spot, mult, sym, cur)
        g["_px"] = px
        return g

    def ok(px, c, lato):
        q = px.get(c.conId)
        return q if q and q.get("bid") is not None and q.get("ask") is not None and q.get("iv") else None

    for exp in esp:
        if solo is not None and exp not in solo:
            continue
        d = per_exp[exp]; ks_p, ks_c, px, mult, iv = d["ks_p"], d["ks_c"], d["px"], d["mult"], d["iv_atm"]
        if not iv:
            continue
        comuni = sorted(k for k in ks_p if k in ks_c)
        if not comuni:
            continue
        k0 = min(comuni, key=lambda k: abs(k - spot))
        for w in larghezze:
            # iron butterfly: corto lo straddle al denaro, ali a distanza w
            cs = [ks_p.get(k0), ks_c.get(k0), ks_p.get(k0 - w), ks_c.get(k0 + w)]
            if all(cs) and all(ok(px, c, 0) for c in cs):
                legs = [L(cs[0], px[cs[0].conId], -1, mult), L(cs[1], px[cs[1].conId], -1, mult),
                        L(cs[2], px[cs[2].conId], +1, mult), L(cs[3], px[cs[3].conId], +1, mult)]
                r = _riga_extra(sym, cur, spot, exp, d["dte"], "iron butterfly", legs, iv,
                                rv_hi, rv_lo, k0 - w, k0 + w, w, 0.5, None)
                if r:
                    r["spread"] = liquidita([l["_px"] for l in legs]); out.append(r)
            # put ratio spread 1x2: compra 1 put piu' alta, vendi 2 put a distanza w. La perdita
            # massima e' finita (si realizza se il sottostante va a zero) ma molto grande: il call
            # ratio, a rischio illimitato, non si costruisce.
            for k, c in sorted(ks_p.items()):
                q = ok(px, c, 0)
                if not q or q.get("delta") is None or not (0.25 <= abs(q["delta"]) <= 0.50):
                    continue
                c_s = ks_p.get(k - w)
                if not c_s or not ok(px, c_s, 0):
                    continue
                legs = [L(c, q, +1, mult), L(c_s, px[c_s.conId], -2, mult)]
                r = _riga_extra(sym, cur, spot, exp, d["dte"], "ratio put 1x2", legs, iv,
                                rv_hi, rv_lo, k - w, k, w, abs(q["delta"]), None, n=250)
                if r:
                    out.append(r)
            # put backspread 1x2: vendi 1 put piu' alta, compra 2 put a distanza w. A rischio DEFINITO
            # (la perdita massima sta nello strike basso) e a vega/gamma lunghe: positivo nei crolli.
            for k, c in sorted(ks_p.items()):
                q = ok(px, c, 0)
                if not q or q.get("delta") is None or not (0.25 <= abs(q["delta"]) <= 0.50):
                    continue
                c_s = ks_p.get(k - w)
                if not c_s or not ok(px, c_s, 0):
                    continue
                legs = [L(c, q, -1, mult), L(c_s, px[c_s.conId], +2, mult)]
                r = _riga_extra(sym, cur, spot, exp, d["dte"], "put backspread 1x2", legs, iv,
                                rv_hi, rv_lo, k - w, k, w, abs(q["delta"]), None, n=250)
                if r:
                    out.append(r)
            # broken wing butterfly: ala vicina larga w, ala lontana larga 2w (rischio solo sul lato lontano)
            for right, ks, sgn in (("P", ks_p, -1), ("C", ks_c, +1)):
                for k, c in sorted(ks.items()):
                    q = ok(px, c, 0)
                    if not q or q.get("delta") is None or not (0.15 <= abs(q["delta"]) <= 0.45):
                        continue
                    k2, k3 = k + sgn * w, k + sgn * 3 * w
                    c2, c3 = ks.get(k2), ks.get(k3)
                    if not c2 or not c3 or not ok(px, c2, 0) or not ok(px, c3, 0):
                        continue
                    legs = [L(c, q, +1, mult), L(c2, px[c2.conId], -2, mult), L(c3, px[c3.conId], +1, mult)]
                    r = _riga_extra(sym, cur, spot, exp, d["dte"], "bwb " + ("put" if right == "P" else "call"),
                                    legs, iv, rv_hi, rv_lo, min(k, k3), max(k, k3), w, abs(q["delta"]),
                                    None, n=250)
                    if r:
                        out.append(r)
            # verticali a debito: lunga vicina al denaro (delta 0,35-0,65), corta a distanza w
            for right, ks in (("C", ks_c), ("P", ks_p)):
                for k, c in sorted(ks.items()):
                    q = ok(px, c, 0)
                    if not q or q.get("delta") is None or not (0.35 <= abs(q["delta"]) <= 0.65):
                        continue
                    k_s = k + w if right == "C" else k - w
                    c_s = ks.get(k_s)
                    if not c_s or not ok(px, c_s, 0):
                        continue
                    legs = [L(c, q, +1, mult), L(c_s, px[c_s.conId], -1, mult)]
                    r = _riga_extra(sym, cur, spot, exp, d["dte"],
                                    "bull call" if right == "C" else "bear put", legs, iv,
                                    rv_hi, rv_lo, k, k_s, w, abs(q["delta"]), None)
                    if r and r["lato"] == "debito":
                        out.append(r)
    def coppie():
        """(corta, lunga): la lunga e' la scadenza appena aggiunta (`solo`), la corta una delle precedenti
        con 14-90 giorni di scarto (le 3 piu' vicine alla lunga), cosi' ogni coppia esce una volta sola."""
        for b in esp:
            if solo is not None and b not in solo:
                continue
            prec = [a for a in esp if a < b and 14 <= per_exp[b]["dte"] - per_exp[a]["dte"] <= 90]
            for a in prec[-3:]:
                yield a, b

    # calendar: corta la scadenza vicina, lunga una piu' lontana, stesso strike, lato OTM
    for a, b in coppie():
        da, db = per_exp[a], per_exp[b]
        if not da["iv_atm"]:
            continue
        for right in ("P", "C"):
            ka, kb = (da["ks_p"], db["ks_p"]) if right == "P" else (da["ks_c"], db["ks_c"])
            for k in sorted(set(ka) & set(kb)):
                if abs(k / spot - 1) > 0.025 or (right == "P") != (k <= spot):
                    continue
                ca, cb = ka[k], kb[k]
                qa, qb = ok(da["px"], ca, 0), ok(db["px"], cb, 0)
                if not qa or not qb:
                    continue
                legs = [L(ca, qa, -1, da["mult"]), L(cb, qb, +1, da["mult"])]
                r = _riga_extra(sym, cur, spot, a, da["dte"], "calendar " + ("put" if right == "P" else "call"),
                                legs, da["iv_atm"], rv_hi, rv_lo, k, k, 0, abs(qa.get("delta") or 0), None)
                if r:
                    r["exp_lunga"] = b
                    out.append(r)
    # double calendar e double diagonal: put sotto e call sopra il prezzo, corta vicina e lunga
    # sulla mensile successiva. Nel diagonal le lunghe stanno una larghezza piu' lontane.
    def scegli(ks, lo, hi, quanti=3):
        c = sorted(k for k in ks if lo * spot <= k <= hi * spot)
        if len(c) <= quanti:
            return c
        passo = (len(c) - 1) / (quanti - 1)
        return [c[round(i * passo)] for i in range(quanti)]

    for a, b in coppie():
        da, db = per_exp[a], per_exp[b]
        if not da["iv_atm"]:
            continue
        kps = [k for k in scegli(da["ks_p"], 0.965, 0.99) if k in db["ks_p"]]
        kcs = [k for k in scegli(da["ks_c"], 1.01, 1.035) if k in db["ks_c"]]
        for kp in kps:
            for kc in kcs:
                qpa, qca = ok(da["px"], da["ks_p"][kp], 0), ok(da["px"], da["ks_c"][kc], 0)
                qpb, qcb = ok(db["px"], db["ks_p"][kp], 0), ok(db["px"], db["ks_c"][kc], 0)
                if all((qpa, qca, qpb, qcb)):
                    m = da["mult"]
                    legs = [L(da["ks_p"][kp], qpa, -1, m), L(db["ks_p"][kp], qpb, +1, m),
                            L(da["ks_c"][kc], qca, -1, m), L(db["ks_c"][kc], qcb, +1, m)]
                    r = _riga_extra(sym, cur, spot, a, da["dte"], "double calendar", legs, da["iv_atm"],
                                    rv_hi, rv_lo, kp, kc, kc - kp, 0.3, None, n=250)
                    if r:
                        r["exp_lunga"] = b
                        out.append(r)
                for w in larghezze[:1]:
                    cp, cc = db["ks_p"].get(kp - w), db["ks_c"].get(kc + w)
                    if not cp or not cc or not qpa or not qca:
                        continue
                    qp2, qc2 = ok(db["px"], cp, 0), ok(db["px"], cc, 0)
                    if not qp2 or not qc2:
                        continue
                    m = da["mult"]
                    legs = [L(da["ks_p"][kp], qpa, -1, m), L(cp, qp2, +1, m),
                            L(da["ks_c"][kc], qca, -1, m), L(cc, qc2, +1, m)]
                    r = _riga_extra(sym, cur, spot, a, da["dte"], "double diagonal", legs, da["iv_atm"],
                                    rv_hi, rv_lo, kp - w, kc + w, w, 0.3, None, n=250)
                    if r:
                        r["exp_lunga"] = b
                        out.append(r)
    return out


def nome_struttura(r):
    if r["tipo"] in ("iron condor", "iron butterfly"):
        return f"{r['tipo']} {r['ks']:g}-{r['kl']:g} w{r['w']:g}"
    if r["tipo"].startswith("calendar"):
        return f"{r['tipo']} {r['ks']:g}"
    return f"{r['tipo']} {r['ks']:g}/{r['kl']:g}"


def selezione(buone, quante, per_famiglia=2):
    """Le migliori in assoluto piu' le migliori di ogni famiglia, cosi' una famiglia che domina
    per valore atteso non nasconde le altre. Ordine finale: valore atteso."""
    buone = [r for r in buone if r["ev_rv"] > 0]      # valore atteso negativo non e' un'occasione
    # Strutture che rischiano oltre 30 volte quanto possono guadagnare (put ratio: la perdita massima
    # e' a sottostante zero) hanno un EV positivo solo perche' il modello log-normale dimentica la coda.
    buone = [r for r in buone if r["rischio"] <= 30 * (r.get("profitto") or r["credito"] or 1)]
    scelte = list(buone[:quante])
    fam = lambda r: r["tipo"].split()[0] if r["tipo"] in ("bull put", "bear call") else r["tipo"]
    for f in {fam(r) for r in buone}:
        for r in [x for x in buone if fam(x) == f][:per_famiglia]:
            if r not in scelte:
                scelte.append(r)
    return sorted(scelte, key=lambda r: -r["ev_rv"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posizioni", action="store_true",
                    help="RV di pareggio sulle posizioni aperte invece della scansione")
    ap.add_argument("--sym", nargs="+", default=["ESTX50", "XSP"])
    ap.add_argument("--dte", nargs=2, type=int, default=[25, 70])
    ap.add_argument("--width", nargs="+", type=float, default=None,
                    help="sovrascrive le larghezze del mercato")
    ap.add_argument("--delta", nargs=2, type=float, default=[0.08, 0.32])
    ap.add_argument("--banda", type=float, default=None,
                    help="sovrascrive la banda del mercato")
    ap.add_argument("--json", default=None, help="scrive le occasioni qui (per la dashboard)")
    ap.add_argument("--quante", type=int, default=8, help="quante occasioni salvare")
    a = ap.parse_args()
    if a.posizioni:
        return posizioni_aperte()

    print("=== OCCASIONI A RISCHIO DEFINITO (credito e debito) ===")
    print(f"{dt.datetime.now():%d/%m/%Y %H:%M}  |  scadenze SOLO mensili\n")

    rv, rv_lo = {}, {}
    for sym in a.sym:
        if sym not in MERCATI:
            print(f"  {sym}: mercato non configurato, salto"); continue
        m = rv_storiche_di(MERCATI[sym][2])
        rv[sym] = max(m.values()) if m else None
        rv_lo[sym] = min(m.values()) if m else None
        det = "  ".join(f"RV{n} {v*100:.1f}%" for n, v in sorted(m.items())) if m else "n/d"
        print(f"  {sym}: {det}  ->  riferimento {rv[sym]*100:.1f}%" if rv.get(sym)
              else f"  {sym}: storico non disponibile")
    salva_rv(rv)
    print()

    ib = connect("127.0.0.1", 7496, 196)
    righe = []
    try:
        ib.reqMarketDataType(1)
        for sym in a.sym:
            if sym in MERCATI:
                righe += analizza(ib, sym, a, rv.get(sym), rv_lo.get(sym))
    finally:
        ib.disconnect()

    for r in righe:
        rif = r.get("rvref") or rv.get(r["sym"])
        if r["rv0"] and rif:
            r["margine"] = (r["rv0"] - rif) * 100 if r.get("verso", "sotto") == "sotto" else (rif - r["rv0"]) * 100
        else:
            r["margine"] = None
        r["cr"] = r.get("profitto", r["credito"]) / r["rischio"] * 100 if r["rischio"] else None
    # SI ORDINA PER VALORE ATTESO alla volatilita' realmente realizzata, non per margine:
    # il margine da solo premia le put lontanissime che pagano il 4% del rischio, e penalizza
    # i condor che pagano il 100%. Il valore atteso e' l'unico numero che li mette a confronto.
    buone = [r for r in righe if r.get("ev_rv") is not None]
    buone.sort(key=lambda r: -r["ev_rv"])

    print(f"\n{'struttura':>34s} {'DTE':>4s} {'Δ':>5s} {'cred':>6s} {'risch':>7s} {'c/r':>5s} "
          f"{'prob':>5s} {'RV0':>6s} {'marg':>6s} {'EV':>7s} {'sprd':>5s}")
    print("  " + "-" * 112)
    for r in buone[:20]:
        nome = f"{r['sym']} {nome_struttura(r)} {r['exp'][4:6]}/{r['exp'][6:8]}"
        rv0_s = f"{r['rv0']*100:.1f}%" if r["rv0"] else "n/d"
        mar_s = f"{r['margine']:+.1f}" if r["margine"] is not None else "n/d"
        spr_s = f"{r['spread']:.0f}%" if r["spread"] is not None else "n/d"
        print(f"{nome:>34s} {r['dte']:>4d} {r['delta']:>5.2f} {r['credito']:>6.0f} "
              f"{r['rischio']:>7.0f} {r['cr']:>4.0f}% {r['prob']*100:>4.0f}% "
              f"{rv0_s:>6s} {mar_s:>6s} {r['ev_rv']:>+7.0f} {spr_s:>5s}")

    if a.json:
        import json as _j
        from pathlib import Path
        tenute = []
        for n, r in enumerate(selezione(buone, a.quante)):
            tenute.append({k: r.get(k) for k in
                           ("sym", "cur", "spot", "exp", "dte", "tipo", "ks", "kl", "w", "delta",
                            "iv", "credito", "rischio", "cr", "prob", "rv0", "margine",
                            "ev_iv", "ev_rv", "spread", "ordine", "lato", "importo", "profitto",
                            "verso", "exp_lunga", "ff", "oi_min")} | {"id": f"opp{n+1}"})
        Path(a.json).write_text(_j.dumps(
            {"generato": dt.datetime.now().isoformat(timespec="seconds"),
             "rv": rv, "occasioni": tenute}, indent=1, default=str))
        print(f"\nScritte {len(tenute)} occasioni in {a.json}")

    print("\nCome si legge:")
    print("  EV      valore atteso con la volatilita' REALMENTE realizzata dal sottostante")
    print("          (non con l'implicita, dove sarebbe zero per costruzione). E' il criterio")
    print("          di ordinamento: mette insieme probabilita' e dimensione di utili e perdite.")
    print("  RV0     volatilita' realizzata alla quale il valore atteso va a zero.")
    print("  marg    RV0 meno la volatilita' realmente realizzata, in punti. Il Piano chiede +2.")
    print("  c/r     credito in percentuale del rischio: quanto ti pagano per rischiare.")
    print("\nAVVERTENZA: RV0 usa una log-normale piatta, che sottostima la coda sinistra. Lo skew")
    print("sulle put esiste perche' il mercato sa che li' quel modello sbaglia: il margine delle")
    print("put molto lontane dal prezzo e' quindi SOPRASTIMATO.")
    print("Mancano open interest, dimensionamento e controllo di portafoglio. Nessun ordine.")


if __name__ == "__main__":
    main()
