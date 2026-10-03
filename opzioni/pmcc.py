"""Covered call mensile e PMCC su catene reali (SOLA LETTURA), un ciclo a 30 giorni simulato.
Uso: MDT=2 uv run --with yfinance pmcc.py AAPL MSFT ...   (MDT=2 a mercato chiuso: prezzi congelati)"""
import os, sys, datetime as dt, numpy as np, yfinance as yf
from math import sqrt, log, exp
from scipy.stats import norm
from ib_async import Stock, Option
from ib_common import connect, clean
R=0.04
def chunks(s,n):
    for i in range(0,len(s),n): yield s[i:i+n]
def bs(S,K,T,v,q=0.0):
    if T<=0: return max(S-K,0)
    d1=(log(S/K)+(R-q+0.5*v*v)*T)/(v*sqrt(T)); d2=d1-v*sqrt(T)
    return S*exp(-q*T)*norm.cdf(d1)-K*exp(-R*T)*norm.cdf(d2)
def delta(S,K,T,v): return norm.cdf((log(S/K)+(R+0.5*v*v)*T)/(v*sqrt(T)))
def iv(px,S,K,T):
    lo,hi=0.03,2.0
    if px<=max(S-K*exp(-R*T),0)+1e-6: return None
    for _ in range(60):
        m=(lo+hi)/2
        if bs(S,K,T,m)>px: hi=m
        else: lo=m
    return (lo+hi)/2
def quotes(ib,sym,e,ks):
    cons=[Option(sym,e,k,"C","SMART",currency="USD",tradingClass=sym) for k in ks]
    ok=[]
    for blk in chunks(cons,50): ok+=[x for x in ib.qualifyContracts(*blk) if x and x.conId]
    out={}
    for blk in chunks(ok,80):
        ts=[ib.reqMktData(x,"101",False,False) for x in blk]; ib.sleep(6)
        for x in ts:
            b,a=clean(x.bid),clean(x.ask)
            if b is not None and a is not None and a>0: out[x.contract.strike]=dict(bid=b,ask=a,oi=clean(x.callOpenInterest))
        for x in blk: ib.cancelMktData(x)
    return out
