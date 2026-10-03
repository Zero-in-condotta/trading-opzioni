"""Costruttore di strategie in opzioni con dati live da TWS (SOLA LETTURA).

Ispirato a OptionStrat, ma con due cose che OptionStrat non ha: i prezzi arrivano dal
TUO conto IBKR, e se sul sottostante scelto hai gia' posizioni aperte la pagina te lo
dice e ti propone di sommarle, cosi vedi in diretta l'effetto di quello che stai
progettando sul libro che hai gia' a mercato.

A differenza degli altri strumenti del progetto questa pagina NON e' un file statico:
serve un piccolo server locale, perche' la catena va chiesta a TWS dopo che hai scelto
il sottostante. Il server resta in ascolto solo su 127.0.0.1.

    uv run --with yfinance chain_builder.py            # poi apri http://127.0.0.1:8770
    uv run --with yfinance chain_builder.py --open     # apre da solo il browser

REGOLA D'ORO DEL PROGETTO, RISPETTATA: la connessione e' readonly=True, non viene mai
chiamato placeOrder. Questa pagina progetta e basta: gli ordini si inseriscono a mano in TWS.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import queue
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ib_async import Index, Option, Stock

from ib_common import DATA, PORTS, clean, connect, setup_logging, spot_of
import deals

try:  # la mappa dei sottostanti e' gia' in strategies.py, non la riscrivo
    from strategies import cfg
except Exception:  # pragma: no cover - fallback se manca yfinance
    _IDX = {"SPX": ("CBOE", "USD", 100), "XSP": ("CBOE", "USD", 100), "VIX": ("CBOE", "USD", 100),
            "RUT": ("RUSSELL", "USD", 100), "NDX": ("NASDAQ", "USD", 100)}
    def cfg(n):
        if n in _IDX:
            e, c, m = _IDX[n]
            return dict(kind="IND", sym=n, exch=e, mult=m, cur=c)
        return dict(kind="STK", sym=n, exch="SMART", mult=100, cur="USD")

LOG = setup_logging("chain_builder")
JOBS: "queue.Queue" = queue.Queue()
STOP = threading.Event()
CACHE: dict = {}


# --------------------------------------------------------------------------------------
# Ponte fra i thread HTTP e il thread che possiede la connessione IBKR.
# ib_async non e' thread-safe: tutte le chiamate devono avvenire nel thread principale.
# I gestori HTTP mettono in coda una funzione e aspettano il risultato.
# --------------------------------------------------------------------------------------
def submit(fn, timeout: float = 60.0):
    box, ev = {}, threading.Event()
    JOBS.put((fn, box, ev))
    if not ev.wait(timeout):
        raise TimeoutError("TWS non ha risposto entro il tempo previsto")
    if "err" in box:
        raise RuntimeError(box["err"])
    return box["val"]


def pump(ib):
    """Ciclo del thread principale: esegue i lavori in coda e tiene vivo il loop di ib_async."""
    while not STOP.is_set():
        try:
            fn, box, ev = JOBS.get_nowait()
        except queue.Empty:
            ib.sleep(0.05)
            continue
        try:
            box["val"] = fn(ib)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Lavoro fallito: %s", exc)
            box["err"] = f"{type(exc).__name__}: {exc}"
        finally:
            ev.set()


# --------------------------------------------------------------------------------------
# Chiamate a IBKR
# --------------------------------------------------------------------------------------
def resolve(ib, sym: str):
    """Contratto del sottostante, con cache: qualifyContracts costa un giro di rete."""
    key = ("und", sym)
    if key in CACHE:
        return CACHE[key]
    c = cfg(sym)
    und = Index(c["sym"], c["exch"], c["cur"]) if c["kind"] == "IND" else Stock(c["sym"], c.get("exch") or "SMART", c["cur"])
    got = ib.qualifyContracts(und)
    if not got or not got[0].conId:
        raise RuntimeError(f"sottostante {sym} non riconosciuto da IBKR")
    CACHE[key] = (got[0], c)
    return CACHE[key]


def secdef(ib, und, c):
    key = ("sd", und.symbol)
    if key in CACHE:
        return CACHE[key]
    chains = ib.reqSecDefOptParams(und.symbol, "", und.secType, und.conId)
    keep = []
    for ch in chains:
        if c["kind"] == "STK" and ch.exchange != c.get("oexch", "SMART"):
            continue
        if c["kind"] == "IND" and not c.get("tclass") and ch.exchange not in ("SMART", "CBOE", "RUSSELL", "NASDAQ"):
            continue
        if c.get("tclass") and ch.tradingClass != c["tclass"]:
            continue
        keep.append(ch)
    if not keep:
        keep = list(chains)
    CACHE[key] = keep
    return keep


# Classi note come AM-settled: il regolamento avviene sull'apertura del giorno dopo (SET),
# non sulla chiusura. Si porta dietro un rischio overnight che le classi settimanali non hanno.
# SPX standard e' AM, SPXW e' PM. XSP e' PM su tutte le scadenze, quindi non compare qui.
AM_CLASSES = {"SPX"}


def expiry_kind(e: str) -> str:
    """monthly = terza di venerdi (dove sta la liquidita istituzionale), quarterly = la
    monthly di marzo/giugno/settembre/dicembre, weekly = altri venerdi, daily = il resto.

    monthly-am: le SPX standard regolano sull'APERTURA del venerdi, quindi l'ultimo giorno
    di negoziazione che IBKR riporta e il GIOVEDI precedente. Senza questo caso finirebbero
    fra le giornaliere e sfuggirebbero proprio al filtro che deve escluderle. Verificato il
    29/09/2026 sulla catena SPX reale: 20261015, 20261119, 20261217... tutti giovedi."""
    d = dt.datetime.strptime(e, "%Y%m%d").date()
    nxt = d + dt.timedelta(days=1)
    if d.weekday() == 3 and 15 <= nxt.day <= 21:
        return "monthly-am"
    if d.weekday() == 4 and 15 <= d.day <= 21:
        return "quarterly" if d.month in (3, 6, 9, 12) else "monthly"
    return "weekly" if d.weekday() == 4 else "daily"


def api_underlying(ib, sym):
    und, c = resolve(ib, sym)
    spot = spot_of(ib, und)
    chains = secdef(ib, und, c)
    today = dt.date.today()
    by_exp: dict[str, set] = {}
    strikes: dict[str, set] = {}
    for ch in chains:
        for e in ch.expirations:
            if (dt.datetime.strptime(e, "%Y%m%d").date() - today).days < 0:
                continue
            by_exp.setdefault(e, set()).add(ch.tradingClass)
            strikes.setdefault(ch.tradingClass, set()).update(ch.strikes)
    exps = [{"date": e, "dte": (dt.datetime.strptime(e, "%Y%m%d").date() - today).days,
             "classes": sorted(by_exp[e]), "kind": expiry_kind(e)} for e in sorted(by_exp)]
    return {"symbol": und.symbol, "secType": und.secType, "exchange": c.get("oexch") or c["exch"],
            "currency": c["cur"], "mult": c["mult"], "spot": spot, "amClasses": sorted(AM_CLASSES),
            "expiries": exps, "strikes": {k: sorted(v) for k, v in strikes.items()}}


# Linee di dati simultanee concesse da IBKR: di solito 100 sui conti piccoli. Si sta sotto
# con margine, e si cancella la sottoscrizione a fine blocco prima di chiedere il successivo.
BLOCK = 90


def _quote_rows(ib, contracts, wait):
    """Una richiesta per blocco, una sola attesa, poi si legge: stesso schema di collect_chain."""
    out = []
    for i in range(0, len(contracts), BLOCK):
        blk = contracts[i:i + BLOCK]
        tk = [ib.reqMktData(c, "100,101", False, False) for c in blk]
        # Attesa adattiva invece di un sonno fisso: quasi sempre le quotazioni arrivano in
        # un paio di secondi, e su una catena intera i blocchi sono parecchi. `wait` resta
        # il tetto massimo, non il tempo speso.
        # Si aspetta il PREZZO, non le greche: il modello IBKR restituisce la IV prima che
        # il book sia arrivato, e uscire su quella lasciava un quinto della catena senza bid
        # (misurato: 192 contratti quotati su 240). `wait` resta il tetto massimo.
        waited = 0.0
        while waited < wait:
            ib.sleep(0.25)
            waited += 0.25
            if waited < 2.0:
                continue
            ready = sum(1 for t in tk if clean(t.bid) is not None or clean(t.ask) is not None)
            if ready >= len(tk):
                break
        for t in tk:
            g = t.modelGreeks
            c = t.contract
            oi = clean(t.callOpenInterest if c.right == "C" else t.putOpenInterest)
            out.append({
                "conId": c.conId, "expiry": c.lastTradeDateOrContractMonth, "strike": c.strike,
                "right": c.right, "tradingClass": c.tradingClass,
                "bid": clean(t.bid), "ask": clean(t.ask), "last": clean(t.last),
                "volume": clean(t.volume), "oi": oi,
                "iv": clean(g.impliedVol) if g else None,
                "delta": clean(g.delta) if g else None, "gamma": clean(g.gamma) if g else None,
                "vega": clean(g.vega) if g else None, "theta": clean(g.theta) if g else None,
                "und": clean(g.undPrice) if g else None,
            })
        for c in blk:
            ib.cancelMktData(c)
    return out


def api_chain(ib, sym, expiry, tclass, width, maxn, wait):
    """TUTTI gli strike entro +-width dal prezzo, senza diradare il passo.
    `maxn` e' solo una rete di sicurezza contro richieste patologiche: se scatta si tagliano
    gli estremi, mai il centro, e la risposta lo dichiara con `capped`."""
    und, c = resolve(ib, sym)
    spot = spot_of(ib, und)
    if not spot:
        raise RuntimeError("prezzo del sottostante non disponibile (mercato chiuso o dati non abilitati)")
    chains = secdef(ib, und, c)
    allk = sorted({k for ch in chains if ch.tradingClass == tclass and expiry in ch.expirations for k in ch.strikes})
    if not allk:
        raise RuntimeError(f"nessuno strike per {sym} {expiry} classe {tclass}")
    lo, hi = spot * (1 - width), spot * (1 + width)
    ks = [k for k in allk if lo <= k <= hi] or allk
    capped = len(ks) > maxn
    if capped:
        ks.sort(key=lambda k: abs(k - spot))
        ks = ks[:maxn]
    ks = sorted(ks)
    cons = [Option(und.symbol, expiry, k, r, c.get("oexch") or "SMART",
                   tradingClass=tclass, currency=c["cur"]) for k in ks for r in ("P", "C")]
    # qualifyContracts non consuma linee di dati: si puo' chiedere a blocchi larghi, e con
    # una catena intera sono i viaggi di rete in fila a pesare, non il singolo blocco.
    valid = []
    for i in range(0, len(cons), 200):
        valid += [x for x in ib.qualifyContracts(*cons[i:i + 200]) if x and x.conId]
    rows = _quote_rows(ib, valid, wait)
    real = sorted({c.strike for c in valid})
    return {"symbol": und.symbol, "spot": spot, "expiry": expiry, "tradingClass": tclass,
            "mult": c["mult"], "currency": c["cur"], "rows": rows,
            # gli strike VERI, non quelli chiesti: secdef li elenca tutti insieme, ma per una
            # singola scadenza molti non esistono (tipico sulle azioni)
            "width": width, "count": len(real), "contracts": len(valid), "capped": capped,
            "asked": len(ks), "lo": min(real) if real else None, "hi": max(real) if real else None}


def api_atmiv(ib, sym, want_exp, wait):
    """Volatilita implicita al denaro per una scadenza, con pochi contratti invece di tutta
    la catena. Serve a IndexMachine, che altrimenti la fa scrivere a mano.

    La data chiesta non deve per forza essere una scadenza vera: si prende la piu vicina
    disponibile e si dichiara quale, perche' la finestra in IndexMachine e' libera."""
    und, c = resolve(ib, sym)
    spot = spot_of(ib, und)
    if not spot:
        raise RuntimeError("prezzo del sottostante non disponibile (mercato chiuso o dati non abilitati)")
    chains = secdef(ib, und, c)
    today = dt.date.today()
    cand: dict[str, set] = {}
    for ch in chains:
        for e in ch.expirations:
            if (dt.datetime.strptime(e, "%Y%m%d").date() - today).days >= 0:
                cand.setdefault(e, set()).add(ch.tradingClass)
    if not cand:
        raise RuntimeError(f"nessuna scadenza disponibile per {sym}")
    exp = min(cand, key=lambda e: abs(int(e) - int(want_exp)))
    # fra due classi sulla stessa data si preferisce quella non AM: e' quella su cui si opera
    classes = sorted(cand[exp], key=lambda t: (t in AM_CLASSES, t))
    tclass = classes[0]
    allk = sorted({k for ch in chains if ch.tradingClass == tclass and exp in ch.expirations for k in ch.strikes})
    if not allk:
        raise RuntimeError(f"nessuno strike per {sym} {exp}")
    near = sorted(allk, key=lambda k: abs(k - spot))[:5]
    cons = [Option(und.symbol, exp, k, r, c.get("oexch") or "SMART",
                   tradingClass=tclass, currency=c["cur"]) for k in near for r in ("P", "C")]
    valid = [x for x in ib.qualifyContracts(*cons) if x and x.conId]
    rows = [r for r in _quote_rows(ib, valid, wait) if r["iv"] is not None]
    if not rows:
        raise RuntimeError(f"nessuna volatilita implicita disponibile su {sym} {exp}")
    k_atm = min({r["strike"] for r in rows}, key=lambda k: abs(k - spot))
    at = [r["iv"] for r in rows if r["strike"] == k_atm]
    # anche gli strike davvero quotabili su quella scadenza, cosi IndexMachine puo' far
    # scegliere fra quelli invece di accettare un numero qualsiasi
    lo, hi = spot * 0.70, spot * 1.15
    return {"symbol": und.symbol, "spot": spot, "expiry": exp, "tradingClass": tclass,
            "strikes": [k for k in allk if lo <= k <= hi],
            "strike": k_atm, "iv": sum(at) / len(at), "kind": expiry_kind(exp),
            "dte": (dt.datetime.strptime(exp, "%Y%m%d").date() - today).days,
            "exact": exp == str(want_exp)}


def api_portfolio(ib, sym, wait):
    """Gambe in opzione gia' aperte sul sottostante, con greche aggiornate."""
    items = [it for it in ib.portfolio()
             if it.contract.secType == "OPT" and (not sym or it.contract.symbol == sym)]
    quotes = {}
    if items and sym:
        cons = [it.contract for it in items]
        for c in cons:
            ib.qualifyContracts(c)
        quotes = {r["conId"]: r for r in _quote_rows(ib, cons, wait)}
    legs = []
    for it in items:
        c = it.contract
        q = quotes.get(c.conId, {})
        legs.append({
            "symbol": c.symbol, "expiry": c.lastTradeDateOrContractMonth, "strike": c.strike,
            "right": c.right, "position": it.position, "avgCost": it.averageCost,
            "mult": float(c.multiplier or 100), "currency": c.currency,
            "marketPrice": it.marketPrice, "unrealizedPNL": it.unrealizedPNL,
            "iv": q.get("iv"), "delta": q.get("delta"), "vega": q.get("vega"), "theta": q.get("theta"),
        })
    return {"legs": legs}


