"""Analisi serale (SOLA LETTURA): regime, portafoglio, avvisi delle regole, scansione dell'edge. Scrive il rapporto in OPZIONI_NOTE_DIR (default data/serate).
Uso:  uv run --with yfinance evening.py [--no-edge]
Non invia ordini. Non stampa il numero di conto."""
import argparse, datetime as dt, subprocess, re, sys, csv
from pathlib import Path
import numpy as np, yfinance as yf
from ib_async import Index
from ib_common import connect, clean, DATA
import transform

# Cartella dei rapporti serali: variabile d'ambiente OPZIONI_NOTE_DIR (es. una cartella del proprio vault di note),
# altrimenti data/serate dentro il repository.
import os
VAULT=Path(os.environ.get("OPZIONI_NOTE_DIR") or (DATA/"serate"))
FX_NOTE="(i valori in USD e EUR non sono convertiti)"

def yq(sym):
    d=yf.download(sym,period="1y",progress=False,auto_adjust=False)["Close"].squeeze().dropna()
    return d
def ib_last(ib,contract,wait=3):
    try:
        ib.qualifyContracts(contract)
        t=ib.reqMktData(contract,"",False,False); ib.sleep(wait)
        v=clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(contract); return v
    except Exception: return None

def regime(ib,lines):
    spx=yq("^GSPC"); vix=yq("^VIX"); v3=yq("^VIX3M")
    vix_now=ib_last(ib,Index("VIX","CBOE")) or float(vix.iloc[-1])
    v3_now=ib_last(ib,Index("VIX3M","CBOE")) or float(v3.iloc[-1])
    v2tx=ib_last(ib,Index("V2TX","EUREX"))
    estx=ib_last(ib,Index("ESTX50","EUREX"))
    sma=spx.rolling(200).mean().iloc[-1]; px=float(spx.iloc[-1])
    ratio=vix_now/v3_now
    ch2=(vix.iloc[-1]/vix.iloc[-3]-1)*100 if len(vix)>3 else 0
    lines.append("## 1. Regime")
    lines.append(f"- **VIX {vix_now:.2f}**, VIX3M {v3_now:.2f}, **VIX/VIX3M {ratio:.2f}** ({'contango' if ratio<1 else 'BACKWARDATION'}); variazione VIX su 2 sedute {ch2:+.0f}%")
    lines.append(f"- SPX {px:,.0f} contro media a 200 giorni {sma:,.0f} ({(px/sma-1)*100:+.1f}%); ESTX50 {estx if estx else 'n/d'}; V2TX {v2tx if v2tx else 'n/d'}")
    level=0; why=[]
    if vix_now>40: level=max(level,3); why.append("VIX > 40")
    if vix_now>30 or ratio>1: level=max(level,2); why.append("VIX > 30 o backwardation")
    if ch2>=25 or ratio>0.95: level=max(level,1); why.append("VIX +25% in 2 sedute o VIX/VIX3M > 0,95")
    beta="normale" if (vix_now<22 and ratio<0.9 and px>sma) else ("elevato" if vix_now<30 else "crisi")
    lines.append(f"- **Circuit breaker: livello {level}** {('('+', '.join(why)+')') if why else '(nessun trigger di mercato)'}; regime del beta: **{beta}** (Piano v2 §4)")
    return vix_now,ratio,level