def analyse(ib,sym):
    u=Stock(sym,"SMART","USD"); ib.qualifyContracts(u)
    t=ib.reqMktData(u,"",False,False); ib.sleep(3); S=clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(u)
    if not S: print(sym,"senza prezzo"); return
    ch=[c for c in ib.reqSecDefOptParams(sym,"",u.secType,u.conId) if c.exchange=="SMART" and c.tradingClass==sym]
    today=dt.date.today(); sh=lg=None
    for c in ch:
        for e in c.expirations:
            d=(dt.datetime.strptime(e,"%Y%m%d").date()-today).days
            if 26<=d<=45 and (sh is None or abs(d-30)<abs(sh[0]-30)): sh=(d,e,c.strikes)
            if 330<=d<=520 and (lg is None or abs(d-400)<abs(lg[0]-400)): lg=(d,e,c.strikes)
    if not sh or not lg: print(sym,"scadenze mancanti"); return
    tk=yf.Ticker(sym)
    try: q=float(tk.info.get("dividendRate") or 0)/S
    except Exception: q=0.0
    c=yf.download(sym,period="1y",progress=False,auto_adjust=False)["Close"].squeeze().dropna(); r=np.log(c).diff().dropna()
    rv=max(r.iloc[-10:].std(),r.iloc[-20:].std())*sqrt(252)+0.01
    ed=None
    try:
        cal=tk.calendar; ee=cal.get("Earnings Date") if isinstance(cal,dict) else None
        if ee: ed=ee[0] if isinstance(ee,(list,tuple)) else ee; ed=ed if isinstance(ed,dt.date) else ed.date()
    except Exception: pass
    ds,es,ks=sh; dl,el,kl=lg; ts_=ds/365; tl=dl/365
    Sq=quotes(ib,sym,es,sorted(k for k in ks if 1.0*S<=k<=1.12*S))
    Lq=quotes(ib,sym,el,sorted(k for k in kl if 0.55*S<=k<=0.95*S))
    if not Sq or not Lq: print(sym,"catena incompleta"); return
    print(f"\n=== {sym}: spot {S:.2f} (100 azioni = {S*100:,.0f} $), call corta {es} ({ds} gg), LEAP {el} ({dl} gg), RV attesa {rv*100:.0f}%"+(f", utili {ed}" if ed else ""))
    z=np.random.default_rng(2).standard_normal(200000)
    for mu,lab in ((0.0,"drift 0%"),(0.08,"drift 8%")):
        pass
    # call corta a circa 0,25-0,30 delta
    best=None
    for k,v in Sq.items():
        mid=(v["bid"]+v["ask"])/2; im=iv(mid,S,k,ts_)
        if im is None: continue
        dl_=delta(S,k,ts_,im)
        if best is None or abs(dl_-0.27)<abs(best[2]-0.27): best=(k,v,dl_,im)
    if not best: print("  nessuna call corta valida"); return
    ks_,vs,dls,ims=best
    sp=lambda b:(b["ask"]-b["bid"])/((b["ask"]+b["bid"])/2)*100
    print(f"  COVERED CALL: vendi call {ks_:g} (delta {dls:.2f}, bid {vs['bid']:.2f} = {vs['bid']/S*100:.2f}% del titolo in {ds} gg, IV {ims*100:.0f}%, spread {sp(vs):.0f}%, OI {vs['oi']})")
    for mu in (0.0,0.08):
        F=S*np.exp((mu-0.5*rv*rv)*ts_+rv*sqrt(ts_)*z)
        st=F-S+S*q*ts_; cc=st+vs["bid"]-np.maximum(F-ks_,0)
        print(f"    ciclo di {ds} gg, drift {mu*100:.0f}%: azione sola media {st.mean()/S*100:+.2f}% (5° pct {np.percentile(st,5)/S*100:+.1f}%) | con call venduta media {cc.mean()/S*100:+.2f}% (5° pct {np.percentile(cc,5)/S*100:+.1f}%, tetto {(ks_-S+vs['bid'])/S*100:+.1f}%)")
    # LEAP a circa 0,80 delta
    bl=None
    for k,v in Lq.items():
        mid=(v["bid"]+v["ask"])/2; il=iv(mid,S,k,tl)
        if il is None: continue
        d_=delta(S,k,tl,il)
        if bl is None or abs(d_-0.80)<abs(bl[2]-0.80): bl=(k,v,d_,il)
    if not bl: print("  nessuna LEAP valida"); return
    kL,vL,dL,ivL=bl
    debit=vL["ask"]-vs["bid"]
    intr=max(S-kL,0); tv=vL["ask"]-intr
    print(f"  PMCC: compri LEAP {kL:g} (delta {dL:.2f}, ask {vL['ask']:.2f}, IV {ivL*100:.0f}%, spread {sp(vL):.0f}%, OI {vL['oi']}) e vendi call {ks_:g}: debito {debit:.2f} = {debit*100:,.0f} $ ({debit/S*100:.0f}% del valore di 100 azioni); valore temporale nella LEAP {tv:.2f} ({tv/S*100:.1f}% del titolo)")
    for mu in (0.0,0.08):
        F=S*np.exp((mu-0.5*rv*rv)*ts_+rv*sqrt(ts_)*z)
        leap=np.array([bs(f,kL,tl-ts_,ivL,q) for f in F[:20000]])
        pnl=leap-vL["ask"]+vs["bid"]-np.maximum(F[:20000]-ks_,0)
        print(f"    ciclo di {ds} gg, drift {mu*100:.0f}%: P&L medio {pnl.mean()*100:+,.0f} $ ({pnl.mean()/debit*100:+.1f}% del debito), 5° pct {np.percentile(pnl,5)*100:+,.0f} $ ({np.percentile(pnl,5)/debit*100:+.0f}%), perdita massima teorica {debit*100:,.0f} $")
ib=connect("127.0.0.1",7496,151)
try:
    ib.reqMarketDataType(int(os.environ.get("MDT","1")))
    for s in sys.argv[1:]:
        try: analyse(ib,s)
        except Exception as ex: print(s,"errore:",str(ex)[:120])
finally: ib.disconnect()
