"""Stadio 2 su indici con regolamento in contanti (SOLA LETTURA): put spread e iron condor a circa 16 delta.
Uso: uv run --with yfinance stage2_index.py SPX NDX RUT XSP
La RV attesa = il piu' alto tra RV10 e RV20 close-to-close (Yahoo) + 1 punto (regola prudente)."""
import os, sys, datetime as dt, numpy as np
from math import sqrt
import yfinance as yf
from ib_async import Index, Option, Stock
from ib_common import connect, clean

IDX={"SPX":("SPX","CBOE","^GSPC"),"NDX":("NDX","NASDAQ","^NDX"),"RUT":("RUT","RUSSELL","^RUT"),
     "XSP":("XSP","CBOE","^GSPC"),"XND":("XND","NASDAQ","^NDX"),"MRUT":("MRUT","RUSSELL","^RUT"),"DJX":("DJX","CBOE","^DJI"),"QQQ":("QQQ","STK","QQQ"),"SPY":("SPY","STK","SPY"),"IWM":("IWM","STK","IWM")}
DTE=(28,40)
def chunks(s,n):
    for i in range(0,len(s),n): yield s[i:i+n]
def rv_cc(y):
    c=yf.download(y,period="1y",progress=False,auto_adjust=False)["Close"].squeeze().dropna()
    r=np.log(c).diff().dropna()
    return {n:r.iloc[-n:].std()*sqrt(252)*100 for n in (10,20,60)}
def ev_of(spot,T,legs,cr,sig,z):
    F=spot*np.exp(-0.5*sig*sig*T+sig*sqrt(T)*z); loss=0
    for kind,ks,kl in legs:
        loss=loss+((np.maximum(ks-F,0)-np.maximum(kl-F,0)) if kind=="P" else (np.maximum(F-ks,0)-np.maximum(F-kl,0)))
    return (cr-loss).mean()
def breakeven(spot,T,legs,cr,z):
    lo,hi=0.03,1.5
    for _ in range(45):
        m=(lo+hi)/2
        if ev_of(spot,T,legs,cr,m,z)>0: lo=m
        else: hi=m
    return (lo+hi)/2
