"""Rendering delle occasioni di mercato per la dashboard (usato dal server locale).

Legge il job di ricerca piu' recente (data/occasioni/<job>.json, scritto a flusso da
scan_ticker.py) oppure, se non ce ne sono, l'ultima scansione programmata (opportunita.json);
applica i filtri (gia' implementate, valore atteso positivo, tetto 2% NAV) e la concentrazione
usando data/stato_portafoglio.json, che la dashboard riscrive a ogni generazione.
Tutti gli importi mostrati sono in EUR al cambio dell'ultima generazione; il limite d'ordine
nelle istruzioni copiabili resta nella valuta del contratto.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import os
import re

from ib_common import DATA

DIR = DATA / "occasioni"
STATO = DATA / "stato_portafoglio.json"
OPP_FILE = DATA / "opportunita.json"
CONC_SOGLIA = 0.02          # perdita massima aggregata per (sottostante, mese) in % NAV
RISCHIO_MAX_NAV = 0.02      # non si propone cio' che rischia piu' del 2% del NAV
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre"]


def mese_etichetta(k):
    return f"{MESI[int(k[4:6]) - 1].capitalize()} {k[:4]}"


def carica_stato():
    try:
        return json.loads(STATO.read_text())
    except Exception:
        return {"nav": 0.0, "fx": {"EUR": 1.0}, "conc": {}, "posizioni": []}


def eur(v, cur, fx):
    r = fx.get(cur or "EUR") or 1.0
    return v / r


def gia_implementata(o, posizioni):
    """Tutte le gambe (sottostante, scadenza, strike, tipo, VERSO) dentro un unico gruppo aperto."""
    legs = set()
    for m in re.finditer(r"(VENDI|COMPRA)\s+([\d.]+)\s+([CP])\s+([\d.]+)\s+(\d{8})", o.get("ordine", "")):
        legs.add((o["sym"], m.group(5), float(m.group(4)), m.group(3), -1 if m.group(1) == "VENDI" else 1))
    if not legs:
        return False
    for g in posizioni:
        if legs <= {(l[0], l[1], float(l[2]), l[3], l[4]) for l in g}:
            return True
    return False


def _job_corrente(job):
    DIR.mkdir(exist_ok=True)
    if job and job != "last":
        f = DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '', job)}.json"
        return f if f.exists() else None
    fs = sorted(DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return fs[-1] if fs else None


def carica_job(job="last"):
    """Il job (dict) o, in mancanza, l'ultima scansione programmata presentata come job."""
    f = _job_corrente(job)
    if f:
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    try:
        d = json.loads(OPP_FILE.read_text())
        return dict(job="programmata", tickers=sorted({o["sym"] for o in d.get("occasioni", [])}), fase="finito",
                    messaggio="ultima scansione programmata (16:00)", aggiornato=d.get("generato"),
                    righe=d.get("occasioni", []), errori=[], info={})
    except Exception:
        return dict(job=None, tickers=[], fase="vuoto", messaggio="nessuna ricerca ancora fatta",
                    aggiornato=None, righe=[], errori=[], info={})


PIANO = dict(margine_min=2.0, spread_max=10.0, oi_min=100)
FAM_CALENDAR = ("calendar", "double calendar", "double diagonal")


def guadagno_protezioni(o, st, dm=-0.20):
    """Guadagno delle protezioni di coda sullo STESSO sottostante se cala di `dm` (valore intrinseco
    alla scadenza dello scenario meno il valore di oggi: stima prudente, trascura il valore temporale)."""
    spot = o.get("spot") or 0.0
    s2 = spot * (1 + dm)
    tot = 0.0
    for p in st.get("protezioni", []):
        if p["sym"] != o["sym"]:
            continue
        intr = max(p["strike"] - s2, 0.0) if p["right"] == "P" else max(s2 - p["strike"], 0.0)
        tot += p["qty"] * p["mult"] * (intr - p["prezzo_ora"])
    return tot


def rischio_eff(o, st):
    """Rischio con cui si misura il tetto del 2% NAV. Per i put ratio 1x2 (perdita massima a
    sottostante zero, enorme) e' la perdita se il sottostante cala del 20%, AL NETTO delle protezioni
    di coda sullo stesso sottostante; per tutte le altre strutture e' la perdita massima."""
    if o["tipo"].startswith("ratio put") and o.get("pl20") is not None:
        return max(-o["pl20"] - guadagno_protezioni(o, st), 0.0)
    return o["rischio"]


