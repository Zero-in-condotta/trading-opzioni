"""Storico giornaliero del P/L (SOLA LETTURA): chiuso (realizzato) e floating (aperto), in EUR (valuta base).

Fonte dello storico: Flex Query di IBKR (l'API di TWS non restituisce le operazioni passate).
Configurazione una tantum in ~/.config/opzioni/flex.env (mai in chat, mai nel repo):
    FLEX_TOKEN=...
    FLEX_QUERY_ID=...
La query (Activity Flex Query, periodo "Last 365 Calendar Days", formato XML) deve contenere le sezioni
  - Net Asset Value (NAV) in Base  -> "Equity Summary in Base" per giorno (reportDate, total)
  - Trades (livello Execution)     -> tradeDate, fifoPnlRealized, fxRateToBase
  - Cash Transactions              -> type, amount, fxRateToBase, dateTime (versamenti, interessi, commissioni)

Calcolo per ogni giorno d:
  totale(d)   = NAV(d) - versamenti netti cumulati fino a d      (tutto il guadagno dall'apertura del conto)
  chiuso(d)   = P/L realizzato delle operazioni chiuse + interessi/dividendi/commissioni non di trading, cumulati
  floating(d) = totale(d) - chiuso(d)                            (quello che dipende ancora dalle posizioni aperte)
Il punto di oggi usa i dati live di TWS (reqPnL), perche' la Flex Query si aggiorna solo a fine giornata.
Senza Flex Query lo storico parte dal primo rilevamento live (dashboard oraria) e il chiuso da zero.

Uso: uv run pnl_history.py   (aggiorna data/pnl_daily.csv e stampa le ultime righe)"""
from __future__ import annotations
import csv, datetime as dt, os
from pathlib import Path
from ib_common import DATA, connect

CONF = Path.home() / ".config/opzioni/flex.env"
CSV_FILE = DATA / "pnl_daily.csv"
FIELDS = ["date", "chiuso", "floating", "totale", "fonte"]


def _conf():
    if not CONF.exists():
        return None
    kv = dict(l.strip().split("=", 1) for l in CONF.read_text().splitlines() if "=" in l and not l.startswith("#"))
    return (kv.get("FLEX_TOKEN"), kv.get("FLEX_QUERY_ID")) if kv.get("FLEX_TOKEN") and kv.get("FLEX_QUERY_ID") else None


