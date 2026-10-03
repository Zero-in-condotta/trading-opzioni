"""Simula un double diagonal (SOLA LETTURA) con prezzi reali: vende put e call OTM vicine,
compra put e call sulla scadenza lontana.
Uso: uv run --with yfinance double_diagonal.py ENI 20 25              (stessi strike vicina/lontana)
     uv run --with yfinance double_diagonal.py ENI 20 25 20.5 25      (strike lontana poi strike vicina, se diversi)
Segue lo stesso metodo di strategies.py: RV attesa = max(RV10,RV20 c2c)+1; simulazione log-normale
a due stadi (prezzo a T1 con RV reale, poi la gamba lunga rivalutata con la sua IV / con la forward vol)."""
import sys, datetime as dt, numpy as np, yfinance as yf
from math import sqrt
from strategies import cfg, fetch, bs, rvs, earn, chunks
from ib_common import connect, clean
from ib_async import Index, Stock

sym, kp, kc = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
kp_n = float(sys.argv[4]) if len(sys.argv) > 4 else kp
kc_n = float(sys.argv[5]) if len(sys.argv) > 5 else kc
c = cfg(sym)
ib = connect("127.0.0.1", 7496, 140)
try:
    ib.reqMarketDataType(1)
    und = Index(c["sym"], c["exch"]) if c["kind"] == "IND" else Stock(c["sym"], c["exch"] if c.get("oexch") else "SMART", c["cur"])
    ib.qualifyContracts(und)
    t = ib.reqMktData(und, "", False, False); ib.sleep(3)
    spot = clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(und)
    if not spot:
        spot = float(yf.download(c["yt"], period="5d", progress=False, auto_adjust=False)["Close"].squeeze().dropna().iloc[-1])
        print(f"{sym}: prezzo dal Yahoo (nessun dato IBKR sul sottostante)")
    chains = ib.reqSecDefOptParams(c["sym"], "", und.secType, und.conId)
    today = dt.date.today(); near = far = None
    for ch in chains:
        if c.get("tclass") and ch.tradingClass != c["tclass"]: continue
        if c["kind"] == "STK" and ch.exchange != c.get("oexch", "SMART"): continue
        if c["kind"] == "IND" and not c.get("tclass") and ch.exchange not in ("SMART", "CBOE", "RUSSELL"): continue
        for e in ch.expirations:
            d = (dt.datetime.strptime(e, "%Y%m%d").date() - today).days
            if 24 <= d <= 46 and (near is None or abs(d - 33) < abs(near[0] - 33)): near = (d, e, ch.tradingClass, ch.exchange)
            if 55 <= d <= 130 and (far is None or abs(d - 90) < abs(far[0] - 90)): far = (d, e, ch.tradingClass, ch.exchange)
    if not near or not far:
        print(f"{sym}: scadenze non trovate (near={near}, far={far})"); sys.exit(1)
    d1, e1, tc1, ex1 = near; d2, e2, tc2, ex2 = far
    T1, T2 = d1 / 365, d2 / 365
    oexch = c["oexch"] if c.get("oexch") else "SMART"
    near_data = fetch(ib, c, und, spot, e1, tc1, oexch, sorted({kp_n, kc_n}))
    far_data = fetch(ib, c, und, spot, e2, tc2, oexch, sorted({kp, kc}))
    missing = [f"P{kp_n:g}@vicina" if ("P", kp_n) not in near_data else "",
               f"C{kc_n:g}@vicina" if ("C", kc_n) not in near_data else "",
               f"P{kp:g}@lontana" if ("P", kp) not in far_data else "",
               f"C{kc:g}@lontana" if ("C", kc) not in far_data else ""]
    missing = [m for m in missing if m]
    if missing:
        print(f"{sym}: dati mancanti su {missing}"); sys.exit(1)
    npm, ncm = near_data[("P", kp_n)], near_data[("C", kc_n)]
    fpm, fcm = far_data[("P", kp)], far_data[("C", kc)]
    credito_naturale = npm["bid"] + ncm["bid"] - fpm["ask"] - fcm["ask"]
    mid = lambda v: (v["bid"] + v["ask"]) / 2
    credito_medio = mid(npm) + mid(ncm) - mid(fpm) - mid(fcm)
    lim = credito_naturale + 0.4 * (credito_medio - credito_naturale)
    mult = c["mult"]
    rv = rvs(c["yt"]); ex_rv = max(rv[10], rv[20]) + 1
    ed = earn(c["yt"])
    print(f"\n=== Double diagonal {sym}: vicina put {kp_n:g}/call {kc_n:g}, lontana put {kp:g}/call {kc:g} | vicina {e1} ({d1} gg, classe {tc1}) / lontana {e2} ({d2} gg) | spot {spot:.2f} {c['cur']}")
    print(f"RV c2c 10/20/60 {rv[10]:.0f}/{rv[20]:.0f}/{rv[60]:.0f}% -> attesa {ex_rv:.1f}%" + (f" | ATTENZIONE utili {ed}" if ed and ed <= dt.datetime.strptime(e1,'%Y%m%d').date() else ""))
    print(f"Vicina: put {kp_n:g} bid {npm['bid']:.3f} (IV {npm['iv']*100:.1f}%, delta {npm['delta']:+.2f}, OI {npm['oi']}) | call {kc_n:g} bid {ncm['bid']:.3f} (IV {ncm['iv']*100:.1f}%, delta {ncm['delta']:+.2f}, OI {ncm['oi']})")
    print(f"Lontana: put {kp:g} ask {fpm['ask']:.3f} (IV {fpm['iv']*100:.1f}%, delta {fpm['delta']:+.2f}, OI {fpm['oi']}) | call {kc:g} ask {fcm['ask']:.3f} (IV {fcm['iv']*100:.1f}%, delta {fcm['delta']:+.2f}, OI {fcm['oi']})")
    print(f"Credito netto: naturale {credito_naturale:+.3f} | medio {credito_medio:+.3f} | limite {lim:+.3f} ({lim*mult:+.0f} {c['cur']})")

    z = np.random.default_rng(4).standard_normal(200000)
    F = spot * np.exp(-0.5 * (ex_rv/100)**2 * T1 + (ex_rv/100) * sqrt(T1) * z)
    corta = np.maximum(F - kc_n, 0) + np.maximum(kp_n - F, 0)  # quello che devi ai corti a T1 (se tenuti fino li)
    def lunga(sig):
        Tr = T2 - T1
        return np.array([bs(f, kc, Tr, sig, "C") for f in F]) + np.array([bs(f, kp, Tr, sig, "P") for f in F])
    for sig, lab in [(fpm["iv"] if abs(fpm["iv"]-fcm["iv"])<0.15 else (fpm["iv"]+fcm["iv"])/2, "IV attuale della lontana"),
                       (max(ex_rv/100, (fpm["iv"]+fcm["iv"])/2*0.85), "ipotesi IV più bassa (-15%)")]:
        val_lunga = lunga(sig)
        pnl = (val_lunga - corta + lim) * mult
        print(f"  Se a {d1} gg la lontana vale IV {sig*100:.1f}% ({lab}): P&L medio {pnl.mean():+,.0f} {c['cur']} (5° pct {np.percentile(pnl,5):+,.0f}, 95° pct {np.percentile(pnl,95):+,.0f}) | prob. P&L<0: {(pnl<0).mean()*100:.0f}%")
    # scenario di stress: mossa immediata a T1 di ampiezza crescente, rivalutando la lunga alla SUA iv attuale (congelata)
    print("  Stress (mossa immediata, IV della lontana congelata all'attuale):")
    for pct in (-0.30, -0.20, -0.10, 0, 0.10, 0.20, 0.30):
        Fs = spot * (1 + pct)
        Tr = T2 - T1
        vl = bs(Fs, kc, Tr, fcm["iv"], "C") + bs(Fs, kp, Tr, fpm["iv"], "P")
        co = max(Fs - kc_n, 0) + max(kp_n - Fs, 0)
        pnl = (vl - co + lim) * mult
        print(f"    {pct:+.0%}: spot {Fs:6.2f} -> P&L {pnl:+,.0f} {c['cur']}")
finally:
    ib.disconnect()
