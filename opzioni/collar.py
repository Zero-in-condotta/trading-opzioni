"""Collar reali su azioni USA (SOLA LETTURA): azione + put comprata + call venduta, scadenza a circa 90 giorni.
Uso: MDT=2 uv run --with yfinance collar.py WMT BAC KO ...   (MDT=2 a mercato chiuso: prezzi congelati)"""
import os, sys, datetime as dt, numpy as np, yfinance as yf
from math import sqrt
from ib_async import Stock, Option
from ib_common import connect, clean
def chunks(s,n):
    for i in range(0,len(s),n): yield s[i:i+n]
def analyse(ib,sym):
    u=Stock(sym,"SMART","USD"); ib.qualifyContracts(u)
    t=ib.reqMktData(u,"",False,False); ib.sleep(3); spot=clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(u)
    if not spot: print(sym,"senza prezzo"); return
    ch=[c for c in ib.reqSecDefOptParams(sym,"",u.secType,u.conId) if c.exchange=="SMART" and c.tradingClass==sym]
    today=dt.date.today(); best=None
    for c in ch:
        for e in c.expirations:
            d=(dt.datetime.strptime(e,"%Y%m%d").date()-today).days
            if 75<=d<=110 and (best is None or abs(d-90)<abs(best[0]-90)): best=(d,e,c.strikes)
    if not best: print(sym,"nessuna scadenza 75-110 gg"); return
    d,e,ks=best; T=d/365
    ks=sorted(k for k in ks if 0.84*spot<=k<=1.22*spot)
    cons=[Option(sym,e,k,r,"SMART",currency="USD",tradingClass=sym) for k in ks for r in ("P","C")]
    ok=[]
    for blk in chunks(cons,50): ok+=[x for x in ib.qualifyContracts(*blk) if x and x.conId]
    P={};C={}
    for blk in chunks(ok,80):
        ts=[ib.reqMktData(x,"101",False,False) for x in blk]; ib.sleep(6)
        for x in ts:
            b,a=clean(x.bid),clean(x.ask)
            if b is not None and a is not None and a>0:
                (P if x.contract.right=="P" else C)[x.contract.strike]=dict(bid=b,ask=a,oi=clean(x.putOpenInterest if x.contract.right=="P" else x.callOpenInterest))
        for x in blk: ib.cancelMktData(x)
    if len(P)<3 or len(C)<3: print(sym,"catena incompleta"); return
    tk=yf.Ticker(sym)
    try: dy=float(tk.info.get("dividendRate") or 0)/spot
    except Exception: dy=0.0
    c=yf.download(sym,period="1y",progress=False,auto_adjust=False)["Close"].squeeze().dropna(); r=np.log(c).diff().dropna()
    rv=max(r.iloc[-10:].std(),r.iloc[-20:].std())*sqrt(252)+0.01
    ed=None
    try:
        cal=tk.calendar; ee=cal.get("Earnings Date") if isinstance(cal,dict) else None
        if ee: ed=ee[0] if isinstance(ee,(list,tuple)) else ee; ed=ed if isinstance(ed,dt.date) else ed.date()
    except Exception: pass
    print(f"\n=== {sym}: spot {spot:.2f} (100 azioni = {spot*100:,.0f} $), scadenza {e} ({d} gg), dividendo circa {dy*100:.1f}%/anno, RV attesa {rv*100:.0f}%" + (f", utili {ed}" if ed else ""))
    z=np.random.default_rng(1).standard_normal(200000); mu=0.08
    F=spot*np.exp((mu-0.5*rv*rv)*T+rv*sqrt(T)*z)
    def outcome(kp,pput,kc,ccall):
        col=np.maximum(kp,np.minimum(F,kc))-spot-pput+ccall+spot*dy*T
        st=F-spot+spot*dy*T
        return col.mean()/spot*100, np.percentile(col,5)/spot*100, st.mean()/spot*100, np.percentile(st,5)/spot*100
    for m in (0.90,0.925,0.95):
        kp=min(P,key=lambda k:abs(k-m*spot)); pp=P[kp]
        cands=[k for k in C if k>spot*1.005]
        good=[k for k in cands if C[k]["bid"]-pp["ask"]>=-0.004*spot]
        if not good: print(f"  put {kp:g}: nessuna call che finanzia la put (a costo ~0)"); continue
        kc=max(good); cc=C[kc]; net=cc["bid"]-pp["ask"]
        o=outcome(kp,pp["ask"],kc,cc["bid"])
        sp=lambda b:(b["ask"]-b["bid"])/((b["ask"]+b["bid"])/2)*100
        print(f"  put {kp:g} (ask {pp['ask']:.2f}) + call {kc:g} (bid {cc['bid']:.2f}): costo netto {-net:+.2f} ({-net/spot*100:+.2f}%) | pavimento {kp/spot*100-100:+.1f}% tetto {kc/spot*100-100:+.1f}% | a 3 mesi (mu 8%): collar media {o[0]:+.1f}% / 5° pct {o[1]:+.1f}% contro azione media {o[2]:+.1f}% / 5° pct {o[3]:+.1f}% | spread% put {sp(pp):.0f} call {sp(cc):.0f}, OI put {pp['oi']} call {cc['oi']}")
ib=connect("127.0.0.1",7496,150)
try:
    ib.reqMarketDataType(int(os.environ.get("MDT","1")))
    for s in sys.argv[1:]:
        try: analyse(ib,s)
        except Exception as ex: print(s,"errore:",str(ex)[:90])
finally: ib.disconnect()
