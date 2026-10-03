"""Valutazione del double calendar con prezzi reali (SOLA LETTURA), metodo DC Time Machine.
Per ogni coppia di scadenze (corta, lunga) costruisce il double calendar con gambe a circa 35 delta
(put sotto, call sopra) e calcola:
  - debito (mid e naturale), rischio massimo circa = debito, IV corta/lunga per lato e loro rapporto
  - quanto deve guadagnare il double calendar per poter essere trasformato in iron condor senza rischio
    (soglia = debito + larghezza ali; credito della trasformazione = valore DC + valore dell'iron condor
    sulla scadenza corta, quindi guadagno necessario = larghezza - valore attuale dell'IC con ali w)
  - P/L dopo 1 giorno con IV invariate per mosse 0, +-0,5 e +-1 deviazione standard giornaliera,
    e con IV corta -10% (rapporto corta/lunga che rientra, la leva principale del metodo)
  - valore atteso tenendo fino alla scadenza corta (senza trasformare): prezzo simulato con la RV attesa,
    gamba lunga rivalutata con la sua IV attuale
  - liquidita' (spread % e open interest per gamba)
Uso: uv run --with yfinance double_calendar.py SPX XSP ESTX50            (stile video: corta 6-15 gg, lunga +1..4 gg)
     uv run --with yfinance double_calendar.py XSP ESTX50 --long         (stile nostro: corta 24-46 gg, lunga 55-95 gg)
Non invia ordini."""
from __future__ import annotations
import argparse, datetime as dt
from math import sqrt
import numpy as np, yfinance as yf
from ib_async import Index, Stock
from ib_common import connect, clean
from strategies import cfg, fetch, bs, rvs

WING = {"SPX": 5, "XSP": 1, "ESTX50": 25, "RUT": 5}
TARGET_DELTA = 0.35


def expiries(ib, c, und):
    out = {}
    for ch in ib.reqSecDefOptParams(c["sym"], "", und.secType, und.conId):
        if c["sym"] == "ESTX50" and not (ch.exchange == "EUREX" and ch.tradingClass.startswith("OES")):
            continue
        if c["sym"] in ("SPX", "XSP", "RUT") and ch.exchange not in ("SMART", "CBOE", "RUSSELL"):
            continue
        for e in ch.expirations:
            prev = out.get(e)
            # a parita' di scadenza preferisci le classi settimanali (SPXW) e SMART
            if prev is None or ch.tradingClass.endswith("W") or (ch.exchange == "SMART" and prev[1] != "SMART" and not prev[0].endswith("W")):
                out[e] = (ch.tradingClass, ch.exchange, set(ch.strikes))
    return out