def analyse(ib,name):
    sym,exch,yt=IDX[name]
    u=Stock(sym,"SMART","USD") if exch=="STK" else Index(sym,exch); 
    try: ib.qualifyContracts(u)
    except Exception as e: print(name,"contratto non disponibile:",e); return
    if not u.conId: print(name,"indice non trovato/permessi assenti"); return
    t=ib.reqMktData(u,"",False,False); ib.sleep(3); spot=clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(u)
    if not spot:
        try:
            spot=float(yf.download(yt,period="5d",progress=False,auto_adjust=False)["Close"].squeeze().dropna().iloc[-1])
            print(name,"ATTENZIONE: prezzo dell'indice non disponibile da IBKR (nessun abbonamento): uso l'ultima chiusura di Yahoo",round(spot,2))
        except Exception: print(name,"senza prezzo (permessi dati?)"); return
    chains=ib.reqSecDefOptParams(sym,"",u.secType,u.conId)
    today=dt.date.today(); best=None; strikes=set()
    for c in chains:
        if c.exchange not in ("SMART","CBOE","RUSSELL"): continue
        if exch=="STK" and c.exchange!="SMART": continue
        for e in c.expirations:
            d=(dt.datetime.strptime(e,"%Y%m%d").date()-today).days
            if DTE[0]<=d<=DTE[1] and (best is None or abs(d-33)<abs(best[0]-33)): best=(d,e,c.tradingClass,set(c.strikes))
    if not best: print(name,"nessuna scadenza in finestra"); return
    d,e,tc,ks=best; T=d/365
    ks=sorted(k for k in ks if 0.88*spot<=k<=1.12*spot)
    cons=[Option(sym,e,k,r,"SMART",currency="USD",tradingClass=tc) for k in ks for r in ("P","C")]
    ok=[]
    for blk in chunks(cons,50): ok+=[c for c in ib.qualifyContracts(*blk) if c and c.conId]
    rows={}
    for blk in chunks(ok,80):
        ts=[ib.reqMktData(c,"101",False,False) for c in blk]; ib.sleep(6)
        for x in ts:
            g=x.modelGreeks; b,a=clean(x.bid),clean(x.ask)
            if b is not None and a is not None and g and g.impliedVol and g.delta:
                rows[(x.contract.right,x.contract.strike)]=dict(bid=b,ask=a,iv=g.impliedVol,delta=g.delta,oi=clean(x.putOpenInterest if x.contract.right=="P" else x.callOpenInterest))
        for c in blk: ib.cancelMktData(c)
    P={k:v for (r,k),v in rows.items() if r=="P"}; C={k:v for (r,k),v in rows.items() if r=="C"}
    if len(P)<5 or len(C)<5: print(name,"catena incompleta"); return
    kp=min(P,key=lambda k:abs(abs(P[k]["delta"])-0.16)); kc=min(C,key=lambda k:abs(abs(C[k]["delta"])-0.16))
    wid=0.01*spot
    lp=min([k for k in P if k<kp],key=lambda k:abs(k-(kp-wid)),default=None); lc=min([k for k in C if k>kc],key=lambda k:abs(k-(kc+wid)),default=None)
    if lp and (kp-lp)>1.6*wid: lp=None
    if lc and (lc-kc)>1.6*wid: lc=None
    rv=rv_cc(yt); exp_rv=max(rv[10],rv[20])+1
    z=np.random.default_rng(1).standard_normal(400000)
    print(f"\n{name}: spot {spot:,.2f}, scadenza {e} ({d} gg, classe {tc}) | RV close-to-close 10/20/60g: {rv[10]:.1f}/{rv[20]:.1f}/{rv[60]:.1f}% -> RV attesa prudente {exp_rv:.1f}%")
    print(f"  IV gamba corta: put {P[kp]['iv']*100:.1f}% (delta {P[kp]['delta']:+.2f}), call {C[kc]['iv']*100:.1f}% (delta {C[kc]['delta']:+.2f})")
    def cred(short,long_):
        nat=short["bid"]-long_["ask"]; mid=(short["bid"]+short["ask"])/2-(long_["bid"]+long_["ask"])/2; return nat,mid,nat+0.4*(mid-nat)
    sp=lambda r:(r["ask"]-r["bid"])/((r["ask"]+r["bid"])/2)*100
    res=[]
    if lp: 
        nat,mid,lim=cred(P[kp],P[lp]); w=kp-lp; legs=[("P",kp,lp)]
        res.append(("Put spread",f"{lp:g}/{kp:g}",w,nat,mid,lim,legs,[P[kp],P[lp]]))
    if lc:
        nat,mid,lim=cred(C[kc],C[lc]); w=lc-kc; legs=[("C",kc,lc)]
        res.append(("Call spread",f"{kc:g}/{lc:g}",w,nat,mid,lim,legs,[C[kc],C[lc]]))
    if lp and lc:
        n1,m1,l1=cred(P[kp],P[lp]); n2,m2,l2=cred(C[kc],C[lc]); w=max(kp-lp,lc-kc)
        res.append(("Iron condor",f"{lp:g}/{kp:g} + {kc:g}/{lc:g}",w,n1+n2,m1+m2,l1+l2,[("P",kp,lp),("C",kc,lc)],[P[kp],P[lp],C[kc],C[lc]]))
    for nm,desc,w,nat,mid,lim,legs,lg in res:
        be=breakeven(spot,T,legs,lim,z)*100; ev=ev_of(spot,T,legs,lim,exp_rv/100,z)
        mult=100
        print(f"  {nm} {desc}: larghezza {w:g} | credito naturale {nat:.2f} medio {mid:.2f} limite {lim:.2f} ({lim/w*100:.0f}% della larghezza) | rischio {((w-lim)*mult):,.0f} $")
        print(f"     spread gambe % {[round(sp(x),1) for x in lg]} | OI {[x['oi'] for x in lg]} | RV di pareggio {be:.1f}% -> margine {be-exp_rv:+.1f} punti | EV a RV attesa {ev*mult:+.0f} $")
ib=connect("127.0.0.1",7496,107)
try:
    ib.reqMarketDataType(int(os.environ.get('MDT','1')))
    for n in sys.argv[1:]: analyse(ib,n)
finally: ib.disconnect()
