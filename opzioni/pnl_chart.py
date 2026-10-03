"""Blocchi HTML della dashboard per lo storico del P/L: grafico giornaliero (chiuso, floating, totale) e riepilogo
da inizio conto. Nessuna dipendenza esterna: SVG disegnato da JS inline con i dati incorporati."""
from __future__ import annotations
import html, json

CHART = r"""
<div class="card viz" id="pnl-card">
  <div class="card-head"><h2>P/L giornaliero: chiuso e floating (EUR)</h2>
    <div class="legend"><span><i class="sw sw-t"></i>Totale</span><span><i class="sw sw-c"></i>Chiuso (realizzato)</span><span><i class="sw sw-f"></i>Floating (aperto)</span></div></div>
  <div class="note">__NOTE__</div>
  <div class="chart-wrap"><svg id="pnl-svg" role="img" aria-label="P/L giornaliero chiuso, floating e totale"></svg><div id="pnl-tip" class="tip" hidden></div></div>
  <details><summary>Tabella</summary><table><tr><th>Data</th><th>Chiuso</th><th>Floating</th><th>Totale</th><th>Fonte</th></tr>__ROWS__</table></details>
</div>
<style>
  .viz { --s-t: var(--text); --s-c:#3987e5; --s-f:#d95926; --grid:#262e42; }
  @media (prefers-color-scheme: light) { .viz { --s-c:#2a78d6; --s-f:#eb6834; --grid:#e3e6ec; } }
  .viz .legend { display:flex; gap:14px; flex-wrap:wrap; font-size:0.8rem; color:var(--muted); }
  .viz .sw { display:inline-block; width:14px; height:3px; border-radius:2px; vertical-align:middle; margin-right:6px; }
  .viz .sw-t { background:var(--s-t); } .viz .sw-c { background:var(--s-c); } .viz .sw-f { background:var(--s-f); }
  .viz .chart-wrap { position:relative; }
  .viz svg { width:100%; height:260px; display:block; }
  .viz .tip { position:absolute; pointer-events:none; background:var(--card); border:1px solid var(--border); border-radius:8px;
              padding:6px 10px; font-size:0.8rem; line-height:1.5; white-space:nowrap; box-shadow:0 2px 8px rgba(0,0,0,.25); }
  .viz details { margin-top:8px; font-size:0.85rem; } .viz summary { cursor:pointer; color:var(--muted); }
  .summary-row { display:flex; flex-wrap:wrap; gap:12px; margin-top:8px; }
</style>
<script>
(function(){
  const D = __DATA__;
  const svg = document.getElementById('pnl-svg'), tip = document.getElementById('pnl-tip');
  const NS = 'http://www.w3.org/2000/svg';
  const fmt = v => (v>=0?'+':'') + Math.round(v).toLocaleString('it-IT') + ' €';
  const dfmt = s => s.slice(8,10)+'/'+s.slice(5,7);
  function el(t,a,p){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);(p||svg).appendChild(e);return e;}
  function draw(){
    svg.innerHTML=''; if(!D.length) return;
    const W=svg.clientWidth||600, H=260, L=64, R=110, T=12, B=28;
    svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const S=[['totale','var(--s-t)','Totale'],['chiuso','var(--s-c)','Chiuso'],['floating','var(--s-f)','Floating']];
    let lo=0, hi=0; D.forEach(r=>S.forEach(([k])=>{lo=Math.min(lo,r[k]);hi=Math.max(hi,r[k]);}));
    const pad=(hi-lo)*0.1||50; lo-=pad; hi+=pad;
    const raw=(hi-lo)/4, mag=Math.pow(10,Math.floor(Math.log10(raw))), st=[1,2,2.5,5,10].map(m=>m*mag).find(m=>m>=raw);
    lo=Math.floor(lo/st)*st; hi=Math.ceil(hi/st)*st;
    const x=i=> D.length===1 ? L+(W-L-R)/2 : L+i*(W-L-R)/(D.length-1);
    const y=v=> T+(hi-v)*(H-T-B)/(hi-lo);
    // griglia recessiva: 4 linee + zero
    for(let v=lo;v<=hi+1e-9;v+=st){ el('line',{x1:L,x2:W-R,y1:y(v),y2:y(v),stroke:'var(--grid)','stroke-width':1});
      el('text',{x:L-8,y:y(v)+4,'text-anchor':'end','font-size':11,fill:'var(--muted)'}).textContent=fmt(v);}
    el('line',{x1:L,x2:W-R,y1:y(0),y2:y(0),stroke:'var(--muted)','stroke-width':1,'stroke-dasharray':'3 3'});
    const step=Math.max(1,Math.ceil(D.length/8));
    D.forEach((r,i)=>{ if(i%step===0||i===D.length-1) el('text',{x:x(i),y:H-8,'text-anchor':'middle','font-size':11,fill:'var(--muted)'}).textContent=dfmt(r.date); });
    S.forEach(([k,c,lab])=>{
      if(D.length>1) el('polyline',{points:D.map((r,i)=>x(i)+','+y(r[k])).join(' '),fill:'none',stroke:c,'stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'});
      D.forEach((r,i)=>el('circle',{cx:x(i),cy:y(r[k]),r:4,fill:c,stroke:'var(--card)','stroke-width':2}));
    });
    // etichette dirette a fine linea, distanziate di almeno 14px se si sovrappongono
    const last=D[D.length-1];
    const labs=S.map(([k,c,lab])=>({t:lab+' '+fmt(last[k]),y:y(last[k])})).sort((a,b)=>a.y-b.y);
    for(let i=1;i<labs.length;i++) if(labs[i].y-labs[i-1].y<14) labs[i].y=labs[i-1].y+14;
    labs.forEach(l=>el('text',{x:x(D.length-1)+10,y:l.y+4,'font-size':11,fill:'var(--text)'}).textContent=l.t);
    const cross=el('line',{y1:T,y2:H-B,stroke:'var(--muted)','stroke-width':1,visibility:'hidden'});
    const hit=el('rect',{x:L-10,y:0,width:W-L-R+20,height:H,fill:'transparent'});
    hit.addEventListener('mousemove',ev=>{
      const b=svg.getBoundingClientRect(), mx=(ev.clientX-b.left)*W/b.width;
      let i=0,best=1e9; D.forEach((r,j)=>{const d=Math.abs(x(j)-mx); if(d<best){best=d;i=j;}});
      const r=D[i]; cross.setAttribute('x1',x(i)); cross.setAttribute('x2',x(i)); cross.setAttribute('visibility','visible');
      tip.hidden=false; tip.innerHTML=`<b>${r.date.split('-').reverse().join('/')}</b><br>Totale ${fmt(r.totale)}<br>Chiuso ${fmt(r.chiuso)}<br>Floating ${fmt(r.floating)}<br><span style="color:var(--muted)">fonte: ${r.fonte}</span>`;
      const px=x(i)*b.width/W; tip.style.left=Math.min(px+12,b.width-170)+'px'; tip.style.top='8px';
    });
    hit.addEventListener('mouseleave',()=>{tip.hidden=true;cross.setAttribute('visibility','hidden');});
  }
  draw(); window.addEventListener('resize',draw);
})();
</script>
"""