def portfolio(ib,lines):
    port=ib.portfolio(); today=dt.date.today()
    legs=[]
    for p in port:
        c=p.contract
        try: exp=dt.datetime.strptime(c.lastTradeDateOrContractMonth,"%Y%m%d").date(); dte=(exp-today).days
        except Exception: exp=None; dte=None
        legs.append(dict(sym=c.symbol,right=c.right,k=c.strike,exp=exp,dte=dte,q=p.position,avg=p.averageCost,val=p.marketValue,pnl=p.unrealizedPNL,cur=c.currency,mult=float(c.multiplier or 1)))
    used=set(); structs=[]
    def find(i,cond):
        for j,l in enumerate(legs):
            if j!=i and j not in used and cond(legs[i],l): return j
    for i,a in enumerate(legs):
        if i in used or a["exp"] is None: continue
        # verticale: stesso sottostante, scadenza, tipo; quantita opposte
        j=find(i,lambda a,b:b["sym"]==a["sym"] and b["exp"]==a["exp"] and b["right"]==a["right"] and b["q"]==-a["q"] and b["k"]!=a["k"])
        if j is not None:
            used|={i,j}; structs.append(("verticale",[a,legs[j]])); continue
        # calendar: stesso strike e tipo, scadenze diverse, quantita opposte
        j=find(i,lambda a,b:b["sym"]==a["sym"] and b["right"]==a["right"] and b["k"]==a["k"] and b["exp"]!=a["exp"] and b["q"]==-a["q"])
        if j is not None:
            used|={i,j}; structs.append(("calendar",[a,legs[j]])); continue
    for i,a in enumerate(legs):
        if i not in used: structs.append(("singola",[a]))
    lines.append("## 2. Portafoglio e regole (Piano §6)")
    lines.append("| Struttura | Scadenza / DTE | Credito o debito iniziale | Costo di chiusura | P&L | Stato regola |"); lines.append("|---|---|---|---|---|---|")
    alerts=[]
    for kind,ls in structs:
        cur=ls[0]["cur"]; sym=ls[0]["sym"]
        pnl=sum(l["pnl"] for l in ls)
        if kind=="verticale":
            credit=sum(-l["q"]*l["avg"] for l in ls)      # premio incassato meno pagato (positivo se credito)
            close=-sum(l["val"] for l in ls)              # costo di chiusura (positivo se si paga)
            shortleg=[l for l in ls if l["q"]<0][0]; dte=shortleg["dte"]
            name=f"{sym} {'put' if ls[0]['right']=='P' else 'call'} spread {min(l['k'] for l in ls):g}/{max(l['k'] for l in ls):g} (x{abs(ls[0]['q']):.0f})"
            if credit>0:
                status="ok"
                if close<=0.5*credit: status="TAKE PROFIT 50%"; alerts.append(f"{name}: take profit raggiunto (costo di chiusura {close:.0f} contro credito {credit:.0f})")
                elif close>=3*credit: status="PERDITA 2x: aggiustare"; alerts.append(f"{name}: costo di chiusura a 2x il credito, valutare un aggiustamento (nessuno stop loss)")
                if dte is not None and dte<=21: status=(status+" + 21 DTE").replace("ok + ",""); alerts.append(f"{name}: {dte} DTE, chiudere o rollare (regola 21 DTE)")
                lines.append(f"| {name} | {shortleg['exp']} / {dte} | credito {credit:.0f} {cur} | {close:.0f} | {pnl:+.0f} | {status} |")
            else:
                lines.append(f"| {name} (a debito) | {shortleg['exp']} / {dte} | debito {-credit:.0f} {cur} | valore {-close:.0f} | {pnl:+.0f} | valutare |")
        elif kind=="calendar":
            near=min(ls,key=lambda l:l["exp"]); far=max(ls,key=lambda l:l["exp"])
            debit=sum(l["q"]*l["avg"] for l in ls); value=sum(l["val"] for l in ls)
            name=f"{sym} calendar {'put' if near['right']=='P' else 'call'} {near['k']:g} (corta {near['exp']}, lunga {far['exp']})"
            status="ok"
            if debit>0 and (value-debit)/debit<=-0.5: status="PERDITA -50% del debito: aggiustare"; alerts.append(f"{name}: perdita oltre il 50% del debito, valutare un aggiustamento o la trasformazione (nessuno stop loss)")
            if near["dte"] is not None and near["dte"]<=21: status=("21 DTE della gamba corta" if status=="ok" else status+" + 21 DTE"); alerts.append(f"{name}: gamba corta a {near['dte']} DTE, chiudere prima della scadenza")
            lines.append(f"| {name} | {near['exp']} / {near['dte']} | debito {debit:.0f} {cur} | valore {value:.0f} | {pnl:+.0f} | {status} |")
        else:
            l=ls[0]
            tipo="LONG protezione" if l["q"]>0 else "SHORT scoperta"
            name=f"{sym} {tipo} {l['right']} {l['k']:g}"
            st="costo dell'assicurazione" if l["q"]>0 else "RISCHIO NON DEFINITO"
            lines.append(f"| {name} (x{abs(l['q']):.0f}) | {l['exp']} / {l['dte']} | costo {l['q']*l['avg']:.0f} {cur} | valore {l['val']:.0f} | {pnl:+.0f} | {st} |")
    tot={}
    for l in legs: tot[l["cur"]]=tot.get(l["cur"],0)+l["pnl"]
    lines.append(f"\nP&L non realizzato totale: "+", ".join(f"{v:+.0f} {k}" for k,v in tot.items())+f" {FX_NOTE}")
    return alerts

