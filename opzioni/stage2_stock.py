"""Stadio 2 dello scanner: put credit spread su titoli (SOLA LETTURA).
Uso: uv run stage2_stock.py AMAT:44.69 SONY:24.5 PDD:29.18   (ticker:RV attesa % da OQuants)
"""
import sys, math, datetime as dt
import numpy as np
from math import log, sqrt
from ib_async import Stock, Option
from ib_common import connect, clean

TARGET_DTE=(21,62)
def chunks(s,n):
    for i in range(0,len(s),n): yield s[i:i+n]

def analyse(ib, sym, rv_exp):
    u=Stock(sym,"SMART","USD"); ib.qualifyContracts(u)
    t=ib.reqMktData(u,"",False,False); ib.sleep(3)
    spot=clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(u)
    chains=[c for c in ib.reqSecDefOptParams(sym,"",u.secType,u.conId) if c.exchange=="SMART"]
    today=dt.date.today(); exps=set(); strikes=set()
    for c in chains:
        for e in c.expirations:
            d=(dt.datetime.strptime(e,"%Y%m%d").date()-today).days
            if TARGET_DTE[0]<=d<=TARGET_DTE[1]: exps.add((d,e,c.tradingClass))
        strikes|=set(c.strikes)
    if not exps: print(sym,"nessuna scadenza in finestra"); return
    d,e,tc=sorted(exps,key=lambda x:abs(x[0]-35))[0]
    ks=sorted(k for k in strikes if 0.72*spot<=k<=1.02*spot)
    cons=[Option(sym,e,k,"P","SMART",currency="USD",tradingClass=tc) for k in ks]
    ok=[]
    for blk in chunks(cons,50): ok+= [c for c in ib.qualifyContracts(*blk) if c and c.conId]
    rows=[]
    for blk in chunks(ok,80):
        ts=[ib.reqMktData(c,"101",False,False) for c in blk]; ib.sleep(6)
        for x in ts:
            g=x.modelGreeks
            rows.append(dict(k=x.contract.strike,bid=clean(x.bid),ask=clean(x.ask),iv=(g.impliedVol if g else None),delta=(g.delta if g else None),oi=clean(x.putOpenInterest)))
        for c in blk: ib.cancelMktData(c)
    rows=[r for r in rows if r["bid"] and r["ask"] and r["iv"] and r["delta"]]
    rows.sort(key=lambda r:r["k"])
    if len(rows)<4: print(sym,"catena vuota o senza dati"); return
    # put corta vicina a -0.16, ala = strike inferiore a distanza circa 5% dello spot
    short=min(rows,key=lambda r:abs(abs(r["delta"])-0.16))
    target=short["k"]-0.05*spot
    long_=min([r for r in rows if r["k"]<short["k"]],key=lambda r:abs(r["k"]-target),default=None)
    if long_ is None: print(sym,"ala non trovata"); return
    w=short["k"]-long_["k"]; T=d/365
    nat=short["bid"]-long_["ask"]; mid=(short["bid"]+short["ask"])/2-(long_["bid"]+long_["ask"])/2
    lim=nat+0.4*(mid-nat)
    z=np.random.default_rng(1).standard_normal(400000)
    def ev(sig,cr):
        F=spot*np.exp(-0.5*sig*sig*T+sig*sqrt(T)*z)
        return (cr-(np.maximum(short["k"]-F,0)-np.maximum(long_["k"]-F,0))).mean()
    # RV di pareggio
    lo,hi=0.05,1.5
    for _ in range(50):
        m=(lo+hi)/2
        if ev(m,lim)>0: lo=m
        else: hi=m
    be=(lo+hi)/2
    sp=lambda r:(r["ask"]-r["bid"])/((r["ask"]+r["bid"])/2)*100
    print(f"\n{sym}: spot {spot:.2f}, scadenza {e} ({d} gg), classe {tc}")
    print(f"  put spread {long_['k']}/{short['k']} (delta corta {short['delta']:+.2f}, IV corta {short['iv']*100:.1f}%, IV lunga {long_['iv']*100:.1f}%), larghezza {w:.2f}")
    print(f"  credito naturale {nat:.2f} | medio {mid:.2f} | limite {lim:.2f} -> guadagno max {lim*100:.0f} $, perdita max {(w-lim)*100:.0f} $, credito/larghezza {lim/w*100:.0f}%")
    print(f"  spread gambe % (corta {sp(short):.1f}, lunga {sp(long_):.1f}) | OI corta {short['oi']}, lunga {long_['oi']}")
    print(f"  EV per contratto con RV attesa {rv_exp:.1f}%: {ev(rv_exp/100,lim)*100:+.0f} $ ; con RV +5 punti: {ev((rv_exp+5)/100,lim)*100:+.0f} $ ; RV di pareggio {be*100:.1f}% (margine {be*100-rv_exp:+.1f} punti)")

ib=connect("127.0.0.1",7496,106)
try:
    ib.reqMarketDataType(1)
    for a in sys.argv[1:]:
        s,r=a.split(":"); analyse(ib,s,float(r))
finally:
    ib.disconnect()
