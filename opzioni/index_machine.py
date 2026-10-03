"""IndexMachine (SOLA LETTURA): studio della natura stagionale di un indice dentro una
finestra temporale, e proiezione in avanti degli strike con volatilita implicita.

Ricostruzione dell'omonima applicazione di Insider Academy. Vedi il vault:
(documentazione dello strumento)

Questo script NON calcola nulla di operativo: scarica solo lo storico dei sottostanti
(yfinance) e scrive data/index_machine.html con i dati incorporati. Tutta l'analisi
(stagionalita, finestra, esclusione anni, probabilita, Monte Carlo) gira nella pagina,
cosi la finestra e i parametri si cambiano senza rilanciare lo script.

    uv run --with yfinance index_machine.py
    uv run --with yfinance index_machine.py --from 1990 --open
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = DATA / "index_machine.html"

# Sottostanti esposti dalla pagina. Piu strumenti possono condividere la stessa serie
# yfinance con un fattore di scala: XSP quota un decimo di SPX, la stagionalita e la
# stessa ma i livelli vanno letti nelle unita giuste.
INSTRUMENTS = [
    ("SPX", "^GSPC", 1.0, "S&P 500 (indice)"),
    ("XSP", "^GSPC", 0.1, "Mini-SPX, un decimo di SPX"),
    ("SPY", "SPY", 1.0, "ETF sull'S&P 500"),
    ("RUT", "^RUT", 1.0, "Russell 2000"),
    ("NDX", "^NDX", 1.0, "Nasdaq 100"),
    ("USO", "USO", 1.0, "ETC petrolio"),
    ("UNG", "UNG", 1.0, "ETC natural gas"),
]

# Ticker yfinance che non coincidono con il simbolo IBKR (azioni europee soprattutto).
ALIAS = {"ESTX50": "^STOXX50E", "SAP": "SAP.DE", "SIE": "SIE.DE", "ALV": "ALV.DE",
         "ASML": "ASML.AS", "MC": "MC.PA", "TTE": "TTE.PA", "OR": "OR.PA",
         "ISP": "ISP.MI", "ENI": "ENI.MI", "ENEL": "ENEL.MI", "G": "G.MI"}


def fetch(tickers: list[str], start: str) -> dict:
    import yfinance as yf

    out = {}
    for t in tickers:
        df = yf.download(t, start=start, auto_adjust=False, progress=False, threads=False)
        if df is None or df.empty:
            print(f"  {t}: nessun dato", file=sys.stderr)
            continue
        close = df["Close"]
        if hasattr(close, "columns"):  # yfinance restituisce a volte colonne multi-indice
            close = close.iloc[:, 0]
        close = close.dropna()
        dates = [int(d.strftime("%Y%m%d")) for d in close.index]
        vals = [round(float(v), 4) for v in close.values]
        out[t] = {"d": dates, "c": vals}
        print(f"  {t}: {len(vals)} sedute, dal {dates[0]} al {dates[-1]}")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="start", default="1990-01-01", help="anno di inizio dello storico (default 1990-01-01)")
    p.add_argument("tickers", nargs="*",
                   help="sottostanti da includere: i predefiniti (SPX XSP SPY RUT NDX USO UNG) "
                        "oppure qualunque ticker, azioni comprese (AAPL MSFT NVDA). Senza argomenti usa i predefiniti.")
    p.add_argument("--open", action="store_true", help="apri la pagina nel browser al termine")
    args = p.parse_args()

    # Le azioni fanno la stessa identica analisi degli indici: cambia solo la fonte dello
    # storico. La stagionalita di un titolo esiste come quella di un indice (stagione degli
    # utili, rally di fine anno), solo con meno anni a disposizione.
    if args.tickers:
        insts = []
        for t in args.tickers:
            t = t.upper()
            pre = next((i for i in INSTRUMENTS if i[0] == t), None)
            insts.append(pre or (t, ALIAS.get(t, t), 1.0, "azione o altro ticker"))
    else:
        insts = INSTRUMENTS

    tickers = sorted({src for _, src, _, _ in insts})
    print(f"Scarico lo storico da {args.start}:")
    series = fetch(tickers, args.start)
    if not series:
        print("Nessuna serie scaricata, mi fermo.", file=sys.stderr)
        return 1

    payload = {
        "generated": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "series": series,
        "instruments": [
            {"name": n, "src": s, "scale": sc, "note": note}
            for n, s, sc, note in insts
            if s in series
        ],
    }

    DATA.mkdir(exist_ok=True)
    html = TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":")))
    OUT.write_text(html, encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print(f"\nScritto {OUT} ({size:.0f} KB)")
    if args.open:
        subprocess.run(["xdg-open", str(OUT)], check=False)
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>IndexMachine</title>
<style>
  :root { --bg:#0f1420; --card:#171d2b; --text:#e5e9f0; --muted:#8a94a6; --pos:#3ecf8e; --neg:#f0576b; --border:#262e42;
          --s1:#3987e5; --s2:#e66767; --grid:#262e42; --warnbg:#2b2210; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f4f5f7; --card:#ffffff; --text:#1a1f2b; --muted:#6b7280; --pos:#0a8f5c; --neg:#c0263f; --border:#e3e6ec;
            --s1:#2a78d6; --s2:#e34948; --grid:#e3e6ec; --warnbg:#fff6e0; }
  }
  * { box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:24px 16px; }
  h1 { font-size:1.4rem; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:0.85rem; margin-bottom:18px; }
  .sub a { color:var(--s1); }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px 18px; margin-bottom:16px; position:relative; }
  .card h2 { font-size:1.02rem; margin:0 0 2px; }
  .note { color:var(--muted); font-size:0.8rem; margin:2px 0 12px; line-height:1.5; }
  .controls { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; }
  .field { display:flex; flex-direction:column; gap:4px; }
  .field label { color:var(--muted); font-size:0.72rem; text-transform:uppercase; letter-spacing:.03em; }
  input, select { background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:7px; padding:7px 9px; font:inherit; font-size:0.88rem; }
  #fromtws { cursor:pointer; background:var(--s1); border:1px solid var(--s1); color:#fff; font-weight:600;
             border-radius:7px; padding:7px 11px; font:inherit; font-size:0.88rem; }
  #fromtws:disabled { opacity:.55; cursor:default; }
  #twsmsg a { color:var(--s1); }
  input[type=number] { width:110px; }
  .overview { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px; }
  .stat { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:12px 16px; min-width:150px; flex:1; }
  .stat .label { color:var(--muted); font-size:0.72rem; text-transform:uppercase; letter-spacing:.03em; }
  .stat .value { font-size:1.2rem; font-weight:600; margin-top:3px; }
  .stat .sub2 { color:var(--muted); font-size:0.75rem; margin-top:2px; }
  .layout { display:flex; gap:16px; flex-wrap:wrap; align-items:flex-start; }
  .col { flex:1; min-width:330px; }
  .crit { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:10px; }
  .crit label { font-size:0.85rem; display:flex; align-items:center; gap:5px; }
  .legend { display:flex; gap:14px; flex-wrap:wrap; font-size:0.78rem; color:var(--muted); margin-bottom:6px; }
  .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
  svg { display:block; width:100%; max-width:720px; margin-inline:auto; height:auto; overflow:visible; }
  svg text { fill:var(--muted); font-size:10px; font-family:inherit; }
  .tip { position:fixed; pointer-events:none; background:var(--card); border:1px solid var(--border); border-radius:8px;
         padding:7px 10px; font-size:0.78rem; line-height:1.45; box-shadow:0 6px 20px rgba(0,0,0,.28); opacity:0; transition:opacity .08s; z-index:50; white-space:nowrap; }
  table { width:100%; border-collapse:collapse; font-size:0.82rem; }
  th { text-align:right; color:var(--muted); font-weight:500; padding:4px 8px; border-bottom:1px solid var(--border); }
  th:first-child, td:first-child { text-align:left; }
  td { padding:4px 8px; border-bottom:1px solid var(--border); text-align:right; }
  tr.off td { color:var(--muted); text-decoration:line-through; }
  details summary { cursor:pointer; color:var(--muted); font-size:0.82rem; }
  .pos { color:var(--pos); } .neg { color:var(--neg); }
  .warn { background:var(--warnbg); }
  footer { color:var(--muted); font-size:0.75rem; margin-top:24px; line-height:1.6; }
  .chip { display:inline-block; background:var(--bg); border:1px solid var(--border); border-radius:20px; padding:2px 9px; font-size:0.75rem; margin:2px 3px 0 0; cursor:pointer; }
  .chip.off { opacity:.45; text-decoration:line-through; }
  .card h2 { display:flex; align-items:center; gap:7px; }
  .help { all:unset; cursor:pointer; width:17px; height:17px; border-radius:50%; border:1px solid var(--muted);
          color:var(--muted); font-size:11px; font-weight:600; line-height:1; display:inline-flex;
          align-items:center; justify-content:center; flex:0 0 auto; }
  .help:hover, .help[aria-expanded=true] { color:var(--card); background:var(--s1); border-color:var(--s1); }
  .help:focus-visible { outline:2px solid var(--s1); outline-offset:2px; }
  .pop { position:absolute; display:none; width:min(380px, calc(100% - 36px)); background:var(--card); border:1px solid var(--border);
         border-radius:10px; padding:14px 16px; font-size:0.82rem; line-height:1.55; z-index:60;
         box-shadow:0 10px 34px rgba(0,0,0,.34); }
  .pop.open { display:block; }
  .pop h3 { margin:0 0 8px; font-size:0.9rem; }
  .pop dt { color:var(--s1); font-weight:600; font-size:0.74rem; text-transform:uppercase; letter-spacing:.03em; margin-top:9px; }
  .pop dt:first-of-type { margin-top:0; }
  .pop dd { margin:2px 0 0; }
  .pop dl { margin:0; }
  .pop .care { color:var(--s2); }
  .pop .close { all:unset; float:right; cursor:pointer; color:var(--muted); font-size:1rem; line-height:1; padding:0 2px; }
</style></head>
<body>
<h1>IndexMachine</h1>
<div class="sub">Natura stagionale del sottostante nella finestra scelta, e proiezione degli strike con la volatilita implicita.
 &middot; <a href="dashboard.html">torna al portafoglio</a> &middot; dati al <span id="gen"></span>
 &middot; rilancia <code>uv run --with yfinance index_machine.py</code> per aggiornare lo storico</div>

<div class="card">
  <div class="controls">
    <div class="field"><label for="inst">Sottostante</label><select id="inst"></select></div>
    <div class="field"><label for="d0">Inizio finestra</label><input type="date" id="d0"></div>
    <div class="field"><label for="d1">Scadenza</label><input type="date" id="d1"></div>
    <div class="field"><label for="spot">Prezzo</label><input type="number" id="spot" step="0.01"></div>
    <div class="field"><label for="iv">Vol. implicita ATM %</label><input type="number" id="iv" step="0.1" value="15"></div>
    <div class="field"><label for="rate">Tasso %</label><input type="number" id="rate" step="0.1" value="0"></div>
    <div class="field"><label>&nbsp;</label><button id="fromtws" type="button">Prendi da TWS</button></div>
    <div class="field"><label for="paths">Percorsi MC</label><select id="paths">
      <option>10000</option><option selected>20000</option><option>50000</option><option>100000</option></select></div>
  </div>
  <div class="note" id="instnote"></div>
  <div class="note" id="twsmsg" style="margin:0"></div>
</div>

<div class="overview" id="tiles"></div>

<div class="card">
  <h2>Selezione dello strike</h2>
  <div class="note">I tre strike automatici escono dall'analisi storica della finestra, esclusi gli anni disattivati.
    <b>Aggressivo</b> = 5&deg; percentile dei rendimenti a scadenza. <b>Medio</b> = peggior rendimento a scadenza osservato.
    <b>Cautelativo</b> = peggior escursione nel transitorio osservata. Lo strike mostrato e sempre <i>lato put</i>, sotto il prezzo.</div>
  <div class="crit">
    <label><input type="radio" name="crit" value="auto" checked> Automatico</label>
    <select id="autopick"><option value="0">Aggressivo</option><option value="1">Medio</option><option value="2" selected>Cautelativo</option></select>
    <label><input type="radio" name="crit" value="prob"> Per probabilita a scadenza</label><input type="number" id="vprob" value="98" step="0.1" style="width:80px">
    <label><input type="radio" name="crit" value="pct"> Per ritracciamento %</label><input type="number" id="vpct" value="10" step="0.5" style="width:80px">
    <label><input type="radio" name="crit" value="pts"> Per distanza in punti</label><input type="number" id="vpts" value="300" step="1" style="width:90px">
    <label><input type="radio" name="crit" value="man"> Strike manuale</label>
    <input type="number" id="vman" step="0.01" style="width:110px" list="reali" placeholder="scegli tu">
    <datalist id="reali"></datalist>
  </div>
  <div id="autolist" class="note"></div>
</div>

<div class="layout">
  <div class="col">
    <div class="card"><h2>Curva delle probabilita <button class="help" data-help="prob" aria-expanded="false" aria-label="Come si legge questo grafico">?</button></h2>
      <div class="note">Passa sopra la curva: il primo numero e la probabilita che il sottostante <b>non superi</b> quel livello a scadenza, il secondo che lo <b>raggiunga o superi</b>. Linea rossa = strike selezionato.
        <b>Clicca sulla curva per scegliere tu lo strike</b>, invece di prendere quello automatico.</div>
      <div id="c_prob"></div></div>
    <div class="card"><h2>Stagionalita annuale <button class="help" data-help="seas" aria-expanded="false" aria-label="Come si legge questo grafico">?</button></h2>
      <div class="note">Percorso medio dell'anno, base 100 alla prima seduta. La banda evidenziata e la finestra selezionata.</div>
      <div id="c_seas"></div></div>
  </div>
  <div class="col">
    <div class="card"><h2>Monte Carlo &mdash; probabilita nel transitorio <button class="help" data-help="mc" aria-expanded="false" aria-label="Come si legge questo grafico">?</button></h2>
      <div class="note">Percorsi simulati fino a scadenza. Serve a rispondere a una domanda diversa da quella di Black-Scholes: non "dove finisce", ma "quante volte tocca lo strike anche solo per un momento".</div>
      <div id="c_mc"></div></div>
    <div class="card"><h2>Rendimenti mensili <button class="help" data-help="mon" aria-expanded="false" aria-label="Come si legge questo grafico">?</button></h2>
      <div class="note">Media dei rendimenti di ogni mese, con la deviazione standard come baffo verticale.</div>
      <div id="c_mon"></div></div>
  </div>
</div>

<div class="card">
  <h2>Anni nella finestra <button class="help" data-help="years" aria-expanded="false" aria-label="Come si legge questo grafico">?</button></h2>
  <div class="note">Rendimento a scadenza e peggior escursione nel transitorio, anno per anno. <b>Clicca un anno per escluderlo dai calcoli</b>, come si fa con l'occhietto nel software originale: serve quando le condizioni di quell'anno non sono paragonabili a oggi.</div>
  <div class="legend"><span><i style="background:var(--s1)"></i>Rendimento a scadenza</span><span><i style="background:var(--s2)"></i>Peggior escursione nel transitorio</span></div>
  <div id="c_years"></div>
  <div id="chips" style="margin-top:10px"></div>
  <details style="margin-top:12px"><summary>Tabella dei dati</summary><div id="tbl" style="margin-top:8px"></div></details>
</div>

<footer>
  Ricostruzione di IndexMachine (Insider Academy) a partire dal video-manuale pubblico. Non e il loro software e non ne condivide le formule:
  gli strike automatici usano i criteri dichiarati qui sopra, non i loro.<br>
  Storico da yfinance. Prezzo e volatilita implicita si possono scrivere a mano oppure prendere da TWS con il pulsante
  <b>Prendi da TWS</b>, che chiede la IV al denaro della scadenza scelta al server di <code>chain_builder.py</code>
  (funziona solo aprendo questa pagina da <a href="http://127.0.0.1:8770/index_machine.html">127.0.0.1:8770</a>).
  Le probabilita a scadenza usano il modello lognormale di Black-Scholes, quelle nel transitorio un Monte Carlo con moto browniano geometrico.
  <b>Nessun ordine, nessuna connessione al conto: questa pagina non tocca IBKR.</b>
</footer>
<div class="tip" id="tip"></div>
<div class="pop" id="pop" role="dialog" aria-label="Come si legge"></div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const P = JSON.parse(document.getElementById('payload').textContent);
document.getElementById('gen').textContent = P.generated;
const $ = id => document.getElementById(id);
const tip = $('tip');

/* ---------- matematica ---------- */
function ncdf(x){ // Abramowitz-Stegun 7.1.26 su erf
  const s = x < 0 ? -1 : 1; x = Math.abs(x)/Math.SQRT2;
  const t = 1/(1+0.3275911*x);
  const y = 1 - (((((1.061405429*t - 1.453152027)*t) + 1.421413741)*t - 0.284496736)*t + 0.254829592)*t*Math.exp(-x*x);
  return 0.5*(1 + s*y);
}
function nppf(p){ // Acklam
  if (p <= 0) return -Infinity; if (p >= 1) return Infinity;
  const a=[-39.69683028665376,220.9460984245205,-275.9285104469687,138.3577518672690,-30.66479806614716,2.506628277459239];
  const b=[-54.47609879822406,161.5858368580409,-155.6989798598866,66.80131188771972,-13.28068155288572];
  const c=[-0.007784894002430293,-0.3223964580411365,-2.400758277161838,-2.549732539343734,4.374664141464968,2.938163982698783];
  const d=[0.007784695709041462,0.3224671290700398,2.445134137142996,3.754408661907416];
  const pl=0.02425; let q,r;
  if (p < pl){ q=Math.sqrt(-2*Math.log(p));
    return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
  if (p > 1-pl){ q=Math.sqrt(-2*Math.log(1-p));
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
  q=p-0.5; r=q*q;
  return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1);
}
const mean = a => a.reduce((s,x)=>s+x,0)/a.length;
function sd(a){ if(a.length<2) return 0; const m=mean(a); return Math.sqrt(a.reduce((s,x)=>s+(x-m)*(x-m),0)/(a.length-1)); }
function pct(a,q){ if(!a.length) return NaN; const s=[...a].sort((x,y)=>x-y); const i=(s.length-1)*q;
  const lo=Math.floor(i), hi=Math.ceil(i); return lo===hi ? s[lo] : s[lo]+(s[hi]-s[lo])*(i-lo); }
const fmtP = x => (x*100).toFixed(2)+'%';
// una probabilita non si scrive mai 100%: si satura, come fa il software originale
const fmtProb = x => (Math.min(0.9999, Math.max(0.0001, x))*100).toFixed(2)+'%';
const fmtN = (x,d=2) => x.toLocaleString('it-IT',{minimumFractionDigits:d,maximumFractionDigits:d});

/* ---------- dati ---------- */
let excluded = new Set();
function inst(){ return P.instruments.find(i=>i.name===$('inst').value) || P.instruments[0]; }
function serie(){ const i=inst(); const s=P.series[i.src]; return {d:s.d, c:s.c.map(v=>v*i.scale), scale:i.scale}; }
const ymd = n => ({y:Math.floor(n/10000), m:Math.floor(n/100)%100, d:n%100});
const md  = n => n%10000;                       // MMDD
const toN = s => +s.replaceAll('-','');         // "2026-01-16" -> 20260116

/* Per ogni anno trova entrata e uscita della finestra e calcola le statistiche.
   Se la finestra scavalca l'anno (es. 28/11 -> 16/01) l'uscita si cerca nell'anno dopo. */
function windowStats(){
  const S = serie(), a0 = md(toN($('d0').value)), a1 = md(toN($('d1').value));
  const cross = a1 <= a0, out = [];
  const years = [...new Set(S.d.map(n=>ymd(n).y))].sort();
  for (const Y of years){
    const from = Y*10000 + a0, to = (cross?Y+1:Y)*10000 + a1;
    let i0=-1, i1=-1;
    for (let i=0;i<S.d.length;i++){ if(S.d[i]>=from){ i0=i; break; } }
    for (let i=S.d.length-1;i>=0;i--){ if(S.d[i]<=to){ i1=i; break; } }
    if (i0<0 || i1<=i0) continue;
    if (S.d[i0] > to) continue;
    const p0 = S.c[i0]; let mx=p0, mn=p0, dd=0, rets=[];
    for (let i=i0;i<=i1;i++){ const v=S.c[i];
      if (v>mx) mx=v; if (v<mn) mn=v;
      const dr = v/mx-1; if (dr<dd) dd=dr;
      if (i>i0) rets.push(Math.log(S.c[i]/S.c[i-1]));
    }
    out.push({ year:Y, entry:p0, exit:S.c[i1], days:i1-i0,
               profit:S.c[i1]/p0-1, best:mx/p0-1, worst:mn/p0-1, dd,
               vol: sd(rets)*Math.sqrt(252) });
  }
  return out;
}
const kept = rows => rows.filter(r=>!excluded.has(r.year));

/* Stagionalita annuale: ogni anno normalizzato a 100 alla prima seduta, media per
   seduta progressiva dell'anno. */
function seasonal(){
  const S = serie(), byYear = new Map();
  for (let i=0;i<S.d.length;i++){ const y=ymd(S.d[i]).y;
    if(!byYear.has(y)) byYear.set(y,[]); byYear.get(y).push(S.c[i]); }
  const acc = [], cnt = [];
  for (const [y,v] of byYear){ if (excluded.has(y) || v.length<200) continue;
    for (let i=0;i<v.length;i++){ acc[i]=(acc[i]||0)+v[i]/v[0]*100; cnt[i]=(cnt[i]||0)+1; } }
  return acc.map((s,i)=> cnt[i]>=3 ? s/cnt[i] : null).filter(x=>x!==null);
}

/* Rendimenti mensili: chiusura di fine mese su chiusura di fine mese precedente. */
function monthly(){
  const S = serie(), last = new Map();
  for (let i=0;i<S.d.length;i++){ const {y,m}=ymd(S.d[i]); last.set(y*100+m, S.c[i]); }
  const keys=[...last.keys()].sort((a,b)=>a-b), by=Array.from({length:12},()=>[]);
  for (let i=1;i<keys.length;i++){
    const k=keys[i], pk=keys[i-1], y=Math.floor(k/100), m=k%100;
    if (excluded.has(y)) continue;
    const expected = m===1 ? (y-1)*100+12 : k-1;
    if (pk!==expected) continue;
    by[m-1].push(last.get(k)/last.get(pk)-1);
  }
  return by.map(v=> v.length ? {mean:mean(v), sd:sd(v), n:v.length} : {mean:0,sd:0,n:0});
}

/* ---------- proiezione ---------- */
function ctx(){
  const S=+$('spot').value, iv=+$('iv').value/100, r=+$('rate').value/100;
  const days = Math.max(1, Math.round((toDate($('d1').value)-toDate($('d0').value))/86400000));
  const T = days/365, vt = iv*Math.sqrt(T);
  return {S, iv, r, days, T, vt};
}
const toDate = s => new Date(s+'T00:00:00');
// probabilita che il sottostante sia >= K a scadenza (lognormale)
function pAbove(K,{S,iv,r,T,vt}){ if(K<=0) return 1; return ncdf((Math.log(S/K)+(r-iv*iv/2)*T)/vt); }
// strike che lascia probabilita p di restare sopra
function kForProb(p,{S,iv,r,T,vt}){ return S*Math.exp((r-iv*iv/2)*T - nppf(p)*vt); }

function autoStrikes(rows,c){
  const k=kept(rows); if(!k.length) return [];
  const p5 = pct(k.map(r=>r.profit),0.05);
  const wp = Math.min(...k.map(r=>r.profit));
  const ww = Math.min(...k.map(r=>r.worst));
  return [
    {label:'Aggressivo',  move:p5, K:c.S*(1+Math.min(p5,-0.0001))},
    {label:'Medio',       move:wp, K:c.S*(1+Math.min(wp,-0.0001))},
    {label:'Cautelativo', move:ww, K:c.S*(1+Math.min(ww,-0.0001))},
  ];
}
function chosenK(rows,c){
  const crit=document.querySelector('input[name=crit]:checked').value;
  if (crit==='prob') return kForProb(Math.min(0.9999,Math.max(0.5,+$('vprob').value/100)), c);
  if (crit==='pct')  return c.S*(1-Math.abs(+$('vpct').value)/100);
  if (crit==='pts')  return c.S-Math.abs(+$('vpts').value);
  if (crit==='man')  return +$('vman').value || c.S*0.9;
  const a=autoStrikes(rows,c); return a.length ? a[+$('autopick').value].K : c.S*0.9;
}

/* Monte Carlo: moto browniano geometrico a passo giornaliero.
   Restituisce la probabilita di NON toccare mai K e la mediana del primo contatto. */
function montecarlo(K,c,n){
  const steps=c.days, dt=c.T/steps, drift=(c.r-c.iv*c.iv/2)*dt, vol=c.iv*Math.sqrt(dt);
  let safe=0; const touches=[]; const sample=[]; const keep=Math.min(140,n);
  for (let p=0;p<n;p++){
    let s=c.S, hit=0; const path = p<keep ? [s] : null;
    for (let t=1;t<=steps;t++){
      let u=0,v=0; do{u=Math.random();}while(u===0); v=Math.random();
      const z=Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);
      s*=Math.exp(drift+vol*z);
      if (path) path.push(s);
      if (!hit && s<=K) hit=t;
    }
    if (path) sample.push(path);
    if (hit) touches.push(hit); else safe++;
  }
  touches.sort((a,b)=>a-b);
  return { pSafe:safe/n, first: touches.length ? touches[Math.floor(touches.length/2)] : null, sample, steps };
}

/* ---------- grafici ---------- */
// Altezze ridotte del 22% il 29/09/2026: i grafici occupavano troppo schermo.
const W=560, PAD={l:46,r:14,t:10,b:22};
function frame(h){ return {w:W, h, iw:W-PAD.l-PAD.r, ih:h-PAD.t-PAD.b}; }
function svg(h, inner){ return `<svg viewBox="0 0 ${W} ${h}" preserveAspectRatio="xMidYMid meet" role="img">${inner}</svg>`; }
function yAxis(f, lo, hi, fmt, ticks=5){
  let s='';
  for (let i=0;i<=ticks;i++){ const v=lo+(hi-lo)*i/ticks, y=PAD.t+f.ih-(v-lo)/(hi-lo)*f.ih;
    s+=`<line x1="${PAD.l}" x2="${PAD.l+f.iw}" y1="${y}" y2="${y}" stroke="var(--grid)" stroke-width="1"/>`;
    s+=`<text x="${PAD.l-6}" y="${y+3}" text-anchor="end">${fmt(v)}</text>`; }
  return s;
}
function showTip(e, html){ tip.innerHTML=html; tip.style.opacity=1;
  const r=tip.getBoundingClientRect();
  tip.style.left = Math.min(window.innerWidth-r.width-8, e.clientX+14)+'px';
  tip.style.top  = Math.max(8, e.clientY-r.height-10)+'px'; }
function hideTip(){ tip.style.opacity=0; }

function chartProb(rows,c,K){
  const h=195, f=frame(h);
  const lo=Math.min(c.S*Math.exp(-3.4*c.vt), K*0.985), hi=Math.max(c.S*Math.exp(3.4*c.vt), K*1.015), N=160;
  const pts=[], xs=[];
  for (let i=0;i<=N;i++){ const x=lo+(hi-lo)*i/N; xs.push(x); pts.push(1-pAbove(x,c)); } // P(S_T <= x)
  const X = v => PAD.l + (v-lo)/(hi-lo)*f.iw;
  const Y = p => PAD.t + f.ih - p*f.ih;
  let d=''; pts.forEach((p,i)=> d += (i?'L':'M')+X(xs[i]).toFixed(1)+' '+Y(p).toFixed(1));
  const area = d + `L${X(xs[N]).toFixed(1)} ${PAD.t+f.ih} L${X(xs[0]).toFixed(1)} ${PAD.t+f.ih} Z`;
  let s = yAxis(f,0,1,v=>(v*100).toFixed(0)+'%');
  s += `<path d="${area}" fill="var(--s1)" opacity=".14"/>`;
  s += `<path d="${d}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round"/>`;
  for (let i=-3;i<=3;i++){ if(!i) continue; const v=c.S*Math.exp(i*c.vt); if(v<lo||v>hi) continue;
    s += `<line x1="${X(v)}" x2="${X(v)}" y1="${PAD.t}" y2="${PAD.t+f.ih}" stroke="var(--grid)" stroke-dasharray="3 3"/>`;
    s += `<text x="${X(v)}" y="${PAD.t+f.ih+14}" text-anchor="middle">${i>0?'+':''}${i}&sigma;</text>`; }
  s += `<line x1="${X(c.S)}" x2="${X(c.S)}" y1="${PAD.t}" y2="${PAD.t+f.ih}" stroke="var(--muted)" stroke-width="1.5"/>`;
  s += `<circle cx="${X(c.S)}" cy="${PAD.t+f.ih}" r="4.5" fill="var(--muted)"/>`;
  if (K>lo && K<hi){
    s += `<line x1="${X(K)}" x2="${X(K)}" y1="${PAD.t}" y2="${PAD.t+f.ih}" stroke="var(--s2)" stroke-width="2"/>`;
    // vicino al bordo sinistro l'etichetta finirebbe sotto l'asse: la si ancora a sinistra
    const near = X(K) < PAD.l+34;
    s += `<text x="${X(K)+(near?5:0)}" y="${PAD.t-2}" text-anchor="${near?'start':'middle'}" fill="var(--s2)">K ${fmtN(K,0)}</text>`; }
  s += `<rect id="probhit" x="${PAD.l}" y="${PAD.t}" width="${f.iw}" height="${f.ih}" fill="transparent" style="cursor:crosshair"/>`;
  const el = document.createElement('div'); el.innerHTML = svg(h,s);
  const hit = el.querySelector('#probhit');
  hit.addEventListener('mousemove', e=>{
    const r = hit.getBoundingClientRect();
    const v = lo + (e.clientX-r.left)/r.width*(hi-lo);
    const below = 1-pAbove(v,c);
    showTip(e, `<b>${fmtN(v,2)}</b><br>non supera: <b>${fmtProb(below)}</b><br>raggiunge o supera: <b>${fmtProb(1-below)}</b>`);
  });
  hit.addEventListener('mouseleave', hideTip);
  // Cliccare sulla curva sceglie lo strike: e il modo piu diretto di dire "voglio quello",
  // perche mentre scegli stai gia leggendo la probabilita di quel livello.
  hit.addEventListener('click', e=>{
    const r = hit.getBoundingClientRect();
    const v = lo + (e.clientX-r.left)/r.width*(hi-lo);
    setManualK(v);
  });
  return el;
}

/* Imposta lo strike a mano e passa il criterio su "manuale". Se ci sono gli strike veri
   presi da TWS si aggancia al piu vicino: inutile studiare un livello non quotabile. */
function setManualK(v){
  const real = (window.REALK || []);
  if (real.length) v = real.reduce((b,k)=> Math.abs(k-v) < Math.abs(b-v) ? k : b, real[0]);
  $('vman').value = (+v).toFixed(2);
  document.querySelector('input[name=crit][value=man]').checked = true;
  render();
}

function chartMC(mc,c,K){
  const h=195, f=frame(h);
  const all = mc.sample.flat();
  const lo=Math.min(K*0.97,...all), hi=Math.max(...all);
  const X = t => PAD.l + t/mc.steps*f.iw;
  const Y = v => PAD.t + f.ih - (v-lo)/(hi-lo)*f.ih;
  let s = yAxis(f,lo,hi,v=>fmtN(v,0));
  for (const p of mc.sample){ let d='';
    for (let t=0;t<p.length;t++) d += (t?'L':'M')+X(t).toFixed(1)+' '+Y(p[t]).toFixed(1);
    s += `<path d="${d}" fill="none" stroke="var(--s1)" stroke-width="0.7" opacity=".16"/>`; }
  s += `<line x1="${PAD.l}" x2="${PAD.l+f.iw}" y1="${Y(K)}" y2="${Y(K)}" stroke="var(--s2)" stroke-width="2"/>`;
  s += `<text x="${PAD.l+f.iw}" y="${Y(K)-5}" text-anchor="end" fill="var(--s2)">strike ${fmtN(K,0)}</text>`;
  s += `<text x="${PAD.l}" y="${h-4}">oggi</text><text x="${PAD.l+f.iw}" y="${h-4}" text-anchor="end">scadenza (${mc.steps} gg)</text>`;
  s += `<rect id="mchit" x="${PAD.l}" y="${PAD.t}" width="${f.iw}" height="${f.ih}" fill="transparent" style="cursor:crosshair"/>`;
  const el = document.createElement('div'); el.innerHTML = svg(h,s);
  const hit = el.querySelector('#mchit');
  hit.addEventListener('mousemove', e=>{
    const r=hit.getBoundingClientRect();
    const t=Math.round((e.clientX-r.left)/r.width*mc.steps);
    let below=0; for(const p of mc.sample) if(p[Math.min(t,p.length-1)]<=K) below++;
    showTip(e, `giorno <b>${t}</b> di ${mc.steps}<br>percorsi sotto lo strike: <b>${(below/mc.sample.length*100).toFixed(1)}%</b><br><span style="color:var(--muted)">su ${mc.sample.length} percorsi disegnati</span>`);
  });
  hit.addEventListener('mouseleave', hideTip);
  return el;
}

function chartSeas(){
  const v = seasonal(); const h=156, f=frame(h);
  if (v.length<10) return document.createTextNode('Dati insufficienti.');
  const lo=Math.min(...v), hi=Math.max(...v);
  const X = i => PAD.l + i/(v.length-1)*f.iw;
  const Y = y => PAD.t + f.ih - (y-lo)/(hi-lo)*f.ih;
  let s = yAxis(f,lo,hi,y=>y.toFixed(0));
  // banda della finestra selezionata, in frazione d'anno
  const doy = s2 => { const d=toDate(s2); return (d-new Date(d.getFullYear(),0,1))/86400000/365; };
  const b0=doy($('d0').value), b1=doy($('d1').value);
  const seg = b1>b0 ? [[b0,b1]] : [[b0,1],[0,b1]];
  for (const [x0,x1] of seg)
    s += `<rect x="${PAD.l+x0*f.iw}" y="${PAD.t}" width="${Math.max(1,(x1-x0)*f.iw)}" height="${f.ih}" fill="var(--s2)" opacity=".10"/>`;
  let d=''; v.forEach((y,i)=> d += (i?'L':'M')+X(i).toFixed(1)+' '+Y(y).toFixed(1));
  s += `<path d="${d}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round"/>`;
  const mesi=['gen','feb','mar','apr','mag','giu','lug','ago','set','ott','nov','dic'];
  for (let m=0;m<12;m++) s += `<text x="${PAD.l+(m+0.5)/12*f.iw}" y="${h-4}" text-anchor="middle">${mesi[m]}</text>`;
  const el=document.createElement('div'); el.innerHTML=svg(h,s);
  return el;
}

function chartMonthly(){
  const m = monthly(); const h=164, f=frame(h);
  const hi = Math.max(...m.map(x=>x.mean+x.sd)), lo = Math.min(...m.map(x=>x.mean-x.sd));
  const Y = v => PAD.t + f.ih - (v-lo)/(hi-lo)*f.ih;
  const bw = f.iw/12, bar = bw*0.62;
  let s = yAxis(f,lo,hi,v=>(v*100).toFixed(1)+'%');
  s += `<line x1="${PAD.l}" x2="${PAD.l+f.iw}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--muted)" stroke-width="1.5"/>`;
  const mesi=['gen','feb','mar','apr','mag','giu','lug','ago','set','ott','nov','dic'];
  m.forEach((x,i)=>{
    const cx = PAD.l+(i+0.5)*bw, y0=Y(0), y1=Y(x.mean);
    const top=Math.min(y0,y1), hgt=Math.max(1.5,Math.abs(y1-y0));
    s += `<rect class="mbar" data-i="${i}" x="${cx-bar/2}" y="${top}" width="${bar}" height="${hgt}" rx="3"
           fill="${x.mean>=0?'var(--s1)':'var(--s2)'}" stroke="var(--card)" stroke-width="1"/>`;
    s += `<line x1="${cx}" x2="${cx}" y1="${Y(x.mean-x.sd)}" y2="${Y(x.mean+x.sd)}" stroke="var(--muted)" stroke-width="1" opacity=".5"/>`;
    s += `<text x="${cx}" y="${h-4}" text-anchor="middle">${mesi[i]}</text>`;
  });
  const el=document.createElement('div'); el.innerHTML=svg(h,s);
  el.querySelectorAll('.mbar').forEach(b=>{
    const x=m[+b.dataset.i];
    b.addEventListener('mousemove',e=>showTip(e,
      `<b>${mesi[+b.dataset.i]}</b><br>media: <b>${fmtP(x.mean)}</b><br>dev. standard: ${fmtP(x.sd)}<br><span style="color:var(--muted)">${x.n} anni</span>`));
    b.addEventListener('mouseleave',hideTip);
  });
  return el;
}

function chartYears(rows){
  if(!rows.length) return document.createTextNode('Nessun anno con dati completi in questa finestra.');
  const h=180, f=frame(h);
  const hi = Math.max(0,...rows.map(r=>r.profit)), lo = Math.min(...rows.map(r=>r.worst), ...rows.map(r=>r.profit));
  const Y = v => PAD.t + f.ih - (v-lo)/(hi-lo)*f.ih;
  const bw = f.iw/rows.length, bar = Math.max(2, bw*0.34);
  let s = yAxis(f,lo,hi,v=>(v*100).toFixed(0)+'%');
  s += `<line x1="${PAD.l}" x2="${PAD.l+f.iw}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--muted)" stroke-width="1.5"/>`;
  rows.forEach((r,i)=>{
    const cx=PAD.l+(i+0.5)*bw, off=excluded.has(r.year)?0.2:1;
    for (const [v,col,dx] of [[r.profit,'var(--s1)',-bar*0.56],[r.worst,'var(--s2)',bar*0.56]]){
      const y0=Y(0), y1=Y(v), top=Math.min(y0,y1), hgt=Math.max(1.5,Math.abs(y1-y0));
      s += `<rect x="${cx+dx-bar/2}" y="${top}" width="${bar}" height="${hgt}" rx="3" fill="${col}" opacity="${off}" stroke="var(--card)" stroke-width="1"/>`;
    }
    if (rows.length<=30 || i%2===0)
      s += `<text x="${cx}" y="${h-4}" text-anchor="middle" opacity="${off}">${String(r.year).slice(2)}</text>`;
    s += `<rect class="yhit" data-y="${r.year}" x="${cx-bw/2}" y="${PAD.t}" width="${bw}" height="${f.ih+18}" fill="transparent" style="cursor:pointer"/>`;
  });
  const el=document.createElement('div'); el.innerHTML=svg(h,s);
  el.querySelectorAll('.yhit').forEach(g=>{
    const r = rows.find(x=>x.year==g.dataset.y);
    g.addEventListener('mousemove',e=>showTip(e,
      `<b>${r.year}</b> &middot; ${r.days} sedute<br>a scadenza: <b class="${r.profit>=0?'pos':'neg'}">${fmtP(r.profit)}</b><br>
       massimo favorevole: ${fmtP(r.best)}<br>peggior escursione: <b>${fmtP(r.worst)}</b><br>max drawdown: ${fmtP(r.dd)}<br>
       volatilita di periodo: ${fmtP(r.vol)}<br><span style="color:var(--muted)">clicca per ${excluded.has(r.year)?'reinserire':'escludere'}</span>`));
    g.addEventListener('mouseleave',hideTip);
    g.addEventListener('click',()=>{ const y=+g.dataset.y;
      excluded.has(y)?excluded.delete(y):excluded.add(y); hideTip(); render(); });
  });
  return el;
}

/* ---------- render ---------- */
function render(){
  const rows = windowStats(), c = ctx(), k = kept(rows);
  const K = chosenK(rows,c);
  window.LASTK = K;
  const pExp = pAbove(K,c);
  const mc = montecarlo(K,c,+$('paths').value);
  const dist = K/c.S-1;
  const hist = k.length ? k.filter(r=>r.worst>dist).length/k.length : NaN;

  $('instnote').textContent = inst().note + ' — serie ' + inst().src +
    (inst().scale!==1 ? ', prezzi scalati x'+inst().scale : '') +
    ' · ' + rows.length + ' anni con dati nella finestra, ' + excluded.size + ' esclusi';

  $('tiles').innerHTML = `
    <div class="stat"><div class="label">Strike selezionato</div><div class="value">${fmtN(K,2)}</div>
      <div class="sub2">${fmtP(dist)} dal prezzo &middot; ${fmtN(c.S-K,0)} punti</div></div>
    <div class="stat"><div class="label">Probabilita a scadenza</div><div class="value">${fmtProb(pExp)}</div>
      <div class="sub2">Black-Scholes, ${c.days} giorni</div></div>
    <div class="stat"><div class="label">Probabilita nel transitorio</div><div class="value">${fmtProb(mc.pSafe)}</div>
      <div class="sub2">Monte Carlo${mc.first?`, primo contatto ~${mc.first}&deg; giorno`:', nessun contatto tipico'}</div></div>
    <div class="stat"><div class="label">Probabilita storica</div><div class="value">${isNaN(hist)?'n/d':fmtProb(hist)}</div>
      <div class="sub2">${k.length} anni, mai scesi cosi</div></div>
    <div class="stat"><div class="label">Volatilita di periodo</div><div class="value">${k.length?fmtP(mean(k.map(r=>r.vol))):'n/d'}</div>
      <div class="sub2">media storica &middot; IV ATM ${fmtP(c.iv)}</div></div>`;

  const auto = autoStrikes(rows,c);
  $('autolist').innerHTML = auto.length
    ? 'Strike automatici: ' + auto.map(a=>`<b>${a.label}</b> ${fmtN(a.K,2)} (${fmtP(a.move)}, prob. ${fmtProb(pAbove(a.K,c))})`).join(' &middot; ')
    : 'Nessun anno utile per calcolare gli strike automatici.';

  const put = (id,node)=>{ const e=$(id); e.replaceChildren(); e.append(node); };
  put('c_prob', chartProb(rows,c,K));
  put('c_mc',   chartMC(mc,c,K));
  put('c_seas', chartSeas());
  put('c_mon',  chartMonthly());
  put('c_years',chartYears(rows));

  $('chips').innerHTML = rows.map(r=>
    `<span class="chip ${excluded.has(r.year)?'off':''}" data-y="${r.year}">${r.year}</span>`).join('');
  $('chips').querySelectorAll('.chip').forEach(ch=> ch.onclick = ()=>{
    const y=+ch.dataset.y; excluded.has(y)?excluded.delete(y):excluded.add(y); render(); });

  $('tbl').innerHTML = `<table><tr><th>Anno</th><th>Entrata</th><th>Uscita</th><th>A scadenza</th>
      <th>Max favorevole</th><th>Peggior escursione</th><th>Max drawdown</th><th>Vol. periodo</th></tr>` +
    rows.map(r=>`<tr class="${excluded.has(r.year)?'off':''}"><td>${r.year}</td><td>${fmtN(r.entry,2)}</td><td>${fmtN(r.exit,2)}</td>
      <td class="${r.profit>=0?'pos':'neg'}">${fmtP(r.profit)}</td><td>${fmtP(r.best)}</td><td>${fmtP(r.worst)}</td>
      <td>${fmtP(r.dd)}</td><td>${fmtP(r.vol)}</td></tr>`).join('') + '</table>';
}

/* ---------- aiuto per grafico ---------- */
const HELP = {
prob: {t:'Curva delle probabilita', h:`
<dl>
<dt>A cosa serve</dt><dd>Leggere, per qualunque livello di prezzo, quante probabilita ci sono che il sottostante ci arrivi <b>entro la scadenza</b>. E il modo per smettere di scegliere gli strike a sensazione.</dd>
<dt>Come si legge</dt><dd>Il pallino grigio in basso e dove si trova adesso il sottostante. Passa il mouse su un punto qualsiasi: il primo numero e la probabilita che il prezzo <b>non superi</b> quel livello, il secondo che lo <b>raggiunga o superi</b>. Mettiti sulla linea rossa per leggere la probabilita di successo dello strike che hai scelto.</dd>
<dt>Cosa guardare</dt><dd>La forma. La curva e ripida vicino al prezzo e piatta sulle code: spostare lo strike di poco quando sei vicino al denaro cambia molto le probabilita, spostarlo quando sei gia lontano ne cambia pochissime ma ti costa quasi tutto il premio. Le linee a &plusmn;1, 2, 3&sigma; dicono quanto e largo il mercato secondo la volatilita che hai scritto: alza la IV e vedrai la curva allargarsi.</dd>
<dt class="care">Attenzione</dt><dd class="care">Sono probabilita <b>a scadenza</b>. Non dicono assolutamente nulla su cosa succede nel mezzo: per quello serve il Monte Carlo qui a fianco.</dd>
</dl>`},

mc: {t:'Monte Carlo', h:`
<dl>
<dt>A cosa serve</dt><dd>Rispondere a una domanda diversa: non "dove finisce" ma "quante volte tocca lo strike <b>anche solo per un momento</b>". E la domanda che conta davvero quando sei short e guardi il conto a meta operazione.</dd>
<dt>Come si legge</dt><dd>Ogni filo e un percorso simulato del prezzo da oggi a scadenza. La linea rossa e lo strike. Passa il mouse: per ogni giorno vedi quanti percorsi si trovano sotto lo strike in quel momento.</dd>
<dt>Cosa guardare</dt><dd><b>La differenza tra le due probabilita nelle caselle in alto.</b> Se a scadenza hai 99% e nel transitorio 96%, vuol dire che in circa tre casi su cento passerai un brutto momento pur finendo bene. Quel numero e la misura di quanto rischi di spaventarti prima di avere ragione, ed e li che si decide se il sizing regge.</dd>
<dt class="care">Attenzione</dt><dd class="care">L'ipotesi di primo contatto e un dato <b>empirico</b>, non una previsione: i percorsi sono casuali per costruzione. E il modello assume volatilita costante, quindi sottostima gli shock veri.</dd>
</dl>`},

seas: {t:'Stagionalita annuale', h:`
<dl>
<dt>A cosa serve</dt><dd>Vedere la forma tipica dell'anno per questo sottostante, e capire dove cade la finestra che hai scelto.</dd>
<dt>Come si legge</dt><dd>Ogni anno viene riportato a 100 alla prima seduta e se ne fa la media. La banda rosa e la finestra selezionata: la vedi spostarsi quando cambi le date qui sopra.</dd>
<dt>Cosa guardare</dt><dd>Se la tua finestra cade in un tratto storicamente in salita, laterale o in discesa. Vendere put in un periodo che storicamente tira su e vento a favore; farlo in un tratto storicamente debole non e vietato, ma vuol dire che stai rinunciando a un pezzo di vantaggio e dovresti saperlo.</dd>
<dt class="care">Attenzione</dt><dd class="care">La curva sale sempre verso destra perche gli indici azionari salgono nel lungo periodo. Quello e <b>drift</b>, non stagionalita: la stagionalita sono le gobbe e le pause, non la pendenza generale.</dd>
</dl>`},

mon: {t:'Rendimenti mensili', h:`
<dl>
<dt>A cosa serve</dt><dd>Quantificare, mese per mese, quanto il sottostante si muove in media e quanto e imprevedibile.</dd>
<dt>Come si legge</dt><dd>La barra e il rendimento medio del mese, il baffo verticale e &plusmn;1 deviazione standard. Passa il mouse per i numeri esatti e per quanti anni compongono la media.</dd>
<dt>Cosa guardare</dt><dd><b>Sempre media e deviazione insieme, mai la sola media.</b> Un mese con media +2,5% e deviazione 4,4% significa che l'intervallo normale va da -1,9% a +6,9%: e quello il movimento che ti devi aspettare, non il +2,5%. Un baffo lungo su una barra corta e un mese imprevedibile, e li vendere opzioni strette e il modo piu rapido per farsi male.</dd>
<dt class="care">Attenzione</dt><dd class="care">Il segno della media non e una previsione direzionale. Serve a dimensionare il movimento, non a scommettere sul verso.</dd>
</dl>`},

years: {t:'Anni nella finestra', h:`
<dl>
<dt>A cosa serve</dt><dd>E il cuore del metodo. Dice cosa ha fatto <b>davvero</b> questo sottostante in questa identica finestra, anno per anno, invece di quello che un modello si immagina.</dd>
<dt>Come si legge</dt><dd>La barra blu e come e finita (dall'entrata alla scadenza), la rossa e quanto e sceso nel peggior momento attraversato. Passa il mouse per tutti i numeri dell'anno, <b>clicca per escluderlo</b> dai calcoli.</dd>
<dt>Cosa guardare</dt><dd><b>Il rosso, non il blu.</b> Le barre rosse sono quelle che determinano lo strike cautelativo e che ti dicono quanto dolore avresti dovuto sopportare. Le poche barre rosse molto lunghe sono gli anni di crisi: e li che si prende la decisione vera, cioe se quell'anno e paragonabile a oggi oppure no.</dd>
<dt class="care">La regola sull'esclusione</dt><dd class="care">Escludi un anno <b>solo se sai dire perche</b> le condizioni di allora oggi non ci sono: recessione conclamata, guerra, shock di offerta. Escludere gli anni finche i numeri non diventano belli e esattamente il modo in cui ci si inganna da soli, e il software non puo accorgersene al posto tuo.</dd>
</dl>`},
};

const pop = $('pop');
function closePop(){ pop.classList.remove('open'); pop.dataset.open='';
  document.querySelectorAll('.help').forEach(b=>b.setAttribute('aria-expanded','false')); }
function openPop(btn, key){
  const H = HELP[key]; if(!H) return;
  pop.innerHTML = `<button class="close" aria-label="Chiudi">&times;</button><h3>${H.t}</h3>${H.h}`;
  pop.classList.add('open'); pop.dataset.open = key;
  btn.setAttribute('aria-expanded','true');
  // ancorato alla card, non alla finestra: cosi resta aperto mentre si scorre
  btn.closest('.card').appendChild(pop);
  placePop(btn);
}
function placePop(btn){
  const card = btn.closest('.card');
  pop.style.left = Math.max(8, Math.min(btn.offsetLeft, card.clientWidth - pop.offsetWidth - 8)) + 'px';
  pop.style.top  = (btn.offsetTop + btn.offsetHeight + 8) + 'px';
}
document.addEventListener('click', e=>{
  const b = e.target.closest('.help');
  if (b){ const k=b.dataset.help;
    if (pop.dataset.open===k) closePop(); else { closePop(); openPop(b,k); }
    return; }
  if (!e.target.closest('#pop') || e.target.closest('.close')) closePop();
});
document.addEventListener('keydown', e=>{ if (e.key==='Escape') closePop(); });
window.addEventListener('resize', ()=>{
  if (!pop.classList.contains('open')) return;
  const b = document.querySelector('.help[aria-expanded=true]');
  if (b) placePop(b); else closePop();
});

/* ---------- avvio ---------- */
$('inst').innerHTML = P.instruments.map(i=>`<option>${i.name}</option>`).join('');
(function initDates(){
  const S = serie(), last = S.d[S.d.length-1], iso = n => String(n).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3');
  $('d0').value = iso(last);
  const d1 = new Date(iso(last)+'T00:00:00'); d1.setDate(d1.getDate()+45);
  $('d1').value = d1.toISOString().slice(0,10);
  $('spot').value = S.c[S.c.length-1].toFixed(2);
})();
/* ---------- volatilita implicita presa da TWS ---------- */
/* Il server del costruttore di strategie (chain_builder.py) e' sempre acceso e parla con
   TWS: gli si chiede la IV al denaro della scadenza scelta invece di copiarla a mano.
   Funziona solo se questa pagina e' aperta DAL server, non come file locale: da file://
   una fetch verso 127.0.0.1 sarebbe cross-origin e verrebbe bloccata. */
const SERVED = location.protocol.startsWith('http');
const IMURL = 'http://127.0.0.1:8770/index_machine.html';
$('fromtws').onclick = async ()=>{
  const msg = $('twsmsg');
  if (!SERVED){
    msg.innerHTML = `Per prendere la volatilita da TWS apri questa pagina dal server: `
      + `<a href="${IMURL}">${IMURL}</a> (la stessa pagina, servita da <code>chain_builder.py</code>).`;
    return;
  }
  const sym = inst().name, exp = $('d1').value.replaceAll('-','');
  $('fromtws').disabled = true;
  msg.textContent = 'chiedo la catena a TWS...';
  try {
    const r = await fetch(`/api/atmiv?sym=${encodeURIComponent(sym)}&exp=${exp}`);
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    $('iv').value  = (j.iv*100).toFixed(1);
    $('spot').value = j.spot.toFixed(2);
    window.REALK = j.strikes || [];
    $('reali').innerHTML = (j.strikes||[]).map(k=>`<option value="${k}">`).join('');
    const d = j.expiry.slice(6,8)+'/'+j.expiry.slice(4,6)+'/'+j.expiry.slice(0,4);
    msg.innerHTML = `Da TWS: prezzo <b>${j.spot}</b>, volatilita implicita <b>${(j.iv*100).toFixed(1)}%</b> `
      + `allo strike ${j.strike} della scadenza <b>${d}</b> (${j.dte} giorni, classe ${j.tradingClass})`
      + (j.strikes && j.strikes.length ? ` &middot; ${j.strikes.length} strike reali disponibili nel campo "Strike manuale"` : '')
      + (j.exact ? '' : ` &mdash; <b style="color:var(--neg)">la data che avevi scelto non e una scadenza vera, ho usato la piu vicina</b>`);
    render();
  } catch(e){
    msg.innerHTML = `Non riesco: ${e.message}. Serve TWS aperto e il servizio `
      + `<code>opzioni-chain-builder</code> attivo.`;
  }
  $('fromtws').disabled = false;
};

['inst','d0','d1','spot','iv','rate','paths','autopick','vprob','vpct','vpts','vman'].forEach(id=>{
  $(id).addEventListener('change', ()=>{
    if (id==='inst'){ excluded.clear(); const S=serie(); $('spot').value=S.c[S.c.length-1].toFixed(2); }
    render();
  });
});
document.querySelectorAll('input[name=crit]').forEach(r=> r.addEventListener('change', ()=>{
  // passando a "manuale" si parte dallo strike che si sta gia guardando: un campo vuoto
  // farebbe saltare il valore a caso
  if (r.value === 'man' && r.checked && !$('vman').value && window.LASTK)
    $('vman').value = window.LASTK.toFixed(2);
  render();
}));
render();
</script>
</body></html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