def _d(x):
    s = "".join(ch for ch in str(x) if ch.isdigit())[:8]
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])) if len(s) == 8 else None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def from_flex():
    """Ritorna (righe, messaggio). Righe: dict date/chiuso/floating/totale in EUR."""
    c = _conf()
    if not c:
        return [], f"Flex Query non configurata ({CONF} assente)"
    from ib_async import FlexReport
    try:
        r = FlexReport(token=c[0], queryId=c[1])
    except Exception as e:
        return [], f"Flex Query non scaricata: {e}"
    nav = {}
    for e in r.extract("EquitySummaryByReportDateInBase", parseNumbers=False):
        d = _d(getattr(e, "reportDate", ""))
        if d:
            nav[d] = _num(getattr(e, "total", 0))
    if not nav:
        return [], "la Flex Query non contiene la sezione NAV giornaliera (Equity Summary in Base)"
    dep, other = {}, {}
    for e in r.extract("CashTransaction", parseNumbers=False):
        if getattr(e, "levelOfDetail", "DETAIL") not in ("DETAIL", "") or getattr(e, "currency", "") == "BASE_SUMMARY":
            continue
        d = _d(getattr(e, "reportDate", "") or getattr(e, "dateTime", "") or getattr(e, "settleDate", ""))
        amt = _num(getattr(e, "amount", 0)) * (_num(getattr(e, "fxRateToBase", 1)) or 1)
        tgt = dep if getattr(e, "type", "") == "Deposits/Withdrawals" else other
        tgt[d] = tgt.get(d, 0) + amt
    real = {}
    for e in r.extract("Trade", parseNumbers=False):
        if getattr(e, "levelOfDetail", "EXECUTION") not in ("EXECUTION", ""):
            continue
        d = _d(getattr(e, "tradeDate", ""))
        real[d] = real.get(d, 0) + _num(getattr(e, "fifoPnlRealized", 0)) * (_num(getattr(e, "fxRateToBase", 1)) or 1)
    rows, dcum, ccum = [], 0.0, 0.0
    days = sorted(nav)
    # versamenti e realizzati precedenti al primo giorno della query entrano nel primo punto
    for d in sorted(set(dep) | set(other) | set(real)):
        if d is not None and d < days[0]:
            dcum += dep.get(d, 0); ccum += other.get(d, 0) + real.get(d, 0)
    for d in days:
        dcum += dep.get(d, 0); ccum += other.get(d, 0) + real.get(d, 0)
        tot = nav[d] - dcum
        rows.append(dict(date=d.isoformat(), chiuso=round(ccum, 2), floating=round(tot - ccum, 2), totale=round(tot, 2), fonte="flex"))
    # La query copre un anno ma il conto e' operativo da molto meno: i giorni iniziali a zero
    # schiacciano il grafico. Si tagliano, tenendo l'ultimo giorno a zero come punto di
    # partenza, cosi la prima variazione si vede come variazione e non come primo punto.
    first = next((i for i, r in enumerate(rows)
                  if r["chiuso"] or r["floating"] or r["totale"]), None)
    if first is not None and first > 0:
        rows = rows[first - 1:]

    msg = f"Flex Query: {len(rows)} giorni dal {rows[0]['date'][8:10]}/{rows[0]['date'][5:7]}/{rows[0]['date'][:4]}"
    if nav[days[0]] > 0 and not any(d <= days[0] for d in dep):
        msg += " (ATTENZIONE: il conto aveva gia' un NAV all'inizio del periodo senza versamenti registrati: allungare il periodo della query)"
    return rows, msg


def live_point(ib):
    """P/L di oggi da TWS: floating = non realizzato, realizzato = solo di oggi. Valori in valuta base."""
    acct = ib.managedAccounts()[0]
    p = ib.reqPnL(acct)
    ib.sleep(3)
    real, unreal = p.realizedPnL, p.unrealizedPnL
    ib.cancelPnL(acct)
    ok = lambda x: x is not None and x == x and abs(x) < 1e12
    return (real if ok(real) else 0.0), (unreal if ok(unreal) else None)


def _load():
    if not CSV_FILE.exists():
        return []
    with open(CSV_FILE) as f:
        return [dict(r, chiuso=float(r["chiuso"]), floating=float(r["floating"]), totale=float(r["totale"])) for r in csv.DictReader(f)]


def update(ib):
    """Aggiorna data/pnl_daily.csv e ritorna (righe, messaggio)."""
    today = dt.date.today().isoformat()
    rows, msg = from_flex()
    old = _load()
    if not rows:  # niente Flex: si continua lo storico dei rilevamenti live
        rows = [r for r in old if r["date"] != today]
    else:         # la Flex copre il passato; si conservano solo i rilevamenti live successivi all'ultima data Flex
        last = rows[-1]["date"]
        rows += [r for r in old if r["date"] > last and r["date"] != today and r.get("fonte") == "live"]
    real_today, floating = live_point(ib)
    if floating is not None:
        base = rows[-1]["chiuso"] if rows and rows[-1]["date"] < today else 0.0
        if rows and rows[-1]["date"] == today:  # la Flex ha gia' oggi: si sostituisce col dato live
            base = rows[-2]["chiuso"] if len(rows) > 1 else 0.0
            rows = rows[:-1]
        ch = base + real_today
        rows.append(dict(date=today, chiuso=round(ch, 2), floating=round(floating, 2), totale=round(ch + floating, 2), fonte="live"))
    DATA.mkdir(exist_ok=True)
    with open(CSV_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    return rows, msg


if __name__ == "__main__":
    ib = connect("127.0.0.1", 7496, 143)
    try:
        rows, msg = update(ib)
    finally:
        ib.disconnect()
    print(msg)
    for r in rows[-10:]:
        print(r)
