"""Box spread su indici europei cash-settled (SPX, XSP): rendimento implicito della liquidita' (SOLA LETTURA).

Un box long (compri call K1, vendi call K2, compri put K2, vendi put K1) paga SEMPRE K2 - K1 a scadenza,
qualunque cosa faccia il sottostante: e' un prestito a tasso fisso. Il rendimento annuo implicito e'
((K2-K1) / costo)^(365/giorni) - 1, con prezzi ESEGUIBILI (compri all'ask, vendi al bid). Si confronta con il
rendimento di T-bill/monetari a pari durata: ha senso solo se lo supera, al netto di commissioni e di
margine (verificare su IBKR). Gli indici sono europei: nessuna assegnazione anticipata.
Scrive data/occasioni_box/<job>.json. Uso: scan_box.py --job J --sym SPX XSP
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, signal, sys

from ib_common import DATA, connect, clean
import scan_vrp as sv
from ib_async import Index

DIR = DATA / "occasioni_box"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--sym", nargs="+", default=["SPX", "XSP"])
    ap.add_argument("--port", type=int, default=7496)
    ap.add_argument("--mdt", type=int, default=1)
    a = ap.parse_args()
    DIR.mkdir(exist_ok=True)
    f = DIR / f"{a.job}.json"
    st = dict(job=a.job, tipo="box", tickers=a.sym, fase="scan", messaggio="avvio", righe=[], errori=[], pid=os.getpid())

    def scrivi():
        st["aggiornato"] = dt.datetime.now().isoformat(timespec="seconds")
        tmp = f.with_suffix(".tmp"); tmp.write_text(json.dumps(st, default=str)); tmp.replace(f)
    signal.signal(signal.SIGTERM, lambda *_: (st.update(fase="annullato"), scrivi(), os._exit(0)))
    scrivi()
    try:
        ib = connect("127.0.0.1", a.port, 232); ib.reqMarketDataType(a.mdt)
    except Exception as exc:  # noqa: BLE001
        st.update(fase="errore", messaggio=f"connessione a TWS fallita: {exc}"); scrivi(); return 1
    try:
        for sym in a.sym:
            exch, cur, _t, larg, _b, tipo = sv.config_sottostante(sym)
            if sym not in ("SPX", "XSP"):
                st["errori"].append(f"{sym}: i box si cercano solo su SPX e XSP (europee, cash-settled)"); continue
            und = Index(sym, sv.ESCH_INDICE.get(sym, exch)); ib.qualifyContracts(und)
            t = ib.reqMktData(und, "", False, False); ib.sleep(3)
            spot = next((v for v in (clean(t.last), clean(t.close)) if v), None); ib.cancelMktData(und)
            if not spot:
                st["errori"].append(f"{sym}: prezzo non disponibile"); continue
            step = 100 if sym == "SPX" else 10
            oggi = dt.date.today()
            for exp, dte in sv.scadenze(ib, und, 60, 400, sym=sym, weekly=False):
                st["messaggio"] = f"{sym} {exp[6:8]}/{exp[4:6]}/{exp[:4]}: leggo i box"; scrivi()
                cs = {r: dict(sv.catena(ib, exp, r, sym, exch, cur)) for r in ("P", "C")}
                ks = sorted(k for k in cs["P"] if k in cs["C"] and abs(k - spot) <= 0.06 * spot and k % (step / 10) == 0)
                if len(ks) < 2:
                    continue
                coppie = [(k1, k2) for k1 in ks for k2 in ks if k2 - k1 == step][:6]
                contratti = {k: (cs["C"][k], cs["P"][k]) for k in ks}
                px = sv.quota(ib, [c for k in ks for c in contratti[k]], wait=6)
                for k1, k2 in coppie:
                    q = [px.get(contratti[k1][0].conId), px.get(contratti[k2][0].conId),
                         px.get(contratti[k2][1].conId), px.get(contratti[k1][1].conId)]
                    if any(x is None or x.get("bid") is None or x.get("ask") is None or x["ask"] <= 0 for x in q):
                        continue
                    mult = float(contratti[k1][0].multiplier or 100)
                    # long box: +C(K1) -C(K2) +P(K2) -P(K1): si paga all'ask le lunghe e si incassa al bid le corte
                    costo = (q[0]["ask"] - q[1]["bid"] + q[2]["ask"] - q[3]["bid"]) * mult
                    pay = (k2 - k1) * mult
                    if costo <= 0 or costo >= pay:
                        continue
                    rend = (pay / costo) ** (365 / dte) - 1
                    oi = [clean(getattr(x, "oi", None)) for x in q]
                    st["righe"].append(dict(sym=sym, cur=cur, exp=exp, dte=dte, k1=k1, k2=k2, costo=costo, pagamento=pay,
                                            rend=rend, spread=sv.liquidita(q), oi_min=sv.oi_minimo([{"oi": x.get("oi")} for x in q]),
                                            ordine=f"COMPRA 1 C {k1:g} {exp} + VENDI 1 C {k2:g} {exp} + COMPRA 1 P {k2:g} {exp} + VENDI 1 P {k1:g} {exp}"))
                scrivi()
        st.update(fase="finito", messaggio="ricerca completata"); scrivi()
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
