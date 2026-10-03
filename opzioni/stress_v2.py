"""Stress test del portafoglio di opzioni, v2 (SOLA LETTURA, 02/10/2026).

Ogni opzione e' rivalutata con Black-Scholes sotto cinque scenari di mercato, immediati e a 7 e
14 giorni. Il P/L di scenario e' sempre (valore stressato - valore corrente con lo stesso modello),
cosi lo scarto del modello rispetto ai prezzi veri si compensa. Rispetto alla v1:
  1. CAMBIO EUR/USD letto da TWS (Forex), non piu' scritto nel codice (era 1,153 contro ~1,126).
  2. BETA calcolati dallo storico (calcola_beta.py -> data/betas.json) invece di 1,0/1,3 fissi.
     Per i titoli si usa il piu' alto fra beta 2 anni, beta 1 anno e 1,0 (scelta prudente: la
     correlazione con il mercato e' bassa e il rischio idiosincratico non si vede nel beta).
  3. SKEW: dopo lo shock la volatilita' di ogni opzione si rilegge dalla CURVA DELLA CATENA al
     nuovo livello di moneyness (sticky moneyness) e poi si somma lo shock di volatilita';
     una put lontana che con il ribasso si avvicina al denaro prende la IV del denaro, non la
     sua IV da put lontana. La curva si ricava da strike reali (puts sotto, calls sopra il prezzo).
  4. TEMPO: gli stessi scenari anche dopo 7 e 14 giorni (decadimento dell'estrinseco).
Con il mercato chiuso, dove TWS non da' le greche, si usano gli ultimi valori buoni salvati in
data/stress_cache.json (segnalati come 'non aggiornati'); mai si scarta una posizione in silenzio.
Scrive data/stress.json (letto dalla dashboard) e stampa un riassunto.
"""
import datetime as dt, json, sys
from math import log, sqrt, exp
from statistics import NormalDist

from ib_async import Option, Forex, Index, Stock
import deals
from ib_common import connect, clean, DATA

N = NormalDist().cdf
SCEN = [("S1 correzione", -5, 8), ("S2 shock", -10, 20), ("S3 crash", -20, 40),
        ("S4 gap weekend", -7, 15), ("S5 rally violento", 7, -6)]
ORIZZONTI = [0, 7, 14]
INDICI = {"ESTX50", "XSP", "SPX", "NDX", "XND", "RUT", "MRUT", "DJX", "VIX"}
VOL_SCALE_TITOLI = 0.7        # la IV di un titolo reagisce allo shock meno della IV dell'indice (assunzione)
FILE_CACHE = DATA / "stress_cache.json"


def bs(S, K, T, s, cp, r=0.02):
    if T <= 0:
        return max(S - K, 0) if cp == "C" else max(K - S, 0)
    d1 = (log(S / K) + (r + .5 * s * s) * T) / (s * sqrt(T)); d2 = d1 - s * sqrt(T)
    return S * N(d1) - K * exp(-r * T) * N(d2) if cp == "C" else K * exp(-r * T) * N(-d2) - S * N(-d1)


def carica(f, d):
    try:
        return json.loads(f.read_text())
    except Exception:
        return d


def interpola(punti, m):
    """IV sulla curva (lista ordinata di (moneyness, iv)), lineare, piatta fuori dagli estremi."""
    if not punti:
        return None
    if m <= punti[0][0]:
        return punti[0][1]
    if m >= punti[-1][0]:
        return punti[-1][1]
    for (m0, v0), (m1, v1) in zip(punti, punti[1:]):
        if m0 <= m <= m1:
            return v0 + (v1 - v0) * (m - m0) / (m1 - m0) if m1 > m0 else v0
    return punti[-1][1]


