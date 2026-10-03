"""Ricerca di occasioni su uno o piu' ticker (indici o azioni USA), a flusso (SOLA LETTURA).

Lanciato dal server locale (chain_builder.py) quando premi "Cerca occasioni" nella dashboard:
scrive data/occasioni/<job>.json e lo AGGIORNA a ogni scadenza analizzata, cosi' la pagina mostra
le occasioni man mano che vengono trovate. Nessun ordine, nessun legame col caricamento della
dashboard. Uso manuale: uv run --with yfinance scan_ticker.py --job prova --sym AAPL ESTX50
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, signal, sys, time
from types import SimpleNamespace

from ib_common import DATA, connect
import scan_vrp as sv
import stat_ev

CAMPI = ("ev_vol", "ev_st", "prob_st", "pl5", "pl20", "rvref", "utili", "sym", "cur", "spot", "exp", "dte", "tipo", "ks", "kl", "w", "delta", "iv", "credito", "rischio",
         "cr", "prob", "rv0", "margine", "ev_iv", "ev_rv", "spread", "ordine", "lato", "importo",
         "profitto", "verso", "exp_lunga", "ff", "oi_min")
DIR = DATA / "occasioni"


class Stato:
    def __init__(self, job, tickers):
        self.f = DIR / f"{job}.json"
        self.d = dict(job=job, tickers=tickers, fase="scan", messaggio="avvio", avviato=dt.datetime.now().isoformat(timespec="seconds"),
                      aggiornato=None, info={}, rv={}, righe=[], errori=[], pid=os.getpid())
        self.n = 0

    def scrivi(self):
        self.d["aggiornato"] = dt.datetime.now().isoformat(timespec="seconds")
        tmp = self.f.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, default=str))
        tmp.replace(self.f)


def completa(r, rv_hi):
    # valore atteso PRUDENTE: il minore fra quello con la volatilita' prevista (HAR) e quello sulla
    # distribuzione storica (bootstrap); si tengono entrambi per mostrarli
    r["ev_vol"] = r.get("ev_rv")
    if r.get("ev_st") is not None and r.get("ev_rv") is not None:
        r["ev_rv"] = min(r["ev_rv"], r["ev_st"])
    rif = r.get("rvref") or rv_hi
    if r.get("rv0") and rif:
        r["margine"] = (r["rv0"] - rif) * 100 if r.get("verso", "sotto") == "sotto" else (rif - r["rv0"]) * 100
    else:
        r["margine"] = None
    prof = r.get("profitto", r["credito"])
    r["cr"] = prof / r["rischio"] * 100 if r["rischio"] else None
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--sym", nargs="+", required=True)
    ap.add_argument("--dte", nargs=2, type=int, default=[7, 200])
    ap.add_argument("--no-weekly", action="store_true", help="solo scadenze mensili")
    ap.add_argument("--port", type=int, default=7496)
    ap.add_argument("--mdt", type=int, default=1, help="tipo di dati: 1 live (default), 2 congelati (solo per prove a mercato chiuso)")
    a = ap.parse_args()
    DIR.mkdir(exist_ok=True)
    tickers = [t.upper().strip() for t in a.sym if t.strip()]
    st = Stato(a.job, tickers)
    st.scrivi()

    def termina(*_):
        st.d.update(fase="annullato", messaggio="ricerca annullata")
        st.scrivi()
        os._exit(0)
    signal.signal(signal.SIGTERM, termina)

    args = SimpleNamespace(width=None, banda=None, dte=tuple(a.dte), delta=(0.08, 0.32), weekly=not a.no_weekly)
    try:
        ib = connect("127.0.0.1", a.port, 230)
        ib.reqMarketDataType(a.mdt)
    except Exception as exc:  # noqa: BLE001
        st.d.update(fase="errore", messaggio=f"connessione a TWS fallita: {exc}")
        st.scrivi()
        return 1
    try:
        for sym in tickers:
            cfg = sv.config_sottostante(sym)
            m = sv.rv_storiche_di(cfg[2]) or {}
            rv_hi, rv_lo = (max(m.values()), min(m.values())) if m else (None, None)
            har30 = stat_ev.vol_har(cfg[2], 45)
            st.d["rv"][sym] = dict(rv_hi=rv_hi, rv_lo=rv_lo, finestre=m, har_45gg=har30)
            tk_st = cfg[2]
            rv_fn = lambda d, _t=tk_st, _f=rv_hi: stat_ev.vol_har(_t, d) or _f

            def hook(legs, _t=tk_st):
                r = stat_ev.ev_storico(legs, _t)
                if not r:
                    return None
                spot_ = next((l.get("und") for l in legs if l.get("und")), None)
                return dict(ev_st=r[0], prob_st=r[1], pl5=r[2],
                            pl20=stat_ev.pl_scenario(legs, spot_, -0.20) if spot_ else None)
            sv.EV_HOOK = hook
            utili = None
            if cfg[5] == "STK":
                try:
                    import yfinance as yf
                    cal = yf.Ticker(sym).calendar
                    ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
                    ed = [x for x in (ed or []) if x >= dt.date.today()]
                    utili = min(ed) if ed else None
                except Exception:
                    utili = None
            st.d["rv"][sym]["utili"] = str(utili) if utili else None
            if not rv_hi:
                st.d["errori"].append(f"{sym}: volatilita' storica non disponibile (yfinance), niente valore atteso")
                st.scrivi()
                continue
            st.d["messaggio"] = f"{sym}: avvio"
            st.scrivi()

            ultimo = {"msg": None}

            def on_rows(rows, messaggio=None, info=None, _sym=sym, _hi=rv_hi, _u=ultimo):
                if info:
                    st.d["info"][_sym] = info
                if messaggio:
                    st.d["messaggio"] = messaggio
                    _u["msg"] = messaggio
                for r in rows:
                    if r.get("ev_rv") is None:
                        continue
                    completa(r, _hi)
                    if utili and r.get("exp") and dt.date.today() <= utili <= dt.datetime.strptime(r["exp"], "%Y%m%d").date():
                        r["utili"] = str(utili)
                    st.n += 1
                    out = {k: r.get(k) for k in CAMPI}
                    out["id"] = f"{_sym}-{st.n}"
                    st.d["righe"].append(out)
                if rows:
                    st.d["messaggio"] = f"{_sym}: trovate {len(st.d['righe'])} strutture finora"
                st.scrivi()
            n_prima = len(st.d["righe"])
            try:
                sv.analizza(ib, sym, args, rv_fn, rv_fn, on_rows=on_rows, cfg=cfg)
                if len(st.d["righe"]) == n_prima:
                    padre = sv.PADRE.get(sym)
                    quote = ("nessuna quotazione utilizzabile dalla catena: con ogni probabilita' mancano i dati in tempo reale "
                             "per questo sottostante (TWS: 'market data not subscribed'); i dati ritardati non si usano "
                             "perche' i prezzi non sono eseguibili")
                    st.d["errori"].append(f"{sym}: nessuna struttura trovata. {ultimo['msg'] or ''} {quote}".strip()
                                          + (f" (volatilita' storica presa da {padre})" if padre else ""))
                    st.scrivi()
            except Exception as exc:  # noqa: BLE001
                st.d["errori"].append(f"{sym}: {exc}")
                st.scrivi()
        st.d.update(fase="finito", messaggio="ricerca completata")
        st.scrivi()
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