def account(ib,lines,alerts,vix_level):
    v={a.tag:float(a.value) for a in ib.accountSummary() if a.tag in ("NetLiquidation","MaintMarginReq","ExcessLiquidity","Cushion","AvailableFunds") and a.currency in ("EUR","")}
    nav=v.get("NetLiquidation"); mm=v.get("MaintMarginReq")
    hist=DATA/"nav_history.csv"; rows=[]
    if hist.exists(): rows=list(csv.reader(open(hist)))
    today=dt.date.today().isoformat()
    if not any(r and r[0]==today for r in rows):
        with open(hist,"a") as f: csv.writer(f).writerow([today,f"{nav:.2f}"])
        rows.append([today,f"{nav:.2f}"])
    peak=max(float(r[1]) for r in rows); dd=(nav/peak-1)*100
    lines.append("## 3. Conto e rischio")
    lines.append(f"- NAV **{nav:,.0f} EUR**, margine di mantenimento {mm:,.0f} EUR (**{mm/nav*100:.1f}% del NAV**, limite 50% in regime normale), liquidità in eccesso {v.get('ExcessLiquidity',0):,.0f} EUR, cushion {v.get('Cushion',0):.2f}")
    lines.append(f"- Drawdown dal massimo registrato: **{dd:.1f}%** (allerta a −8%, kill-switch a −15%; il massimo parte dal primo rapporto e **non tiene conto dei versamenti**: quando arriva il bonifico va azzerato a mano)")
    if dd<=-8: alerts.append(f"DRAWDOWN {dd:.1f}%: livello 2 dei circuit breaker")
    elif dd<=-4: alerts.append(f"Drawdown {dd:.1f}%: livello 1")
    if mm/nav>0.5: alerts.append(f"Margine oltre il 50% del NAV ({mm/nav*100:.0f}%)")

def edge(lines):
    lines.append("## 4. Scansione dell'edge (prezzi reali, RV di pareggio contro RV attesa)")
    lines.append("Regola: entrare solo con margine ≥ +2 punti, spread delle gambe ≤ 10% e open interest sufficiente. RV attesa = max(RV10, RV20 close-to-close) + 1.\n")
    try:
        out=subprocess.run(["uv","run","--with","yfinance","stage2_index.py","SPX","XSP","RUT"],capture_output=True,text=True,timeout=900,cwd=str(Path(__file__).parent)).stdout
    except Exception as e:
        lines.append(f"(scansione non riuscita: {e})"); return []
    keep=[l for l in out.splitlines() if not re.search(r"Unknown contract|Error \d+|^\s*$",l)]
    lines.append("```"); lines+=keep; lines.append("```")
    cands=[]
    for i,l in enumerate(keep):
        m=re.search(r"margine ([+-][\d.]+) punti",l)
        if not m or float(m.group(1))<2.0: continue
        sp=re.search(r"spread gambe % \[([^\]]*)\] \| OI \[([^\]]*)\]",keep[i+1]) if i+1<len(keep) else None
        ok_liq=False
        if sp:
            try:
                spreads=[float(x) for x in sp.group(1).split(",")]
                ois=[None if "None" in x else float(x) for x in sp.group(2).split(",")]
                ok_liq=max(spreads)<=10 and all(o is not None and o>=100 for o in ois)
            except Exception: pass
        if ok_liq: cands.append(keep[i-1].strip()[:110])
    lines.append("\n**Verdetto:** "+("OPERAZIONI CANDIDATE ESEGUIBILI (margine ≥ +2, spread ≤ 10%, open interest ≥ 100): "+" | ".join(cands) if cands else "**NESSUNA OPERAZIONE CONSIGLIATA.** Se compare un margine ≥ +2 ma con spread > 10% o open interest < 100, la struttura non è eseguibile a prezzi decenti e non conta."))
    return cands

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--no-edge",action="store_true"); ap.add_argument("--port",type=int,default=7496); a=ap.parse_args()
    ib=connect("127.0.0.1",a.port,120)
    lines=[f"# Serata del {dt.date.today().isoformat()} (generata alle {dt.datetime.now():%H:%M}, sola lettura)\n"]
    alerts=[]
    try:
        ib.reqMarketDataType(1)
        vix,ratio,lvl=regime(ib,lines); lines.append("")
        alerts+=portfolio(ib,lines); lines.append("")
        try:
            tl,ta,_=transform.check(ib); lines+=tl; alerts+=ta; lines.append("")
        except Exception as e: lines.append(f"(controllo trasformazione calendar non riuscito: {e})\n")
        account(ib,lines,alerts,lvl); lines.append("")
    finally: ib.disconnect()
    if not a.no_edge: edge(lines); lines.append("")
    lines.insert(1,"## Avvisi\n"+("\n".join(f"- {x}" for x in alerts) if alerts else "- nessuno\n")+"\n")
    VAULT.mkdir(parents=True,exist_ok=True)
    p=VAULT/f"{dt.date.today().isoformat()}.md"; p.write_text("\n".join(lines)); print("\n".join(lines)); print("\nRapporto salvato in",p)
main()
