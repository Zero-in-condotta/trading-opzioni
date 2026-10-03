"""Stadio 2 in serie su molti titoli (SOLA LETTURA): put spread a circa 16 delta, RV close-to-close da Yahoo, utili da Yahoo.
Uso: uv run --with yfinance stage2_batch.py INTU CHTR ...  (una riga per titolo)"""
import sys, datetime as dt
import numpy as np, yfinance as yf
from math import sqrt
from ib_async import Stock, Option
from ib_common import connect, clean
DTE=(28,46)
def chunks(s,n):
    for i in range(0,len(s),n): yield s[i:i+n]
def yahoo(sym):
    tk=yf.Ticker(sym)
    c=yf.download(sym,period="1y",progress=False,auto_adjust=False)["Close"].squeeze().dropna()
    r=np.log(c).diff().dropna()
    rv={n:r.iloc[-n:].std()*sqrt(252)*100 for n in (10,20,60)}
    big=int((r.iloc[-504:].abs()>0.06).sum()) if len(r)>=250 else int((r.abs()>0.06).sum())
    ed=None
    try:
        cal=tk.calendar; e=cal.get("Earnings Date") if isinstance(cal,dict) else None
        if e: ed=e[0] if isinstance(e,(list,tuple)) else e
    except Exception: pass
    return rv,ed,big
def run(ib,sym):
    rv,ed,big=yahoo(sym); exp_rv=max(rv[10],rv[20])+1
    u=Stock(sym,"SMART","USD"); ib.qualifyContracts(u)
    t=ib.reqMktData(u,"",False,False); ib.sleep(3)
    spot=clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(u)
    if not spot: print(f"{sym}: senza prezzo"); return
    chains=[c for c in ib.reqSecDefOptParams(sym,"",u.secType,u.conId) if c.exchange=="SMART"]
    today=dt.date.today(); best=None; strikes=set()
    for c in chains:
        for e in c.expirations:
            d=(dt.datetime.strptime(e,"%Y%m%d").date()-today).days
            if DTE[0]<=d<=DTE[1] and (best is None or abs(d-35)<abs(best[0]-35)): best=(d,e,c.tradingClass)
        strikes|=set(c.strikes)
    if not best: print(f"{sym}: nessuna scadenza 28-46 gg"); return
    d,e,tc=best; T=d/365
    exp_date=dt.datetime.strptime(e,"%Y%m%d").date()
    flag=""
    if ed is not None:
        try:
            edd=ed if isinstance(ed,dt.date) else ed.date()
            if edd<=exp_date: flag=f"UTILI {edd} PRIMA DELLA SCADENZA"
            else: flag=f"utili {edd}"
        except Exception: pass
    ks=sorted(k for k in strikes if 0.75*spot<=k<=1.0*spot)
    cons=[Option(sym,e,k,"P","SMART",currency="USD",tradingClass=tc) for k in ks]
    ok=[]
    for blk in chunks(cons,50): ok+=[c for c in ib.qualifyContracts(*blk) if c and c.conId]
    rows=[]
    for blk in chunks(ok,80):
        ts=[ib.reqMktData(c,"101",False,False) for c in blk]; ib.sleep(5)
        for x in ts:
            g=x.modelGreeks; b,a=clean(x.bid),clean(x.ask)
            if b is not None and a is not None and g and g.impliedVol and g.delta:
                rows.append(dict(k=x.contract.strike,bid=b,ask=a,iv=g.impliedVol,delta=g.delta,oi=clean(x.putOpenInterest)))
        for c in blk: ib.cancelMktData(c)
    rows.sort(key=lambda r:r["k"])
    if len(rows)<4: print(f"{sym}: catena vuota"); return
    short=min(rows,key=lambda r:abs(abs(r["delta"])-0.16))
    tgt=short["k"]-0.05*spot
    lg=[r for r in rows if r["k"]<short["k"]]
    if not lg: print(f"{sym}: ala non trovata"); return
    long_=min(lg,key=lambda r:abs(r["k"]-tgt)); w=short["k"]-long_["k"]
    nat=short["bid"]-long_["ask"]; mid=(short["bid"]+short["ask"])/2-(long_["bid"]+long_["ask"])/2; lim=nat+0.4*(mid-nat)
    z=np.random.default_rng(1).standard_normal(300000)
    def ev(sig):
        F=spot*np.exp(-0.5*sig*sig*T+sig*sqrt(T)*z)
        return (lim-(np.maximum(short["k"]-F,0)-np.maximum(long_["k"]-F,0))).mean()
    lo,hi=0.03,2.0
    for _ in range(40):
        m=(lo+hi)/2
        if ev(m)>0: lo=m
        else: hi=m
    be=(lo+hi)/2*100
    sp=lambda r:(r["ask"]-r["bid"])/((r["ask"]+r["bid"])/2)*100
    print(f"{sym:5s} {spot:8.2f} | {e} {d}gg | put {long_['k']:g}/{short['k']:g} (d {short['delta']:+.2f}, IV {short['iv']*100:4.1f}%) cred {lim:5.2f} ({lim/w*100:3.0f}% di {w:g}) rischio {(w-lim)*100:6,.0f}$ | spread% {sp(short):4.1f}/{sp(long_):5.1f} OI {short['oi']}/{long_['oi']} | RV c2c 10/20/60 {rv[10]:.0f}/{rv[20]:.0f}/{rv[60]:.0f} att {exp_rv:.0f} | pareggio {be:.1f} margine {be-exp_rv:+.1f} | EV {ev(exp_rv/100)*100:+.0f}$ | gap>6% (2a) {big} | {flag}")
ib=connect("127.0.0.1",7496,109)
try:
    ib.reqMarketDataType(1)
    for s in sys.argv[1:]:
        try: run(ib,s)
        except Exception as ex: print(s,"errore:",str(ex)[:80])
finally: ib.disconnect()
