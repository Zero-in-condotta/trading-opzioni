"""Trasformazione dei calendar in spread "senza rischio" (metodo DC Time Machine, SOLA LETTURA).
Fonte: video Theta Profits (metodo DC Time Machine), valutazione in:
knowledge/METODI_OPERATIVI.md (sezione «DC Time Machine»)

Per ogni calendar aperto (stesso strike e tipo, scadenze diverse) e per ogni double calendar
(calendar put + calendar call con le stesse due scadenze) calcola se l'ordine di trasformazione
  vendi le gambe lunghe (scadenza lontana) + compra le ali sulla scadenza corta a distanza w
incassa almeno  debito pagato + w.  Se si', il caso peggiore a scadenza e' >= 0.
  - calendar singolo -> credit spread verticale
  - double calendar  -> iron condor (a scadenza puo' perdere un solo lato, quindi basta + w)

Uso: uv run transform.py            (stampa il controllo)
     da codice: check(ib) -> (righe markdown, avvisi, risultati)
Non invia ordini: propone l'ordine esatto, lo inserisce l'utente da TWS."""
from __future__ import annotations
import copy, datetime as dt
from ib_async import Option
from ib_common import connect, clean

# larghezze delle ali da provare (punti di indice o $ per azione); la prima e' quella di riferimento
WIDTHS = {"XSP": [5, 2, 1, 10], "SPX": [5, 10, 25], "ESTX50": [25, 50], "RUT": [5, 10]}
DEFAULT_WIDTHS = [2.5, 5, 1]
BUFFER = 0.02  # margine minimo sopra la soglia, in % della larghezza, per commissioni e arrotondamenti


def _calendars(ib):
    """Raggruppa le posizioni in calendar (singoli) e double calendar."""
    legs = [p for p in ib.portfolio() if p.contract.secType in ("OPT", "FOP")]
    used, cals = set(), []
    for i, a in enumerate(legs):
        if i in used or a.position >= 0:
            continue
        ca = a.contract
        for j, b in enumerate(legs):
            cb = b.contract
            if j in used or j == i or b.position != -a.position:
                continue
            if (cb.symbol, cb.right, cb.strike) == (ca.symbol, ca.right, ca.strike) and \
               cb.lastTradeDateOrContractMonth > ca.lastTradeDateOrContractMonth:
                used |= {i, j}
                cals.append(dict(short=a, long=b))
                break
    # accoppia un calendar put e uno call con le stesse scadenze e quantita' -> double calendar
    out, taken = [], set()
    for i, c in enumerate(cals):
        if i in taken:
            continue
        for j, d in enumerate(cals):
            if j <= i or j in taken:
                continue
            s1, s2 = c["short"].contract, d["short"].contract
            if s1.symbol == s2.symbol and s1.right != s2.right and \
               s1.lastTradeDateOrContractMonth == s2.lastTradeDateOrContractMonth and \
               c["long"].contract.lastTradeDateOrContractMonth == d["long"].contract.lastTradeDateOrContractMonth and \
               c["short"].position == d["short"].position:
                taken |= {i, j}
                out.append([c, d])
                break
        else:
            taken.add(i)
            out.append([c])
    return out


def _quote(ib, contracts, wait=5):
    ts = [ib.reqMktData(c, "", False, False) for c in contracts]
    ib.sleep(wait)
    q = []
    for t in ts:
        b, a = clean(t.bid), clean(t.ask)
        ok = b is not None and a is not None and a > 0 and b >= 0
        q.append(dict(bid=b if ok else None, ask=a if ok else None, mid=(b + a) / 2 if ok else None))
        ib.cancelMktData(t.contract)
    return q


def _wing(ib, short_contract, w):
    c = copy.copy(short_contract)
    c.conId = 0
    c.strike = short_contract.strike - w if short_contract.right == "P" else short_contract.strike + w
    c = Option(c.symbol, c.lastTradeDateOrContractMonth, c.strike, c.right, c.exchange or "SMART",
               multiplier=c.multiplier, currency=c.currency, tradingClass=c.tradingClass)
    try:
        q = ib.qualifyContracts(c)
        return q[0] if q and q[0].conId else None
    except Exception:
        return None