def filtra(righe, st, rigidi=True, ordine="er"):
    """(visibili ordinate, motivi di esclusione). rigidi=True applica i filtri del Piano: margine di
    volatilita' >= +2 punti (dove calcolato), spread <= 10%, open interest >= 100 (se ignoto, escluso),
    niente utili prima della scadenza per le strutture a credito."""
    nav, fx = st.get("nav") or 0.0, st.get("fx") or {"EUR": 1.0}
    vis = []
    nasc = {"implementate": 0, "oltre_tetto": 0, "ev_non_positivo": 0, "sotto_soglia_piano": 0, "utili": 0}
    for o in righe:
        if (o.get("ev_rv") or 0) <= 0:
            nasc["ev_non_positivo"] += 1
            continue
        if gia_implementata(o, st.get("posizioni", [])):
            nasc["implementate"] += 1
            continue
        r_eff = rischio_eff(o, st)
        if nav and r_eff / (fx.get(o.get("cur") or "EUR") or 1.0) > RISCHIO_MAX_NAV * nav:
            nasc["oltre_tetto"] += 1
            continue
        if rigidi:
            if o.get("utili") and not o["tipo"].startswith(FAM_CALENDAR) and o.get("lato") != "debito":
                nasc["utili"] += 1
                continue
            marg, spr, oi = o.get("margine"), o.get("spread"), o.get("oi_min")
            if (marg is not None and marg < PIANO["margine_min"]) or (spr is not None and spr > PIANO["spread_max"]) \
                    or oi is None or oi < PIANO["oi_min"]:
                nasc["sotto_soglia_piano"] += 1
                continue
        o["_r_eff"] = r_eff
        vis.append(o)
    if ordine == "ev":
        vis.sort(key=lambda o: -o["ev_rv"])
    else:                                          # valore atteso per unita' di rischio
        vis.sort(key=lambda o: -(o["ev_rv"] / max(o["_r_eff"], o["rischio"] * 0.05, 1.0)))
    return vis, nasc