def api_group(ib, gid, wait):
    """Un gruppo di data/portfolio_map.json (gli stessi affari della dashboard) con le
    posizioni vere che gli corrispondono, cosi il costruttore puo' disegnarne il payoff."""
    f = DATA / "portfolio_map.json"
    if not f.exists():
        raise RuntimeError("data/portfolio_map.json non trovato")
    grp = next((g for g in json.loads(f.read_text()).get("groups", []) if g["id"] == gid), None)
    if not grp:
        raise RuntimeError(f"gruppo '{gid}' non presente in portfolio_map.json")
    keys = {(l["symbol"], l.get("expiry"), l.get("strike"), l.get("right")) for l in grp["legs"]}
    items = []
    for it in ib.portfolio():
        c = it.contract
        k = (c.symbol, getattr(c, "lastTradeDateOrContractMonth", "") or None,
             getattr(c, "strike", 0.0) or None, getattr(c, "right", "") or None)
        if k in keys:
            items.append(it)
    if not items:
        raise RuntimeError(f"nessuna posizione aperta corrisponde al gruppo '{grp['nome']}'")
    # Un payoff si disegna su UN sottostante: se il gruppo ne mescola piu d'uno (le
    # protezioni di coda lo fanno) si tiene quello con piu gambe e si dichiara il taglio.
    counts: dict[str, int] = {}
    for it in items:
        counts[it.contract.symbol] = counts.get(it.contract.symbol, 0) + 1
    sym = max(counts, key=lambda s: counts[s])
    kept = [it for it in items if it.contract.symbol == sym]
    cons = [it.contract for it in kept]
    for c in cons:
        ib.qualifyContracts(c)
    quotes = {r["conId"]: r for r in _quote_rows(ib, cons, wait)}
    und, _ = resolve(ib, sym)
    spot = spot_of(ib, und)
    today = dt.date.today()
    legs = []
    for it in kept:
        c = it.contract
        q = quotes.get(c.conId, {})
        iv = q.get("iv")
        if iv is None and spot and it.marketPrice:
            # A mercati chiusi TWS non manda le greche: la IV si ricava invertendo
            # Black-Scholes sul prezzo, come fa gia' dashboard.py. Senza, la curva "ora"
            # sarebbe disegnata con una volatilita inventata.
            T = (dt.datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date() - today).days / 365
            iv = deals.implied_vol(it.marketPrice, spot, c.strike, T, c.right)
        legs.append({"symbol": c.symbol, "expiry": c.lastTradeDateOrContractMonth, "strike": c.strike,
                     "right": c.right, "position": it.position, "avgCost": it.averageCost,
                     "mult": float(c.multiplier or 100), "currency": c.currency,
                     "marketPrice": it.marketPrice, "unrealizedPNL": it.unrealizedPNL,
                     "iv": iv, "ivFallback": q.get("iv") is None and iv is not None,
                     "delta": q.get("delta")})
    return {"id": gid, "nome": grp["nome"], "note": grp.get("note", ""), "symbol": sym, "spot": spot,
            "symbols": sorted(counts), "dropped": len(items) - len(kept), "legs": legs}


def api_symbols(ib):
    """Sottostanti su cui ci sono gia' opzioni aperte, per proporli in cima alla lista."""
    syms = sorted({it.contract.symbol for it in ib.portfolio() if it.contract.secType == "OPT"})
    return {"portfolio": syms}


# --------------------------------------------------------------------------------------
# Server HTTP locale
# --------------------------------------------------------------------------------------
# ---- ricerca di occasioni asincrona (bottone "Cerca occasioni" nella dashboard) ----
# La ricerca NON e' legata al caricamento della pagina: gira in un processo a parte
# (scan_ticker.py) che scrive data/occasioni/<job>.json a ogni scadenza analizzata. La pagina
# interroga /api/occasioni e mostra i risultati man mano; ricaricarla non interrompe nulla.
JOB = {"proc": None, "id": None}
JOB_LOCK = threading.Lock()


def _job_attivo():
    p = JOB["proc"]
    return p is not None and p.poll() is None


PRESET = {
    "@ETF": ["SPY", "QQQ", "IWM", "GLD", "TLT", "EEM", "FEZ", "XLF", "XLE"],
    "@INDICI": ["ESTX50", "XSP", "SPX", "RUT", "MRUT", "DJX"],
}


def _preset(nome):
    if nome == "@TOP":
        import strategies
        return list(strategies.US_LIST)
    return PRESET.get(nome)


