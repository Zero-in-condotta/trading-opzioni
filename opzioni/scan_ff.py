"""Screener del FORWARD FACTOR su molti ticker (SOLA LETTURA), a flusso.

Per ogni ticker legge la IV al denaro di ogni scadenza mensile (7-250 giorni) e calcola, per ogni
coppia (corta, lunga), la volatilita' forward fra le due e il forward factor:
    FF = IV_corta / vol_forward - 1,    vol_forward = sqrt((IV_l^2 T_l - IV_c^2 T_c) / (T_l - T_c))
FF positivo = la scadenza corta e' piu' cara del periodo successivo: e' la condizione in cui un calendar
(vendi la corta, compra la lunga) ha vantaggio di struttura (il Piano chiede >= +0,20).
Scrive data/occasioni_ff/<job>.json aggiornandolo a ogni ticker. Uso: scan_ff.py --job J --sym SPY QQQ
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, signal, sys
from math import sqrt
from types import SimpleNamespace

from ib_common import DATA, connect, clean
import scan_vrp as sv
from ib_async import Index, Stock

DIR = DATA / "occasioni_ff"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--sym", nargs="+", required=True)
    ap.add_argument("--port", type=int, default=7496)
    ap.add_argument("--mdt", type=int, default=1)
    a = ap.parse_args()
    DIR.mkdir(exist_ok=True)
    f = DIR / f"{a.job}.json"
    tickers = [t.upper() for t in a.sym]
    st = dict(job=a.job, tipo="ff", tickers=tickers, fase="scan", messaggio="avvio", righe=[], errori=[],
              avviato=dt.datetime.now().isoformat(timespec="seconds"), pid=os.getpid(), fatti=0)

    def scrivi():
        st["aggiornato"] = dt.datetime.now().isoformat(timespec="seconds")
        tmp = f.with_suffix(".tmp"); tmp.write_text(json.dumps(st, default=str)); tmp.replace(f)

    def termina(*_):
        st.update(fase="annullato", messaggio="annullato"); scrivi(); os._exit(0)
    signal.signal(signal.SIGTERM, termina)
    scrivi()
    try:
        ib = connect("127.0.0.1", a.port, 231)
        ib.reqMarketDataType(a.mdt)
    except Exception as exc:  # noqa: BLE001
        st.update(fase="errore", messaggio=f"connessione a TWS fallita: {exc}"); scrivi(); return 1
    oggi = dt.date.today()
    try:
        for n, sym in enumerate(tickers, 1):
            st["messaggio"] = f"{sym} ({n}/{len(tickers)}): leggo le IV al denaro"; scrivi()
            try:
                exch, cur, _t, _w, _b, tipo = sv.config_sottostante(sym)
                und = Stock(sym, "SMART", cur) if tipo == "STK" else Index(sym, sv.ESCH_INDICE.get(sym, exch))
                ib.qualifyContracts(und)
                t = ib.reqMktData(und, "", False, False); ib.sleep(3)
                spot = next((v for v in (clean(t.last), clean(t.close)) if v), None)
                ib.cancelMktData(und)
                if not spot:
                    st["errori"].append(f"{sym}: prezzo non disponibile"); continue
                utili = None
                if tipo == "STK":
                    try:
                        import yfinance as yf
                        cal = yf.Ticker(sym).calendar
                        ed = [x for x in ((cal.get("Earnings Date") if isinstance(cal, dict) else None) or []) if x >= oggi]
                        utili = min(ed) if ed else None
                    except Exception:
                        utili = None
                iv_exp = []
                for exp, dte in sv.scadenze(ib, und, 7, 250, sym=sym, weekly=False):
                    cs = {r: dict(sv.catena(ib, exp, r, sym, exch, cur)) for r in ("P", "C")}
                    comuni = sorted(k for k in cs["P"] if k in cs["C"])
                    if not comuni:
                        continue
                    k0 = min(comuni, key=lambda k: abs(k - spot))
                    px = sv.quota(ib, [cs["C"][k0], cs["P"][k0]], wait=5)
                    ivs = [px[c.conId]["iv"] for c in (cs["C"][k0], cs["P"][k0]) if px.get(c.conId) and px[c.conId].get("iv")]
                    if ivs:
                        iv_exp.append((exp, dte, sum(ivs) / len(ivs), k0))
                for i, (e1, d1, v1, k1) in enumerate(iv_exp):
                    for e2, d2, v2, k2 in iv_exp[i + 1:]:
                        if not (14 <= d2 - d1 <= 120):
                            continue
                        T1, T2 = d1 / 365, d2 / 365
                        fv = (v2 * v2 * T2 - v1 * v1 * T1) / (T2 - T1)
                        if fv <= 0:
                            continue
                        ff = v1 / sqrt(fv) - 1
                        ute = bool(utili and oggi <= utili <= dt.datetime.strptime(e1, "%Y%m%d").date())
                        st["righe"].append(dict(sym=sym, spot=spot, front=e1, back=e2, dte_f=d1, dte_b=d2, iv_f=v1, iv_b=v2,
                                                ff=ff, k=k1, utili=str(utili) if ute else None, cur=cur))
                st["fatti"] = n
            except Exception as exc:  # noqa: BLE001
                st["errori"].append(f"{sym}: {exc}")
            scrivi()
        st.update(fase="finito", messaggio="screener completato"); scrivi()
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