def li_html(o, st, quando):
    nav, fx, conc = st.get("nav") or 0.0, st.get("fx") or {"EUR": 1.0}, st.get("conc") or {}
    cur = o.get("cur", "")
    E = lambda v: eur(v, cur, fx)
    oid = "o" + re.sub(r"[^A-Za-z0-9_]", "_", str(o["id"]))
    debito = o.get("lato") == "debito"
    imp = o.get("importo") if o.get("importo") is not None else o["credito"]
    prof = o.get("profitto") if o.get("profitto") is not None else o["credito"]
    if o["tipo"] in ("iron condor", "iron butterfly"):
        nome = f"{o['sym']} {o['tipo']} {o['ks']:g}-{o['kl']:g} (ali {o['w']:g})"
    elif o["tipo"].startswith("calendar"):
        nome = f"{o['sym']} {o['tipo']} {o['ks']:g}"
    else:
        nome = f"{o['sym']} {o['tipo']} {o['ks']:g}/{o['kl']:g}"
    scad = f"{o['exp'][6:8]}/{o['exp'][4:6]}/{o['exp'][:4]}"
    conc_txt, conc_alta = None, False
    if nav and fx.get(cur):
        gia = (conc.get(f"{o['sym']}|{o['exp'][:6]}") or {}).get("eur", 0.0)
        dopo = gia + E(o["rischio"])
        conc_alta = dopo / nav > CONC_SOGLIA
        conc_txt = (f"{o['sym']} {mese_etichetta(o['exp'][:6])}: oggi {gia / nav * 100:.2f}% del NAV, "
                    f"con questa {dopo / nav * 100:.2f}% (soglia {CONC_SOGLIA * 100:.0f}%)")
    istr = "\n".join([
        f"APRI {o['id']} — {nome}",
        f"Scadenza:  {scad} ({o['dte']} giorni)",
        f"Ordine:    {o['ordine']}",
        (f"           limite: DEBITO complessivo <= {imp:,.0f} {cur} (circa {E(imp):,.0f} EUR)" if debito else
         f"           limite: credito complessivo >= {imp:,.0f} {cur} (circa {E(imp):,.0f} EUR)"),
        f"Rischio massimo: {E(o['rischio']):,.0f} EUR | Profitto massimo: {E(prof):,.0f} EUR"
        + (" (stimato, calendar)" if o["tipo"].startswith("calendar") else ""),
        f"Probabilita' di chiudere in utile: {o['prob'] * 100:.0f}%",
        *([f"Forward factor: {o['ff']:+.2f} (Piano: nuove calendar solo con >= +0,20)"] if o.get("ff") is not None else []),
        "Open interest gamba piu' sottile: " + (f"{o['oi_min']:,.0f}" if o.get("oi_min") is not None else "n/d") + " (Piano: >= 100)",
        f"Valore atteso prudente: {E(o['ev_rv']):+,.0f} EUR"
        + (f" (volatilita' prevista {E(o['ev_vol']):+,.0f}, storico {E(o['ev_st']):+,.0f})"
           if o.get("ev_vol") is not None and o.get("ev_st") is not None else "")
        + (f" | RV di pareggio {o['rv0'] * 100:.1f}% contro realizzata, margine {o['margine']:+.1f} punti"
           if o.get("rv0") and o.get("margine") is not None else ""),
        *([f"Concentrazione: {conc_txt}" + (" — SOPRA LA SOGLIA" if conc_alta else "")] if conc_txt else []),
        f"Calcolato: {quando} con {o['sym']} a {o['spot']:,.2f}",
    ])
    det = [
        "<table class='ba'><tr><th>&nbsp;</th><th class='num'>&nbsp;</th></tr>"
        f"<tr><td>Scadenza</td><td class='num'>{scad} &middot; {o['dte']} giorni</td></tr>"
        + (f"<tr><td>Debito pagato</td><td class='num neg'>{E(-imp):+,.0f} EUR</td></tr>"
           f"<tr><td>Profitto massimo</td><td class='num pos'>{E(prof):+,.0f} EUR</td></tr>" if debito else
           f"<tr><td>Credito incassato</td><td class='num pos'>{E(imp):+,.0f} EUR</td></tr>"
           f"<tr><td>Profitto massimo</td><td class='num pos'>{E(prof):+,.0f} EUR</td></tr>")
        + f"<tr><td>Rischio massimo</td><td class='num neg'>{E(-o['rischio']):+,.0f} EUR</td></tr>"
        f"<tr><td>Profitto massimo in % del rischio</td><td class='num'>{(o.get('cr') or 0):.0f}%</td></tr>"
        f"<tr><td>Probabilit&agrave; di chiudere in utile</td><td class='num'><b>{o['prob'] * 100:.0f}%</b></td></tr>"
        f"<tr><td>Valore atteso prudente (il minore dei due)</td><td class='num'><b>{E(o['ev_rv']):+,.0f} EUR</b></td></tr>"
        + (f"<tr><td>&nbsp;&nbsp;con la volatilit&agrave; prevista (HAR)</td><td class='num'>{E(o['ev_vol']):+,.0f} EUR</td></tr>"
           if o.get("ev_vol") is not None else "")
        + (f"<tr><td>&nbsp;&nbsp;sulla distribuzione storica (bootstrap, crisi incluse)</td><td class='num'>{E(o['ev_st']):+,.0f} EUR &middot; utile nel {o['prob_st'] * 100:.0f}% dei casi</td></tr>"
           if o.get("ev_st") is not None else "")
        + (f"<tr><td>Caso storico sfavorevole (5&deg; percentile)</td><td class='num neg'>{E(o['pl5']):+,.0f} EUR</td></tr>"
           if o.get("pl5") is not None else "")
        + (f"<tr><td>Se il sottostante cala del 20% a scadenza</td><td class='num {'pos' if o['pl20'] >= 0 else 'neg'}'>{E(o['pl20']):+,.0f} EUR</td></tr>"
           if o.get("pl20") is not None else "")
        + (f"<tr><td>Rischio per il tetto del 2% (&minus;20% al netto delle protezioni)</td><td class='num'>{E(o['_r_eff']):,.0f} EUR &middot; perdita teorica a sottostante zero {E(o['rischio']):,.0f} EUR</td></tr>"
           if o["tipo"].startswith("ratio put") and o.get("_r_eff") is not None else "")
        + (f"<tr><td>Utili</td><td class='num {'neg' if not o['tipo'].startswith(FAM_CALENDAR) else ''}'>il {o['utili']}, prima della scadenza</td></tr>"
           if o.get("utili") else "")
        + (f"<tr><td>Volatilit&agrave; di pareggio</td><td class='num'>{o['rv0'] * 100:.1f}%"
           f" &middot; margine {o['margine']:+.1f} punti</td></tr>" if o.get("rv0") and o.get("margine") is not None else "")
        + (f"<tr><td>Forward factor (Piano: &ge; +0,20)</td><td class='num {'neg' if o['ff'] < 0.2 else 'pos'}'>{o['ff']:+.2f}</td></tr>"
           if o.get("ff") is not None else "")
        + (f"<tr><td>Open interest, gamba pi&ugrave; sottile (Piano: &ge; 100)</td><td class='num {'neg' if o['oi_min'] < 100 else 'pos'}'>{o['oi_min']:,.0f}</td></tr>"
           if o.get("oi_min") is not None else "<tr><td>Open interest</td><td class='num'>n/d</td></tr>")
        + (f"<tr><td>Concentrazione dopo l'operazione</td><td class='num {'neg' if conc_alta else ''}'>{conc_txt}</td></tr>"
           if conc_txt else "")
        + (f"<tr><td>Spread denaro-lettera peggiore</td><td class='num'>{o['spread']:.0f}%</td></tr>"
           if o.get("spread") is not None else "")
        + "</table>",
        f"<code class='ordine'>{_html.escape(o['ordine'])}</code>",
        f"<div class='istrbox'><button class='actbtn copia' id='cp-{oid}' onclick='copiaIstr(&quot;{oid}&quot;)'>"
        "Copia istruzione per Claude</button>"
        f"<pre class='istr' id='istr-{oid}' hidden>{_html.escape(istr)}</pre>"
        "<span class='sub'>Il bottone copia soltanto: nessun ordine parte da qui.</span></div>",
    ]
    return (
        f"<li><div class='acthead'><span class='badge pronta'>EV {E(o['ev_rv']):+,.0f}</span>"
        f"<span class='what'>{_html.escape(nome)}</span>"
        f"<span class='who'>{o['prob'] * 100:.0f}% in utile &middot; rischio {E(o.get('_r_eff', o['rischio'])):,.0f} EUR "
        f"&middot; EV/rischio {o['ev_rv'] / max(o.get('_r_eff', o['rischio']), 1.0) * 100:+.1f}% &middot; {o['dte']} gg"
        + (f" &middot; FF {o['ff']:+.2f}" if o.get("ff") is not None else "")
        + (f" &middot; OI {o['oi_min']:,.0f}" if o.get("oi_min") is not None else "")
        + ("<b class='neg'> &middot; &#9888; concentrazione</b>" if conc_alta else "")
        + (f"<b class='neg'> &middot; &#9888; utili {o['utili']}</b>" if o.get("utili") and not o["tipo"].startswith(FAM_CALENDAR) else "")
        + (f"<b> &middot; utili {o['utili']}</b>" if o.get("utili") and o["tipo"].startswith(FAM_CALENDAR) else "")
        + "</span>"
        f"<button class='actbtn' id='actb-{oid}' onclick='toggleAct(&quot;{oid}&quot;)'>Dettaglio</button></div>"
        f"<div class='actdet' id='act-{oid}' hidden>{''.join(det)}</div></li>")


