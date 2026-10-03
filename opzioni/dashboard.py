"""Dashboard HTML del portafoglio (SOLA LETTURA): raggruppa le posizioni secondo
`data/portfolio_map.json` (nome mnemonico per gruppo, es. "AAA - ZEBRA x2"),
mostra il P/L di ciascun gruppo (calcolato da TWS, non ricalcolato a mano), il
Vega per gruppo e totale, e una sintesi dell'ultimo stress test (rilanciato ora
via stress.py). Genera un file HTML locale da aprire nel browser. Qualunque
posizione aperta che non trova un gruppo nella mappa viene mostrata a parte
come "non mappata", mai ignorata in silenzio.

Uso: uv run dashboard.py
Apri poi: file://<percorso stampato in fondo>
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from pathlib import Path

from ib_common import DATA, ROOT, clean, connect
import html as _html
import transform
import actions
import deals
import pnl_history
from pnl_chart import chart_html, summary_html

MAP_FILE = DATA / "portfolio_map.json"
OUT_FILE = DATA / "dashboard.html"


def leg_key(c):
    """Chiave di match: sottostante, scadenza (None per azioni), strike, right."""
    expiry = getattr(c, "lastTradeDateOrContractMonth", "") or None
    strike = getattr(c, "strike", 0.0) or None
    right = getattr(c, "right", "") or None
    return (c.symbol, expiry, strike, right)


def load_map():
    if not MAP_FILE.exists():
        return {"groups": []}
    return json.loads(MAP_FILE.read_text())


# Tutti gli importi della pagina sono in EUR al cambio di TWS del momento della ricarica:
# render() riempie _FX. Fa eccezione solo cio' che deve restare nella valuta del contratto
# per poter essere eseguito (limite dell'ordine) e i prezzi unitari delle opzioni.
_FX = {"EUR": 1.0}


def eur(v, cur):
    """Importo in EUR al cambio di adesso (invariato se la valuta e' EUR o il cambio manca)."""
    if v is None or isinstance(v, str):
        return v
    r = _FX.get(cur or "EUR") or 1.0
    return v / r


_RE_USD = None


def eur_testo(html):
    """Converte in EUR ogni 'importo USD' del testo della pagina, al cambio di adesso, tranne dentro
    le istruzioni copiabili (<pre class='istr'>), dove il limite dell'ordine resta nella valuta del
    contratto, e dentro script/stile."""
    import re as _re
    r = _FX.get("USD")
    if not r:
        return html
    pat = _re.compile(r"([+\-\u2212]?)(\d[\d,]*(?:\.\d+)?)\s*(?:USD|\$)")

    def conv(m):
        v = float(m.group(2).replace(",", "")) / r
        return f"{m.group(1)}{v:,.0f} EUR"
    parti = _re.split(r"(<pre class='istr'.*?</pre>|<script.*?</script>|<style.*?</style>)", html, flags=_re.S)
    return "".join(p if i % 2 else pat.sub(conv, p) for i, p in enumerate(parti))


def fmt_money(x, cur):
    sign = "+" if x >= 0 else ""
    return f"{sign}{eur(x, cur):,.0f} EUR"


GREEKS = {}


def fetch_vegas(ib, contracts):
    """Vega e theta per contratto (sola lettura), stesso pattern di stress.py: una richiesta
    per tutti i contratti, un solo sleep, poi si legge modelGreeks."""
    vegas = {}
    tk = []
    for c in contracts:
        ib.qualifyContracts(c)
        t = ib.reqMktData(c, "", False, False)
        tk.append((c, t))
    ib.sleep(8)
    for c, t in tk:
        g = t.modelGreeks
        vegas[leg_key(c)] = g.vega if g and g.vega is not None else None
        GREEKS[leg_key(c)] = dict(iv=g.impliedVol if g else None, und=g.undPrice if g else None, delta=g.delta if g else None,
                                  theta=g.theta if g else None, bid=clean(t.bid), ask=clean(t.ask))
        ib.cancelMktData(c)
    return vegas


def fill_missing_iv(ib, rows):
    """Se TWS non da' le greche (mercato chiuso), IV ricavata invertendo Black-Scholes sul prezzo di mercato."""
    from ib_async import Stock, Index
    need = {r["symbol"] for r in rows if r["secType"] == "OPT" and (not r.get("iv") or not r.get("und"))}
    spots = {}
    for sym in need:
        und = Index(sym, "EUREX" if sym == "ESTX50" else "CBOE") if sym in ("ESTX50", "SPX", "XSP", "VIX") else Stock(sym, "SMART", "USD")
        try:
            ib.qualifyContracts(und); t = ib.reqMktData(und, "", False, False); ib.sleep(3)
            v = t.last if t.last == t.last and t.last and t.last > 0 else t.close
            spots[sym] = v if v == v and v and v > 0 else None
            ib.cancelMktData(und)
        except Exception:
            spots[sym] = None
    today = dt.date.today()
    for r in rows:
        if r["secType"] != "OPT" or (r.get("iv") and r.get("und")):
            continue
        r["und"] = r.get("und") or spots.get(r["symbol"])
        T = (dt.datetime.strptime(r["expiry"], "%Y%m%d").date() - today).days / 365
        r["iv"] = deals.implied_vol(r["marketPrice"], r["und"], r["strike"], T, r["right"])
    for r in rows:
        if r["secType"] == "OPT" and r.get("delta") is None and r.get("iv") and r.get("und"):
            T = (dt.datetime.strptime(r["expiry"], "%Y%m%d").date() - today).days / 365
            h = r["und"] * 0.001
            r["delta"] = (deals.bs(r["und"] + h, r["strike"], T, r["iv"], r["right"]) - deals.bs(r["und"] - h, r["strike"], T, r["iv"], r["right"])) / (2 * h)
        # theta = quanto vale in meno fra un giorno, a parita' di tutto il resto
        if r["secType"] == "OPT" and r.get("theta") is None and r.get("iv") and r.get("und"):
            T = (dt.datetime.strptime(r["expiry"], "%Y%m%d").date() - today).days / 365
            if T > 0:
                r["theta"] = (deals.bs(r["und"], r["strike"], max(T - 1 / 365, 0), r["iv"], r["right"])
                              - deals.bs(r["und"], r["strike"], T, r["iv"], r["right"]))


def check_price_rules(groups, unmapped):
    """Avvisi quando un sottostante supera (above) o scende sotto (below) un livello deciso in anticipo."""
    f = DATA / "price_rules.json"
    if not f.exists():
        return []
    rules = json.loads(f.read_text()).get("rules", [])
    spots = {}
    for l in [l for g in groups.values() for l in g["legs"]] + unmapped:
        if l.get("und"):
            spots[l["symbol"]] = l["und"]
    out = []
    for r in rules:
        px = spots.get(r["symbol"])
        if px is None:
            continue
        hit = ("above" in r and px >= r["above"]) or ("below" in r and px <= r["below"])
        if hit:
            lvl = f">= {r['above']:g}" if "above" in r else f"<= {r['below']:g}"
            out.append(f"{r['symbol']} a {px:.2f} ({lvl}): {r['action']}. Ordini: " + " | ".join(r.get("orders", [])))
    return out


def check_drawdown(nav):
    """Allerta -8% e kill-switch -15% sul NAV rispetto al picco (Piano Finale, decisione 2).

    Registra il NAV live a ogni giro (ogni 10 minuti) in nav_history.csv, tenendo il
    massimo di giornata. Claude/la dashboard AVVISANO soltanto: la chiusura la decide
    e la esegue l'utente a mano (regola del 28/09/2026).
    ATTENZIONE: versamenti e prelievi falsano il picco e vanno registrati a mano nel file.
    """
    f = DATA / "nav_history.csv"
    rows = {}
    if f.exists():
        for line in f.read_text().splitlines():
            if "," in line:
                d, v = line.rsplit(",", 1)
                try:
                    rows[d.strip()] = float(v)
                except ValueError:
                    pass
    today = dt.date.today().isoformat()
    rows[today] = max(rows.get(today, 0.0), nav)
    f.write_text("\n".join(f"{d},{v:.2f}" for d, v in sorted(rows.items())) + "\n")
    peak = max(rows.values())
    if peak <= 0:
        return [], 0.0, 0.0
    dd = (nav - peak) / peak
    out = []
    if dd <= -0.15:
        out.append(f"KILL-SWITCH: NAV {nav:,.0f} EUR, drawdown {dd*100:.1f}% dal picco {peak:,.0f} EUR. "
                   f"Piano: fermare l'operativita'. Decide e chiude l'utente a mano.")
    elif dd <= -0.08:
        out.append(f"ALLERTA drawdown {dd*100:.1f}%: NAV {nav:,.0f} EUR contro un picco di {peak:,.0f} EUR. "
                   f"Kill-switch (-15%) a {peak*0.85:,.0f} EUR.")
    return out, dd, peak


def run_stress():
    """Rilancia stress.py come sottoprocesso (stesso pattern di session_scan.py) e
    ne fa il parsing per una sintesi a punti. Nessun ordine, sola lettura."""
    try:
        proc = subprocess.run(["uv", "run", "stress.py"], capture_output=True, text=True,
                               timeout=120, cwd=str(ROOT))
    except Exception as e:
        return None, f"stress.py non eseguito: {e}"
    out = proc.stdout
    rows = []
    for line in out.splitlines():
        m = re.match(r"(S\d [^(]+)\(([+-]\d+)%, IV ([+-]\d+)\)\s+([\d,\-]+) EUR \(([+-][\d.]+)%\)\s+([\d,\-]+) EUR \(([+-][\d.]+)%\)\s+([\d,\-]+) EUR", line.strip())
        if m:
            rows.append(dict(nome=m.group(1).strip(), mossa=m.group(2), iv=m.group(3),
                              con_pct=m.group(5), senza_pct=m.group(7), valore=m.group(8)))
    nav_m = re.search(r"NAV ([\d,]+) EUR", out)
    nav = nav_m.group(1) if nav_m else None
    if not rows:
        return None, (out[-500:] if out else (proc.stderr or "nessun output")[-500:])
    return {"nav": nav, "rows": rows, "generated": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}, None


def fx_rates(ib, currencies):
    """Cambi VERI da TWS verso la valuta base (EUR): Forex('EURxxx') dice quanti xxx vale
    un euro, quindi importo_in_EUR = importo / cambio. Nessuna stima, nessun cambio dedotto
    da altri numeri della pagina: quello era sbagliato di 8 punti percentuali."""
    from ib_async import Forex
    out = {"EUR": 1.0}
    for c in sorted(set(currencies) - {"EUR", None, ""}):
        try:
            f = Forex("EUR" + c)
            ib.qualifyContracts(f)
            t = ib.reqMktData(f, "", False, False)
            ib.sleep(3)
            v = next((x for x in (t.marketPrice(), t.last, t.close)
                      if x and x == x and x > 0), None)
            ib.cancelMktData(f)
            out[c] = float(v) if v else None
        except Exception:
            out[c] = None
    return out


def build_data(ib):
    items = ib.portfolio()
    mapping = load_map()
    # Una gamba puo' stare in piu' gruppi se la mappa indica la quantita' ("qty", con segno) di
    # ciascuno: serve quando due strutture condividono uno strike (es. il 5850 put del bull put
    # e del condor). Senza "qty" la gamba va intera al gruppo, come prima.
    leg_to_group = {}
    for g in mapping.get("groups", []):
        for leg in g["legs"]:
            k = (leg["symbol"], leg.get("expiry"), leg.get("strike"), leg.get("right"))
            leg_to_group.setdefault(k, []).append((g["id"], leg.get("qty")))

    opt_contracts = [it.contract for it in items if it.contract.secType == "OPT"]
    vegas = fetch_vegas(ib, opt_contracts)

    groups = {g["id"]: {"nome": g["nome"], "note": g.get("note", ""), "legs": [], "pnl": 0.0,
                         "value": 0.0, "debito": 0.0, "vega": 0.0, "vega_ok": True,
                         "theta": 0.0, "theta_ok": True, "cur": None}
              for g in mapping.get("groups", [])}
    unmapped = []
    total_vega = 0.0
    vega_missing = False
    total_theta = 0.0
    theta_missing = False

    for it in items:
        c = it.contract
        k = leg_key(c)
        vega_leg = vegas.get(k)
        row = dict(
            symbol=c.symbol, secType=c.secType,
            expiry=getattr(c, "lastTradeDateOrContractMonth", ""),
            strike=getattr(c, "strike", None), right=getattr(c, "right", ""),
            position=it.position, avgCost=it.averageCost, marketPrice=it.marketPrice,
            marketValue=it.marketValue, unrealizedPNL=it.unrealizedPNL,
            currency=c.currency, vega=vega_leg, theta=GREEKS.get(k, {}).get("theta"),
            mult=float(c.multiplier or 1),
            iv=GREEKS.get(k, {}).get("iv"), und=GREEKS.get(k, {}).get("und"), delta=GREEKS.get(k, {}).get("delta"),
            bid=GREEKS.get(k, {}).get("bid"), ask=GREEKS.get(k, {}).get("ask"),
        )
        if vega_leg is not None:
            total_vega += vega_leg * it.position * float(c.multiplier or 100)
        else:
            vega_missing = True
        assegn = [(gid, q) for gid, q in leg_to_group.get(k, []) if gid in groups]
        if not assegn:
            unmapped.append(row)
            continue
        diviso = len(assegn) > 1 or assegn[0][1] is not None
        if diviso and sum(q or 0 for _, q in assegn) != it.position:
            # le quantita' della mappa non coprono la posizione: la parte scoperta si vede
            resto = it.position - sum(q or 0 for _, q in assegn)
            if resto:
                u = dict(row, position=resto, marketValue=it.marketValue * resto / it.position,
                         unrealizedPNL=it.unrealizedPNL * resto / it.position)
                unmapped.append(u)
        for gid, q in assegn:
            qty = it.position if q is None else q
            f = qty / it.position if it.position else 0.0
            r = dict(row, position=qty, marketValue=it.marketValue * f,
                     unrealizedPNL=it.unrealizedPNL * f)
            groups[gid]["legs"].append(r)
            groups[gid]["pnl"] += it.unrealizedPNL * f
            groups[gid]["value"] += it.marketValue * f
            groups[gid]["debito"] += it.averageCost * qty
            groups[gid]["cur"] = groups[gid]["cur"] or c.currency
            if vega_leg is not None:
                groups[gid]["vega"] += vega_leg * qty * float(c.multiplier or 100)
            else:
                groups[gid]["vega_ok"] = False

    fill_missing_iv(ib, [l for g in groups.values() for l in g["legs"]] + unmapped)

    # Theta sommato qui e non nel ciclo: fill_missing_iv lo ricava con Black-Scholes quando
    # TWS non da' le greche (mercato chiuso), e nel ciclo quel valore non c'e' ancora.
    for g in groups.values():
        for l in g["legs"]:
            if l.get("theta") is not None:
                g["theta"] += l["theta"] * l["position"] * l["mult"]
                total_theta += l["theta"] * l["position"] * l["mult"]
            elif l["secType"] == "OPT":
                g["theta_ok"] = False
                theta_missing = True
    for l in unmapped:
        if l.get("theta") is not None:
            total_theta += l["theta"] * l["position"] * l["mult"]
        elif l["secType"] == "OPT":
            theta_missing = True

    account = {"_fx": fx_rates(ib, {l["currency"] for l in
                                    [x for g in groups.values() for x in g["legs"]] + unmapped})}
    want = {"NetLiquidation", "TotalCashValue", "GrossPositionValue", "AvailableFunds",
            "ExcessLiquidity", "InitMarginReq", "MaintMarginReq", "Cushion"}
    for a in ib.accountSummary():
        if a.tag in want and (a.currency in ("EUR", "BASE") or a.tag == "Cushion"):
            account[a.tag] = (float(a.value), a.currency)

    return groups, unmapped, account, total_vega, vega_missing, total_theta, theta_missing


POS_ASSETS = """
<style>
  .deals { width:100%; border-collapse:collapse; font-size:0.86rem; }
  .deals td:first-child { min-width:220px; }
  .deals th { text-align:left; color:var(--muted); font-weight:500; padding:6px 8px; border-bottom:1px solid var(--border); white-space:nowrap; }
  .deals td { padding:7px 8px; border-bottom:1px solid var(--border); vertical-align:top; }
  .deals td.num, .deals th.num { text-align:right; white-space:nowrap; }
  .deals tr.deal { cursor:pointer; }
  .deals tr.deal:hover { background:rgba(128,128,128,.08); }
  .deals tr.deal.warnrow { background:var(--warnbg); }
  .deals tr.deal.hedge { background:rgba(57,135,229,.12); }
  .deals tr.deal.hedge td:first-child { box-shadow:inset 3px 0 0 #3987e5; }
  .scanopt { display:flex; flex-wrap:wrap; gap:6px 16px; align-items:center; font-size:0.78rem; color:var(--muted); margin:2px 0 8px; }
  .scanopt input[type=number], .scanopt select { padding:2px 4px; border:1px solid var(--border); border-radius:5px; background:transparent; color:inherit; font:inherit; }
  .ffbox .scanbox { justify-content:flex-start; }
  .ffbox { margin-top:14px; padding-top:10px; border-top:1px solid var(--border); }
  .scanbox { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .occ-sym { padding:5px 9px; border:1px solid var(--border); border-radius:6px; background:var(--card, transparent); color:inherit; font:inherit; font-size:0.85rem; text-transform:uppercase; }
  .deals tr.sechead td { background:var(--surface-2, rgba(128,128,128,.10)); padding-top:9px; padding-bottom:9px; border-top:1px solid var(--border); border-bottom:1px solid var(--border); font-weight:600; cursor:pointer; }
  .deals tr.sechead:hover td { filter:brightness(0.97); }
  .sectog { display:inline-block; width:1em; transition:transform .12s; }
  .deals tr.sechead.closed .sectog { transform:rotate(-90deg); }
  .deals tr.secoff { display:none !important; }
  .deals tr.tot td { border-top:2px solid var(--border); border-bottom:0; font-weight:600; padding-top:9px; }
  .hedgetog { font-size:0.78rem; color:var(--muted); display:inline-flex; align-items:center; gap:5px; margin-right:10px; cursor:pointer; }
  .deals .arr { font-weight:700; }
  .deals .tog { display:inline-block; width:14px; color:var(--muted); transition:transform .15s; }
  .deals tr.deal.open .tog { transform:rotate(90deg); }
  .deals tr.legs > td { padding:4px 8px 12px 30px; background:rgba(128,128,128,.04); }
  .deals tr.legs table { font-size:0.8rem; }
  .deals .sub { color:var(--muted); font-size:0.78rem; margin:0; }
  .deals-tools { display:flex; gap:8px; }
  .deals-tools button { background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:6px; padding:3px 10px; font-size:0.78rem; cursor:pointer; }
  .table-scroll { overflow-x:auto; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:7px;
         vertical-align:middle; flex:none; }
  .dot.up { background:var(--pos); }
  .dot.down { background:var(--neg); }
  .dot.nd { background:var(--muted); opacity:.45; }
</style>
<script>
  function toggleDeal(id){ const r=document.getElementById('deal-'+id), l=document.getElementById('legs-'+id); const o=l.hidden; l.hidden=!o; r.classList.toggle('open',o); }
  function vistaTotali(con){
    document.querySelectorAll('tr.tot').forEach(r => { if (r.dataset.vista === 'all' || r.dataset.vista === 'nh') r.hidden = (r.dataset.vista === 'all') !== con; });
  }
  function toggleSec(sk, forza){
    const heads = document.querySelectorAll('tr.sechead[data-sec="'+sk+'"]');
    if (!heads.length) return;
    const chiudi = forza === undefined ? !heads[0].classList.contains('closed') : forza;
    heads.forEach(h => h.classList.toggle('closed', chiudi));
    document.querySelectorAll('tr[data-sec="'+sk+'"]:not(.sechead)').forEach(r => r.classList.toggle('secoff', chiudi));
    try { const m = JSON.parse(localStorage.getItem('sezioniChiuse') || '{}'); m[sk] = chiudi; localStorage.setItem('sezioniChiuse', JSON.stringify(m)); } catch (e) {}
  }
  document.addEventListener('DOMContentLoaded', () => {
    // di default TUTTE le sezioni sono chiuse; si ricorda solo quelle che l'utente ha aperto
    let m = {};
    try { m = JSON.parse(localStorage.getItem('sezioniChiuse') || '{}'); } catch (e) {}
    const viste = new Set();
    document.querySelectorAll('tr.sechead[data-sec]').forEach(h => viste.add(h.dataset.sec));
    viste.forEach(k => toggleSec(k, m[k] !== false));
  });
  function allDeals(open){ document.querySelectorAll('tr.legs').forEach(l=>l.hidden=!open); document.querySelectorAll('tr.deal').forEach(r=>r.classList.toggle('open',open)); }
</script>
"""


MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre"]
# Soglia di concentrazione: perdita massima aggregata per (sottostante, mese di scadenza piu'
# vicina) in % del NAV. Scelta di Claude (il Piano fissa solo 1-2% per trade): 2%. Un avviso, non un blocco.
CONC_SOGLIA = 0.02


def mese_etichetta(chiave):
    if not chiave or chiave == "zzzzzz":
        return "Altre posizioni"
    return f"{MESI[int(chiave[4:6]) - 1].capitalize()} {chiave[:4]}"


def chiave_mese(g):
    """AAAAMM della scadenza piu' vicina fra le opzioni del gruppo ('zzzzzz' se non ce ne sono)."""
    exps = [l["expiry"] for l in g.get("legs", []) if l.get("secType") == "OPT" and l.get("expiry")]
    return min(exps)[:6] if exps else "zzzzzz"


def cap_calendar(ls, mn):
    """Per le strutture con scadenze diverse in debito (calendar, diagonal) la perdita massima
    e' il debito pagato: deals.extremes la gonfia valutando la gamba lunga a sottostante zero."""
    if isinstance(mn, (int, float)) and len({l["expiry"] for l in ls}) > 1:
        debito = sum(l["avgCost"] * l["position"] for l in ls)
        if debito > 0:
            return max(mn, -debito)
    return mn


def concentrazioni(groups, fx):
    """{(sottostante, AAAAMM): EUR di perdita massima} sommando i gruppi non-protezione.
    Le protezioni di coda si escludono: la loro 'perdita' e' il premio pagato. Per ogni
    sottostante del gruppo si guarda solo la scadenza piu' vicina di quelle gambe. Un estremo
    illimitato o non calcolabile pesa 'None' (si segnala, non si somma)."""
    out = {}
    for g in groups.values():
        if "protezion" in g["nome"].lower():
            continue
        opts = [l for l in g["legs"] if l.get("secType") == "OPT"]
        for sym in dict.fromkeys(l["symbol"] for l in opts):
            ls = [l for l in opts if l["symbol"] == sym]
            r = (fx or {}).get(ls[0]["currency"])
            try:
                _mx, mn, _e = deals.extremes(ls)
                mn = cap_calendar(ls, mn)
            except Exception:
                mn = "n/d"
            k = (sym, chiave_mese({"legs": ls}))
            d = out.setdefault(k, {"eur": 0.0, "parz": False, "gruppi": []})
            if isinstance(mn, (int, float)) and r:
                d["eur"] += -min(mn, 0.0) / r
            else:
                d["parz"] = True
            d["gruppi"].append(g["nome"])
    return out


def _money(v, cur):
    if v is None:
        return "illimitato"
    if isinstance(v, str):
        return v
    return f"{eur(v, cur):+,.0f} EUR"


ACT_ASSETS = """
<style>
  .acts { list-style:none; margin:0; padding:0; }
  .acts li { border-bottom:1px solid var(--border); padding:10px 0; }
  .acts li:last-child { border-bottom:0; }
  .acthead { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .badge { font-size:0.68rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
           border-radius:5px; padding:2px 7px; white-space:nowrap; }
  .badge.pronta { background:var(--pos); color:#06281b; }
  .badge.vicina { background:#c8911f; color:#201603; }
  .badge.info   { background:var(--border); color:var(--muted); }
  .acthead .who { color:var(--muted); font-size:0.8rem; }
  .acthead .what { font-weight:600; font-size:0.9rem; }
  .actbtn { margin-left:auto; background:transparent; color:#3987e5; border:1px solid #3987e5;
            border-radius:6px; padding:3px 11px; font-size:0.78rem; cursor:pointer; white-space:nowrap; }
  .actbtn:hover { background:#3987e5; color:#fff; }
  .actdet { margin-top:10px; padding:12px 14px; background:rgba(128,128,128,.07);
            border-radius:8px; font-size:0.84rem; line-height:1.55; }
  .actdet p { margin:0 0 10px; }
  .ba { width:100%; border-collapse:collapse; margin:4px 0 12px; font-size:0.84rem; }
  .ba th { text-align:left; color:var(--muted); font-weight:500; padding:5px 8px; border-bottom:1px solid var(--border); }
  .ba td { padding:5px 8px; border-bottom:1px solid var(--border); }
  .ba td.num, .ba th.num { text-align:right; white-space:nowrap; }
  .ba tr td:first-child { color:var(--muted); }
  .ordine { display:block; background:var(--bg); border:1px solid var(--border); border-radius:6px;
            padding:8px 10px; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:0.78rem;
            white-space:pre-wrap; margin-bottom:10px; }
  .actnota { color:#d79a28; font-size:0.82rem; margin:0 0 10px; }
  .trig { color:var(--muted); font-size:0.82rem; }
  .trig b { color:var(--text); }
  .regole { list-style:none; margin:0 0 12px; padding:0; display:flex; flex-wrap:wrap; gap:4px 16px; font-size:0.8rem; }
  .regole li.ok { color:var(--pos); }
  .regole li.ko { color:var(--neg); }
  .regole li.na { color:var(--muted); }
  .istrbox { margin-top:12px; padding-top:10px; border-top:1px dashed var(--border); }
  .actbtn.copia { color:var(--pos); border-color:var(--pos); font-weight:600; }
  .actbtn.copia:hover { background:var(--pos); color:#06281b; }
  .actbtn.copia.done { background:var(--pos); color:#06281b; }
  .istr { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:9px 11px;
          font-family:ui-monospace,Menlo,Consolas,monospace; font-size:0.75rem; white-space:pre-wrap;
          margin:9px 0 4px; user-select:all; }
  .istrbox .sub { display:block; margin-top:5px; }
  .ba tr.cons { background:rgba(62,207,142,.10); }
  .cons-tag { margin-left:7px; font-size:0.62rem; padding:1px 5px; }
  .consiglio { background:rgba(62,207,142,.10); border-left:3px solid var(--pos);
               border-radius:0 6px 6px 0; padding:8px 11px; margin:0 0 12px; font-size:0.83rem; }
  .consiglio.inerte { background:rgba(128,128,128,.10); border-left-color:var(--muted); }
  .acts .sub { color:var(--muted); font-size:0.8rem; }
  /* lo sfondo di base vale come ripiego: se un giorno comparisse uno stato senza una sua
     regola, l'etichetta resta leggibile invece di sparire */
  .actflag { font-size:0.68rem; font-weight:700; border-radius:4px; padding:1px 5px; margin-left:7px;
             text-decoration:none; white-space:nowrap; background:var(--border); color:var(--muted); }
  .actflag.pronta { background:var(--pos); color:#06281b; }
  .actflag.vicina { background:#c8911f; color:#201603; }
  /* senza questa, le azioni di sola informazione (es. difesa laterale su una posizione
     non ancora sotto pressione) uscivano senza sfondo e illeggibili */
  .actflag.info { background:var(--border); color:var(--muted); }
</style>
<script>
  function toggleAct(i){ const d=document.getElementById('act-'+i); d.hidden=!d.hidden;
    const b=document.getElementById('actb-'+i); if(b) b.textContent = d.hidden ? 'Dettaglio' : 'Chiudi'; }
</script>
"""


def istruzione(a, groups, quando):
    """Il blocco di testo che l'utente copia e incolla in chat per chiedere un'azione.

    Deve essere senza ambiguita' per me: identifica l'azione (tipo + gruppo), riporta l'ordine
    esatto con il limite, la situazione prima e dopo, e **quando e a che prezzo** e' stato
    calcolato, cosi' posso riverificare che nel frattempo non sia cambiato nulla."""
    g = (groups or {}).get(a.get("gid")) or {}
    sym = spot = None
    for l in g.get("legs", []):
        if l.get("und"):
            sym, spot = l["symbol"], l["und"]
            break
    cur = a.get("cur") or ""
    r = [f"ESEGUI {a.get('kind','?')}/{a.get('gid','?')}",
         f"Posizione: {g.get('nome') or a.get('nome','?')}",
         f"Azione:    {a.get('titolo','?')}"]
    if a.get("ordine"):
        r.append(f"Ordine:    {a['ordine']}")
    p, d = a.get("prima"), a.get("dopo")

    def riga(et, x):
        if not x:
            return None
        pz = f", prob {x['prob']*100:.0f}%" if x.get("prob") is not None else ""
        return (f"{et:10s} perdita max {_money(x.get('max_perdita'), cur)}, "
                f"profitto max {_money(x.get('max_profitto'), cur)}{pz}")
    for et, x in (("Prima:", p), ("Dopo:", d)):
        v = riga(et, x)
        if v:
            r.append(v)
    r.append(f"Calcolato: {quando}" + (f" con {sym} a {spot:,.2f}" if spot else ""))
    return "\n".join(r)


ACT_COPIA = """
<script>
  function copiaIstr(i){
    const el = document.getElementById('istr-'+i), b = document.getElementById('cp-'+i);
    const t = el.textContent;
    const fatto = () => { b.textContent = 'Copiato — incollalo in chat'; b.classList.add('done');
                          setTimeout(()=>{ b.textContent='Copia istruzione per Claude'; b.classList.remove('done'); }, 4000); };
    // navigator.clipboard non funziona su file:// in molti browser: si tiene il ripiego
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(t).then(fatto).catch(()=>ripiego(t, fatto, el));
    } else ripiego(t, fatto, el);
  }
  function ripiego(t, fatto, el){
    const ta = document.createElement('textarea');
    ta.value = t; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch(e) { ok = false; }
    document.body.removeChild(ta);
    if (ok) fatto();
    else { el.hidden = false; el.scrollIntoView({block:'nearest'});
           alert('Il browser non permette la copia automatica da un file locale.\\nIl testo e\\' ora visibile: selezionalo e copialo a mano.'); }
  }
</script>
"""


OPP_FILE = DATA / "opportunita.json"


def opportunita_card(groups=None, conc=None, nav=None, fx=None):
    """Riquadro delle occasioni: SOLO un guscio. Le occasioni non si calcolano ne' si leggono al
    caricamento della pagina: la ricerca la lancia il bottone (server locale, porta 8770) in modo
    asincrono, e i risultati arrivano a flusso man mano che vengono trovati. Al caricamento la
    pagina chiede al server l'ultima ricerca (anche se ancora in corso), quindi ricaricare la
    pagina e' istantaneo e non interrompe nulla."""
    return """<div class='card' id='occ-card'><div class='card-head'><h2>Occasioni sul mercato</h2>
<form class='scanbox' onsubmit='return false'>
<input id='occ-sym' class='occ-sym' value='ESTX50 XSP' placeholder='ticker: AAPL, ESTX50 XSP, @ETF...' size='24' autocomplete='off' title='Ticker separati da spazio o virgola: indici (ESTX50, XSP, SPX, RUT, NDX, XND, MRUT, DJX) o azioni/ETF USA. Elenchi pronti: @ETF, @INDICI, @TOP (fino a 40 ticker)'>
<button class='actbtn' id='btn-cerca'>Cerca occasioni</button>
<button class='actbtn' id='btn-annulla' hidden>Annulla</button></form></div>
<div class='scanopt'>
<label title='Orizzonte delle scadenze in giorni'>giorni <input id='occ-dmin' type='number' min='1' max='400' value='7' style='width:52px'> &ndash; <input id='occ-dmax' type='number' min='2' max='400' value='200' style='width:56px'></label>
<label><input id='occ-weekly' type='checkbox' checked> include le weekly (venerd&igrave;, fino a 45 gg)</label>
<label title='Margine di volatilit&agrave; &ge; +2 punti, spread &le; 10%, open interest &ge; 100, niente utili prima della scadenza per le strutture a credito'><input id='occ-piano' type='checkbox' checked> filtri del Piano</label>
<label>ordina per <select id='occ-ordine'><option value='er'>valore atteso / rischio</option><option value='ev'>valore atteso assoluto</option></select></label>
</div>
<div class='note' id='occ-stato'>Caricamento...</div>
<div class='note'>Strutture a <b>rischio definito</b> (iron condor, iron butterfly, verticali a credito e a debito, calendar, double calendar/diagonal, broken wing butterfly, put backspread; i put ratio sono dimensionati sulla perdita a &minus;20% al netto delle protezioni). <b>Valore atteso prudente</b> = il minore fra quello con la volatilit&agrave; <b>prevista</b> (HAR) e quello sulla <b>distribuzione storica</b> (bootstrap, crisi incluse); prezzi eseguibili; solo quelle a valore atteso positivo, non gi&agrave; implementate e sotto il 2% del NAV. <b>Non sono segnali</b>, sono candidati da verificare. Nessun ordine parte da qui.</div>
<ul class='acts' id='occ-lista'></ul>
<div class='ffbox'><form class='scanbox' onsubmit='return false'><b>Screener forward factor</b>
<input id='ff-sym' class='occ-sym' value='@ETF' size='24' autocomplete='off' title='Ticker o elenchi (@ETF, @INDICI, @TOP): cerca dove i calendar hanno vantaggio di struttura a termine (FF &ge; +0,20)'>
<button class='actbtn' id='btn-ff'>Cerca calendar</button>
<button class='actbtn' id='btn-box' title='Box spread su SPX e XSP: rendimento implicito della liquidita&agrave;'>Box spread (liquidit&agrave;)</button></form>
<div class='note' id='ff-stato'></div><div id='ff-tab'></div>
<div class='note' id='box-stato'></div><div id='box-tab'></div></div></div>
<script>
(function(){
  const API = location.protocol.startsWith('http') ? '' : 'http://127.0.0.1:8770';
  const $ = id => document.getElementById(id);
  let timer = null, ultimo = '', ultimoFF = '', ultimoBX = '', toccato = false;
  $('occ-sym').addEventListener('input', () => { toccato = true; });
  function riassunto(r){
    let t = '';
    if (r.fase === 'scan') t = 'ricerca in corso: ' + (r.messaggio || '') + ' &mdash; i risultati compaiono man mano';
    else if (r.fase === 'finito') t = 'ricerca completata';
    else if (r.fase === 'annullato') t = 'ricerca annullata';
    else if (r.fase === 'errore' || r.fase === 'interrotta') t = '<b class="neg">' + (r.messaggio || 'ricerca interrotta') + '</b>';
    else t = r.messaggio || 'nessuna ricerca ancora fatta';
    if (r.tickers && r.tickers.length) t += ' &middot; ' + r.tickers.join(', ');
    t += ' &middot; <b>' + r.n_mostrate + '</b> mostrate su ' + r.n_totali + ' trovate';
    const n = r.nascoste || {}, h = [];
    if (n.implementate) h.push(n.implementate + ' gi&agrave; implementate');
    if (n.oltre_tetto) h.push(n.oltre_tetto + ' oltre il 2% del NAV');
    if (n.sotto_soglia_piano) h.push(n.sotto_soglia_piano + ' sotto le soglie del Piano');
    if (n.utili) h.push(n.utili + ' con utili prima della scadenza');
    if (n.ev_non_positivo) h.push(n.ev_non_positivo + ' con valore atteso non positivo');
    if (h.length) t += ' (nascoste: ' + h.join(', ') + ')';
    if (r.aggiornato) t += ' &middot; aggiornata ' + r.aggiornato;
    (r.errori || []).forEach(e => { t += '<br><span class="neg">' + e + '</span>'; });
    return t;
  }
  function inLista(html){
    if (html === ultimo) return;
    const lista = $('occ-lista');
    const aperti = Array.from(lista.querySelectorAll('.actdet:not([hidden])')).map(e => e.id);
    lista.innerHTML = html; ultimo = html;
    aperti.forEach(id => { const d = $(id); if (d) { d.hidden = false; const b = $('actb-' + id.slice(4)); if (b) b.textContent = 'Chiudi'; } });
  }
  const qs = () => 'job=last&filtri=' + ($('occ-piano').checked ? 1 : 0) + '&ordine=' + $('occ-ordine').value;
  async function aggiorna(){
    try {
      const [r, f, bx] = await Promise.all([
        fetch(API + '/api/occasioni?' + qs(), {cache: 'no-store'}).then(x => x.json()),
        fetch(API + '/api/ff?job=last', {cache: 'no-store'}).then(x => x.json()),
        fetch(API + '/api/box', {cache: 'no-store'}).then(x => x.json())]);
      $('occ-stato').innerHTML = riassunto(r);
      inLista(r.html || '');
      let tf = '';
      if (f.fase === 'scan') tf = 'screener in corso: ' + (f.messaggio || '');
      else if (f.fase === 'finito') tf = 'screener completato &middot; ' + f.n + ' coppie di scadenze';
      else if (f.fase === 'annullato' || f.fase === 'interrotta' || f.fase === 'errore') tf = '<b class="neg">' + (f.messaggio || f.fase) + '</b>';
      (f.errori || []).forEach(e => { tf += '<br><span class="neg">' + e + '</span>'; });
      $('ff-stato').innerHTML = tf;
      if ((f.html || '') !== ultimoFF) { $('ff-tab').innerHTML = f.html || ''; ultimoFF = f.html || ''; }
      let tb = '';
      if (bx.fase === 'scan') tb = 'box spread in corso: ' + (bx.messaggio || '');
      else if (bx.fase === 'finito') tb = 'box spread: ' + bx.n + ' trovati';
      else if (bx.fase === 'annullato' || bx.fase === 'interrotta' || bx.fase === 'errore') tb = '<b class="neg">' + (bx.messaggio || bx.fase) + '</b>';
      (bx.errori || []).forEach(e => { tb += '<br><span class="neg">' + e + '</span>'; });
      $('box-stato').innerHTML = tb;
      if ((bx.html || '') !== ultimoBX) { $('box-tab').innerHTML = bx.html || ''; ultimoBX = bx.html || ''; }
      const attiva = r.fase === 'scan' || f.fase === 'scan' || bx.fase === 'scan';
      $('btn-cerca').disabled = attiva; $('btn-ff').disabled = attiva; $('btn-box').disabled = attiva; $('btn-annulla').hidden = !attiva;
      if (!toccato && r.tickers && r.tickers.length && r.tickers.length < 8) $('occ-sym').value = r.tickers.join(' ');
      if (attiva && !timer) timer = setInterval(aggiorna, 3000);
      if (!attiva && timer) { clearInterval(timer); timer = null; }
    } catch (e) {
      $('occ-stato').innerHTML = '<b class="neg">server non raggiungibile.</b> Apri la dashboard da <b>http://127.0.0.1:8770/dashboard.html</b> (il server &egrave; il servizio nvo-chain-builder).';
      $('btn-cerca').disabled = true; $('btn-ff').disabled = true; $('btn-box').disabled = true;
    }
  }
  async function avvia(url, stato){
    $('btn-cerca').disabled = true; $('btn-ff').disabled = true; $('btn-box').disabled = true; stato.textContent = 'avvio...';
    try {
      const r = await (await fetch(API + url, {method: 'POST'})).json();
      if (r.errore) stato.innerHTML = '<b class="neg">' + r.errore + '</b>';
      ultimo = ''; ultimoFF = ''; ultimoBX = '';
    } catch (e) {}
    aggiorna();
  }
  $('btn-cerca').addEventListener('click', () => {
    const sym = $('occ-sym').value.trim();
    if (!sym) { $('occ-stato').textContent = 'scrivi almeno un ticker'; return; }
    avvia('/api/occasioni-cerca?sym=' + encodeURIComponent(sym) + '&dmin=' + $('occ-dmin').value + '&dmax=' + $('occ-dmax').value
          + '&weekly=' + ($('occ-weekly').checked ? 1 : 0), $('occ-stato'));
  });
  $('btn-ff').addEventListener('click', () => {
    const sym = $('ff-sym').value.trim();
    if (!sym) { $('ff-stato').textContent = 'scrivi almeno un ticker o un elenco (@ETF)'; return; }
    avvia('/api/ff-cerca?sym=' + encodeURIComponent(sym), $('ff-stato'));
  });
  $('btn-box').addEventListener('click', () => avvia('/api/box-cerca?sym=SPX XSP', $('box-stato')));
  window.cercaTicker = function(sym){ $('occ-sym').value = sym; toccato = true; $('btn-cerca').click(); $('occ-card').scrollIntoView({behavior: 'smooth'}); };
  $('occ-sym').addEventListener('keydown', e => { if (e.key === 'Enter') $('btn-cerca').click(); });
  $('ff-sym').addEventListener('keydown', e => { if (e.key === 'Enter') $('btn-ff').click(); });
  ['occ-piano', 'occ-ordine'].forEach(id => $(id).addEventListener('change', () => { ultimo = ''; aggiorna(); }));
  $('btn-annulla').addEventListener('click', async () => {
    try { await fetch(API + '/api/occasioni-annulla', {method: 'POST'}); } catch (e) {}
    aggiorna();
  });
  aggiorna();
})();
</script>"""


def actions_card(az, groups=None):
    """Le azioni migliorative trovate su ogni posizione, con la situazione prima e dopo.
    Nessun ordine parte da qui: si mostra l'ordine esatto, lo inserisce l'utente."""
    if not az:
        return ""
    quando = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    pronte = [a for a in az if a["stato"] == actions.PRONTA]
    body = []
    for i, a in enumerate(az):
        cur = a.get("cur") or ""
        det = []
        if a.get("trigger"):
            det.append(f"<p class='trig'><b>Innesco:</b> {_html.escape(a['trigger'])}</p>")
        if a.get("perche"):
            det.append(f"<p>{_html.escape(a['perche'])}</p>")
        if a.get("regole"):
            # le quattro regole del metodo, verificate sulla proposta: si vede subito se
            # rispetta il metodo o se e' una deroga
            seg = {"si": ("ok", "&#10003;"), "no": ("ko", "&#10007;"), "na": ("na", "&ndash;")}
            voci = "".join(f"<li class='{seg[e][0]}'>{seg[e][1]} {_html.escape(t)}</li>"
                           for t, e in a["regole"])
            det.append("<ul class='regole'>" + voci + "</ul>")
        p, d = a.get("prima"), a.get("dopo")
        if p and d:
            def riga(et, k, cls=""):
                return (f"<tr><td>{et}</td><td class='num {cls}'>{_money(p.get(k), cur)}</td>"
                        f"<td class='num {cls}'>{_money(d.get(k), cur)}</td></tr>")
            cassa = (f"<tr><td>Cassa gia' impegnata</td>"
                     f"<td class='num'>{_money(-p.get('cassa', 0), cur)}</td>"
                     f"<td class='num'>{_money(-d.get('cassa', 0), cur)}</td></tr>")

            def pct(v):
                return "n/d" if v is None else f"{v * 100:.0f}%"
            # la probabilita' e' la riga che dice se l'aggiustamento conviene davvero: una
            # perdita massima piu' bassa comprata abbassando la probabilita' di vincere
            # non e' un miglioramento, e' uno scambio
            pr, pd_ = p.get("prob"), d.get("prob")
            freccia = ""
            if pr is not None and pd_ is not None:
                diff = (pd_ - pr) * 100
                freccia = (f" <span class='{'pos' if diff >= 0 else 'neg'}'>"
                           f"({diff:+.0f} punti)</span>")
            prob_riga = (f"<tr><td>Probabilit&agrave; di chiudere in utile</td>"
                         f"<td class='num'>{pct(pr)}</td>"
                         f"<td class='num'><b>{pct(pd_)}</b>{freccia}</td></tr>")
            pa_riga = ""
            fmt_pa = lambda v: ("" if v is None else
                                " – ".join(f"{k:,.2f}" for k in v) if isinstance(v, tuple)
                                else f"{v:,.2f}")
            if p.get("pareggio") is not None or d.get("pareggio") is not None:
                pa_riga = (f"<tr><td>Pareggio a scadenza</td>"
                           f"<td class='num'>{fmt_pa(p.get('pareggio')) or 'n/d'}</td>"
                           f"<td class='num'>{fmt_pa(d.get('pareggio')) or 'n/d'}</td></tr>")
            def ev_s(x):
                v = x.get("ev")
                return "n/d" if v is None else f"{eur(v, cur):+,.0f} EUR"
            ev_riga = (f"<tr><td>Valore atteso</td><td class='num'>{ev_s(p)}</td>"
                       f"<td class='num'><b>{ev_s(d)}</b></td></tr>"
                       if (p.get("ev") is not None or d.get("ev") is not None) else "")
            det.append("<table class='ba'><tr><th>&nbsp;</th><th class='num'>Prima</th>"
                       "<th class='num'>Dopo l'aggiustamento</th></tr>"
                       + riga("Perdita massima", "max_perdita", "neg")
                       + riga("Profitto massimo", "max_profitto", "pos")
                       + cassa + pa_riga + prob_riga + ev_riga + "</table>")
            det.append("<p class='sub'><b>Probabilit&agrave;</b>: calcolata con la volatilit&agrave; "
                       "<i>implicita</i>, quindi prudente per chi vende premio. <b>Valore atteso</b>: "
                       f"calcolato con la volatilit&agrave; realizzata <i>attesa</i>, posta al "
                       f"{actions.FATTORE_RV*100:.0f}% dell'implicita &mdash; con l'implicita sarebbe "
                       "zero per costruzione, perch&eacute; &egrave; esattamente ci&ograve; che il "
                       "mercato prezza. Quel {} &egrave; un'ipotesi: cambiarla cambia la classifica. "
                       "Entrambe valgono alla scadenza pi&ugrave; vicina e servono a confrontare "
                       "strutture fra loro, non sono previsioni.</p>".format(f"{actions.FATTORE_RV:.2f}"))
            if p.get("stimato") or d.get("stimato"):
                det.append("<p class='sub'>* scadenze diverse: stima alla scadenza piu' vicina, "
                           "gamba lunga valutata alla volatilita implicita di adesso.</p>")
        if a.get("chiudendo_ora") is not None:
            det.append(f"<p class='sub'>Alternativa: chiudere e basta vale "
                       f"<b>{eur(a['chiudendo_ora'], cur):+,.0f} EUR</b> a prezzi eseguibili.</p>")
        if a.get("nota"):
            det.append(f"<p class='actnota'>&#9888; {_html.escape(a['nota'])}</p>")
        if a.get("parziale"):
            det.append(f"<p class='sub'>{_html.escape(a['parziale'])}</p>")
        if a.get("alternative"):
            def et(x):
                return _html.escape(x.get("etichetta") or f"alza a {x['strike']:g}")

            def pa(x):
                # il pareggio e' la colonna che decide: una perdita massima piu' bassa comprata
                # spostando il pareggio sopra il prezzo attuale non e' un affare
                v = x.get("pareggio")
                if v is None:
                    return ""
                if isinstance(v, tuple):
                    return " – ".join(f"{k:,.2f}" for k in v)
                return f"{v:,.2f}"
            mostra_pa = any(x.get("pareggio") is not None for x in a["alternative"])
            mostra_pr = any(x.get("prob") is not None for x in a["alternative"])
            # probabilita' attuale, per confronto riga per riga
            p_ora = (a.get("prima") or {}).get("prob")

            def pr(x):
                v = x.get("prob")
                if v is None:
                    return "n/d"
                t = f"{v * 100:.0f}%"
                if p_ora is not None:
                    d_ = (v - p_ora) * 100
                    t += f" <span class='{'pos' if d_ >= 0 else 'neg'}'>({d_:+.0f})</span>"
                return t
            mostra_ev = any(x.get("ev") is not None for x in a["alternative"])

            def ev_c(x):
                v = x.get("ev")
                if v is None:
                    return "n/d"
                v = eur(v, cur)
                t = f"<b>{v:+,.0f}</b>" if x.get("consigliata") else f"{v:+,.0f}"
                rr = x.get("rr")
                return t + (f"<br><span class='sub'>r/r {rr:.2f}</span>" if rr else "")
            alt = "".join(
                f"<tr{' class=cons' if x.get('consigliata') else ''}><td>{et(x)}"
                + ("<span class='badge pronta cons-tag'>migliore</span>" if x.get("consigliata") else "")
                + "</td>"
                f"<td class='num'>{eur(x['credito'], cur):+,.0f} EUR</td>"
                f"<td class='num neg'>{_money(x['max_perdita'], cur)}</td>"
                f"<td class='num pos'>{_money(x['max_profitto'], cur)}</td>"
                + (f"<td class='num'>{pa(x)}</td>" if mostra_pa else "")
                + (f"<td class='num'>{pr(x)}</td>" if mostra_pr else "")
                + (f"<td class='num'>{ev_c(x)}</td>" if mostra_ev else "")
                + "</tr>"
                for x in a["alternative"])
            testa_ora = ("" if p_ora is None else
                         f"<tr><td><i>oggi, senza fare nulla</i></td><td class='num'>—</td>"
                         f"<td class='num neg'>{_money((a.get('prima') or {}).get('max_perdita'), cur)}</td>"
                         f"<td class='num pos'>{_money((a.get('prima') or {}).get('max_profitto'), cur)}</td>"
                         + (f"<td class='num'>{pa(a['prima'])}</td>" if mostra_pa else "")
                         + (f"<td class='num'>{p_ora * 100:.0f}%</td>" if mostra_pr else "")
                         + (f"<td class='num'>{ev_c(a['prima'])}</td>" if mostra_ev else "")
                         + "</tr>")
            det.append("<table class='ba'><tr><th>Alternativa</th><th class='num'>Credito netto</th>"
                       "<th class='num'>Perdita massima</th><th class='num'>Profitto massimo</th>"
                       + ("<th class='num'>Pareggio</th>" if mostra_pa else "")
                       + ("<th class='num'>Prob. in utile</th>" if mostra_pr else "")
                       + ("<th class='num'>Valore atteso</th>" if mostra_ev else "")
                       + "</tr>" + testa_ora + alt + "</table>")
            c = a.get("consiglio")
            if c:
                coda = ((f", rendimento/rischio {c['rr']:.2f}" if c.get("rr") else "")
                        + (f", probabilit&agrave; {c['prob']*100:.0f}%" if c.get("prob") is not None else ""))
                if c.get("inazione"):
                    m = c.get("meglio_di") or {}
                    testo = ("<b>Matematicamente conviene NON FARE NULLA.</b> Lasciare la posizione "
                             f"com'&egrave; vale {eur(c['ev'], cur):+,.0f} EUR{coda}; il migliore degli "
                             f"aggiustamenti possibili ({_html.escape(str(m.get('etichetta','')))}) "
                             f"vale {eur(m.get('ev', 0), cur):+,.0f} EUR. Nessuna delle manovre disponibili "
                             "migliora il valore atteso ai prezzi di adesso: le si paga in spread e "
                             "in probabilit&agrave; pi&ugrave; di quanto rendano.")
                else:
                    testo = ("<b>Matematicamente la migliore: "
                             f"{_html.escape(c.get('etichetta') or str(c.get('strike')))}</b> &mdash; "
                             f"valore atteso {eur(c['ev'], cur):+,.0f} EUR{coda}, meglio che non fare nulla.")
                det.append("<p class='consiglio" + (" inerte" if c.get("inazione") else "") + "'>"
                           + testo
                           + " Il valore atteso &egrave; l'unico numero che mette insieme probabilit&agrave; "
                           "e dimensione di guadagni e perdite: una probabilit&agrave; alta con un payoff "
                           "sbilanciato pu&ograve; valere meno di una probabilit&agrave; bassa ben pagata.</p>")
        if a.get("ordine"):
            det.append(f"<code class='ordine'>{_html.escape(a['ordine'])}</code>")
        if a.get("gid"):
            u = f"http://127.0.0.1:8770/?deal={a['gid']}"
            if a.get("add_legs"):
                u += "&add=" + _html.escape(json.dumps(a["add_legs"], separators=(",", ":")))
                etichetta = "Apri nel costruttore con l'aggiustamento gia' inserito"
            else:
                etichetta = "Apri la posizione nel costruttore"
            det.append(f"<a class='beicon' href='{u}' target='_blank'>{etichetta}</a>")
        # istruzione da copiare e incollare in chat: e' il modo per chiedermi questa azione
        # senza ambiguita'. Il bottone copia, non esegue: l'ordine resta da inserire a mano.
        if a.get("gid") and a.get("ordine"):
            txt = istruzione(a, groups, quando)
            det.append(f"<div class='istrbox'>"
                       f"<button class='actbtn copia' id='cp-{i}' onclick='copiaIstr({i})'>"
                       "Copia istruzione per Claude</button>"
                       f"<pre class='istr' id='istr-{i}' hidden>{_html.escape(txt)}</pre>"
                       "<span class='sub'>Il bottone copia soltanto: nessun ordine parte da qui.</span>"
                       "</div>")
        body.append(
            f"<li><div class='acthead'><span class='badge {a['stato']}'>{a['stato']}</span>"
            f"<span class='what'>{_html.escape(a['titolo'])}</span>"
            f"<span class='who'>{_html.escape(a['nome'])}</span>"
            f"<button class='actbtn' id='actb-{i}' onclick='toggleAct({i})'>Dettaglio</button></div>"
            f"<div class='actdet' id='act-{i}' hidden>{''.join(det)}</div></li>")
    titolo = ("&#9888; Azioni migliorative" if pronte else "Azioni migliorative")
    nota = (f"{len(pronte)} eseguibile/i adesso ai prezzi denaro-lettera."
            if pronte else "Niente da eseguire adesso: sotto, cosa sta maturando.")
    return (f"<div class='card{' warn' if pronte else ''}'><div class='card-head'>"
            f"<h2>{titolo}</h2><div class='note'>{nota}</div></div>"
            "<div class='note'>Rivalutate a ogni ricarica della pagina, a prezzi eseguibili (si vende al "
            "denaro, si compra alla lettera). Nessun ordine parte da qui: l'ordine esatto e' nel dettaglio, "
            "lo inserisci tu da TWS. Nessuna di queste azioni e' uno stop loss.</div>"
            "<div class='note'><b>Due promemoria dal metodo</b> (sintesi in <i>Risorse/Aggiustamenti - "
            "competenze dai video</i>): entrare bene conta cento volte piu' che aggiustare bene, quindi "
            "queste proposte non autorizzano un ingresso peggiore; e conta <b>l'effetto sul portafoglio</b> "
            "piu' che sulla singola posizione &mdash; un aggiustamento che salva una posizione ma sbilancia "
            "il libro e' un aggiustamento sbagliato, e questo controllo il motore <b>non lo fa</b>: lo devi "
            "fare tu guardando delta e vega totali in cima alla pagina.</div>"
            f"<ul class='acts'>{''.join(body)}</ul></div>")


def conc_html(conc, nav, fx):
    """Perdita massima aggregata per sottostante e mese di scadenza, in % del NAV."""
    if not conc or not nav:
        return ""
    righe = []
    for (sym, mk), d in sorted(conc.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        pct = d["eur"] / nav
        alta = pct > CONC_SOGLIA
        righe.append(
            f"<li class='{'neg' if alta else ''}'>{'&#9888; ' if alta else ''}<b>{sym} {mese_etichetta(mk)}</b>: "
            f"perdita massima aggregata {d['eur']:,.0f} EUR = <b>{pct*100:.2f}% del NAV</b>"
            f" ({len(d['gruppi'])} posizioni)" + (" &middot; parziale" if d["parz"] else "")
            + (f" &mdash; <b>sopra la soglia del {CONC_SOGLIA*100:.0f}%</b>" if alta else "") + "</li>")
    return ("<div class='note'><b>Concentrazione</b> (perdita massima sommata per sottostante e mese di scadenza "
            "pi&ugrave; vicina, protezioni escluse; soglia di avviso " + f"{CONC_SOGLIA*100:.0f}% del NAV, scelta di Claude, "
            "non una regola del Piano). Le perdite massime si sommano perch&eacute; le strutture sullo stesso "
            "sottostante e scadenza tendono a perdere insieme: &egrave; un limite superiore prudente.<ul>"
            + "".join(righe) + "</ul></div>")


def positions_table(groups, unmapped, fx=None, az=None, nav=None, conc=None):
    """Una riga per trade (espandibile con le gambe): strategia, P/L, massimo profitto e massima perdita."""
    # per ogni gruppo, l'azione migliorativa piu' urgente: diventa un'etichetta sulla riga
    per_gruppo = {}
    for a in az or []:
        gid = a.get("gid")
        if gid and (gid not in per_gruppo or a["stato"] == actions.PRONTA):
            per_gruppo[gid] = a
    items = [(gid, g, False) for gid, g in groups.items() if g["legs"]]
    is_hedge = lambda g: "protezion" in g["nome"].lower()
    if unmapped:
        items.append(("unmapped", {"nome": "Posizioni non mappate", "note": "aperte sul conto ma assenti da data/portfolio_map.json",
                                   "legs": unmapped}, True))
    # sezioni per mese/anno della scadenza piu' vicina; dentro la sezione le protezioni in fondo
    sec_of = lambda it: "zzzzzzz" if it[0] == "unmapped" else chiave_mese(it[1])
    items.sort(key=lambda it: (sec_of(it), is_hedge(it[1])))
    SECT = {}
    body = []
    # totali di colonna, tenuti per valuta: sommare euro e dollari darebbe un numero falso.
    # I set raccolgono le valute in cui il totale non e' un numero: 'ill' = una posizione ha
    # estremo illimitato, 'nd' = manca una quotazione, quindi il totale e' parziale.
    nuovo_T = lambda: {"cash": {}, "pnl": {}, "close": {}, "mx": {}, "mn": {}}
    nuovo_F = lambda: {"close": set(), "mx": set(), "mn": set(), "mx_ill": set(), "mn_ill": set()}
    # due serie: "all" con tutto, "nh" senza le protezioni di coda. Le protezioni hanno un
    # massimo profitto enorme (vale solo in un crollo) che schiaccia il totale e lo rende
    # illeggibile: per questo la vista senza e' quella predefinita.
    TT, FF = {"all": nuovo_T(), "nh": nuovo_T()}, {"all": nuovo_F(), "nh": nuovo_F()}
    sec_corr = None
    for n, (gid, g, warn) in enumerate(items):
        sk = sec_of((gid, g))
        if sk != sec_corr:
            sec_corr = sk
            SECT[sk] = {"all": (nuovo_T(), nuovo_F()), "nh": (nuovo_T(), nuovo_F()), "n": 0, "n_nh": 0,
                        "v_all": 0.0, "v_nh": 0.0, "t_all": 0.0, "t_nh": 0.0, "v_parz": False}
            body.append(("SUB", sk))      # riga di sezione con i totali, risolta dopo (serve tot_cell)
        SECT[sk]["n"] += 1
        if not is_hedge(g):
            SECT[sk]["n_nh"] += 1
        # a quali totali contribuisce questo gruppo
        viste = ("all",) if is_hedge(g) else ("all", "nh")
        T = {k: {c: v for c, v in TT["all"][k].items()} for k in TT["all"]}  # segnaposto, vedi add()
        def add(key, cur, val, _v=viste, _s=SECT[sk]):
            for vista in _v:
                TT[vista][key][cur] = TT[vista][key].get(cur, 0.0) + val
                t_ = _s[vista][0]
                t_[key][cur] = t_[key].get(cur, 0.0) + val
        def flag(key, cur, _v=viste, _s=SECT[sk]):
            for vista in _v:
                FF[vista][key].add(cur)
                _s[vista][1][key].add(cur)
        legs = g["legs"]
        by_cur = {}
        for l in legs:
            by_cur.setdefault(l["currency"], []).append(l)
        strat = deals.name([l for l in legs if l["secType"] == "OPT"]) if any(l["secType"] == "OPT" for l in legs) else "Azioni"
        pnl_by = {c: sum(l["unrealizedPNL"] for l in ls) for c, ls in by_cur.items()}
        cash_by = {c: sum(l["avgCost"] * l["position"] for l in ls) for c, ls in by_cur.items()}
        for c, v in pnl_by.items():
            add("pnl", c, v)
        for c, v in cash_by.items():
            add("cash", c, v)
        pnl_e = sum(eur(v, c) for c, v in pnl_by.items())
        cash_e = sum(eur(v, c) for c, v in cash_by.items())
        pnl = f"<span class='{'pos' if pnl_e >= 0 else 'neg'}'>{pnl_e:+,.0f} EUR</span>"
        cash = f"{'debito' if cash_e >= 0 else 'credito'} {abs(cash_e):,.0f} EUR"
        # "chiudendo ora": si vende al BID e si ricompra all'ASK, non a meta' spread
        close_parts, close_tot, close_ok = [], 0.0, True
        for c, ls in by_cur.items():
            ev, ok = 0.0, True
            for l in ls:
                px = l.get("bid") if l["position"] > 0 else l.get("ask")
                if px is None:
                    ok = False
                    break
                ev += px * l.get("mult", 100) * l["position"]
            if not ok:
                close_ok = False
                flag("close", c)
                continue
            v = ev - sum(l["avgCost"] * l["position"] for l in ls)
            add("close", c, v)
            close_tot += eur(v, c)
        close_parts = ([f"<span class='{'pos' if close_tot >= 0 else 'neg'}'>{close_tot:+,.0f} EUR</span>"]
                       if close_ok else ["n/d"])
        close_html = "<br>".join(close_parts)
        mx_l, mn_l, est = [], [], False
        opt_legs = [l for l in legs if l["secType"] == "OPT"]
        for c, ls in by_cur.items():
            ls = [dict(l, expiry=l["expiry"]) for l in ls if l["secType"] == "OPT"]
            if not ls:
                continue
            try:
                mx, mn, e = deals.extremes(ls)
                mn = cap_calendar(ls, mn)
            except Exception:
                mx, mn, e = "n/d", "n/d", False
            est = est or e
            for val, key in ((mx, "mx"), (mn, "mn")):
                if isinstance(val, str):        # "n/d": il totale di quella valuta resta parziale
                    flag(key, c)
                elif val is None:               # illimitato: contagia il totale
                    flag(key + "_ill", c)
                else:
                    add(key, c, val)
            mx_l.append((mx, c)); mn_l.append((mn, c))
        def somma_eur(lst):
            if any(v is None for v, _c in lst):
                return "illimitato"
            if any(isinstance(v, str) for v, _c in lst):
                return "n/d"
            return _money(sum(eur(v, c) for v, c in lst), "EUR") if lst else ""
        mxs, mns = [somma_eur(mx_l)], [somma_eur(mn_l)]
        be_parts = []
        fmtk = lambda k: f"{k:,.2f}" if k < 1000 else f"{k:,.0f}"
        for c, ls in by_cur.items():
            ls = [l for l in ls if l["secType"] == "OPT"]
            if not ls:
                continue
            try:
                res = deals.breakevens(ls)
            except Exception:
                res = [("", [], None)]
            for sym, bes, d in res:
                pre = f"{sym} " if len(res) > 1 or len(by_cur) > 1 else ""
                if d == "su": be_parts.append(f"{pre}<span class='arr pos'>&uarr;</span> {fmtk(bes[0])}")
                elif d == "giu": be_parts.append(f"{pre}<span class='arr neg'>&darr;</span> {fmtk(bes[0])}")
                elif d == "tra": be_parts.append(f"{pre}<span class='arr'><span class='neg'>&darr;</span><span class='pos'>&uarr;</span></span> {fmtk(bes[0])} – {fmtk(bes[1])}")
                elif d == "fuori": be_parts.append(f"{pre}fuori da {fmtk(bes[0])} – {fmtk(bes[1])}")
                elif d == "sempre": be_parts.append(f"{pre}sempre in utile")
                elif d == "mai": be_parts.append(f"{pre}mai in utile")
                else: be_parts.append(f"{pre}n/d")
        be_html = "<br>".join(be_parts)
        # Pallino: la posizione scadrebbe in utile o in perdita se il sottostante restasse dove
        # sta adesso, alla scadenza piu' vicina. Si calcola per valuta e si converte in EUR,
        # perche' sommare euro e dollari darebbe un segno falso sui gruppi misti.
        pl_liv, pl_ok = 0.0, bool(opt_legs)
        for c, ls in by_cur.items():
            ls = [l for l in ls if l["secType"] == "OPT"]
            if not ls:
                continue
            v = deals.pl_al_livello(ls)
            r_ = (fx or {}).get(c)
            if v is None or not r_:
                pl_ok = False
                break
            pl_liv += v / r_
        if pl_ok:
            pallino = (f"<span class='dot {'up' if pl_liv >= 0 else 'down'}' "
                       f"title='A scadenza, col sottostante fermo qui: {pl_liv:+,.0f} EUR'></span>")
        else:
            pallino = "<span class='dot nd' title='Non calcolabile: manca il prezzo del sottostante o la volatilita di una gamba lunga'></span>"
        strat = deals.name(opt_legs) if opt_legs else "Azioni"
        star = " *" if est else ""
        exps = sorted({l["expiry"] for l in opt_legs})
        scad = " / ".join(f"{e[6:8]}/{e[4:6]}/{e[2:4]}" for e in exps)
        dparts = []
        for sym in dict.fromkeys(l["symbol"] for l in legs):
            ls = [l for l in legs if l["symbol"] == sym]
            if any(l.get("delta") is None for l in ls):
                dparts.append(f"{sym} n/d" if len(by_cur) > 1 or len({l['symbol'] for l in legs}) > 1 else "n/d"); continue
            d = sum(l["delta"] * l["position"] * l.get("mult", 100) for l in ls)
            unit = "azioni" if ls[0]["currency"] == "USD" and sym not in ("XSP", "SPX") else "unità di indice"
            txt = f"{d:+,.0f}"
            if strat.startswith("ZEBRA"):
                units = -sum(l["position"] for l in ls if l["position"] < 0)
                txt += f"<p class='sub'>{d / units:+.0f} per ZEBRA (obiettivo circa +100)</p>" if units else ""
            pre = f"{sym} " if len({l['symbol'] for l in legs}) > 1 else ""
            dparts.append(f"<span title='{unit}'>{pre}{txt}</span>")
        delta_html = "<br>".join(dparts)
        vega = sum(eur((l.get("vega") or 0) * l["position"] * l.get("mult", 100), l["currency"]) for l in legs)
        vega_s = f"{vega:+,.0f}" + ("" if all(l.get("vega") is not None for l in legs) else " (parz.)")
        theta = sum(eur((l.get("theta") or 0) * l["position"] * l.get("mult", 100), l["currency"]) for l in legs)
        opts = [l for l in legs if l["secType"] == "OPT"]
        theta_s = f"{theta:+,.0f}" + ("" if all(l.get("theta") is not None for l in opts) else " (parz.)")
        for _v in viste:
            SECT[sk]["v_" + _v] += vega
            SECT[sk]["t_" + _v] += theta
        if not all(l.get("vega") is not None for l in legs):
            SECT[sk]["v_parz"] = True
        legrows = "".join(
            f"<tr><td>{l['symbol']} {l['expiry']} {l['strike']:g}{l['right']}</td><td class='num'>{l['position']:+.0f}</td>"
            f"<td class='num'>{l['avgCost']:,.2f}</td><td class='num'>{l['marketPrice']:,.2f}</td>"
            f"<td class='num'>{'n/d' if (l.get('bid') if l['position'] > 0 else l.get('ask')) is None else format(l.get('bid') if l['position'] > 0 else l.get('ask'), ',.2f')}</td>"
            f"<td class='num'>{eur(l['marketValue'], l['currency']):,.0f}</td>"
            f"<td class='num {'pos' if l['unrealizedPNL'] >= 0 else 'neg'}'>{eur(l['unrealizedPNL'], l['currency']):+,.0f} EUR</td>"
            f"<td class='num'>{'n/d' if l.get('iv') is None else format(l['iv']*100, '.0f') + '%'}</td>"
            f"<td class='num'>{'n/d' if l.get('delta') is None else format(l['delta'], '+.2f')}</td>"
            f"<td class='num'>{'n/d' if l.get('delta') is None else format(l['delta']*l['position']*l.get('mult', 100), '+,.0f')}</td>"
            f"<td class='num'>{'n/d' if l.get('vega') is None else format(eur(l['vega']*l['position']*l.get('mult', 100), l['currency']), '+.0f')}</td>"
            f"<td class='num'>{'n/d' if l.get('theta') is None else format(eur(l['theta']*l['position']*l.get('mult', 100), l['currency']), '+,.0f')}</td></tr>"
            for l in legs)
        body.append(
            f"<tr class='deal{' warnrow' if warn else ''}{' hedge' if is_hedge(g) else ''}' id='deal-{n}' data-sec='{sk}' onclick='toggleDeal({n})'>"
            f"<td><span class='tog'>&#9656;</span> {pallino}<b>{('&#9888; ' if warn else '') + g['nome']}</b>"
            + ("" if gid == "unmapped" else
               f" <a class='beicon' href='http://127.0.0.1:8770/?deal={gid}' target='_blank'"
               f" onclick='event.stopPropagation()' title='Apri il payoff nel costruttore di strategie'>payoff</a>")
            + ("" if gid not in per_gruppo else
               f" <a class='actflag {per_gruppo[gid]['stato']}' href='#azioni'"
               f" onclick='event.stopPropagation()'"
               f" title='{_html.escape(per_gruppo[gid]['titolo'])}'>azione</a>")
            + "</td>"
            f"<td>{strat}{star}</td><td>{scad}</td><td class='num'>{be_html}</td><td class='num'>{cash}</td><td class='num'>{pnl}</td><td class='num'>{close_html}</td>"
            f"<td class='num pos'>{'<br>'.join(mxs)}</td><td class='num neg'>{'<br>'.join(mns)}</td><td class='num'>{delta_html}</td><td class='num'>{vega_s}</td><td class='num'>{theta_s}</td></tr>"
            f"<tr class='legs' id='legs-{n}' data-sec='{sk}' hidden><td colspan='12'><p class='sub'>{g.get('note','')}</p><table><tr><th>Gamba</th><th class='num'>Qta</th><th class='num'>Costo medio <span class='sub'>(valuta contratto)</span></th>"
            f"<th class='num'>Prezzo</th><th class='num'>Chiudi a</th><th class='num'>Valore EUR</th><th class='num'>P/L EUR</th><th class='num'>IV</th><th class='num'>Delta (per opzione)</th><th class='num'>Delta posizione</th><th class='num'>Vega</th><th class='num'>Theta al giorno</th></tr>{legrows}</table></td></tr>")
    # ---- righe dei totali, in EUR al cambio di TWS ----
    # Se ne generano DUE: senza protezioni di coda (predefinita) e con. L'interruttore
    # sopra la tabella scambia quale delle due e' visibile, senza ricalcolare nulla.
    fx = fx or {"EUR": 1.0}

    def tot_cell(T, F, key, cls="", cash=False):
        ill, parz = F.get(key + "_ill", set()), F.get(key, set())
        tot, ok = 0.0, True
        for c, v in T[key].items():
            r = fx.get(c)
            if not r:
                ok = False
                continue
            tot += v / r
        if ill:
            return f"<td class='num {cls}'>illimitato</td>"
        if not T[key] and not parz:
            return "<td class='num'></td>"
        txt = (f"{'debito' if tot >= 0 else 'credito'} {abs(tot):,.0f} EUR" if cash
               else f"<span class='{'pos' if tot >= 0 else 'neg'}'>{tot:+,.0f} EUR</span>")
        if parz or not ok:
            txt += " <span class='sub'>(parz.)</span>"
        return f"<td class='num {cls}'>{txt}</td>"

    def sub_rows(sk):
        S = SECT[sk]
        nome = mese_etichetta(sk[:6])
        out_ = []
        solo_protez = S["n_nh"] == 0
        for vista in (("all",) if solo_protez else ("nh", "all")):
            T, F = S[vista]
            et = "" if solo_protez else (" &middot; protezioni di coda escluse" if vista == "nh" else " &middot; protezioni incluse")
            dv = "fisso" if solo_protez else vista
            out_.append(
                f"<tr class='tot subtot sechead' data-sec='{sk}' data-vista='{dv}' onclick='toggleSec(\"{sk}\")'"
                f"{' hidden' if (vista == 'all' and not solo_protez) else ''}>"
                f"<td title='Posizioni con la scadenza pi\u00f9 vicina in questo mese. Totali in EUR al cambio di adesso{et.replace(' &middot; ', ', ')}. Clic per aprire o chiudere.'>"
                f"<span class='sectog'>&#9662;</span> <b>{nome}</b> <span class='sub'>{S['n'] if vista == 'all' else S['n_nh']} pos."
                f"{' + protezioni' if (vista == 'all' and not solo_protez) else ''}</span></td><td colspan='3'></td>"
                + tot_cell(T, F, "cash", cash=True) + tot_cell(T, F, "pnl") + tot_cell(T, F, "close")
                + tot_cell(T, F, "mx") + tot_cell(T, F, "mn")
                + "<td></td>"
                f"<td class='num' title='Vega della sezione in EUR per punto di volatilit\u00e0'>{S['v_' + vista]:+,.0f}{' <span class=sub>(parz.)</span>' if S['v_parz'] else ''}</td>"
                f"<td class='num' title='Theta della sezione in EUR al giorno'>{S['t_' + vista]:+,.0f}</td></tr>")
        return "".join(out_)

    body = [sub_rows(x[1]) if isinstance(x, tuple) else x for x in body]
    cambi = ", ".join(f"EUR{c} {fx[c]:.4f}" for c in sorted(fx) if c != "EUR" and fx.get(c))
    for vista, etichetta, nascosta in (("nh", "protezioni di coda escluse", False),
                                       ("all", "protezioni di coda incluse", True)):
        T, F = TT[vista], FF[vista]
        manca = sorted({c for k in T for c in T[k] if not fx.get(c)})
        body.append(
            f"<tr class='tot' data-vista='{vista}'{' hidden' if nascosta else ''}>"
            f"<td><b>TOTALE</b> <span class='sub'>in EUR{', cambio ' + cambi if cambi else ''}"
            f" &middot; {etichetta}" + (f" &mdash; cambio mancante per {', '.join(manca)}" if manca else "")
            + "</span></td><td colspan='3'></td>"
            + tot_cell(T, F, "cash", cash=True) + tot_cell(T, F, "pnl") + tot_cell(T, F, "close")
            + tot_cell(T, F, "mx") + tot_cell(T, F, "mn")
            + "<td colspan='3'></td></tr>")

    return (POS_ASSETS + "<div class='card'><div class='card-head'><h2>Posizioni</h2><div class='deals-tools'>"
            "<label class='hedgetog'><input type='checkbox' id='tot-hedge' onchange='vistaTotali(this.checked)'> protezioni di coda nel totale</label>"
            "<button onclick='allDeals(true)'>Espandi tutto</button><button onclick='allDeals(false)'>Comprimi tutto</button></div></div>"
            "<div class='table-scroll'><table class='deals'><tr><th>Posizione</th><th>Strategia</th><th>Scadenza</th><th class='num'>Pareggio a scadenza</th><th class='num'>Debito / credito</th>"
            "<th class='num'>P/L</th><th class='num'>Chiudendo ora</th><th class='num'>Max profitto</th><th class='num'>Max perdita</th><th class='num'>Delta</th><th class='num'>Vega</th><th class='num'>Theta</th></tr>"
            + "".join(body) + "</table></div>"
            + conc_html(conc, nav, fx)
            + (            "<div class='note'><b>Pallino</b>: come scadrebbe la posizione <b>se il sottostante restasse "
            "dove sta adesso</b>, alla scadenza pi&ugrave; vicina &mdash; <span class='dot up'></span>in utile, "
            "<span class='dot down'></span>in perdita, <span class='dot nd'></span>non calcolabile. Passa sopra "
            "per l'importo. Non dice nulla su quanto il sottostante possa ancora muoversi: per quello guarda "
            "Pareggio e, nel riquadro Azioni, la probabilit&agrave;. "
            "Clic su una riga per vedere le gambe. Massimo profitto e perdita a scadenza, costo pagato o credito incassato già compresi. * = scadenze diverse: stima alla scadenza più vicina, gamba lunga valutata alla IV attuale. Pareggio: &uarr; guadagni sopra, &darr; guadagni sotto, &darr;&uarr; guadagni tra i due valori. In azzurro le protezioni di coda, sempre in fondo. Delta: equivalente in azioni (o unità di indice) della posizione; +100 = si muove come 100 azioni. <b>Chiudendo ora</b>: P/L che incasseresti davvero uscendo adesso, vendendo le gambe lunghe al denaro e ricomprando le corte alla lettera (il P/L accanto è a metà spread e non è incassabile); commissioni escluse.</div>")
            + "</div>")


def scrivi_stato_portafoglio(groups, account):
    """Stato che il server locale usa per filtrare le occasioni (gia' implementate, tetto 2% NAV)
    e per la concentrazione, senza dover rigenerare la dashboard a ogni ricerca."""
    fx = account.get("_fx") or {"EUR": 1.0}
    nav = (account.get("NetLiquidation") or (0.0,))[0]
    conc = concentrazioni(groups, fx)
    posizioni = []
    for g in groups.values():
        ls = [[l["symbol"], l["expiry"], float(l["strike"] or 0), l["right"], 1 if l["position"] > 0 else -1]
              for l in g["legs"] if l.get("secType") == "OPT"]
        if ls:
            posizioni.append(ls)
    protezioni = []
    for g in groups.values():
        if "protezion" in g["nome"].lower():
            for l in g["legs"]:
                if l.get("secType") == "OPT" and l["position"] > 0:
                    protezioni.append(dict(sym=l["symbol"], strike=float(l["strike"]), right=l["right"], qty=float(l["position"]),
                                           mult=float(l.get("mult") or 100), prezzo_ora=float(l.get("marketPrice") or 0.0),
                                           cur=l["currency"]))
    (DATA / "stato_portafoglio.json").write_text(json.dumps(dict(protezioni=protezioni,
        generato=dt.datetime.now().isoformat(timespec="seconds"), nav=nav, fx=fx,
        conc={f"{k[0]}|{k[1]}": {"eur": v["eur"]} for k, v in conc.items()}, posizioni=posizioni)))


def render(groups, unmapped, account, total_vega, vega_missing, total_theta, theta_missing,
           stress, stress_err, transf=None, pnl=([], ""), az=None):
    now = dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    fx_ = account.get("_fx") or {"EUR": 1.0}
    _FX.clear(); _FX.update(fx_); _FX.setdefault("EUR", 1.0)
    nav_eur = (account.get("NetLiquidation") or (0.0,))[0]
    conc = concentrazioni(groups, fx_)
    # Link a IndexMachine, solo se la pagina e gia stata generata (uv run --with yfinance index_machine.py)
    im_link = (' &middot; <a href="http://127.0.0.1:8770/index_machine.html"'
               ' title="Servita da chain_builder.py, cosi puo chiedere la IV a TWS">IndexMachine</a>'
               if (DATA / "index_machine.html").exists() else "")
    # Il costruttore di strategie gira sempre come servizio utente (opzioni-chain-builder.service).
    im_link += (' &middot; <a href="http://127.0.0.1:8770/" title="Server locale sempre attivo: opzioni-chain-builder.service">'
                'Costruttore strategie</a>')

    def leg_row(l):
        strike = f"{l['strike']:g}" if l["strike"] else ""
        desc = f"{l['symbol']} {l['expiry']} {strike}{l['right']}" if l["secType"] == "OPT" else f"{l['symbol']} azioni"
        qty = f"{l['position']:+.0f}"
        pnl = l["unrealizedPNL"]
        cls = "pos" if pnl >= 0 else "neg"
        # moltiplicatore vero, non 100 fisso: ESTX50 vale 10 e i numeri per gamba
        # non tornavano con il totale del gruppo
        m = l.get("mult") or 100
        vega_s = f"{eur(l['vega']*l['position']*m, l['currency']):+.0f}" if l.get("vega") is not None else "n/d"
        theta_s = f"{eur(l['theta']*l['position']*m, l['currency']):+,.0f}" if l.get("theta") is not None else "n/d"
        return (f"<tr><td>{desc}</td><td>{qty}</td><td>{l['avgCost']:.2f}</td>"
                f"<td>{l['marketPrice']:.2f}</td><td>{eur(l['marketValue'], l['currency']):,.0f}</td>"
                f"<td class='{cls}'>{fmt_money(pnl, l['currency'])}</td><td>{vega_s}</td><td>{theta_s}</td></tr>")

    def group_card(g, warn=False):
        cls = "pos" if g["pnl"] >= 0 else "neg"
        rows = "\n".join(leg_row(l) for l in g["legs"])
        debito = g["debito"]
        pct = (g["pnl"] / abs(debito) * 100) if debito else 0.0
        _c = g.get("cur") or "EUR"
        debito_label = "Debito netto" if debito >= 0 else "Credito netto"
        vega_s = f"{eur(g['vega'], _c):+.0f}" + ("" if g["vega_ok"] else " (parziale)")
        theta_s = f"{eur(g.get('theta', 0), _c):+,.0f}" + ("" if g.get("theta_ok", True) else " (parziale)")
        card_cls = "card warn" if warn else "card"
        title = ("&#9888; " if warn else "") + g["nome"]
        return f"""
        <div class="{card_cls}">
          <div class="card-head">
            <h2>{title}</h2>
            <div class="pnl {cls}">{fmt_money(g['pnl'], g['cur'] or '')} <span class="pct">({pct:+.1f}%)</span></div>
          </div>
          <div class="note">{g['note']} &middot; {debito_label}: {abs(eur(debito, _c)):,.0f} EUR &middot; Vega: {vega_s} &middot; Theta: {theta_s} al giorno</div>
          <table>
            <tr><th>Gamba</th><th>Qta</th><th>Costo medio (val. contratto)</th><th>Prezzo</th><th>Valore EUR</th><th>P/L EUR</th><th>Vega EUR</th><th>Theta EUR</th></tr>
            {rows}
          </table>
        </div>"""

    group_cards = [group_card(g) for g in groups.values() if g["legs"]]

    unmapped_html = ""
    if unmapped:
        u = {"nome": "Posizioni non mappate", "note": "Aperte sul conto ma assenti da data/portfolio_map.json &mdash; aggiungile alla mappa.",
             "legs": unmapped, "pnl": sum(l["unrealizedPNL"] for l in unmapped),
             "debito": sum(l["avgCost"] * l["position"] for l in unmapped),
             "vega": sum((l["vega"] or 0) * l["position"] * (l.get("mult") or 100) for l in unmapped),
             "vega_ok": all(l.get("vega") is not None for l in unmapped),
             "theta": sum((l.get("theta") or 0) * l["position"] * (l.get("mult") or 100) for l in unmapped),
             "theta_ok": all(l.get("theta") is not None for l in unmapped if l["secType"] == "OPT"),
             "cur": unmapped[0]["currency"] if unmapped else ""}
        unmapped_html = group_card(u, warn=True)

    # P/L totale in EUR al cambio di adesso (somma delle gambe, ciascuna convertita)
    pnl_eur = sum(eur(l["unrealizedPNL"], l["currency"])
                  for g in groups.values() for l in g["legs"]) \
        + sum(eur(l["unrealizedPNL"], l["currency"]) for l in unmapped)
    pnl_tiles = (f'<div class="stat"><div class="label">P/L aperto totale in EUR</div>'
                 f'<div class="value {"pos" if pnl_eur >= 0 else "neg"}">{fmt_money(pnl_eur, "EUR")}</div></div>')
    if pnl and pnl[0] and pnl[0][-1].get("fonte") == "live":
        v = pnl[0][-1]["floating"]
        pnl_tiles = (f'<div class="stat"><div class="label">P/L aperto totale in EUR (come TWS)</div>'
                     f'<div class="value {"pos" if v >= 0 else "neg"}">{fmt_money(v, "EUR")}</div></div>') + pnl_tiles.replace("P/L aperto totale in EUR", "P/L aperto, somma gambe al cambio di adesso")

    nav = account.get("NetLiquidation", (0, "EUR"))
    cash = account.get("TotalCashValue", (0, "EUR"))
    gross = account.get("GrossPositionValue", (0, "EUR"))
    initm = account.get("InitMarginReq", (0, "EUR"))
    cushion = account.get("Cushion", (0, ""))

    transf_html = ""
    if transf:
        t_res, t_alerts = transf
        items = "".join(f"<li><b>{_html.escape(a)}</b></li>" for a in t_alerts)
        for r in t_res:
            g = r["group"][0]["short"].contract
            nm = f"{r['sym']} calendar {'put' if g.right == 'P' else 'call'} {g.strike:g}"
            ok = [x for x in r["rows"] if x.get("mid") is not None]
            if not ok:
                items += f"<li>{nm}: quote non disponibili ora</li>"; continue
            parts = ", ".join(("<b>pronto</b>" if x["ok_mid"] else f"mancano {eur(x['gap'], r['cur']):,.0f} EUR") + f" con ali da {x['w']:g}" for x in ok[:2])
            cn = "" if r["close_now"] is None else f" &middot; chiudendo ora: {r['close_now']:+,.0f} {r['cur']}"
            items += f"<li>{nm}: {parts}{cn}</li>"
        if not t_alerts:
            items += ("<li class='sub'>Lo stato completo, con la situazione prima e dopo l'aggiustamento, "
                      "sta nel riquadro <b>Azioni migliorative</b> in cima alla pagina. "
                      "Niente notifiche di sistema: tutto qui dentro.</li>")
        transf_html = f"""<div class="card{' warn' if t_alerts else ''}"><div class="card-head"><h2>{'&#9888; ' if t_alerts else ''}Avvisi: trasformazione calendar e regole di prezzo</h2></div>
          <ul class="stress-list">{items}</ul></div>"""

    stress_html = ""
    # con il mercato chiuso stress.py non riceve le greche e restituisce zeri: non e' un risultato
    if stress and all(float(r["valore"].replace(",", "")) == 0 and float(r["con_pct"]) == 0 for r in stress["rows"]):
        stress = None
        stress_err = ("nessun dato di mercato per le opzioni (mercato chiuso): gli zeri non sono un risultato. "
                      "Lo stress test vero lo fai tu da TWS ogni settimana.")
    if stress:
        rows_html = "\n".join(
            f"<li><b>{r['nome'].strip()}</b> ({r['mossa']}%, IV {r['iv']}): "
            f"<span class='{'pos' if not r['con_pct'].startswith('-') else 'neg'}'>{r['con_pct']}% CON protezioni</span>"
            f" &middot; {r['senza_pct']}% senza &middot; valore protezioni {r['valore']} &euro;</li>"
            for r in stress["rows"]
        )
        stress_html = f"""
        <div class="card">
          <div class="card-head"><h2>Ultimo stress test</h2><div class="note">{stress['generated']}</div></div>
          <div class="note">NAV al momento del test: {stress['nav']} &euro;</div>
          <ul class="stress-list">{rows_html}</ul>
        </div>"""
    else:
        stress_html = f"""<div class="card warn"><div class="card-head"><h2>&#9888; Stress test non disponibile</h2></div>
        <div class="note">{stress_err}</div></div>"""

    vega_note = " (alcune gambe senza dato)" if vega_missing else ""
    theta_note = " (alcune gambe senza dato)" if theta_missing else ""
    # verde se il tempo lavora a favore (posizione netta venditrice), rosso se lo paghi
    theta_cls = "pos" if total_theta >= 0 else "neg"
    all_data = {"groups": groups, "unmapped": unmapped, "account": account,
                "total_vega": total_vega, "total_theta": total_theta, "stress": stress, "generated": now}

    return f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8"><meta http-equiv="refresh" content="600">
<title>Portafoglio Trading Opzioni</title>
<style>
  :root {{ --bg:#0f1420; --card:#171d2b; --text:#e5e9f0; --muted:#8a94a6; --pos:#3ecf8e; --neg:#f0576b; --warnbg:#2b2210; --border:#262e42; }}
  @media (prefers-color-scheme: light) {{
    :root {{ --bg:#f4f5f7; --card:#ffffff; --text:#1a1f2b; --muted:#6b7280; --pos:#0a8f5c; --neg:#c0263f; --warnbg:#fff6e0; --border:#e3e6ec; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:-apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:24px 16px; }}
  h1 {{ font-size:1.4rem; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:0.85rem; margin-bottom:20px; }}
  .sub a {{ color:#3987e5; }}
  .beicon {{ font-size:0.72rem; font-weight:600; color:#3987e5; text-decoration:none;
             border:1px solid #3987e5; border-radius:5px; padding:1px 5px; margin-left:7px; opacity:.75; }}
  .beicon:hover {{ opacity:1; background:#3987e5; color:#fff; }}
  .overview {{ display:flex; flex-wrap:wrap; gap:12px; margin-bottom:24px; }}
  .stat {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:12px 16px; min-width:140px; }}
  .stat .label {{ color:var(--muted); font-size:0.75rem; text-transform:uppercase; letter-spacing:.03em; }}
  .stat .value {{ font-size:1.15rem; font-weight:600; margin-top:2px; }}
  .layout {{ display:flex; gap:16px; flex-wrap:wrap; align-items:flex-start; }}
  .col-main {{ flex:2; min-width:340px; }}
  .col-side {{ flex:1; min-width:300px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px 18px; margin-bottom:16px; }}
  .card.warn {{ background:var(--warnbg); }}
  .card-head {{ display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:8px; }}
  .card h2 {{ font-size:1.02rem; margin:0; }}
  .note {{ color:var(--muted); font-size:0.8rem; margin:4px 0 10px; }}
  .pnl {{ font-size:1.15rem; font-weight:700; }}
  .pnl .pct {{ font-size:0.85rem; font-weight:500; opacity:0.85; }}
  .pos {{ color:var(--pos); }}
  .neg {{ color:var(--neg); }}
  table {{ width:100%; border-collapse:collapse; font-size:0.85rem; }}
  th {{ text-align:left; color:var(--muted); font-weight:500; padding:4px 8px; border-bottom:1px solid var(--border); }}
  td {{ padding:5px 8px; border-bottom:1px solid var(--border); }}
  .stress-list {{ margin:0; padding-left:18px; font-size:0.85rem; line-height:1.6; }}
  footer {{ color:var(--muted); font-size:0.75rem; margin-top:24px; }}
</style></head>
<body>
  <h1>Portafoglio &mdash; Trading Opzioni</h1>
  <div class="sub">Generato il {now} &middot; sola lettura, IBKR &middot; rilancia <code>uv run dashboard.py</code> per aggiornare{im_link}</div>

  <div class="overview">
    {pnl_tiles}
    <div class="stat"><div class="label">NAV</div><div class="value">{nav[0]:,.0f} {nav[1]}</div></div>
    <div class="stat"><div class="label">Liquidit&agrave;</div><div class="value">{cash[0]:,.0f} {cash[1]}</div></div>
    <div class="stat"><div class="label">Valore posizioni</div><div class="value">{gross[0]:,.0f} {gross[1]}</div></div>
    <div class="stat"><div class="label">Margine iniziale</div><div class="value">{initm[0]:,.0f} {initm[1]}</div></div>
    <div class="stat"><div class="label">Cushion</div><div class="value">{cushion[0]*100:.1f}%</div></div>
    <div class="stat"><div class="label">Vega totale{vega_note}</div><div class="value">{total_vega:+.0f}</div></div>
    <div class="stat"><div class="label">Theta totale{theta_note}</div><div class="value {theta_cls}">{total_theta:+,.0f}</div>
      <div class="pct" style="color:var(--muted);font-size:0.75rem">al giorno, a mercati fermi</div></div>
  </div>

  {ACT_ASSETS}{ACT_COPIA}
  {chart_html(*pnl)}

  {positions_table(groups, unmapped, account.get("_fx"), az, nav_eur, conc)}

  {opportunita_card(groups, conc, nav_eur, fx_)}

  <div id="azioni">{actions_card(az, groups)}</div>

  <div class="layout">
    <div class="col-side">{transf_html}</div>
    <div class="col-side">{stress_html}</div>
  </div>

  {summary_html(*pnl)}

  <footer>Dati grezzi per estensioni future (bottoni/grafici) incorporati sotto in JSON.</footer>
  <script type="application/json" id="portfolio-data">{json.dumps(all_data, default=str)}</script>
</body></html>"""


def main():
    stress, stress_err = run_stress()
    ib = connect("127.0.0.1", 7496, 170)
    try:
        groups, unmapped, account, total_vega, vega_missing, total_theta, theta_missing = build_data(ib)
        try:
            t_lines, t_alerts, t_res = transform.check(ib)
            transf = (t_res, t_alerts)
        except Exception as e:
            transf = ([], [f"controllo trasformazione non riuscito: {e}"]); t_alerts = []
        try:
            pnl = pnl_history.update(ib)
        except Exception as e:
            pnl = ([], f"storico P/L non aggiornato: {e}")
        # azioni migliorative su ogni posizione: rivalutate a ogni giro, mostrate in pagina
        try:
            az = actions.check(ib, groups, transf[0] if transf else [])
        except Exception as e:
            az = [dict(gid=None, nome="(motore azioni)", kind="errore", cur="", stato=actions.INFO,
                       titolo=f"controllo non riuscito: {e}", perche="", ordine="",
                       prima=None, dopo=None, chiudendo_ora=None, add_legs=[], nota="")]
    finally:
        ib.disconnect()
    # regole di prezzo (data/price_rules.json): es. un titolo >= soglia -> chiudere tutte le sue posizioni
    price_alerts = check_price_rules(groups, unmapped)
    # drawdown di portafoglio: avviso a -8%, kill-switch a -15% (solo notifica, mai azione)
    dd_alerts, dd_pct, dd_peak = check_drawdown(account.get("NetLiquidation", (0.0, "EUR"))[0])
    if dd_alerts:
        lastd_f = DATA / "last_drawdown_alert.txt"
        lastd = lastd_f.read_text() if lastd_f.exists() else ""
        lastd_f.write_text("\n".join(dd_alerts))
        if "\n".join(dd_alerts) != lastd:
            subprocess.run(["notify-send", "-u", "critical", "Drawdown di portafoglio",
                            "\n".join(dd_alerts)[:900]], check=False)
    price_alerts = dd_alerts + price_alerts
    t_alerts = price_alerts + t_alerts
    if transf:
        transf = (transf[0], price_alerts + transf[1])
    # NIENTE notifica di sistema per le trasformazioni (deciso dall'utente il 01/10/2026):
    # arrivava a ogni giro, anche per trasformazioni che ai prezzi veri non si riempivano.
    # Vive tutto nel riquadro "Azioni migliorative" della pagina. La notifica resta solo per
    # il drawdown (-8% / -15%), che e' un avviso di portafoglio e non puo' aspettare un reload.
    (DATA / "last_transform_alert.txt").write_text("\n".join(t_alerts))
    html = render(groups, unmapped, account, total_vega, vega_missing, total_theta, theta_missing,
                  stress, stress_err, transf, pnl, az)
    html = eur_testo(html)
    DATA.mkdir(exist_ok=True)
    scrivi_stato_portafoglio(groups, account)
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"Scritto: {OUT_FILE}")
    print(f"Apri: file://{OUT_FILE.resolve()}  oppure http://127.0.0.1:8770/dashboard.html (col bottone Ricalcola occasioni)")


if __name__ == "__main__":
    main()