def pairs(exps, long_style):
    today = dt.date.today()
    days = {e: (dt.datetime.strptime(e, "%Y%m%d").date() - today).days for e in exps}
    res = []
    if long_style:
        fr = [e for e in exps if 24 <= days[e] <= 46]
        bk = [e for e in exps if 55 <= days[e] <= 95]
        for f in sorted(fr, key=lambda e: abs(days[e] - 36))[:1]:
            for b in sorted(bk, key=lambda e: abs(days[e] - 85))[:1]:
                res.append((f, b))
    else:
        fr = sorted(e for e in exps if 6 <= days[e] <= 15)
        for f in fr[::max(1, len(fr) // 3)][:3]:
            bk = sorted(e for e in exps if 1 <= days[e] - days[f] <= 4)
            if bk:
                res.append((f, bk[0]))
    return [(f, b, days[f], days[b]) for f, b in res]


def analyse(ib, name, long_style):
    c = cfg(name)
    und = Index(c["sym"], c["exch"]) if c["kind"] == "IND" else Stock(c["sym"], "SMART", c["cur"])
    ib.qualifyContracts(und)
    t = ib.reqMktData(und, "", False, False); ib.sleep(3)
    spot = clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(und)
    if not spot:
        spot = float(yf.download(c["yt"], period="5d", progress=False, auto_adjust=False)["Close"].squeeze().dropna().iloc[-1])
    rv = rvs(c["yt"]); ex_rv = max(rv[10], rv[20]) + 1
    mult = c["mult"]; w = WING.get(name, 5); cur = c["cur"]
    exps = expiries(ib, c, und)
    prs = pairs(exps, long_style)
    print(f"\n=== {name}: spot {spot:,.2f} | RV 10/20/60 {rv[10]:.0f}/{rv[20]:.0f}/{rv[60]:.0f}% -> attesa {ex_rv:.1f}% | ali {w:g} | coppie {[(f, b) for f, b, _, _ in prs]}")
    if not prs:
        print("  nessuna coppia di scadenze adatta"); return []
    out = []
    z = np.random.default_rng(7).standard_normal(20000)
    for f, b, d1, d2 in prs:
        tc1, ex1, ks1 = exps[f]; tc2, ex2, ks2 = exps[b]
        band = 0.05 if long_style else 0.035
        kn = sorted(k for k in ks1 if abs(k - spot) / spot <= band)
        oexch = c.get("oexch") or "SMART"
        F1 = fetch(ib, c, und, spot, f, tc1, oexch, kn)
        P1 = {k: v for (r, k), v in F1.items() if r == "P"}; C1 = {k: v for (r, k), v in F1.items() if r == "C"}
        if len(P1) < 4 or len(C1) < 4:
            print(f"  {f}/{b}: catena corta incompleta ({len(P1)} put, {len(C1)} call)"); continue
        kp = min((k for k in P1 if k < spot), key=lambda k: abs(abs(P1[k]["delta"]) - TARGET_DELTA), default=None)
        kc = min((k for k in C1 if k > spot), key=lambda k: abs(C1[k]["delta"] - TARGET_DELTA), default=None)
        if kp is None or kc is None or (kp - w) not in P1 or (kc + w) not in C1:
            print(f"  {f}/{b}: strike o ali non disponibili"); continue
        F2 = fetch(ib, c, und, spot, b, tc2, oexch, [kp, kc])
        if ("P", kp) not in F2 or ("C", kc) not in F2:
            print(f"  {f}/{b}: quote della scadenza lunga assenti"); continue
        sp, sc, lp, lc = P1[kp], C1[kc], F2[("P", kp)], F2[("C", kc)]
        mid = lambda v: (v["bid"] + v["ask"]) / 2
        debit_mid = mid(lp) + mid(lc) - mid(sp) - mid(sc)
        debit_nat = lp["ask"] + lc["ask"] - sp["bid"] - sc["bid"]
        ic_mid = mid(sp) - mid(P1[kp - w]) + mid(sc) - mid(C1[kc + w])  # valore dell'IC con ali w sulla corta
        need = w - ic_mid                                                  # guadagno del DC necessario (punti)
        T1, T2, day = d1 / 365, d2 / 365, 1 / 365
        def dc_val(S, t_el, ivs_scale=1.0):
            return (bs(S, kp, T2 - t_el, lp["iv"], "P") + bs(S, kc, T2 - t_el, lc["iv"], "C")
                    - bs(S, kp, max(T1 - t_el, 1e-6), sp["iv"] * ivs_scale, "P") - bs(S, kc, max(T1 - t_el, 1e-6), sc["iv"] * ivs_scale, "C"))
        base = dc_val(spot, 0)
        sd = spot * ex_rv / 100 * sqrt(day)
        moves = {m: (dc_val(spot + m * sd, day) - base) * mult for m in (-1, -0.5, 0, 0.5, 1)}
        ivdrop = (dc_val(spot, day, 0.9) - base) * mult
        # EV fino alla scadenza corta, senza trasformare
        S1 = spot * np.exp(-0.5 * (ex_rv / 100) ** 2 * T1 + ex_rv / 100 * sqrt(T1) * z)
        longv = np.array([bs(s, kp, T2 - T1, lp["iv"], "P") + bs(s, kc, T2 - T1, lc["iv"], "C") for s in S1])
        shortv = np.maximum(kp - S1, 0) + np.maximum(S1 - kc, 0)
        lim = debit_nat - 0.6 * (debit_nat - debit_mid)
        pl = (longv - shortv - lim) * mult
        spr = [round((v["ask"] - v["bid"]) / mid(v) * 100, 1) for v in (sp, lp, sc, lc)]
        oi = [v["oi"] for v in (sp, lp, sc, lc)]
        r = dict(name=name, f=f, b=b, d1=d1, d2=d2, kp=kp, kc=kc, debit=lim * mult, need=need * mult,
                 need_pct=need / lim * 100, ratio_p=sp["iv"] / lp["iv"], ratio_c=sc["iv"] / lc["iv"],
                 ev=pl.mean(), pop=(pl > 0).mean() * 100, moves=moves, ivdrop=ivdrop, spr=spr, oi=oi, cur=cur)
        out.append(r)
        print(f"  DC {kp:g}P/{kc:g}C  corta {f} ({d1} gg) / lunga {b} ({d2} gg): debito {lim:.2f} = {lim*mult:,.0f} {cur} (nat {debit_nat:.2f}, mid {debit_mid:.2f}), "
              f"delta {sp['delta']:.2f}/{sc['delta']:.2f}")
        print(f"     IV corta/lunga put {sp['iv']*100:.1f}/{lp['iv']*100:.1f}% (rapporto {r['ratio_p']:.2f}), call {sc['iv']*100:.1f}/{lc['iv']*100:.1f}% (rapporto {r['ratio_c']:.2f})")
        print(f"     trasformazione in IC ali {w:g}: serve un guadagno di {need:.2f} punti = {need*mult:,.0f} {cur} ({r['need_pct']:.0f}% del debito)")
        print(f"     P/L dopo 1 giorno, IV invariate: " + ", ".join(f"{m:+g}sd {v:+.0f}" for m, v in moves.items()) + f" {cur}; con IV corta -10%: {ivdrop:+.0f} {cur}")
        print(f"     tenuto fino alla scadenza corta senza trasformare: EV {r['ev']:+.0f} {cur}, prob. utile {r['pop']:.0f}%")
        print(f"     liquidita': spread% {spr} (corta P, lunga P, corta C, lunga C), OI {oi}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("names", nargs="+"); ap.add_argument("--long", action="store_true")
    ap.add_argument("--mdt", type=int, default=1)
    a = ap.parse_args()
    ib = connect("127.0.0.1", 7496, 132)
    try:
        ib.reqMarketDataType(a.mdt)
        for n in a.names:
            try:
                analyse(ib, n, a.long)
            except Exception as e:
                print(n, "errore:", str(e)[:200])
    finally:
        ib.disconnect()