def main():
    cache = carica(FILE_CACHE, {"pos": {}, "curve": {}})
    betas = carica(DATA / "betas.json", {}).get("beta", {})
    mappa = carica(DATA / "portfolio_map.json", {"groups": []})
    HEDGE = {(l["symbol"], l["expiry"], float(l["strike"]), l["right"])
             for g in mappa["groups"] if g["id"] == "protezioni_coda" for l in g["legs"]}

    ib = connect("127.0.0.1", 7496, 140)
    avvisi, stantii = [], []
    try:
        ib.reqMarketDataType(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
        # ---- 1. cambio vero ----
        fxr, fonte_fx = None, "TWS"
        try:
            f = Forex("EURUSD"); ib.qualifyContracts(f)
            t = ib.reqMktData(f, "", False, False); ib.sleep(3)
            fxr = next((x for x in (t.marketPrice(), t.last, t.close) if x and x == x and x > 0), None)
            ib.cancelMktData(f)
        except Exception:
            pass
        if not fxr:
            fxr, fonte_fx = cache.get("fx") or 1.12, "ULTIMO NOTO (TWS non risponde)"
            avvisi.append("cambio EUR/USD non letto da TWS: usato l'ultimo noto")
        cache["fx"] = fxr

        port = [p for p in ib.portfolio() if p.contract.secType == "OPT"]
        for p in port:
            ib.qualifyContracts(p.contract)
        tk = [(p, ib.reqMktData(p.contract, "", False, False)) for p in port]
        ib.sleep(8)
        rows = []
        und_close = {}
        oggi = dt.date.today()
        for p, t in tk:
            c = p.contract
            g = t.modelGreeks
            chiave = str(c.conId)
            if g and g.impliedVol and g.undPrice:
                iv, S = g.impliedVol, g.undPrice
                cache["pos"][chiave] = dict(iv=iv, S=S, quando=dt.datetime.now().isoformat(timespec="minutes"))
            elif chiave in cache["pos"]:
                iv, S = cache["pos"][chiave]["iv"], cache["pos"][chiave]["S"]
                stantii.append(f"{c.symbol} {c.strike:g}{c.right} {c.lastTradeDateOrContractMonth}: ultimi dati buoni del {cache['pos'][chiave]['quando']}")
            else:
                # ripiego: IV ricavata dal prezzo di portafoglio (ultimo noto) e dalla chiusura del sottostante
                T0 = (dt.datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date() - oggi).days / 365
                S = und_close.get(c.symbol)
                if S is None:
                    try:
                        u = (Index(c.symbol, "EUREX" if c.symbol == "ESTX50" else "CBOE") if c.symbol in INDICI
                             else Stock(c.symbol, "SMART", c.currency))
                        ib.qualifyContracts(u)
                        tu = ib.reqMktData(u, "", False, False); ib.sleep(3)
                        S = next((x for x in (tu.last, tu.close, tu.marketPrice()) if x and x == x and x > 0), None)
                        ib.cancelMktData(u)
                    except Exception:
                        S = None
                    und_close[c.symbol] = S
                iv = None
                if S and p.marketPrice and p.marketPrice > 0 and T0 > 0:
                    try:
                        iv = deals.implied_vol(p.marketPrice, S, c.strike, T0, c.right)
                    except Exception:
                        iv = None
                if not iv or iv != iv or iv <= 0:
                    avvisi.append(f"{c.symbol} {c.strike:g}{c.right} {c.lastTradeDateOrContractMonth}: nessun dato, nessuna cache e IV non ricavabile: ESCLUSA dal test")
                    continue
                cache["pos"][chiave] = dict(iv=iv, S=S, quando=dt.datetime.now().isoformat(timespec="minutes") + " (da prezzo di portafoglio)")
                stantii.append(f"{c.symbol} {c.strike:g}{c.right} {c.lastTradeDateOrContractMonth}: IV ricavata dall'ultimo prezzo ({iv*100:.1f}%), sottostante {S:,.2f}")
            T = (dt.datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date() - oggi).days / 365
            rows.append(dict(sym=c.symbol, exp=c.lastTradeDateOrContractMonth, cp=c.right, K=c.strike, T=T, iv=iv, S=S,
                             q=p.position, mult=float(c.multiplier), cur=c.currency, c=c,
                             hedge=(c.symbol, c.lastTradeDateOrContractMonth, float(c.strike), c.right) in HEDGE
                                   and p.position > 0))
        for p, t in tk:
            ib.cancelMktData(p.contract)

        # ---- 3. curve di skew per (sottostante, scadenza) ----
        curve = {}
        coppie = {}
        for r in rows:
            coppie.setdefault((r["sym"], r["exp"]), r)
        for (sym, exp), r in coppie.items():
            c, S = r["c"], r["S"]
            punti = []
            try:
                cand = []
                for right in ("P", "C"):
                    cd = ib.reqContractDetails(Option(sym, exp, 0, right, c.exchange or "SMART", currency=c.currency,
                                                      tradingClass=c.tradingClass))
                    ks = sorted({d.contract.strike: d.contract for d in cd}.items())
                    ks = [(k, x) for k, x in ks if 0.72 * S <= k <= 1.30 * S and ((right == "P" and k < S) or (right == "C" and k >= S))]
                    if len(ks) > 14:
                        passo = (len(ks) - 1) / 13
                        ks = [ks[round(i * passo)] for i in range(14)]
                    cand += ks
                ts = [(k, x, ib.reqMktData(x, "", False, False)) for k, x in cand]
                ib.sleep(7)
                for k, x, t in ts:
                    gg = t.modelGreeks
                    if gg and gg.impliedVol and gg.impliedVol > 0:
                        punti.append((log(k / S), gg.impliedVol))
                    ib.cancelMktData(x)
            except Exception as e:
                avvisi.append(f"{sym} {exp}: curva non letta ({e})")
            chiave = f"{sym}|{exp}"
            # il punto della posizione stessa ancora la curva al suo livello di IV
            if punti:
                punti = sorted(set(punti))
            if len(punti) >= 4:
                curve[chiave] = (sorted(punti), "live")
                cache["curve"][chiave] = dict(punti=curve[chiave][0], S=S, quando=dt.datetime.now().isoformat(timespec="minutes"))
            elif chiave in cache["curve"]:
                cc = cache["curve"][chiave]
                curve[chiave] = ([tuple(x) for x in cc["punti"]], "cache " + cc["quando"][:16])
                stantii.append(f"curva {sym} {exp}: ultima buona del {cc['quando'][:16]}")
            else:
                # nessuna curva ne' cache: skew PARAMETRICO ancorato alla IV della posizione
                pend = -0.7 if sym in INDICI else -0.4
                m0 = log(r["K"] / S)
                curve[chiave] = ([(m, max(r["iv"] + pend * (m - m0), 0.05)) for m in [x / 100 for x in range(-45, 36, 5)]],
                                 f"parametrica (pendenza {pend})")
                avvisi.append(f"curva {sym} {exp}: non disponibile, uso uno skew parametrico (pendenza {pend}); alla prossima ricarica con mercato aperto si salva la curva vera")
        nav = next(float(a.value) for a in ib.accountSummary() if a.tag == "NetLiquidation" and a.currency == "EUR")
    finally:
        ib.disconnect()
    FILE_CACHE.write_text(json.dumps(cache))

    def parametri(sym):
        if sym in INDICI:
            return 1.0, 1.0
        b = betas.get(sym) or {}
        beta = max(b.get("beta_2a") or 1.0, b.get("beta_1a") or 1.0, 1.0)
        return beta, VOL_SCALE_TITOLI

    def valore(r, dm, dv, h, per_hedge):
        if r["hedge"] and not per_hedge:
            return 0.0
        beta, vs = parametri(r["sym"])
        S2 = r["S"] * (1 + beta * dm / 100)
        T2 = r["T"] - h / 365
        v0 = bs(r["S"], r["K"], r["T"], r["iv"], r["cp"])
        if T2 <= 0:
            v1 = bs(S2, r["K"], 0, 0.2, r["cp"])
        else:
            pts, _ = curve.get(f"{r['sym']}|{r['exp']}", ([], ""))
            base = interpola(pts, log(r["K"] / S2)) or r["iv"]
            dvol = dv / 100 * vs * min(sqrt(30 / max(T2 * 365, 7)), 2.0)
            v1 = bs(S2, r["K"], T2, max(base + dvol, 0.03), r["cp"])
        pl = r["q"] * r["mult"] * (v1 - v0)
        return pl if r["cur"] == "EUR" else pl / fxr

    out_scen = []
    for nome, dm, dv in SCEN:
        o = {}
        for h in ORIZZONTI:
            con = sum(valore(r, dm, dv, h, True) for r in rows)
            sen = sum(valore(r, dm, dv, h, False) for r in rows)
            o[str(h)] = dict(con=con, senza=sen, con_pct=con / nav * 100, senza_pct=sen / nav * 100, protezioni=con - sen)
        out_scen.append(dict(nome=nome, dm=dm, dv=dv, orizzonti=o))
    beta_usati = {s: parametri(s)[0] for s in sorted({r["sym"] for r in rows})}
    res = dict(generato=dt.datetime.now().strftime("%d/%m/%Y %H:%M"), nav=nav, fx=dict(EURUSD=fxr, fonte=fonte_fx),
               beta=beta_usati, vol_scale_titoli=VOL_SCALE_TITOLI, orizzonti=ORIZZONTI, scenari=out_scen,
               curve={k: dict(punti=len(v[0]), fonte=v[1]) for k, v in curve.items()},
               avvisi=avvisi, dati_non_aggiornati=stantii, posizioni=len(rows))
    (DATA / "stress.json").write_text(json.dumps(res, indent=1))
    print(f"NAV {nav:,.0f} EUR (USD convertiti a {fxr:.4f}, {fonte_fx}). Beta usati: "
          + ", ".join(f"{k} {v:.2f}" for k, v in beta_usati.items()))
    for s in out_scen:
        a, b, c7, c14 = s["orizzonti"]["0"], s["orizzonti"]["0"], s["orizzonti"]["7"], s["orizzonti"]["14"]
        print(f"{s['nome']:20s} ({s['dm']:+d}%, IV {s['dv']:+d})  ora CON {a['con']:+8,.0f} EUR ({a['con_pct']:+.1f}%)  "
              f"SENZA {a['senza']:+8,.0f} ({a['senza_pct']:+.1f}%)  | +7g CON {c7['con_pct']:+.1f}%  +14g CON {c14['con_pct']:+.1f}%")
    for a in avvisi + stantii:
        print("AVVISO:", a)


if __name__ == "__main__":
    main()