def chart_html(rows, msg):
    trs = "".join(f"<tr><td>{r['date']}</td><td>{r['chiuso']:+,.0f}</td><td>{r['floating']:+,.0f}</td><td>{r['totale']:+,.0f}</td><td>{r['fonte']}</td></tr>" for r in rows)
    note = html.escape(msg) + (". Un punto al giorno; il punto di oggi e' live (TWS) e si aggiorna ogni ora." if rows else "")
    return CHART.replace("__DATA__", json.dumps(rows)).replace("__ROWS__", trs).replace("__NOTE__", note)


def summary_html(rows, msg):
    if not rows:
        return ""
    r = rows[-1]; since = rows[0]["date"].split("-"); since = f"{since[2]}/{since[1]}/{since[0]}"
    cls = lambda v: "pos" if v >= 0 else "neg"
    tiles = "".join(f'<div class="stat"><div class="label">{lab}</div><div class="value {cls(v)}">{v:+,.0f} &euro;</div></div>'
                    for lab, v in (("Chiuso (realizzato)", r["chiuso"]), ("Floating (aperto)", r["floating"]), ("Totale", r["totale"])))
    scope = "da inizio conto" if rows[0].get("fonte") == "flex" and "ATTENZIONE" not in msg else f"dal {since} (primo rilevamento: lo storico completo arriva con la Flex Query)"
    return f'<div class="card"><div class="card-head"><h2>Riepilogo {scope}</h2></div><div class="summary-row">{tiles}</div></div>'