def _lancia(script, syms, extra):
    """Avvia uno script di ricerca in un processo a parte; un solo processo alla volta."""
    import shutil
    with JOB_LOCK:
        if _job_attivo():
            return {"avviata": False, "errore": "una ricerca e' gia' in corso: attendi o premi Annulla", "job": JOB["id"]}
        jid = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        uv = shutil.which("uv") or str(__import__("pathlib").Path.home() / ".local/bin/uv")
        log = open(DATA.parent / "logs" / f"{script.split('.')[0]}-{jid}.log", "w")
        JOB["proc"] = subprocess.Popen(
            [uv, "run", "--with", "yfinance", "python", "-u", script, "--job", jid, "--sym", *syms, *extra],
            cwd=str(DATA.parent), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        JOB["id"] = jid
    return {"avviata": True, "job": jid, "tickers": syms}


def _tickers(sym_raw):
    import re
    out = []
    for t in re.split(r"[\s,;]+", (sym_raw or "").upper()):
        if not t:
            continue
        if t.startswith("@"):
            p = _preset(t)
            if not p:
                return None, f"elenco {t} sconosciuto (disponibili: @ETF, @INDICI, @TOP)"
            out += p
        elif re.fullmatch(r"[A-Z0-9.^-]{1,12}", t):
            out.append(t)
        else:
            return None, f"ticker non valido: {t}"
    out = list(dict.fromkeys(out))
    if not out or len(out) > 40:
        return None, "scrivi da 1 a 40 ticker (oppure @ETF, @INDICI, @TOP)"
    return out, None


def occ_cerca(sym_raw, dmin=7, dmax=200, weekly=True):
    syms, err = _tickers(sym_raw)
    if err:
        return {"avviata": False, "errore": err}
    dmin, dmax = max(1, min(int(dmin), 400)), max(2, min(int(dmax), 400))
    extra = ["--dte", str(min(dmin, dmax)), str(max(dmin, dmax))] + ([] if weekly else ["--no-weekly"])
    return _lancia("scan_ticker.py", syms, extra)


def box_cerca(sym_raw):
    syms = [t for t in (sym_raw or "SPX XSP").upper().replace(",", " ").split() if t in ("SPX", "XSP")] or ["SPX", "XSP"]
    return _lancia("scan_box.py", syms, [])


def ff_cerca(sym_raw):
    syms, err = _tickers(sym_raw)
    if err:
        return {"avviata": False, "errore": err}
    return _lancia("scan_ff.py", syms, [])


def occ_annulla():
    import os
    import signal as _sg
    with JOB_LOCK:
        if not _job_attivo():
            return {"annullata": False}
        try:
            os.killpg(os.getpgid(JOB["proc"].pid), _sg.SIGTERM)
        except Exception:  # noqa: BLE001
            JOB["proc"].terminate()
    return {"annullata": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "chain_builder"
    ARGS = None  # riempito da main()

    def log_message(self, fmt, *a):  # meno rumore: il log vero e' in logs/
        LOG.debug("%s - %s", self.address_string(), fmt % a)

    def _send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_cors", False):
            # solo gli endpoint della scansione: la dashboard puo' essere aperta da file://
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str).encode(), "application/json; charset=utf-8")

    def do_OPTIONS(self):  # noqa: N802
        self._cors = True
        self._send(204, b"", "text/plain")

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        self._cors = True
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/occasioni-cerca":
            return self._json(occ_cerca(q.get("sym", ""), q.get("dmin", 7), q.get("dmax", 200), q.get("weekly", "1") != "0"))
        if u.path == "/api/box-cerca":
            return self._json(box_cerca(q.get("sym", "")))
        if u.path == "/api/ff-cerca":
            return self._json(ff_cerca(q.get("sym", "")))
        if u.path == "/api/occasioni-annulla":
            return self._json(occ_annulla())
        return self._json({"error": "endpoint sconosciuto"}, 404)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        a = self.ARGS
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, TEMPLATE.encode(), "text/html; charset=utf-8")
            if u.path == "/index_machine.html":
                # Serve la pagina di IndexMachine dalla stessa origine, cosi puo' chiedere la
                # volatilita implicita a /api/atmiv senza bisogno di aprire il server a CORS.
                f = DATA / "index_machine.html"
                if not f.exists():
                    return self._send(404, b"IndexMachine non ancora generata: lancia "
                                           b"'uv run --with yfinance index_machine.py'", "text/plain; charset=utf-8")
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/dashboard.html":
                # stessa origine del server: il bottone "Ricalcola occasioni" funziona senza CORS
                f = DATA / "dashboard.html"
                if not f.exists():
                    return self._send(404, b"dashboard non ancora generata", "text/plain; charset=utf-8")
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/occasioni":
                self._cors = True
                import occ_render
                return self._json(occ_render.risposta(q.get("job", "last"), q.get("filtri", "1") != "0", q.get("ordine", "er")))
            if u.path == "/api/box":
                self._cors = True
                import occ_render
                return self._json(occ_render.risposta_box())
            if u.path == "/api/ff":
                self._cors = True
                import occ_render
                return self._json(occ_render.risposta_ff(q.get("job", "last")))
            if u.path == "/api/atmiv":
                sym = (q.get("sym") or "").upper().strip()
                exp = (q.get("exp") or "").replace("-", "")
                return self._json(submit(lambda ib: api_atmiv(ib, sym, exp, a.wait), timeout=120))
            if u.path == "/api/symbols":
                return self._json(submit(api_symbols))
            if u.path == "/api/underlying":
                sym = (q.get("sym") or "").upper().strip()
                return self._json(submit(lambda ib: api_underlying(ib, sym)))
            if u.path == "/api/chain":
                sym = (q.get("sym") or "").upper().strip()
                exp, tc = q.get("exp", ""), q.get("tc", "")
                w = float(q.get("w", a.width))
                n = int(q.get("n", a.max_strikes))
                return self._json(submit(lambda ib: api_chain(ib, sym, exp, tc, w, n, a.wait), timeout=600))
            if u.path == "/api/group":
                return self._json(submit(lambda ib: api_group(ib, q.get("id", ""), a.wait), timeout=120))
            if u.path == "/api/portfolio":
                sym = (q.get("sym") or "").upper().strip()
                return self._json(submit(lambda ib: api_portfolio(ib, sym, a.wait), timeout=120))
            return self._json({"error": "endpoint sconosciuto"}, 404)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 502)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=PORTS["tws-live"], help="porta TWS (7496 reale, 7497 paper)")
    p.add_argument("--client-id", type=int, default=150)
    p.add_argument("--market-data-type", type=int, default=1, help="1 live, 3 ritardati")
    p.add_argument("--http-port", type=int, default=8770)
    p.add_argument("--width", type=float, default=0.10, help="ampiezza della catena, 0.10 = +-10%% dal prezzo")
    p.add_argument("--max-strikes", type=int, default=400, help="rete di sicurezza: tetto di strike per richiesta")
    p.add_argument("--wait", type=float, default=5.0, help="secondi di attesa per blocco di quotazioni")
    p.add_argument("--open", action="store_true", help="apri il browser all'avvio")
    args = p.parse_args()
    Handler.ARGS = args

    try:
        ib = connect(args.host, args.port, args.client_id)
    except Exception as exc:  # noqa: BLE001
        LOG.error("Connessione a TWS fallita su %s:%s (%s). Apri TWS con l'API attiva.", args.host, args.port, exc)
        return 1
    ib.reqMarketDataType(args.market_data_type)

    srv = ThreadingHTTPServer(("127.0.0.1", args.http_port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{args.http_port}/"
    LOG.info("Pronto su %s  (sola lettura, nessun ordine). Ctrl-C per chiudere.", url)
    if args.open:
        webbrowser.open(url)

    try:
        pump(ib)
    except KeyboardInterrupt:
        LOG.info("Chiusura richiesta.")
    finally:
        STOP.set()
        srv.shutdown()
        ib.disconnect()
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Costruttore strategie</title>
<style>
  :root { --bg:#0f1420; --card:#171d2b; --text:#e5e9f0; --muted:#8a94a6; --pos:#3ecf8e; --neg:#f0576b; --border:#262e42;
          --s1:#3987e5; --grid:#262e42; --warnbg:#2b2210; --chip:#1e2637; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f4f5f7; --card:#ffffff; --text:#1a1f2b; --muted:#6b7280; --pos:#0a8f5c; --neg:#c0263f; --border:#e3e6ec;
            --s1:#2a78d6; --grid:#e3e6ec; --warnbg:#fff6e0; --chip:#eef1f6; }
  }
  * { box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:20px 16px; }
  h1 { font-size:1.35rem; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:0.85rem; margin-bottom:16px; }
  .sub a { color:var(--s1); }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px 16px; margin-bottom:14px; position:relative; }
  .card h2 { font-size:1rem; margin:0 0 8px; display:flex; align-items:center; gap:7px; }
  .note { color:var(--muted); font-size:0.78rem; margin:0 0 10px; line-height:1.5; }
  .row { display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; }
  .field { display:flex; flex-direction:column; gap:4px; }
  .field label { color:var(--muted); font-size:0.7rem; text-transform:uppercase; letter-spacing:.03em; }
  input, select, button { background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:7px;
                          padding:7px 9px; font:inherit; font-size:0.86rem; }
  button { cursor:pointer; }
  button.primary { background:var(--s1); border-color:var(--s1); color:#fff; font-weight:600; }
  button:disabled { opacity:.5; cursor:default; }
  .tiles { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:14px; }
  .tile { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:10px 14px; min-width:135px; flex:1; }
  .tile .label { color:var(--muted); font-size:0.68rem; text-transform:uppercase; letter-spacing:.03em; }
  .tile .value { font-size:1.12rem; font-weight:600; margin-top:2px; }
  .tile .sub2 { color:var(--muted); font-size:0.72rem; margin-top:1px; }
  .layout { display:flex; gap:14px; align-items:flex-start; flex-wrap:wrap; }
  .col-chain { flex:1 1 430px; min-width:380px; }
  .col-build { flex:1.25 1 520px; min-width:380px; }
  .chips { display:flex; flex-wrap:wrap; gap:5px; margin-bottom:10px; max-height:112px; overflow:auto; }
  .chip { background:var(--chip); border:1px solid var(--border); border-radius:18px; padding:3px 10px;
          font-size:0.76rem; cursor:pointer; white-space:nowrap; }
  .chip.on { background:var(--s1); border-color:var(--s1); color:#fff; font-weight:600; }
  .chip .dte { opacity:.7; font-size:0.68rem; margin-left:4px; }
  /* tipo di scadenza: la liquidita sta sulle mensili, le giornaliere sono rumore */
  .chip.k-monthly, .chip.k-quarterly { border-color:var(--s1); font-weight:600; }
  .chip.k-monthly-am { border-color:var(--neg); font-weight:600; }
  .wiz { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px 16px; }
  .wiz h4 { margin:0 0 5px; font-size:0.7rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:600; }
  .wiz button { all:unset; cursor:pointer; display:block; padding:3px 6px; margin:0 -6px; border-radius:5px;
                font-size:0.82rem; line-height:1.35; }
  .wiz button:hover { background:var(--chip); }
  .wiz button.on { background:var(--s1); color:#fff; font-weight:600; }
  .wiz .bull { color:var(--pos); } .wiz .bear { color:var(--neg); } .wiz .neu { color:var(--text); }
  .wiz button.on .bull, .wiz button.on .bear, .wiz button.on .neu { color:#fff; }
  .badge { display:inline-block; font-size:0.68rem; border:1px solid var(--border); border-radius:20px;
           padding:1px 8px; margin-right:5px; color:var(--muted); }
  .badge.ok { color:var(--pos); border-color:var(--pos); }
  .badge.ko { color:var(--neg); border-color:var(--neg); }
  .tabs { display:flex; gap:4px; margin-left:auto; }
  .tabs button { all:unset; cursor:pointer; font-size:0.78rem; padding:3px 11px; border-radius:6px;
                 border:1px solid var(--border); color:var(--muted); }
  .tabs button.on { background:var(--s1); border-color:var(--s1); color:#fff; font-weight:600; }
  .heat { width:100%; border-collapse:separate; border-spacing:1px; font-size:0.7rem; }
  .heat th { padding:3px 4px; color:var(--muted); font-weight:500; text-align:center; white-space:nowrap; border:0; }
  .heat td { padding:3px 5px; text-align:right; border-radius:3px; white-space:nowrap; color:var(--text); }
  .heat td.px { background:var(--chip); color:var(--text); font-weight:600; text-align:right; }
  .heat td.px small { color:var(--muted); font-weight:400; margin-left:4px; }
  .heat tr.spot td.px { outline:1px solid var(--s1); }
  .chip.k-quarterly { box-shadow:inset 0 0 0 1px var(--s1); }
  .chip.k-weekly { }
  .chip.k-daily { opacity:.62; }
  .chip .am { font-size:0.62rem; font-weight:700; color:var(--neg); margin-left:5px;
              border:1px solid var(--neg); border-radius:4px; padding:0 3px; vertical-align:1px; }
  .chip.on .am { color:#fff; border-color:#fff; }
  table { width:100%; border-collapse:collapse; font-size:0.79rem; }
  th { color:var(--muted); font-weight:500; padding:4px 6px; border-bottom:1px solid var(--border); text-align:right; white-space:nowrap; }
  td { padding:3px 6px; border-bottom:1px solid var(--border); text-align:right; white-space:nowrap; }
  .chain-wrap { max-height:min(70vh, 760px); overflow:auto; }
  .chain thead th { position:sticky; background:var(--card); z-index:2; }
  /* due righe di intestazione: la seconda si ferma sotto la prima, senza coprirla */
  .chain thead tr:first-child th { top:0; }
  .chain thead tr:nth-child(2) th { top:24px; }
  .k { text-align:center; font-weight:600; background:var(--chip); }
  .k.atm { color:var(--s1); }
  .q { cursor:pointer; border-radius:4px; }
  .q:hover { background:var(--s1); color:#fff; }
  .side { color:var(--muted); font-size:0.7rem; text-transform:uppercase; letter-spacing:.04em; }
  .legs td { text-align:right; }
  .legs td:first-child, .legs th:first-child { text-align:left; }
  .qty { width:58px; text-align:center; padding:3px; }
  .px { width:76px; text-align:right; padding:3px; }
  .x { all:unset; cursor:pointer; color:var(--muted); padding:0 4px; }
  .x:hover { color:var(--neg); }
  .pos { color:var(--pos); } .neg { color:var(--neg); }
  .banner { background:var(--warnbg); border:1px solid var(--border); border-radius:10px; padding:12px 14px;
            margin-bottom:14px; display:none; font-size:0.86rem; }
  .banner.show { display:block; }
  .banner .row { margin-top:9px; }
  .pill { display:inline-flex; align-items:center; gap:6px; background:var(--chip); border:1px solid var(--border);
          border-radius:18px; padding:4px 11px; font-size:0.78rem; cursor:pointer; }
  .pill.on { background:var(--s1); border-color:var(--s1); color:#fff; }
  .sliders { display:flex; flex-wrap:wrap; gap:16px; margin-top:10px; }
  .slider { flex:1 1 180px; }
  .slider label { display:block; color:var(--muted); font-size:0.72rem; margin-bottom:3px; }
  .slider input[type=range] { width:100%; padding:0; }
  svg { display:block; width:100%; max-width:720px; margin-inline:auto; height:auto; overflow:visible; }
  svg text { fill:var(--muted); font-size:10px; font-family:inherit; }
  .legend { display:flex; gap:14px; flex-wrap:wrap; font-size:0.75rem; color:var(--muted); margin-bottom:4px; }
  .legend svg { display:inline; width:26px; height:8px; vertical-align:middle; margin-right:4px; }
  .tip { position:fixed; pointer-events:none; background:var(--card); border:1px solid var(--border); border-radius:8px;
         padding:7px 10px; font-size:0.78rem; line-height:1.45; box-shadow:0 6px 20px rgba(0,0,0,.28); opacity:0; z-index:50; white-space:nowrap; }
  .help { all:unset; cursor:pointer; width:17px; height:17px; border-radius:50%; border:1px solid var(--muted);
          color:var(--muted); font-size:11px; font-weight:600; display:inline-flex; align-items:center; justify-content:center; flex:0 0 auto; }
  .help:hover, .help[aria-expanded=true] { color:var(--card); background:var(--s1); border-color:var(--s1); }
  .pop { position:absolute; display:none; width:min(370px, calc(100% - 32px)); background:var(--card);
         border:1px solid var(--border); border-radius:10px; padding:14px 16px; font-size:0.81rem; line-height:1.55;
         z-index:60; box-shadow:0 10px 34px rgba(0,0,0,.34); }
  .pop.open { display:block; }
  .pop h3 { margin:0 0 8px; font-size:0.9rem; }
  .pop dt { color:var(--s1); font-weight:600; font-size:0.72rem; text-transform:uppercase; letter-spacing:.03em; margin-top:9px; }
  .pop dt:first-of-type { margin-top:0; }
  .pop dd { margin:2px 0 0; }
  .pop dl { margin:0; }
  .pop .care { color:var(--neg); }
  .pop .close { all:unset; float:right; cursor:pointer; color:var(--muted); font-size:1rem; line-height:1; padding:0 2px; }
  .msg { color:var(--muted); font-size:0.82rem; padding:10px 0; }
  .msg.err { color:var(--neg); }
  footer { color:var(--muted); font-size:0.75rem; margin-top:20px; line-height:1.6; }
</style></head>
<body>
<h1>Costruttore di strategie</h1>
<div class="sub">Catena e prezzi in diretta dal tuo TWS &middot; <a href="/">ricarica</a>
  &middot; <span id="conn">collegato a IBKR, sola lettura</span></div>

<div class="card">
  <div class="row">
    <div class="field"><label for="sym">Sottostante</label>
      <input id="sym" list="syms" placeholder="SPX" style="width:140px" autocomplete="off"><datalist id="syms"></datalist></div>
    <button class="primary" id="load">Carica</button>
    <div class="field"><label>Prezzo</label><div id="spot" style="font-size:1.15rem;font-weight:600;padding:5px 0">&mdash;</div></div>
    <div class="field"><label for="tc">Classe</label><select id="tc" style="width:110px"></select></div>
    <button id="refresh" disabled>Aggiorna quotazioni</button>
    <span id="status" class="msg"></span>
  </div>
</div>

<div class="banner" id="banner"></div>

<div class="tiles" id="tiles"></div>

<div class="layout">
  <div class="col-chain">
    <div class="card">
      <h2>Catena <button class="help" data-help="chain" aria-expanded="false" aria-label="Come si usa">?</button></h2>
      <div class="note">Scegli la scadenza, poi <b>clicca un prezzo</b> per aggiungere la gamba: sul <b>bid</b> vendi, sull'<b>ask</b> compri.</div>
      <div class="row" style="margin-bottom:8px; align-items:center">
        <div class="field"><label for="kinds">Scadenze</label>
          <select id="kinds" style="width:220px">
            <option value="liq" selected>solo le piu liquide (mensili)</option>
            <option value="wk">mensili + settimanali</option>
            <option value="all">tutte, giornaliere comprese</option>
          </select></div>
        <label style="font-size:0.82rem; display:flex; align-items:center; gap:5px; padding-bottom:7px">
          <input type="checkbox" id="showam"> mostra le mensili AM</label>
        <span id="expinfo" class="msg"></span>
      </div>
      <div class="chips" id="exps"></div>
      <div class="row" style="margin:2px 0 10px; align-items:center">
        <div class="slider" style="flex:1 1 230px">
          <label>Ampiezza catena: <b id="l-w">&plusmn;10%</b></label>
          <input type="range" id="w" min="1" max="40" step="1" value="10">
        </div>
        <span id="chaininfo" class="msg"></span>
      </div>
      <div class="chain-wrap"><table class="chain" id="chain"></table></div>
    </div>
  </div>
  <div class="col-build">
    <div class="card">
      <h2>Strategie <button class="help" data-help="wiz" aria-expanded="false" aria-label="Come funziona">?</button></h2>
      <div class="note">Scegli una struttura: le gambe vengono messe sulla catena vera, con gli strike scelti
        <b>per delta</b> e i prezzi presi da bid e ask. Poi la aggiusti come vuoi.</div>
      <div class="wiz" id="wiz"></div>
      <div id="wizinfo" style="margin-top:12px"></div>
    </div>

    <div class="card">
      <h2>Gambe in progetto <button class="help" data-help="legs" aria-expanded="false" aria-label="Come si usa">?</button></h2>
      <div id="legs"></div>
    </div>
    <div class="card">
      <h2>Profitto e perdita <button class="help" data-help="payoff" aria-expanded="false" aria-label="Come si legge">?</button>
        <span class="tabs"><button id="t-graf" class="on">Grafico</button><button id="t-tab">Tabella</button></span></h2>
      <div class="legend">
        <span><svg viewBox="0 0 26 8"><line x1="0" y1="4" x2="26" y2="4" stroke="currentColor" stroke-width="2"/></svg>ora</span>
        <span><svg viewBox="0 0 26 8"><line x1="0" y1="4" x2="26" y2="4" stroke="currentColor" stroke-width="2" stroke-dasharray="6 4"/></svg><span id="lbl-mid">data intermedia</span></span>
        <span><svg viewBox="0 0 26 8"><line x1="0" y1="4" x2="26" y2="4" stroke="currentColor" stroke-width="2" stroke-dasharray="2 3"/></svg><span id="lbl-exp">a scadenza</span></span>
        <span id="be-lbl" style="margin-left:auto"></span>
      </div>
      <div id="chart"></div>
      <div id="heat" style="display:none; overflow:auto"></div>
      <div class="sliders">
        <div class="slider"><label>Data: <b id="l-date">oggi</b></label><input type="range" id="s-date" min="0" max="100" value="0"></div>
        <div class="slider"><label>Ampiezza grafico: <b id="l-range">&plusmn;40%</b></label><input type="range" id="s-range" min="0" max="80" value="40"></div>
        <div class="slider"><label>Volatilita implicita: <b id="l-iv">&times;1,00</b></label><input type="range" id="s-iv" min="30" max="300" value="100"></div>
      </div>
    </div>
    <div class="card">
      <h2>Analisi statistica <button class="help" data-help="mc" aria-expanded="false" aria-label="Come si legge">?</button></h2>
      <div class="note">Monte Carlo sul sottostante, con la volatilita implicita della catena. Simula i percorsi
        fino alla prima scadenza e misura come si comporta la strategia <b>lungo il cammino</b>, non solo dove finisce.</div>
      <div class="row" style="margin-bottom:10px">
        <div class="field"><label for="mcn">Percorsi</label>
          <select id="mcn" style="width:100px"><option>2000</option><option selected>10000</option><option>50000</option></select></div>
        <div class="field"><label for="mcvol">Volatilita del sottostante</label>
          <select id="mcvol" style="width:190px">
            <option value="atm" selected>IV al denaro</option>
            <option value="avg">media delle gambe</option>
          </select></div>
        <button class="primary" id="mcgo">Calcola</button>
        <span id="mcstat" class="msg"></span>
      </div>
      <div id="mcout"></div>
    </div>
  </div>
</div>

<footer>
  Prezzi e greche dal modello IBKR, in diretta dal tuo TWS. Le curve sono calcolate con Black-Scholes
  (tasso 2%) partendo dalla volatilita implicita di ciascuna gamba, quindi sono una stima: il mercato reale
  ha spread, salti di volatilita e liquidita che il modello non vede.
  <b>Sola lettura: questa pagina non invia e non puo' inviare ordini.</b> Gli ordini si inseriscono a mano in TWS.
</footer>
<div class="tip" id="tip"></div>
<div class="pop" id="pop" role="dialog" aria-label="Aiuto"></div>
<script>
const $ = id => document.getElementById(id);
const tip = $('tip'), pop = $('pop');
let UND = null, CHAIN = null, PORT = null, LEGS = [], usePort = false, curExp = null, curTc = null;

/* ---------- matematica ---------- */
const R = 0.02;
function ncdf(x){ const s = x<0?-1:1; x = Math.abs(x)/Math.SQRT2;
  const t = 1/(1+0.3275911*x);
  const y = 1-(((((1.061405429*t-1.453152027)*t)+1.421413741)*t-0.284496736)*t+0.254829592)*t*Math.exp(-x*x);
  return 0.5*(1+s*y); }
const npdf = x => Math.exp(-x*x/2)/Math.sqrt(2*Math.PI);
function d1d2(S,K,T,v){ const vt=v*Math.sqrt(T); const a=(Math.log(S/K)+(R+v*v/2)*T)/vt; return [a, a-vt]; }
function bs(S,K,T,v,cp){
  if (T<=0 || v<=0 || S<=0) return cp==='C' ? Math.max(S-K,0) : Math.max(K-S,0);
  const [a,b]=d1d2(S,K,T,v);
  return cp==='C' ? S*ncdf(a)-K*Math.exp(-R*T)*ncdf(b) : K*Math.exp(-R*T)*ncdf(-b)-S*ncdf(-a);
}
function greeks(S,K,T,v,cp){
  if (T<=0 || v<=0 || S<=0){ const itm = cp==='C' ? S>K : S<K; return {delta: itm?(cp==='C'?1:-1):0, gamma:0, vega:0, theta:0}; }
  const [a,b]=d1d2(S,K,T,v), sq=Math.sqrt(T);
  const th = cp==='C'
    ? (-S*npdf(a)*v/(2*sq) - R*K*Math.exp(-R*T)*ncdf(b))
    : (-S*npdf(a)*v/(2*sq) + R*K*Math.exp(-R*T)*ncdf(-b));
  return { delta: cp==='C' ? ncdf(a) : ncdf(a)-1, gamma: npdf(a)/(S*v*sq),
           vega: S*npdf(a)*sq/100, theta: th/365 };
}
const fmt = (x,d=2) => (x===null||x===undefined||!isFinite(x)) ? '&mdash;' : x.toLocaleString('it-IT',{minimumFractionDigits:d,maximumFractionDigits:d});
const money = x => (x===null||!isFinite(x)) ? '&mdash;' : (x>=0?'+':'') + Math.round(x).toLocaleString('it-IT') + ' ' + (UND?UND.currency:'');
// Gli strike degli indici sono interi, quelli delle azioni no (32,5 - 37,5): arrotondarli
// a zero decimali li renderebbe sbagliati, non solo brutti.
const fmtK = k => fmt(k, Math.abs(k*2 - Math.round(k*2)) > 1e-9 ? 2
                       : Math.abs(k - Math.round(k)) > 1e-9 ? 1 : 0);
const DAY = 86400000;
const toDate = s => new Date(s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6,8)+'T22:00:00');
const dte = s => Math.max(0, (toDate(s)-Date.now())/DAY);

/* ---------- gambe ---------- */
// una gamba: {expiry, strike, right, qty (+compra/-vende), price (per azione), iv, mult, src:'new'|'port', cost}
function allLegs(){
  const out = LEGS.map(l=>({...l, src:'new', cost: l.qty*l.mult*l.price}));
  if (usePort && PORT) for (const p of PORT.legs)
    out.push({expiry:p.expiry, strike:p.strike, right:p.right, qty:p.position, price:p.marketPrice,
              iv:p.iv||0.2, mult:p.mult, src:'port', cost:p.position*p.avgCost});
  return out;
}
function ivMult(){ return +$('s-iv').value/100; }
// P/L della combinazione se il sottostante vale S dopo "elapsed" giorni
function pl(S, elapsed, legs){
  let v = 0;
  for (const l of legs){
    const T = Math.max(0, dte(l.expiry) - elapsed)/365;
    v += l.qty * l.mult * bs(S, l.strike, T, (l.iv||0.2)*ivMult(), l.right);
  }
  return v - legs.reduce((s,l)=>s+l.cost,0);
}
// orizzonte = PRIMA scadenza: le gambe piu lunghe non sono finite, pl() le rivaluta
// con Black-Scholes. Proiettare tutto all'ultima scadenza darebbe numeri senza senso.
const horizon = legs => legs.length ? Math.min(...legs.map(l=>dte(l.expiry))) : 0;
const mixed  = legs => new Set(legs.map(l=>l.expiry)).size > 1;
const firstExp = legs => legs.length ? legs.map(l=>l.expiry).sort()[0] : null;
const dmy = e => e ? e.slice(6,8)+'/'+e.slice(4,6) : '';

/* ---------- caricamento ---------- */
async function get(url){
  const r = await fetch(url);
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  return j;
}
function status(t, err){ const s=$('status'); s.textContent=t||''; s.className = 'msg'+(err?' err':''); }

async function loadSymbols(){
  try { const j = await get('/api/symbols');
    $('syms').innerHTML = [...j.portfolio, 'SPX','XSP','RUT','ESTX50','SPY','AAPL','MSFT','NVDA']
      .filter((v,i,a)=>a.indexOf(v)===i).map(s=>`<option value="${s}">`).join('');
    if (j.portfolio.length && !$('sym').value) $('sym').value = j.portfolio[0];
  } catch(e){ /* la lista e un di piu: se fallisce si scrive il simbolo a mano */ }
}

async function loadUnderlying(skipPort){
  const sym = $('sym').value.toUpperCase().trim();
  if (!sym) return;
  LEGS = []; usePort = false; CHAIN = null; PORT = null; curExp = null;
  $('banner').className = 'banner'; $('chain').innerHTML=''; render();
  status('Cerco ' + sym + ' su IBKR...');
  try {
    UND = await get('/api/underlying?sym='+encodeURIComponent(sym));
  } catch(e){ status('Errore: '+e.message, true); return; }
  $('spot').innerHTML = UND.spot ? fmt(UND.spot,2) + ' <span style="font-size:.7rem;color:var(--muted)">'+UND.currency+'</span>'
                                 : '<span style="color:var(--neg);font-size:.9rem">prezzo non disponibile</span>';
  const classes = [...new Set(UND.expiries.flatMap(e=>e.classes))];
  $('tc').innerHTML = classes.map(c=>`<option>${c}</option>`).join('');
  curTc = classes[0] || null;
  drawExps();
  status('Scadenze disponibili: ' + UND.expiries.length + '. Scegline una.');
  if (!skipPort) loadPortfolio(sym);
}

/* La liquidita sta sulle mensili: le giornaliere e le settimanali sono la maggior parte
   della lista ma quasi sempre non sono dove vuoi costruire. Di default si mostrano solo
   le mensili; le altre restano a un clic di distanza. */
const KLAB = {monthly:'mensile, terza di venerdi', quarterly:'trimestrale',
              'monthly-am':"mensile AM: ultimo giorno di negoziazione giovedi, regolamento sull'apertura di venerdi",
              weekly:'settimanale', daily:'giornaliera'};
function drawExps(){
  if (!UND) return;
  const mode = $('kinds').value, showAm = $('showam').checked;
  const keep = e => {
    if (e.kind === 'monthly-am') return showAm;      // escluse salvo richiesta esplicita
    if (mode === 'all') return true;
    if (mode === 'wk')  return e.kind !== 'daily';
    return e.kind === 'monthly' || e.kind === 'quarterly';
  };
  const all = UND.expiries.filter(e=>e.classes.includes(curTc));
  const shown = all.filter(keep).slice(0,60);
  $('exps').innerHTML = shown.map(e=>{
    const am = e.kind === 'monthly-am';
    return `<span class="chip k-${e.kind} ${e.date===curExp?'on':''}" data-exp="${e.date}" title="${KLAB[e.kind]}">`
      + `${e.date.slice(6,8)}/${e.date.slice(4,6)}<span class="dte">${e.dte}g</span>`
      + `${am?'<span class="am">AM</span>':''}</span>`; }).join('')
    || `<span class="msg">Nessuna scadenza di questo tipo per la classe ${curTc}. Allarga il filtro qui sopra.</span>`;
  $('exps').querySelectorAll('.chip').forEach(c=> c.onclick = ()=> loadChain(c.dataset.exp));
  const nAm = all.filter(e=>e.kind==='monthly-am').length;
  const hid = all.length - shown.length;
  $('expinfo').innerHTML = `${shown.length} su ${all.length}` + (hid>0 ? `, ${hid} nascoste` : '')
    + (nAm && !showAm ? ` &middot; <b style="color:var(--neg)">${nAm} mensili AM escluse</b>` : '');
}

async function loadPortfolio(sym){
  try { PORT = await get('/api/portfolio?sym='+encodeURIComponent(sym)); }
  catch(e){ PORT = null; return; }
  if (!PORT.legs.length) return;
  const n = PORT.legs.length;
  const pnl = PORT.legs.reduce((s,l)=>s+(l.unrealizedPNL||0),0);
  $('banner').innerHTML = `
    <b>Su ${sym} hai gia ${n} gamb${n===1?'a':'e'} aperte in portafoglio</b>
    (P/L corrente <span class="${pnl>=0?'pos':'neg'}">${money(pnl)}</span>).
    Vuoi vedere anche il risultato complessivo, cioe cosa succede al libro che hai gia a mercato
    quando ci aggiungi quello che stai progettando?
    <div class="row">
      <button class="primary" id="b-yes">Si, mostra il totale</button>
      <button id="b-no">No, solo le gambe nuove</button>
    </div>`;
  $('banner').className = 'banner show';
  $('b-yes').onclick = ()=>{ usePort = true;  $('banner').className='banner'; render(); };
  $('b-no').onclick  = ()=>{ usePort = false; $('banner').className='banner'; render(); };
}

async function loadChain(exp){
  if (!UND) return;
  curExp = exp; curTc = $('tc').value || curTc;
  $('exps').querySelectorAll('.chip').forEach(c=> c.classList.toggle('on', c.dataset.exp===exp));
  const w = Math.max(1, +$('w').value)/100;
  // stima grezza: ogni blocco da 90 contratti costa circa 6 secondi fra attesa e qualifica
  const nk = UND.strikes && UND.strikes[curTc]
    ? UND.strikes[curTc].filter(k => k >= UND.spot*(1-w) && k <= UND.spot*(1+w)).length : 0;
  const secs = Math.max(8, Math.round(Math.ceil(nk*2/90)*8));
  status(`Chiedo la catena a TWS: ${nk?nk+' strike, circa '+nk*2+' contratti da quotare, ':''}`
       + `stimo ${secs} second${secs===1?'o':'i'}...`);
  $('refresh').disabled = true;
  const t0 = Date.now();
  try {
    CHAIN = await get(`/api/chain?sym=${encodeURIComponent(UND.symbol)}&exp=${exp}&tc=${encodeURIComponent(curTc)}&w=${w}`);
    status(`Catena aggiornata alle ${new Date().toLocaleTimeString('it-IT')} (${((Date.now()-t0)/1000).toFixed(0)}s)`);
    const dec = CHAIN.hi < 100 ? 1 : 0;   // le azioni hanno strike con i decimali
    $('chaininfo').innerHTML = `${CHAIN.count} strike da ${fmt(CHAIN.lo,dec)} a ${fmt(CHAIN.hi,dec)}, tutti`
      + (CHAIN.asked > CHAIN.count ? ` <span style="color:var(--muted)">(${CHAIN.asked-CHAIN.count} strike elencati da IBKR non esistono su questa scadenza)</span>` : '')
      + (CHAIN.capped ? ' <b style="color:var(--neg)">(tagliata al tetto di sicurezza)</b>' : '');
  } catch(e){ status('Errore: '+e.message, true); CHAIN=null; $('chaininfo').textContent=''; }
  $('refresh').disabled = false;
  drawChain(); drawWiz(); render();
}

/* ---------- un affare preso dalla dashboard ---------- */
/* Arrivando con ?deal=<id> si caricano le gambe di quel gruppo di portfolio_map.json ai
   loro prezzi di carico: payoff, barra del pareggio, greche e Monte Carlo sono gli stessi
   del costruttore, e si puo aggiungere una gamba per simulare un aggiustamento. */
async function loadDeal(id){
  status('Carico l\'affare dal portafoglio...');
  let g;
  try { g = await get('/api/group?id=' + encodeURIComponent(id)); }
  catch(e){ status('Errore: ' + e.message, true); return; }
  $('sym').value = g.symbol;
  await loadUnderlying(true);
  await loadPortfolio(g.symbol);
  LEGS = g.legs.map(l => ({expiry:l.expiry, strike:l.strike, right:l.right, qty:l.position,
                           // prezzo di CARICO, non di mercato: il P/L e quello dall'apertura.
                           // Arrotondato a 4 decimali perche' avgCost/mult tira fuori code
                           // come 6.984564 che nel campo sono solo rumore. Scarto misurato
                           // su una ZEBRA reale: 2 centesimi su 3128 dollari di costo.
                           price: Math.round(l.avgCost / l.mult * 1e4) / 1e4,
                           iv: l.iv || 0.2, mult: l.mult, fromDeal: true}));
  // le gambe dell'affare sono gia qui: vanno tolte dal blocco "portafoglio", altrimenti
  // accendendo l'interruttore verrebbero contate due volte
  if (PORT) PORT.legs = PORT.legs.filter(p => !LEGS.some(l =>
      l.expiry === p.expiry && l.strike === p.strike && l.right === p.right));
  usePort = false;
  const noIv = g.legs.filter(l => l.ivFallback).length;
  $('banner').innerHTML = `<b>${g.nome}</b> &mdash; ${LEGS.length} gambe dal portafoglio, ai prezzi di carico.`
    + (g.note ? `<div style="color:var(--muted);margin-top:4px">${g.note}</div>` : '')
    + (g.dropped ? `<div style="color:var(--neg);margin-top:4px">${g.dropped} gambe su altri sottostanti (${g.symbols.join(', ')}) non sono nel grafico: un payoff si disegna su un sottostante solo.</div>` : '')
    + (noIv ? `<div style="color:var(--muted);margin-top:4px">Mercato chiuso: volatilita di ${noIv} gambe ricavata dal prezzo invece che dalle greche di TWS.</div>` : '')
    + `<div style="margin-top:6px">Puoi modificarle o aggiungerne per simulare un aggiustamento. <b>Nulla di questo tocca il conto.</b></div>`;
  $('banner').className = 'banner show';
  render();
  // la catena della prima scadenza dell'affare, cosi si possono aggiungere gambe
  const exp = LEGS.map(l => l.expiry).sort()[0];
  if (UND.expiries.some(e => e.date === exp && e.classes.includes(curTc))) loadChain(exp);
  else status('Affare caricato. Scegli una scadenza se vuoi aggiungere gambe.');
}

/* ---------- catena ---------- */
function drawChain(){
  const t = $('chain');
  if (!CHAIN){ t.innerHTML=''; return; }
  const spot = CHAIN.spot || UND.spot;
  const byK = new Map();
  for (const r of CHAIN.rows){
    if (!byK.has(r.strike)) byK.set(r.strike, {});
    byK.get(r.strike)[r.right] = r;
  }
  const ks = [...byK.keys()].sort((a,b)=>a-b);
  const atm = ks.reduce((b,k)=> Math.abs(k-spot)<Math.abs(b-spot)?k:b, ks[0]);
  const cell = (r, side, kind) => {
    const v = r ? r[kind] : null;
    if (v===null||v===undefined) return '<td>&mdash;</td>';
    const act = kind==='bid' ? -1 : 1;
    return `<td class="q" data-k="${r.strike}" data-r="${r.right}" data-p="${v}" data-q="${act}"
             title="${act>0?'compra':'vendi'} ${r.right==='C'?'call':'put'} ${r.strike}">${fmt(v,2)}</td>`;
  };
  t.innerHTML = `<thead><tr>
      <th colspan="4" class="side" style="text-align:center">Call</th>
      <th class="k">Strike</th>
      <th colspan="4" class="side" style="text-align:center">Put</th></tr>
    <tr><th>&Delta;</th><th>IV</th><th>bid</th><th>ask</th><th class="k"></th><th>bid</th><th>ask</th><th>IV</th><th>&Delta;</th></tr></thead>
    <tbody>` + ks.map(k=>{
      const c = byK.get(k).C, p = byK.get(k).P;
      return `<tr>
        <td>${c&&c.delta!=null?fmt(c.delta,2):'&mdash;'}</td>
        <td>${c&&c.iv!=null?fmt(c.iv*100,1)+'%':'&mdash;'}</td>
        ${cell(c,'C','bid')}${cell(c,'C','ask')}
        <td class="k ${k===atm?'atm':''}">${fmtK(k)}</td>
        ${cell(p,'P','bid')}${cell(p,'P','ask')}
        <td>${p&&p.iv!=null?fmt(p.iv*100,1)+'%':'&mdash;'}</td>
        <td>${p&&p.delta!=null?fmt(p.delta,2):'&mdash;'}</td></tr>`;
    }).join('') + '</tbody>';
  // le due righe di intestazione restano appiccicate in alto: la seconda va messa
  // esattamente sotto la prima, e l'altezza si misura invece di stimarla in CSS
  const hr1 = t.querySelector('thead tr:first-child');
  if (hr1){ const hh = hr1.getBoundingClientRect().height;
    t.querySelectorAll('thead tr:nth-child(2) th').forEach(th => th.style.top = hh + 'px'); }
  // con una catena lunga il denaro finisce fuori schermo: lo si centra
  const wrap = t.closest('.chain-wrap'), atmRow = t.querySelector('.k.atm');
  if (wrap && atmRow){ const r = atmRow.closest('tr');
    wrap.scrollTop = r.getBoundingClientRect().top - wrap.getBoundingClientRect().top + wrap.scrollTop - wrap.clientHeight/2; }
  t.querySelectorAll('.q').forEach(td => td.onclick = ()=>{
    const k=+td.dataset.k, right=td.dataset.r, price=+td.dataset.p, q=+td.dataset.q;
    const row = CHAIN.rows.find(x=>x.strike===k && x.right===right);
    addLeg({expiry:curExp, strike:k, right, qty:q, price, iv:(row&&row.iv)||0.2, mult:CHAIN.mult});
  });
}
function addLeg(l){
  const same = LEGS.find(x=>x.expiry===l.expiry && x.strike===l.strike && x.right===l.right);
  if (same){ same.qty += l.qty; if (!same.qty) LEGS.splice(LEGS.indexOf(same),1); }
  else LEGS.push(l);
  render();
}

/* Gambe di un aggiustamento proposto dalla dashboard (?add=[...]): si sommano a quelle
   dell'affare, cosi il payoff che compare e' gia quello DOPO l'operazione. I prezzi
   arrivano eseguibili da actions.py (si vende al denaro, si compra alla lettera); se
   per una gamba manca la quotazione si prende quella della catena, se c'e. */
function addProposte(raw){
  let legs; try { legs = JSON.parse(raw); } catch(e){ return; }
  if (!Array.isArray(legs) || !legs.length) return;
  const prima = LEGS.map(l=>({...l}));
  let senzaPrezzo = 0;
  for (const l of legs){
    let price = l.price;
    if (price == null && CHAIN){
      const row = CHAIN.rows.find(x=>x.strike===l.strike && x.right===l.right && curExp===l.expiry);
      if (row) price = l.qty > 0 ? row.ask : row.bid;
    }
    if (price == null){ price = 0; senzaPrezzo++; }
    const esistente = LEGS.find(x=>x.expiry===l.expiry && x.strike===l.strike && x.right===l.right);
    const iv = esistente ? esistente.iv : 0.2;
    addLeg({expiry:l.expiry, strike:l.strike, right:l.right, qty:l.qty, price,
            iv, mult:l.mult||100});
  }
  $('banner').innerHTML += `<div style="margin-top:8px;color:var(--s1)"><b>Aggiustamento proposto gia inserito.</b>
    Quello che vedi e il payoff DOPO l'operazione. Prima dell'aggiustamento erano ${prima.length} gambe.
    ${senzaPrezzo ? `<br><span style="color:var(--neg)">${senzaPrezzo} gambe senza quotazione: prezzo a zero, correggilo a mano.</span>` : ''}
    </div>`;
  render();
}

/* ---------- gambe in progetto ---------- */
function drawLegs(){
  const box = $('legs');
  let h = '';
  if (!LEGS.length) h += '<div class="msg">Nessuna gamba. Clicca un prezzo nella catena per aggiungerne una.</div>';
  else h += `<table class="legs"><tr><th>Gamba</th><th>Qta</th><th>Prezzo</th><th>IV</th><th>Costo</th><th></th></tr>` +
    LEGS.map((l,i)=>`<tr>
      <td>${l.right==='C'?'Call':'Put'} ${fmtK(l.strike)} <span style="color:var(--muted)">${l.expiry.slice(6,8)}/${l.expiry.slice(4,6)}</span>${l.fromDeal?' <span class="am" style="color:var(--s1);border-color:var(--s1)">in essere</span>':''}</td>
      <td><input class="qty" type="number" step="1" value="${l.qty}" data-i="${i}" data-f="qty"></td>
      <td><input class="px" type="number" step="0.01" value="${l.price}" data-i="${i}" data-f="price"></td>
      <td>${l.iv?fmt(l.iv*100,1)+'%':'&mdash;'}</td>
      <td class="${l.qty*l.price>=0?'neg':'pos'}">${money(-l.qty*l.mult*l.price)}</td>
      <td><button class="x" data-del="${i}" title="rimuovi">&times;</button></td></tr>`).join('') + '</table>';

  if (PORT && PORT.legs.length){
    h += `<div style="margin-top:12px"><span class="pill ${usePort?'on':''}" id="tog-port">
            <input type="checkbox" ${usePort?'checked':''} style="pointer-events:none;margin:0"> includi le ${PORT.legs.length} gambe gia in portafoglio</span></div>`;
    if (usePort) h += `<table class="legs" style="margin-top:8px;opacity:.85"><tr><th>In portafoglio</th><th>Qta</th><th>Carico</th><th>P/L</th></tr>` +
      PORT.legs.map(p=>`<tr>
        <td>${p.right==='C'?'Call':'Put'} ${fmtK(p.strike)} <span style="color:var(--muted)">${p.expiry.slice(6,8)}/${p.expiry.slice(4,6)}</span></td>
        <td>${p.position>0?'+':''}${p.position}</td><td>${fmt(p.avgCost/p.mult,2)}</td>
        <td class="${p.unrealizedPNL>=0?'pos':'neg'}">${money(p.unrealizedPNL)}</td></tr>`).join('') + '</table>';
  }
  box.innerHTML = h;
  box.querySelectorAll('input[data-f]').forEach(inp => inp.onchange = ()=>{
    const l = LEGS[+inp.dataset.i]; const v = +inp.value;
    if (inp.dataset.f==='qty'){ if(!v){ LEGS.splice(+inp.dataset.i,1); } else l.qty = v; }
    else l.price = v;
    render();
  });
  box.querySelectorAll('[data-del]').forEach(b => b.onclick = ()=>{ LEGS.splice(+b.dataset.del,1); render(); });
  const tg = $('tog-port'); if (tg) tg.onclick = ()=>{ usePort=!usePort; render(); };
}

/* ---------- sintesi ---------- */
function summary(legs){
  const spot = (CHAIN&&CHAIN.spot) || (UND&&UND.spot) || 0;
  if (!legs.length || !spot) return null;
  const H = horizon(legs);
  const lo = spot*0.01, hi = spot*3, N = 1400;
  let mx=-Infinity, mn=Infinity, bes=[], prev=null;
  // IV di riferimento: la gamba piu vicina al denaro. La media di tutte le gambe la
  // falserebbe, perche le put di coda molto lontane quotano volatilita molto piu alte.
  const near = legs.reduce((b,l)=> Math.abs(l.strike-spot) < Math.abs(b.strike-spot) ? l : b, legs[0]);
  const atmIv = (near.iv || 0.2)*ivMult();
  const T = H/365, vt = atmIv*Math.sqrt(Math.max(T,1e-6));
  let pProfit = 0;
  for (let i=0;i<=N;i++){
    const S = lo + (hi-lo)*i/N, v = pl(S, H, legs);
    if (v>mx) mx=v; if (v<mn) mn=v;
    if (prev && (prev.v<0)!==(v<0)) bes.push(prev.S + (S-prev.S)*(-prev.v)/(v-prev.v));
    if (v>=0 && T>0){ // massa di probabilita lognormale sulla zona in profitto
      const dS = (hi-lo)/N;
      const z = (Math.log(S/spot)-(R-atmIv*atmIv/2)*T)/vt;
      pProfit += npdf(z)/(S*vt)*dS;
    }
    prev = {S,v};
  }
  // code illimitate: call nette comprate -> profitto illimitato, vendute -> perdita illimitata
  const atH = legs.filter(l=>Math.abs(dte(l.expiry)-H) < 0.5);
  const callQ = atH.filter(l=>l.right==='C').reduce((s,l)=>s+l.qty*l.mult,0);
  const putQ  = atH.filter(l=>l.right==='P').reduce((s,l)=>s+l.qty*l.mult,0);
  const cost = legs.reduce((s,l)=>s+l.cost,0);
  let g = {delta:0, gamma:0, vega:0, theta:0};
  for (const l of legs){
    const gg = greeks(spot, l.strike, dte(l.expiry)/365, (l.iv||0.2)*ivMult(), l.right);
    for (const k in g) g[k] += gg[k]*l.qty*l.mult;
  }
  const expLabel = mixed(legs) ? ('alla prima scadenza '+dmy(firstExp(legs))) : 'a scadenza';
  return { cost, expLabel, max: callQ>0?null:mx, min: callQ<0?null:(putQ<0?null:mn), bes, pProfit:Math.min(1,pProfit), g, spot, H };
}

function drawTiles(s, legs){
  if (!s){ $('tiles').innerHTML = '<div class="tile"><div class="label">Nessuna gamba</div><div class="sub2">Carica un sottostante e costruisci la strategia</div></div>'; return; }
  const kind = s.cost>0 ? 'Debito netto' : 'Credito netto';
  const rr = (s.max!==null && s.min!==null && s.min!==0) ? Math.abs(s.max/s.min) : null;
  $('tiles').innerHTML = `
   <div class="tile"><div class="label">${kind}</div><div class="value">${Math.round(Math.abs(s.cost)).toLocaleString('it-IT')} ${UND?UND.currency:''}</div>
     <div class="sub2">${legs.length} gamb${legs.length===1?'a':'e'}${usePort?' (portafoglio incluso)':''}</div></div>
   <div class="tile"><div class="label">Max profitto</div><div class="value pos">${s.max===null?'illimitato':money(s.max)}</div>
     <div class="sub2">${s.expLabel}</div></div>
   <div class="tile"><div class="label">Max perdita</div><div class="value neg">${s.min===null?'illimitata':money(s.min)}</div>
     <div class="sub2">${rr?('rapporto '+fmt(rr,2)):s.expLabel}</div></div>
   <div class="tile"><div class="label">Pareggio</div><div class="value" style="font-size:.98rem">${s.bes.length?s.bes.map(b=>fmtK(b)).join(' &middot; '):'&mdash;'}</div>
     <div class="sub2">${s.bes.length?'sul sottostante':'nessun incrocio'}</div></div>
   <div class="tile"><div class="label">Prob. di profitto</div><div class="value">${s.pProfit?fmt(s.pProfit*100,1)+'%':'&mdash;'}</div>
     <div class="sub2">lognormale, IV al denaro</div></div>
   <div class="tile"><div class="label">Greche</div><div class="value" style="font-size:.95rem">&Delta; ${fmt(s.g.delta,1)}</div>
     <div class="sub2">&Theta; ${fmt(s.g.theta,0)} &middot; V ${fmt(s.g.vega,0)}</div></div>`;
}

/* ---------- grafico ---------- */
// Altezza ridotta del 22% il 29/09/2026, come nelle altre pagine.
// t lascia spazio al righello degli strike sopra l'area del grafico
const W=620, PAD={l:54,r:16,t:46,b:22}, RULER=34;
/* Ampiezza del grafico: 0-80%, predefinita 40% (valore di mezzo). Sotto il 2% il grafico
   non avrebbe un intervallo da disegnare, quindi si ferma li. */
const rangePct = () => Math.max(2, +$('s-range').value) / 100;

function drawChart(legs){
  const box = $('chart');
  const spot = (CHAIN&&CHAIN.spot) || (UND&&UND.spot);
  if (!legs.length || !spot){ box.innerHTML = '<div class="msg">Il grafico appare quando aggiungi la prima gamba.</div>'; return; }
  // larghezza = quella del riquadro che lo contiene; altezza fissa
  const W = Math.max(420, Math.round(box.clientWidth) || 620);
  const h=268, iw=W-PAD.l-PAD.r, ih=h-PAD.t-PAD.b;
  const rg = rangePct();
  const lo = spot*(1-rg), hi = spot*(1+rg), N=220;
  const H = horizon(legs), el = H*(+$('s-date').value/100);
  const series = [ {e:0, dash:''}, {e:el, dash:'6 4'}, {e:H, dash:'2 3'} ];
  const xs = []; for (let i=0;i<=N;i++) xs.push(lo+(hi-lo)*i/N);
  const data = series.map(s => xs.map(S => pl(S, s.e, legs)));
  const all = data.flat();
  let ymin = Math.min(...all), ymax = Math.max(...all);
  const padY = (ymax-ymin)*0.1 || 1; ymin -= padY; ymax += padY;
  if (ymin > 0) ymin = -padY; if (ymax < 0) ymax = padY;
  const X = v => PAD.l + (v-lo)/(hi-lo)*iw;
  const Y = v => PAD.t + ih - (v-ymin)/(ymax-ymin)*ih;
  const y0 = Y(0);

  let s = '';
  for (let i=0;i<=4;i++){ const v=ymin+(ymax-ymin)*i/4, y=Y(v);
    s += `<line x1="${PAD.l}" x2="${PAD.l+iw}" y1="${y}" y2="${y}" stroke="var(--grid)"/>`;
    s += `<text x="${PAD.l-6}" y="${y+3}" text-anchor="end">${Math.round(v).toLocaleString('it-IT')}</text>`; }
  for (let i=0;i<=5;i++){ const v=lo+(hi-lo)*i/5;
    s += `<text x="${X(v)}" y="${h-4}" text-anchor="middle">${fmt(v,0)}</text>`; }
  // due maschere: sopra lo zero si colora di verde, sotto di rosso
  s += `<defs>
    <linearGradient id="gup" gradientUnits="userSpaceOnUse" x1="0" y1="${PAD.t}" x2="0" y2="${y0}">
      <stop offset="0" stop-color="var(--pos)" stop-opacity=".40"/><stop offset="1" stop-color="var(--pos)" stop-opacity="0"/></linearGradient>
    <linearGradient id="gdn" gradientUnits="userSpaceOnUse" x1="0" y1="${y0}" x2="0" y2="${PAD.t+ih}">
      <stop offset="0" stop-color="var(--neg)" stop-opacity="0"/><stop offset="1" stop-color="var(--neg)" stop-opacity=".40"/></linearGradient>
    <clipPath id="up"><rect x="${PAD.l}" y="${PAD.t}" width="${iw}" height="${Math.max(0,y0-PAD.t)}"/></clipPath>
        <clipPath id="dn"><rect x="${PAD.l}" y="${y0}" width="${iw}" height="${Math.max(0,PAD.t+ih-y0)}"/></clipPath></defs>`;
  data.forEach((ys,i)=>{
    let d=''; ys.forEach((v,j)=> d += (j?'L':'M')+X(xs[j]).toFixed(1)+' '+Y(v).toFixed(1));
    const area = d + `L${X(xs[N]).toFixed(1)} ${y0} L${X(xs[0]).toFixed(1)} ${y0} Z`;
    if (i===0){
      // sfumatura che svanisce allontanandosi dallo zero, come su OptionStrat
      s += `<path d="${area}" fill="url(#gup)" clip-path="url(#up)"/>`;
      s += `<path d="${area}" fill="url(#gdn)" clip-path="url(#dn)"/>`;
    }
    const dash = series[i].dash ? `stroke-dasharray="${series[i].dash}"` : '';
    s += `<path d="${d}" fill="none" stroke="var(--pos)" stroke-width="2" ${dash} clip-path="url(#up)"/>`;
    s += `<path d="${d}" fill="none" stroke="var(--neg)" stroke-width="2" ${dash} clip-path="url(#dn)"/>`;
  });
  s += `<line x1="${PAD.l}" x2="${PAD.l+iw}" y1="${y0}" y2="${y0}" stroke="var(--muted)" stroke-width="1.5"/>`;
  s += `<line x1="${X(spot)}" x2="${X(spot)}" y1="${PAD.t}" y2="${PAD.t+ih}" stroke="var(--muted)" stroke-dasharray="3 3"/>`;
  s += `<text x="${X(spot)}" y="${PAD.t-3}" text-anchor="middle">${fmt(spot,0)}</text>`;

  // Pareggio ALLA DATA DELLO SLIDER: dove la curva tratteggiata taglia lo zero. Si muove
  // quando sposti lo slider, perche col passare del tempo la curva si deforma verso il
  // payoff a scadenza e i punti di pareggio scivolano.
  const beS = [];
  for (let j=1;j<=N;j++){
    const a = data[1][j-1], b = data[1][j];
    if ((a < 0) !== (b < 0) && b !== a) beS.push(xs[j-1] + (xs[j]-xs[j-1])*(-a)/(b-a));
  }
  let lastLx = -1e9, lvl = 0;
  for (const b of beS){
    if (b < lo || b > hi) continue;
    const x = X(b);
    s += `<rect x="${(x-1.5).toFixed(1)}" y="${PAD.t}" width="3" height="${ih}" fill="var(--s1)" opacity=".5"/>`;
    // etichette sfalsate quando due pareggi sono vicini, altrimenti si sovrappongono
    lvl = (x - lastLx < 68) ? lvl + 11 : 0;
    lastLx = x;
    s += `<text x="${x}" y="${PAD.t + 10 + lvl}" text-anchor="middle" fill="var(--s1)" style="font-weight:600">${fmtK(b)}</text>`;
  }
  const beIn = beS.filter(b => b >= lo && b <= hi);
  const dlab = $('l-date').textContent;
  $('be-lbl').innerHTML = beIn.length
    ? `pareggio ${dlab === 'oggi' ? 'oggi' : 'al ' + dlab}: <b style="color:var(--s1)">${beIn.map(b=>fmtK(b)).join(' &middot; ')}</b>`
    : (beS.length ? `<span style="color:var(--muted)">pareggio fuori dall'intervallo mostrato</span>`
                  : `<span style="color:var(--muted)">nessun pareggio a questa data</span>`);
  for (const l of legs){ if (l.strike<lo||l.strike>hi) continue;
    s += `<line x1="${X(l.strike)}" x2="${X(l.strike)}" y1="${PAD.t+ih-4}" y2="${PAD.t+ih+3}" stroke="var(--s1)" stroke-width="2"/>`; }

  // ---- righello degli strike sopra il grafico ----
  // badge tutti SOPRA la linea (due file quando sono vicini), etichette dei prezzi sotto:
  // mescolarli a cavallo della linea rendeva illeggibili entrambi
  const RY = 32;
  s += `<line x1="${PAD.l}" x2="${PAD.l+iw}" y1="${RY}" y2="${RY}" stroke="var(--border)"/>`;
  for (let i=0;i<=6;i++){ const v=lo+(hi-lo)*i/6;
    s += `<line x1="${X(v)}" x2="${X(v)}" y1="${RY-3}" y2="${RY}" stroke="var(--border)"/>`
       + `<text x="${X(v)}" y="${RY+9}" text-anchor="middle" style="font-size:8px">${fmt(v,0)}</text>`; }
  s += `<path d="M${X(spot)-4} ${RY} L${X(spot)+4} ${RY} L${X(spot)} ${RY+6} Z" fill="var(--muted)"/>`;
  const badge = (l, y, col) => {
    const t = fmtK(l.strike)+l.right, w = t.length*5.4+10;
    const x = Math.max(PAD.l, Math.min(PAD.l+iw-w, X(l.strike)-w/2));
    return `<line x1="${X(l.strike)}" x2="${X(l.strike)}" y1="${y+13}" y2="${RY}" stroke="${col}" stroke-width="1" opacity=".55"/>`
         + `<rect x="${x.toFixed(1)}" y="${y}" width="${w.toFixed(1)}" height="13" rx="3" fill="${col}"/>`
         + `<text x="${(x+w/2).toFixed(1)}" y="${y+9.5}" text-anchor="middle" fill="#fff" style="font-size:8.5px;font-weight:600">${t}</text>`;
  };
  const ordinati = legs.filter(l=>l.strike>=lo&&l.strike<=hi).sort((a,b)=>a.strike-b.strike);
  let lastx = -1e9, row = 0;
  for (const l of ordinati){
    const x = X(l.strike);
    row = (x - lastx < 48) ? 1-row : 0; lastx = x;
    s += badge(l, row ? 0 : 16, l.right==='P' ? 'var(--neg)' : 'var(--pos)');
  }
  s += `<rect id="hit" x="${PAD.l}" y="${PAD.t}" width="${iw}" height="${ih}" fill="transparent" style="cursor:crosshair"/>`;
  box.innerHTML = `<svg viewBox="0 0 ${W} ${h}" preserveAspectRatio="xMinYMid meet" style="width:100%;max-width:none;height:${h}px;margin:0">${s}</svg>`;
  const hit = box.querySelector('#hit');
  hit.addEventListener('mousemove', e=>{
    const r = hit.getBoundingClientRect(), S = lo+(e.clientX-r.left)/r.width*(hi-lo);
    const vals = series.map(x=>pl(S, x.e, legs));
    tip.innerHTML = `<b>${fmt(S,2)}</b> (${((S/spot-1)*100).toFixed(1)}%)<br>`+
      `ora: <b class="${vals[0]>=0?'pos':'neg'}">${money(vals[0])}</b><br>`+
      `${$('l-date').textContent}: <b class="${vals[1]>=0?'pos':'neg'}">${money(vals[1])}</b><br>`+
      `${$('lbl-exp').textContent}: <b class="${vals[2]>=0?'pos':'neg'}">${money(vals[2])}</b>`;
    tip.style.opacity=1;
    const tr = tip.getBoundingClientRect();
    tip.style.left = Math.min(window.innerWidth-tr.width-8, e.clientX+14)+'px';
    tip.style.top  = Math.max(8, e.clientY-tr.height-10)+'px';
  });
  hit.addEventListener('mouseleave', ()=> tip.style.opacity=0);
}

/* ---------- tabella a mappa di calore ---------- */
/* Le stesse informazioni del grafico lette al contrario: prezzo sulle righe, tempo sulle
   colonne, P/L nella cella. Serve a rispondere a "quanto perdo se fra due settimane sta
   a 6200", che sul grafico si legge male. */
let VISTA = 'grafico';
function drawHeat(legs){
  const box = $('heat');
  const spot = (CHAIN&&CHAIN.spot) || (UND&&UND.spot);
  if (!legs.length || !spot){ box.innerHTML = '<div class="msg">La tabella appare quando aggiungi la prima gamba.</div>'; return; }
  const rg = rangePct(), H = horizon(legs);
  const NR = 21, NC = 9;
  const prezzi = [], tempi = [];
  for (let i=NR-1;i>=0;i--) prezzi.push(spot*(1-rg) + (spot*2*rg)*i/(NR-1));   // dall'alto in basso
  for (let j=0;j<NC;j++) tempi.push(H*j/(NC-1));
  const val = prezzi.map(P => tempi.map(t => pl(P, t, legs)));
  const flat = val.flat();
  const amp = Math.max(Math.abs(Math.min(...flat)), Math.abs(Math.max(...flat))) || 1;
  // Il testo segue l'intensita della tinta: bianco quando la cella e' carica (dove il
  // colore di testo normale non si leggeva), colore normale quando e' quasi trasparente
  // (dove il bianco spariva). Cosi funziona sia in chiaro sia in scuro.
  const col = v => {
    const a = Math.min(1, Math.abs(v)/amp)*0.78 + 0.08;
    const bg = v>=0 ? 'var(--pos)' : 'var(--neg)';
    const fg = a >= 0.30 ? '#fff' : 'var(--text)';
    return `background:color-mix(in srgb, ${bg} ${(a*100).toFixed(0)}%, transparent);color:${fg}`;
  };
  const gg = t => t<0.5 ? 'oggi' : new Date(Date.now()+t*DAY).toLocaleDateString('it-IT',{day:'2-digit',month:'2-digit'});
  let h = `<table class="heat"><tr><th>${UND?UND.symbol:''}</th>`
        + tempi.map((t,j)=>`<th>${gg(t)}${j===NC-1?'<br><span style="font-size:.62rem">scadenza</span>':''}</th>`).join('') + '</tr>';
  prezzi.forEach((P,i)=>{
    const vicino = Math.abs(P-spot) <= (spot*2*rg)/(NR-1)/2;
    h += `<tr class="${vicino?'spot':''}"><td class="px">${fmtK(P)}<small>${((P/spot-1)*100).toFixed(1)}%</small></td>`
       + val[i].map(v=>`<td style="${col(v)}">${Math.round(v).toLocaleString('it-IT')}</td>`).join('') + '</tr>';
  });
  box.innerHTML = h + '</table>';
}

function setVista(v){
  VISTA = v;
  $('t-graf').classList.toggle('on', v==='grafico');
  $('t-tab').classList.toggle('on', v==='tabella');
  $('chart').style.display = v==='grafico' ? '' : 'none';
  $('heat').style.display  = v==='tabella' ? '' : 'none';
  render();
}

/* ---------- render ---------- */
function render(){
  const legs = allLegs();
  const H = horizon(legs);
  const d = +$('s-date').value/100*H;
  $('l-date').textContent = d<0.5 ? 'oggi' : new Date(Date.now()+d*DAY).toLocaleDateString('it-IT',{day:'2-digit',month:'2-digit'});
  $('lbl-mid').textContent = $('l-date').textContent;
  $('lbl-exp').textContent = mixed(legs) ? ('prima scadenza '+dmy(firstExp(legs))) : 'a scadenza';
  $('l-range').innerHTML = '&plusmn;'+Math.round(rangePct()*100)+'%';
  $('l-iv').innerHTML = '&times;'+fmt(ivMult(),2);
  drawLegs();
  const s = summary(legs);
  drawTiles(s, legs);
  if (VISTA==='grafico') drawChart(legs); else drawHeat(legs);
}

/* ---------- catalogo delle strategie ---------- */
/* Ogni gamba dichiara lo strike come REGOLA, non come numero: 'atm', oppure un delta
   obiettivo {d:0.20}, eventualmente spostato di N passi sulla griglia della catena
   {d:0.20, off:-2}. Cosi la stessa ricetta funziona su XSP (passo 1) e su un titolo con passo 2,5
   senza scriverci dentro nessun prezzo. exp:2 = serve una seconda scadenza. */
const STRAT = [
 // --- base ---
 {c:"Base", n:"Call lunga", b:"rial", p:"illimitato", l:"limitata", exp:1,
  d:"La scommessa rialzista piu semplice. Perdi al massimo il premio, guadagni senza tetto. Paghi valore temporale ogni giorno.",
  legs:[{r:"C",q:1,k:{d:0.50}}]},
 {c:"Base", n:"Put lunga", b:"rib", p:"quasi illimitato", l:"limitata", exp:1,
  d:"Speculare alla call lunga. Utile anche come assicurazione su un titolo che hai.",
  legs:[{r:"P",q:1,k:{d:0.50}}]},
 {c:"Base", n:"Put venduta", b:"rial", p:"limitato", l:"molto grande", exp:1,
  d:"Incassi premio e ti impegni a comprare allo strike. Va fatta solo su sottostanti che vorresti davvero possedere.",
  legs:[{r:"P",q:-1,k:{d:0.25}}]},
 {c:"Base", n:"Call venduta scoperta", b:"rib", p:"limitato", l:"illimitata", exp:1,
  d:"Perdita senza tetto: il margine esplode e una sorpresa al rialzo puo fare danni molto seri. Qui e' inclusa per completezza, non come suggerimento.",
  legs:[{r:"C",q:-1,k:{d:0.20}}]},

 // --- verticali ---
 {c:"Verticali", n:"Bull call spread", b:"rial", p:"limitato", l:"limitata", exp:1,
  d:"A debito. Compri la call vicina e vendi quella lontana per abbassare il costo. Serve il movimento E il tempo: vicino alla scadenza vale molto piu che subito.",
  legs:[{r:"C",q:1,k:{d:0.50}},{r:"C",q:-1,k:{d:0.25}}]},
 {c:"Verticali", n:"Bear put spread", b:"rib", p:"limitato", l:"limitata", exp:1,
  d:"L'immagine speculare: a debito, ribassista, rischio definito.",
  legs:[{r:"P",q:1,k:{d:0.50}},{r:"P",q:-1,k:{d:0.25}}]},
 {c:"Verticali", n:"Bull put spread", b:"rial", p:"limitato", l:"limitata", exp:1,
  d:"A credito. Vendi la put piu vicina e compri l'ala sotto. Guadagni se il prezzo sta sopra: e' la struttura del tuo bucket A.",
  legs:[{r:"P",q:-1,k:{d:0.20}},{r:"P",q:1,k:{d:0.20,off:-2}}]},
 {c:"Verticali", n:"Bear call spread", b:"rib", p:"limitato", l:"limitata", exp:1,
  d:"A credito, speculare del bull put. Guadagni se il prezzo sta sotto.",
  legs:[{r:"C",q:-1,k:{d:0.20}},{r:"C",q:1,k:{d:0.20,off:+2}}]},

 // --- neutrali ---
 {c:"Neutrali", n:"Iron condor", b:"neu", p:"limitato", l:"limitata", exp:1,
  d:"Due spread a credito, uno per lato. Guadagni se il prezzo resta in mezzo. La struttura piu usata per vendere tempo con rischio definito.",
  legs:[{r:"P",q:-1,k:{d:0.20}},{r:"P",q:1,k:{d:0.20,off:-2}},{r:"C",q:-1,k:{d:0.20}},{r:"C",q:1,k:{d:0.20,off:+2}}]},
 {c:"Neutrali", n:"Iron butterfly", b:"neu", p:"limitato", l:"limitata", exp:1,
  d:"Come l'iron condor ma con le corte entrambe al denaro: incassi molto di piu, ma la zona di profitto e' stretta.",
  legs:[{r:"P",q:-1,k:"atm"},{r:"C",q:-1,k:"atm"},{r:"P",q:1,k:{off:-3}},{r:"C",q:1,k:{off:+3}}]},
 {c:"Neutrali", n:"Butterfly di call", b:"neu", p:"limitato", l:"limitata", exp:1,
  d:"A debito. Massimo profitto se a scadenza il prezzo finisce esattamente sullo strike centrale.",
  legs:[{r:"C",q:1,k:{off:-2}},{r:"C",q:-2,k:"atm"},{r:"C",q:1,k:{off:+2}}]},
 {c:"Neutrali", n:"Butterfly di put", b:"neu", p:"limitato", l:"limitata", exp:1,
  d:"Identica alla precedente ma costruita con le put. Spesso quota meglio sotto il denaro.",
  legs:[{r:"P",q:1,k:{off:-2}},{r:"P",q:-2,k:"atm"},{r:"P",q:1,k:{off:+2}}]},
 {c:"Neutrali", n:"Strangle venduto", b:"neu", p:"limitato", l:"molto grande", exp:1,
  d:"Vendi put e call lontane. Incassi tempo, ma il rischio non e' definito: e' la struttura che usi sulle materie prime, dove il margine SPAN la rende efficiente.",
  legs:[{r:"P",q:-1,k:{d:0.15}},{r:"C",q:-1,k:{d:0.15}}]},
 {c:"Neutrali", n:"Straddle venduto", b:"neu", p:"limitato", l:"molto grande", exp:1,
  d:"Come lo strangle ma entrambe al denaro. Massimo incasso, massima sensibilita al movimento.",
  legs:[{r:"P",q:-1,k:"atm"},{r:"C",q:-1,k:"atm"}]},
 {c:"Neutrali", n:"Straddle comprato", b:"vol", p:"molto grande", l:"limitata", exp:1,
  d:"Scommetti sul movimento, in qualunque direzione. Costoso: paghi due premi al denaro e il tempo ti lavora contro.",
  legs:[{r:"P",q:1,k:"atm"},{r:"C",q:1,k:"atm"}]},
 {c:"Neutrali", n:"Strangle comprato", b:"vol", p:"molto grande", l:"limitata", exp:1,
  d:"Versione economica dello straddle: strike lontani, serve un movimento piu ampio.",
  legs:[{r:"P",q:1,k:{d:0.25}},{r:"C",q:1,k:{d:0.25}}]},

 // --- due scadenze ---
 {c:"Due scadenze", n:"Calendar put", b:"neu", p:"limitato", l:"limitata", exp:2,
  d:"Vendi la scadenza vicina e compri la lontana allo stesso strike. Guadagna col passare del tempo e con la volatilita in salita. E' lunga vega: se la IV crolla perde anche col prezzo fermo.",
  legs:[{r:"P",q:-1,k:"atm",e:1},{r:"P",q:1,k:"atm",e:2}]},
 {c:"Due scadenze", n:"Calendar call", b:"neu", p:"limitato", l:"limitata", exp:2,
  d:"Identica ma con le call. Si sceglie in base a dove sta lo skew.",
  legs:[{r:"C",q:-1,k:"atm",e:1},{r:"C",q:1,k:"atm",e:2}]},
 {c:"Due scadenze", n:"Diagonal call", b:"rial", p:"limitato", l:"limitata", exp:2,
  d:"Calendar con strike diversi: la lunga piu bassa della corta. Unisce il tempo alla direzione.",
  legs:[{r:"C",q:-1,k:{d:0.30},e:1},{r:"C",q:1,k:{d:0.60},e:2}]},
 {c:"Due scadenze", n:"Diagonal put", b:"rib", p:"limitato", l:"limitata", exp:2,
  d:"Speculare della diagonal call, orientata al ribasso.",
  legs:[{r:"P",q:-1,k:{d:0.30},e:1},{r:"P",q:1,k:{d:0.60},e:2}]},
 {c:"Due scadenze", n:"Double calendar", b:"neu", p:"limitato", l:"limitata", exp:2,
  d:"Due calendar, uno sopra e uno sotto il prezzo. Zona di profitto piu larga di un calendar singolo.",
  legs:[{r:"P",q:-1,k:{d:0.30},e:1},{r:"P",q:1,k:{d:0.30},e:2},{r:"C",q:-1,k:{d:0.30},e:1},{r:"C",q:1,k:{d:0.30},e:2}]},

 // --- rapporto ---
 {c:"Rapporto", n:"ZEBRA (2:1)", b:"rial", p:"illimitato", l:"limitata", exp:1,
  d:"Due call ITM comprate contro una ATM venduta: replica l'azione con circa 100 delta per unita, senza valore temporale netto. E' la struttura usata per sostituire l'azione.",
  legs:[{r:"C",q:2,k:{d:0.70}},{r:"C",q:-1,k:{d:0.50}}]},
 {c:"Rapporto", n:"Call ratio spread", b:"rial", p:"limitato", l:"illimitata", exp:1,
  d:"Compri una call e ne vendi due piu lontane. Incassi, ma sopra la seconda corta la perdita non ha tetto.",
  legs:[{r:"C",q:1,k:{d:0.50}},{r:"C",q:-2,k:{d:0.25}}]},
 {c:"Rapporto", n:"Put backspread", b:"rib", p:"molto grande", l:"limitata", exp:1,
  d:"Vendi una put vicina e ne compri due lontane. Guadagna molto su un crollo, perde poco se non succede nulla.",
  legs:[{r:"P",q:-1,k:{d:0.45}},{r:"P",q:2,k:{d:0.25}}]},
 {c:"Rapporto", n:"Jade lizard", b:"neu", p:"limitato", l:"molto grande", exp:1,
  d:"Put venduta piu bear call spread: se il credito totale supera la larghezza dello spread di call, al rialzo non puoi perdere. Il rischio resta al ribasso.",
  legs:[{r:"P",q:-1,k:{d:0.25}},{r:"C",q:-1,k:{d:0.25}},{r:"C",q:1,k:{d:0.25,off:+2}}]},
];
const BIAS = {rial:["rialzista","bull"], rib:["ribassista","bear"], neu:["neutrale","neu"], vol:["sul movimento","neu"]};

function chainInfo(ch){
  const ks=[...new Set(ch.rows.map(r=>r.strike))].sort((a,b)=>a-b);
  const spot=ch.spot;
  const atm=ks.reduce((b,k)=>Math.abs(k-spot)<Math.abs(b-spot)?k:b, ks[0]);
  return {ks, spot, atm, ai:ks.indexOf(atm), ch,
    row:(k,r)=>ch.rows.find(x=>x.strike===k&&x.right===r),
    byDelta:(r,d)=>{ const c=ch.rows.filter(x=>x.right===r&&x.delta!=null);
      return c.length ? c.reduce((b,x)=>Math.abs(Math.abs(x.delta)-d)<Math.abs(Math.abs(b.delta)-d)?x:b).strike : null; }};
}
function resolveK(spec, right, ci){
  if (spec === "atm") return ci.atm;
  let k = null;
  if (spec.d != null) k = ci.byDelta(right, spec.d);
  if (k == null) k = ci.ks[Math.max(0, Math.min(ci.ks.length-1, ci.ai + (spec.off||0)))];
  else if (spec.off) {
    const i = ci.ks.indexOf(k);
    k = ci.ks[Math.max(0, Math.min(ci.ks.length-1, i + spec.off))];
  }
  return k;
}

let WSEL = null;
function drawWiz(){
  const cats = [...new Set(STRAT.map(s=>s.c))];
  $('wiz').innerHTML = cats.map(c =>
    `<div><h4>${c}</h4>` + STRAT.filter(s=>s.c===c).map((s,i)=>
      `<button data-n="${s.n}" class="${WSEL===s.n?'on':''}"><span class="${BIAS[s.b][1]}">${s.n}</span></button>`
    ).join('') + '</div>').join('');
  $('wiz').querySelectorAll('button').forEach(b => b.onclick = ()=>{ WSEL = b.dataset.n; drawWiz(); });
  const st = STRAT.find(s=>s.n===WSEL);
  if (!st){ $('wizinfo').innerHTML=''; return; }
  const exps = UND ? UND.expiries.filter(e=>e.classes.includes(curTc) && e.date > (curExp||'')) : [];
  $('wizinfo').innerHTML = `
    <div style="border-top:1px solid var(--border); padding-top:11px">
      <b>${st.n}</b>
      <span class="badge ${st.b==='rial'?'ok':st.b==='rib'?'ko':''}" style="margin-left:7px">${BIAS[st.b][0]}</span>
      <span class="badge ok">profitto ${st.p}</span>
      <span class="badge ${st.l.includes('limitata')&&!st.l.includes('il')?'ok':'ko'}">perdita ${st.l}</span>
      <div class="note" style="margin:7px 0 9px">${st.d}</div>
      ${st.exp===2 ? `<div class="row" style="margin-bottom:9px"><div class="field">
          <label for="exp2">Scadenza lunga</label>
          <select id="exp2" style="width:150px">${exps.slice(0,14).map(e=>
            `<option value="${e.date}">${e.date.slice(6,8)}/${e.date.slice(4,6)} (${e.dte}g)</option>`).join('')}</select>
        </div></div>` : ''}
      <button class="primary" id="wizgo">Costruisci sulla catena</button>
      <span id="wizmsg" class="msg"></span>
    </div>`;
  const go = $('wizgo'); if (go) go.onclick = ()=> buildStrat(st);
}

async function buildStrat(st){
  if (!CHAIN){ $('wizmsg').textContent = 'Carica prima un sottostante e una scadenza.'; return; }
  let ch2 = null;
  if (st.exp === 2){
    const e2 = $('exp2') && $('exp2').value;
    if (!e2){ $('wizmsg').textContent = 'Non ci sono scadenze piu lontane di questa.'; return; }
    $('wizmsg').textContent = 'chiedo la seconda scadenza a TWS...';
    try {
      ch2 = await get(`/api/chain?sym=${encodeURIComponent(UND.symbol)}&exp=${e2}&tc=${encodeURIComponent(curTc)}&w=${Math.max(1,+$('w').value)/100}`);
    } catch(e){ $('wizmsg').textContent = 'Errore sulla seconda scadenza: '+e.message; return; }
  }
  const ci1 = chainInfo(CHAIN), ci2 = ch2 ? chainInfo(ch2) : null;
  const out = [], mancanti = [];
  for (const L of st.legs){
    const ci = (L.e === 2) ? ci2 : ci1;
    const k = resolveK(L.k, L.r, ci);
    const row = ci.row(k, L.r);
    // si compra alla lettera e si vende al denaro: il prezzo che porti dentro
    // e' quello vero, spread incluso
    const px = row ? (L.q > 0 ? row.ask : row.bid) : null;
    if (px == null){ mancanti.push(`${L.r}${fmtK(k)}`); continue; }
    out.push({expiry: ci.ch.expiry, strike: k, right: L.r, qty: L.q, price: px,
              iv: (row && row.iv) || 0.2, mult: ci.ch.mult});
  }
  if (!out.length){ $('wizmsg').textContent = 'Nessuna gamba quotata: mercato chiuso o catena vuota.'; return; }
  LEGS = out;
  $('wizmsg').innerHTML = `${out.length} gambe inserite`
    + (mancanti.length ? ` &middot; <b style="color:var(--neg)">saltate per mancanza di prezzo: ${mancanti.join(', ')}</b>` : '');
  render();
}

/* ---------- Monte Carlo sulla strategia ---------- */
/* Simula il sottostante con moto browniano geometrico e rivaluta la posizione a ogni passo.
   Rispetto alla probabilita analitica gia in alto aggiunge cio che dipende dal PERCORSO e
   non solo dal punto d'arrivo: quanto si soffre in mezzo, quante volte uno strike viene
   toccato, com'e fatta la distribuzione del risultato. */
const pctP = x => (Math.min(0.9999, Math.max(0.0001, x))*100).toFixed(1)+'%';

function mcRun(legs, S0, n){
  const H = horizon(legs);                        // giorni alla PRIMA scadenza
  if (!legs.length || H <= 0 || !S0) return null;
  const steps = Math.max(1, Math.min(400, Math.round(H)));
  const dtY = (H/365)/steps;
  // Volatilita del SOTTOSTANTE: una sola. Gli strike hanno IV diverse per via dello skew,
  // ma il sottostante ha un solo processo: usarne una per gamba sarebbe incoerente.
  const nearest = legs.reduce((b,l)=> Math.abs(l.strike-S0) < Math.abs(b.strike-S0) ? l : b, legs[0]);
  const ivU = ($('mcvol').value==='avg'
      ? legs.reduce((s,l)=>s+(l.iv||0.2),0)/legs.length
      : (nearest.iv || 0.2)) * ivMult();
  const drift = (R - ivU*ivU/2)*dtY, vol = ivU*Math.sqrt(dtY);
  // costanti per gamba calcolate una volta sola: dentro il ciclo non si parsano date
  const L = legs.map(l => ({K:l.strike, cp:l.right, q:l.qty*l.mult,
                            T0: dte(l.expiry)/365, iv:(l.iv||0.2)*ivMult(),
                            atH: Math.abs(dte(l.expiry)-H) < 0.5}));
  const cost = legs.reduce((s,l)=>s+l.cost,0);
  const finals = new Float64Array(n), mins = new Float64Array(n);
  const touch = new Int32Array(L.length), itm = new Int32Array(L.length);
  const flag = new Uint8Array(L.length);
  // "va sotto almeno una volta" direbbe solo che il prezzo si muove: per una posizione che
  // parte a zero e quasi sempre il 100%. Serve una soglia che conti per il sizing: quante
  // volte si arriva a meta della perdita massima.
  const sm = summary(legs);
  const half = (sm && sm.min !== null && sm.min < 0) ? sm.min/2 : null;
  let deep = 0;
  for (let p=0; p<n; p++){
    let S = S0, mn = Infinity, last = 0;
    flag.fill(0);
    for (let t=1; t<=steps; t++){
      let u = Math.random(); if (u <= 0) u = 1e-12;
      const z = Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*Math.random());
      S *= Math.exp(drift + vol*z);
      const el = (t/steps)*H/365;
      let val = 0;
      for (let i=0;i<L.length;i++){
        const g = L[i];
        val += g.q * bs(S, g.K, Math.max(0, g.T0-el), g.iv, g.cp);
        if (!flag[i] && (g.cp==='C' ? S>=g.K : S<=g.K)){ flag[i]=1; touch[i]++; }
      }
      last = val - cost;
      if (last < mn) mn = last;
    }
    finals[p] = last; mins[p] = mn;
    if (half !== null && mn <= half) deep++;
    for (let i=0;i<L.length;i++)
      if (L[i].atH && (L[i].cp==='C' ? S>L[i].K : S<L[i].K)) itm[i]++;
  }
  const srt = a => Float64Array.from(a).sort((x,y)=>x-y);
  const pq  = (a,q) => a[Math.min(a.length-1, Math.max(0, Math.round((a.length-1)*q)))];
  const F = srt(finals), M = srt(mins);
  let win = 0, sum = 0;
  for (let i=0;i<n;i++){ if (finals[i] >= 0) win++; sum += finals[i]; }
  return { n, steps, ivU, H, pWin: win/n, ev: sum/n,
           p: [0.05,0.25,0.5,0.75,0.95].map(q=>pq(F,q)),
           ddMed: pq(M,0.5), dd25: pq(M,0.25), dd05: pq(M,0.05),
           half, pDeep: half===null ? null : deep/n,
           legs: L.map((g,i)=>({K:g.K, cp:g.cp, q:g.q, atH:g.atH,
                                pTouch: touch[i]/n, pItm: g.atH ? itm[i]/n : null})) };
}

function drawMc(){
  // il prezzo si prende dalla catena come fa tutto il resto della pagina
  const legs = allLegs(), box = $('mcout');
  const S0 = (CHAIN && CHAIN.spot) || (UND && UND.spot) || 0;
  if (!legs.length){ box.innerHTML = '<div class="msg">Aggiungi almeno una gamba.</div>'; $('mcstat').textContent=''; return; }
  $('mcstat').textContent = 'calcolo...';
  // un tick al browser, cosi il messaggio compare prima del blocco di calcolo
  setTimeout(()=>{
    const t0 = performance.now();
    const r = mcRun(legs, S0, +$('mcn').value);
    if (!r){ box.innerHTML = '<div class="msg err">Niente da simulare: la prima scadenza e oggi, oppure manca il prezzo.</div>';
             $('mcstat').textContent=''; return; }
    const ms = Math.round(performance.now()-t0);
    $('mcstat').textContent = `${r.n.toLocaleString('it-IT')} percorsi, ${r.steps} passi giornalieri, `
      + `volatilita ${pctP(r.ivU)}` + (ms>0 ? ` (${ms} ms)` : '');
    box.innerHTML = `
      <div class="tiles" style="margin-bottom:12px">
        <div class="tile"><div class="label">Profitto a scadenza</div><div class="value">${pctP(r.pWin)}</div>
          <div class="sub2">su ${r.H.toFixed(0)} giorni</div></div>
        <div class="tile"><div class="label">Valore atteso</div><div class="value ${r.ev>=0?'pos':'neg'}">${money(r.ev)}</div>
          <div class="sub2">media dei percorsi</div></div>
        <div class="tile"><div class="label">Arriva a meta perdita massima</div>
          <div class="value">${r.pDeep===null?'&mdash;':pctP(r.pDeep)}</div>
          <div class="sub2">${r.half===null?'perdita non limitata':'sotto '+money(r.half)+' almeno una volta'}</div></div>
        <div class="tile"><div class="label">Peggior momento</div><div class="value neg">${money(r.ddMed)}</div>
          <div class="sub2">mediana &middot; 25&deg; ${money(r.dd25)} &middot; 5&deg; ${money(r.dd05)}</div></div>
      </div>
      <table><tr><th style="text-align:left">Risultato a scadenza</th><th>5&deg;</th><th>25&deg;</th><th>mediana</th><th>75&deg;</th><th>95&deg;</th></tr>
        <tr><td style="text-align:left">percentile</td>${r.p.map(v=>`<td class="${v>=0?'pos':'neg'}">${money(v)}</td>`).join('')}</tr></table>
      <table style="margin-top:12px"><tr><th style="text-align:left">Gamba</th><th>Tocca lo strike</th><th>Finisce ITM</th></tr>`
      + r.legs.map(g=>`<tr><td style="text-align:left">${g.cp==='C'?'Call':'Put'} ${fmtK(g.K)} ${g.q>0?'comprata':'venduta'}</td>`
        + `<td>${pctP(g.pTouch)}</td>`
        + `<td>${g.pItm===null?'<span style="color:var(--muted)">scade dopo</span>':pctP(g.pItm)}</td></tr>`).join('')
      + '</table>';
  }, 30);
}

/* ---------- aiuto ---------- */
const HELP = {
chain: {t:'La catena', h:`<dl>
<dt>Come si usa</dt><dd>Scegli la scadenza fra i chip, aspetta che TWS risponda, poi <b>clicca un prezzo</b>: sul <b>bid</b> aggiungi una gamba <b>venduta</b>, sull'<b>ask</b> una <b>comprata</b>. Cliccando piu volte lo stesso prezzo aumenti la quantita; cliccando il lato opposto la riduci fino ad annullarla.</dd>
<dt>Cosa guardare</dt><dd>Il prezzo che ti porti nella strategia e quello <b>vero</b> del tuo book, non un mid teorico: se compri all'ask e vendi al bid stai gia contando lo spread. La colonna IV dice quanto e cara quella gamba, il delta quanto ti espone.</dd>
<dt class="care">Attenzione</dt><dd class="care">Una riga con bid e ask assenti e una catena senza quotazioni: mercato chiuso, oppure dati non abilitati per quel prodotto. Non usarla, non e un prezzo a zero.</dd></dl>`},
legs: {t:'Gambe in progetto', h:`<dl>
<dt>Come si usa</dt><dd>Quantita positiva = comprata, negativa = venduta. Puoi correggere a mano sia la quantita sia il prezzo, per esempio per simulare un riempimento a meta spread invece che a mercato.</dd>
<dt>Il portafoglio</dt><dd>Se hai gia posizioni sullo stesso sottostante, l'interruttore le somma a quello che stai progettando. Da quel momento tutto quello che leggi sopra e <b>il libro completo</b>: massimo profitto, massima perdita, pareggi e greche sono quelli che avresti davvero dopo aver eseguito.</dd>
<dt class="care">Attenzione</dt><dd class="care">Le gambe in portafoglio entrano nel calcolo con il loro <b>prezzo di carico</b>, non con quello di mercato: il P/L che vedi e quello complessivo dall'apertura, non da adesso.</dd></dl>`},
mc: {t:'Analisi statistica', h:`<dl>
<dt>A cosa serve</dt><dd>La probabilita che vedi nelle caselle in alto e analitica e dice una cosa sola: dove finisce il prezzo a scadenza. Qui si simulano migliaia di <b>percorsi</b> interi, e questo permette di misurare cio che al punto d'arrivo non si vede.</dd>
<dt>Cosa guardare per primo</dt><dd><b>"Va sotto almeno una volta"</b> e <b>"peggior momento"</b>. Una strategia puo avere il 90% di probabilita di finire in profitto e passare comunque quasi sempre da un rosso profondo: sono due cose diverse, e la seconda e quella che ti fa chiudere in perdita per nervi. Il 5&deg; percentile del peggior momento e il numero su cui tarare il sizing.</dd>
<dt>Valore atteso</dt><dd>Media del risultato su tutti i percorsi. Con la volatilita implicita come input tende a stare vicino a zero: e giusto che sia cosi, perche la IV e il prezzo che il mercato fa al rischio. Se esce molto positivo stai scommettendo che la volatilita reale sara piu bassa di quella implicita &mdash; sappilo, invece di scoprirlo dopo.</dd>
<dt>Tocca lo strike / finisce ITM</dt><dd>Per chi vende sono due numeri diversi e contano entrambi: la probabilita di <b>toccare</b> lo strike durante la vita e all'incirca il doppio di quella di finirci dentro. Il primo dice quanto spesso dovrai decidere se aggiustare, il secondo quanto spesso verrai assegnato.</dd>
<dt class="care">Attenzione</dt><dd class="care">Il modello e un moto browniano geometrico con volatilita <b>costante</b>: niente salti, niente aumento di volatilita nei crolli, niente skew lungo il percorso. Sottostima proprio gli scenari che fanno male a chi vende. Prendilo come una misura del comportamento normale, non come una stima del rischio di coda.</dd></dl>`},

wiz: {t:'Strategie', h:`<dl>
<dt>Come funziona</dt><dd>Scegli una struttura dall'elenco: le gambe vengono messe sulla <b>catena vera</b> gia caricata, prendendo il prezzo all'<b>ask se compra</b> e al <b>bid se vende</b>. Quello che vedi e' quindi gia al netto dello spread, non un mid teorico.</dd>
<dt>Come sceglie gli strike</dt><dd>Per <b>delta</b>, non per distanza percentuale. Una corta "a 20 delta" resta a 20 delta che il sottostante sia XSP a 767 o un titolo a 38: e' il modo in cui il rischio si confronta fra prodotti diversi. Le ali si contano in passi sulla griglia reale degli strike di quella scadenza.</dd>
<dt>Due scadenze</dt><dd>Calendar, diagonal e double calendar chiedono una seconda scadenza: compare un menu con quelle piu lontane di quella caricata, e la catena lunga viene scaricata al momento.</dd>
<dt>Cosa fare dopo</dt><dd>La struttura e un <b>punto di partenza</b>, non una proposta: quantita, strike e prezzi restano modificabili. L'analisi statistica e la barra del pareggio si aggiornano da sole.</dd>
<dt class="care">Attenzione</dt><dd class="care">L'elenco comprende anche strutture a perdita non definita (call venduta scoperta, strangle e straddle venduti, call ratio). Sono li per completezza, non come suggerimento: il margine e il rischio di coda vanno guardati prima, non dopo.</dd></dl>`},

payoff: {t:'Profitto e perdita', h:`<dl>
<dt>Come si legge</dt><dd>Asse orizzontale il prezzo del sottostante, verticale il risultato in denaro. Tre curve: <b>continua</b> adesso, <b>tratteggiata</b> alla data dello slider, <b>punteggiata</b> alla prima scadenza. Con scadenze miste la punteggiata e la <b>prima</b> scadenza: le gambe piu lunghe sono ancora vive e vengono rivalutate con Black-Scholes, non date per scadute. Verde sopra lo zero, rosso sotto. La linea verticale e il prezzo di adesso, i trattini in basso sono i tuoi strike.</dd>
<dt>Cosa guardare</dt><dd>La <b>distanza fra la curva di adesso e quella a scadenza</b>: e tutto il valore temporale che devi ancora incassare o pagare. Muovi lo slider della data e guarda come si avvicinano: e il theta che lavora, e ti dice se la posizione ha bisogno di tempo o di movimento.</dd>
<dt>La barra del pareggio</dt><dd>La barra verticale azzurra e il <b>pareggio alla data dello slider</b>, non a scadenza: e il prezzo a cui, in quel giorno, chiuderesti in pari. Muovi lo slider della data e guardala scivolare verso i pareggi finali: quel movimento e il valore temporale che si consuma, e dice quanto margine di errore hai <i>adesso</i> rispetto a quello che avrai alla fine.</dd>
<dt>La scheda Tabella</dt><dd>Le stesse informazioni lette al contrario: il <b>prezzo sulle righe</b>, il <b>tempo sulle colonne</b>, il risultato nella cella, colorato per intensita. Risponde alla domanda che sul grafico si legge male: "se fra due settimane sta a quel livello, quanto sto perdendo?". La riga bordata di azzurro e il prezzo di adesso.</dd>
<dt>Lo slider della volatilita</dt><dd>Moltiplica la IV di tutte le gambe. Portalo a 1,5 e guarda cosa succede alla curva di adesso: e la prova di stress piu onesta che puoi fare su una posizione venduta, ed e esattamente quello che e successo il 5 agosto 2024.</dd>
<dt class="care">Sugli estremi</dt><dd class="care">Massimo profitto e massima perdita sono cercati su tutto l'intervallo di prezzo, <b>da zero in su</b>. Se hai put di coda comprate vedrai un massimo profitto enorme: e vero, ma si realizza solo in un crollo che porta il sottostante vicino allo zero. Guarda dove cade il massimo, non solo quanto vale.</dd>
<dt class="care">Attenzione</dt><dd class="care">Il modello assume volatilita costante per gamba e tasso fisso al 2%. Serve a confrontare strutture fra loro, non a prevedere il prezzo di domani.</dd></dl>`},
};
function closePop(){ pop.classList.remove('open'); pop.dataset.open='';
  document.querySelectorAll('.help').forEach(b=>b.setAttribute('aria-expanded','false')); }
function placePop(btn){ const card = btn.closest('.card');
  pop.style.left = Math.max(8, Math.min(btn.offsetLeft, card.clientWidth - pop.offsetWidth - 8))+'px';
  pop.style.top  = (btn.offsetTop + btn.offsetHeight + 8)+'px'; }
document.addEventListener('click', e=>{
  const b = e.target.closest('.help');
  if (b){ const k=b.dataset.help;
    if (pop.dataset.open===k){ closePop(); return; }
    closePop(); const H=HELP[k]; if(!H) return;
    pop.innerHTML = `<button class="close" aria-label="Chiudi">&times;</button><h3>${H.t}</h3>${H.h}`;
    pop.classList.add('open'); pop.dataset.open=k; b.setAttribute('aria-expanded','true');
    b.closest('.card').appendChild(pop); placePop(b); return; }
  if (!e.target.closest('#pop') || e.target.closest('.close')) closePop();
});
document.addEventListener('keydown', e=>{ if(e.key==='Escape') closePop(); });
window.addEventListener('resize', ()=>{ const b=document.querySelector('.help[aria-expanded=true]'); if(b) placePop(b); });

/* ---------- avvio ---------- */
$('load').onclick = loadUnderlying;
$('sym').addEventListener('keydown', e=>{ if(e.key==='Enter') loadUnderlying(); });
$('refresh').onclick = ()=> curExp && loadChain(curExp);
// lo slider aggiorna l'etichetta mentre lo trascini, ma ricarica la catena solo quando lo
// lasci: a ogni richiesta TWS deve quotare decine di contratti, non va chiamato in continuo.
$('w').addEventListener('input', ()=> $('l-w').innerHTML = '&plusmn;'+$('w').value+'%');
$('w').addEventListener('change', ()=> curExp && loadChain(curExp));
// cambiare classe non richiede di riscaricare il sottostante: le scadenze le abbiamo gia
$('tc').onchange = ()=>{ curTc=$('tc').value; curExp=null; drawExps(); };
$('kinds').onchange = drawExps;
$('showam').onchange = drawExps;
['s-date','s-range','s-iv'].forEach(id=> $(id).addEventListener('input', render));
let _rz; window.addEventListener('resize', ()=>{ clearTimeout(_rz); _rz = setTimeout(()=>{ if (VISTA==='grafico') render(); }, 120); });
$('t-graf').onclick = ()=> setVista('grafico');
$('t-tab').onclick  = ()=> setVista('tabella');
$('mcgo').onclick = drawMc;
// il Monte Carlo non si rilancia da solo a ogni modifica: costa, e si chiede quando serve
['mcn','mcvol'].forEach(id=> $(id).addEventListener('change', drawMc));
loadSymbols(); drawWiz();
const Q = new URLSearchParams(location.search);
const DEAL = Q.get('deal'), ADD = Q.get('add');
if (DEAL) loadDeal(DEAL).then(()=>{ if (ADD) addProposte(ADD); }); else render();
</script>
</body></html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