def pid_vivo(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def risposta(job="last", rigidi=True, ordine="er"):
    """Dizionario JSON per la pagina: stato della ricerca e HTML della lista, aggiornati a flusso."""
    st = carica_stato()
    j = carica_job(job)
    fase = j.get("fase")
    if fase == "scan" and j.get("pid") and not pid_vivo(j["pid"]):
        fase = "interrotta"                      # il processo e' morto senza chiudere il file
    vis, nasc = filtra(j.get("righe", []), st, rigidi, ordine)
    agg = j.get("aggiornato") or ""
    try:
        quando = dt.datetime.fromisoformat(agg).strftime("%d/%m/%Y %H:%M")
    except Exception:
        quando = agg
    return dict(job=j.get("job"), fase=fase, messaggio=j.get("messaggio"), tickers=j.get("tickers", []),
                aggiornato=quando, errori=j.get("errori", []), n_totali=len(j.get("righe", [])),
                n_mostrate=len(vis), nascoste=nasc, rigidi=rigidi, ordine=ordine,
                html="".join(li_html(o, st, quando) for o in vis[:60]))


# ---------------- screener del forward factor ----------------
DIR_FF = DATA / "occasioni_ff"


def risposta_ff(job="last"):
    """Tabella del forward factor, ordinata dal piu' alto; il job e' quello dello screener piu' recente."""
    DIR_FF.mkdir(exist_ok=True)
    fs = sorted(DIR_FF.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not fs:
        return dict(job=None, fase="vuoto", messaggio="nessuno screener ancora fatto", html="", n=0, errori=[], tickers=[])
    try:
        j = json.loads(fs[-1].read_text())
    except Exception:
        return dict(job=None, fase="errore", messaggio="file illeggibile", html="", n=0, errori=[], tickers=[])
    fase = j.get("fase")
    if fase == "scan" and j.get("pid") and not pid_vivo(j["pid"]):
        fase = "interrotta"
    righe = sorted(j.get("righe", []), key=lambda r: -r["ff"])[:60]
    gg = lambda e: f"{e[6:8]}/{e[4:6]}"
    corpo = "".join(
        f"<tr><td><b>{_html.escape(r['sym'])}</b></td><td>{gg(r['front'])} &rarr; {gg(r['back'])} <span class='sub'>({r['dte_f']}&rarr;{r['dte_b']} gg)</span></td>"
        f"<td class='num'>{r['iv_f'] * 100:.1f}% / {r['iv_b'] * 100:.1f}%</td>"
        f"<td class='num {'pos' if r['ff'] >= 0.2 else ('neg' if r['ff'] < 0 else '')}'><b>{r['ff']:+.2f}</b></td>"
        f"<td>{('utili ' + r['utili']) if r.get('utili') else ''}</td>"
        f"<td><button class='actbtn' onclick='cercaTicker(&quot;{_html.escape(r['sym'])}&quot;)'>Cerca occasioni</button></td></tr>"
        for r in righe)
    html = ("<table class='ba'><tr><th>Ticker</th><th>Calendar (corta &rarr; lunga)</th><th class='num'>IV corta / lunga</th>"
            "<th class='num'>Forward factor</th><th>Note</th><th></th></tr>" + corpo + "</table>") if righe else ""
    return dict(job=j.get("job"), fase=fase, messaggio=j.get("messaggio"), tickers=j.get("tickers", []),
                n=len(j.get("righe", [])), errori=j.get("errori", []), html=html, aggiornato=j.get("aggiornato"))


# ---------------- box spread (rendimento della liquidita') ----------------
DIR_BOX = DATA / "occasioni_box"


def risposta_box():
    DIR_BOX.mkdir(exist_ok=True)
    fs = sorted(DIR_BOX.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not fs:
        return dict(fase="vuoto", messaggio="nessuna ricerca di box ancora fatta", html="", n=0, errori=[])
    try:
        j = json.loads(fs[-1].read_text())
    except Exception:
        return dict(fase="errore", messaggio="file illeggibile", html="", n=0, errori=[])
    fase = j.get("fase")
    if fase == "scan" and j.get("pid") and not pid_vivo(j["pid"]):
        fase = "interrotta"
    st = carica_stato()
    fx = st.get("fx") or {"EUR": 1.0}
    righe = sorted(j.get("righe", []), key=lambda r: -r["rend"])[:30]
    gg = lambda e: f"{e[6:8]}/{e[4:6]}/{e[2:4]}"
    corpo = "".join(
        f"<tr><td><b>{r['sym']}</b></td><td>{gg(r['exp'])} <span class='sub'>({r['dte']} gg)</span></td>"
        f"<td class='num'>{r['k1']:g} &ndash; {r['k2']:g}</td>"
        f"<td class='num'>{eur(r['costo'], r['cur'], fx):,.0f} &rarr; {eur(r['pagamento'], r['cur'], fx):,.0f} EUR</td>"
        f"<td class='num pos' data-rend='{r['rend']:.5f}'><b>{r['rend'] * 100:.2f}%</b></td>"
        f"<td class='num'>{('%.0f%%' % r['spread']) if r.get('spread') is not None else 'n/d'}</td>"
        f"<td class='num'>{('%.0f' % r['oi_min']) if r.get('oi_min') is not None else 'n/d'}</td>"
        f"<td><code class='ordine'>{_html.escape(r['ordine'])}</code></td></tr>"
        for r in righe)
    html = ("<table class='ba'><tr><th>Indice</th><th>Scadenza</th><th class='num'>Strike</th><th class='num'>Costo &rarr; incasso</th>"
            "<th class='num'>Rendimento annuo</th><th class='num'>Spread</th><th class='num'>OI</th><th>Ordine (long box = prestito)</th></tr>"
            + corpo + "</table>"
            "<div class='sub'>Il box long paga sempre (K2&minus;K1): e' un prestito a tasso fisso su indici europei, nessuna assegnazione. "
            "Ha senso solo se il rendimento supera quello di T-bill/monetari a pari durata, al netto di commissioni e margine "
            "(da verificare su IBKR prima di usarlo per la liquidita'). Nessun benchmark e' assunto qui.</div>") if righe else ""
    return dict(fase=fase, messaggio=j.get("messaggio"), n=len(j.get("righe", [])), errori=j.get("errori", []), html=html)