def evaluate(ib, group):
    """group = lista di 1 (calendar) o 2 (double calendar) calendar. Ritorna un dict con l'esito."""
    first = group[0]["short"].contract
    mult = float(first.multiplier or 100)
    qty = abs(group[0]["short"].position)
    # debito per unita' (avgCost di IBKR include il moltiplicatore)
    debit = sum((c["long"].averageCost - c["short"].averageCost) / mult for c in group)
    for c in group:
        for k in ("short", "long"):
            ib.qualifyContracts(c[k].contract)
    q = _quote(ib, [c[k].contract for c in group for k in ("short", "long")])
    shorts_q, longs_q = q[0::2], q[1::2]
    have = all(x["bid"] is not None for x in q)
    close_now = sum(l["bid"] - s["ask"] for s, l in zip(shorts_q, longs_q)) if have else None
    value_mid = sum(l["mid"] - s["mid"] for s, l in zip(shorts_q, longs_q)) if have else None
    widths = WIDTHS.get(first.symbol, DEFAULT_WIDTHS)
    rows = []
    for w in widths:
        wings = [_wing(ib, c["short"].contract, w) for c in group]
        if any(x is None for x in wings):
            continue
        wq = _quote(ib, wings)
        if not have or any(x["ask"] is None for x in wq):
            rows.append(dict(w=w, nat=None, mid=None))
            continue
        nat = sum(l["bid"] for l in longs_q) - sum(x["ask"] for x in wq)
        mid = sum(l["mid"] for l in longs_q) - sum(x["mid"] for x in wq)
        need = debit + w * (1 + BUFFER)
        rows.append(dict(w=w, wings=wings, wings_q=wq, nat=nat, mid=mid, need=need,
                         ok_nat=nat >= need, ok_mid=mid >= need,
                         floor=(mid - debit - w) * mult * qty, top=(mid - debit) * mult * qty,
                         gap=(need - mid) * mult * qty))
    return dict(group=group, sym=first.symbol, cur=first.currency, mult=mult, qty=qty, debit=debit,
                close_now=None if close_now is None else (close_now - debit) * mult * qty,
                value_mid=value_mid, rows=rows, double=len(group) == 2,
                # quote delle gambe in essere: servono ad actions.py per dare un prezzo
                # alle gambe dell'ordine quando apre il costruttore di strategie
                shorts_q=shorts_q, longs_q=longs_q)


def _name(r):
    g = r["group"]
    s, l = g[0]["short"].contract, g[0]["long"].contract
    if r["double"]:
        ks = "/".join(f"{c['short'].contract.strike:g}{c['short'].contract.right}" for c in g)
        return f"{r['sym']} double calendar {ks} (corta {s.lastTradeDateOrContractMonth}, lunga {l.lastTradeDateOrContractMonth}) x{r['qty']:g}"
    tipo = "put" if s.right == "P" else "call"
    return f"{r['sym']} calendar {tipo} {s.strike:g} (corta {s.lastTradeDateOrContractMonth}, lunga {l.lastTradeDateOrContractMonth}) x{r['qty']:g}"


def _order(r, row):
    g, parts = r["group"], []
    for c, wing in zip(g, row["wings"]):
        L = c["long"].contract
        parts.append(f"VENDI {r['qty']:g} {L.right} {L.strike:g} {L.lastTradeDateOrContractMonth}")
        parts.append(f"COMPRA {r['qty']:g} {wing.right} {wing.strike:g} {wing.lastTradeDateOrContractMonth}")
    return " + ".join(parts) + f", limite: credito >= {row['need']:.2f}"


def check(ib):
    lines, alerts, res = ["## Trasformazione dei calendar (DC Time Machine)"], [], []
    groups = _calendars(ib)
    if not groups:
        return lines + ["- nessun calendar aperto"], alerts, res
    lines.append("Regola: si trasforma quando il credito dell'ordine (vendi le lunghe, compri le ali) e' >= debito pagato + larghezza delle ali (+2% di margine). "
                 "Valori in valuta per l'intera posizione; 'mid' = prezzo medio, 'nat' = eseguibile subito ai prezzi denaro/lettera.\n")
    lines.append("| Posizione | Debito | Chiudere ora | Ali | Credito mid / nat | Soglia | Manca | Caso peggiore / migliore | Esito |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for g in groups:
        r = evaluate(ib, g); res.append(r); nm = _name(r); cur = r["cur"]
        cn = "n/d" if r["close_now"] is None else f"{r['close_now']:+.0f}"
        if not r["rows"]:
            lines.append(f"| {nm} | {r['debit']:.2f} | {cn} | - | - | - | - | - | ali non trovate |")
        for row in r["rows"]:
            if row["mid"] is None:
                lines.append(f"| {nm} | {r['debit']:.2f} | {cn} | {row['w']:g} | n/d | - | - | - | quote assenti (mercato chiuso?) |")
                continue
            esito = "TRASFORMABILE ORA" if row["ok_nat"] else ("trasformabile con limite al mid" if row["ok_mid"] else "non ancora")
            manca = "-" if row["ok_mid"] else f"{row['gap']:.0f} {cur}"
            lines.append(f"| {nm} | {r['debit']:.2f} | {cn} {cur} | {row['w']:g} | {row['mid']:.2f} / {row['nat']:.2f} | {row['need']:.2f} | {manca} | "
                         f"{row['floor']:+.0f} / {row['top']:+.0f} {cur} | {esito} |")
        best = [x for x in r["rows"] if x.get("ok_mid")]
        if best:
            b = max(best, key=lambda x: x["w"])  # ala piu' larga possibile = guadagno massimo piu' alto
            alerts.append(f"{nm}: TRASFORMABILE senza rischio con ali da {b['w']:g} (caso peggiore {b['floor']:+.0f}, migliore {b['top']:+.0f} {cur}; "
                          f"chiudere ora renderebbe {cn} {cur}). Ordine: {_order(r, b)}")
    return lines, alerts, res


if __name__ == "__main__":
    ib = connect("127.0.0.1", 7496, 131)
    try:
        ib.reqMarketDataType(1)
        lines, alerts, _ = check(ib)
    finally:
        ib.disconnect()
    print("\n".join(lines))
    print("\nAvvisi:\n" + ("\n".join("- " + a for a in alerts) if alerts else "- nessuno"))
