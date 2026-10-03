"""Catena put LEAP di un titolo (SOLA LETTURA). Uso: uv run --with yfinance leap_chain.py NKE [giorni_target]"""
import sys, datetime as dt
from ib_async import Stock, Option
from ib_common import connect, clean
sym=sys.argv[1]; target=int(sys.argv[2]) if len(sys.argv)>2 else 365
ib=connect("127.0.0.1",7496,110)
try:
    ib.reqMarketDataType(1)
    u=Stock(sym,"SMART","USD"); ib.qualifyContracts(u)
    t=ib.reqMktData(u,"",False,False); ib.sleep(3); spot=clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(u)
    ch=[c for c in ib.reqSecDefOptParams(sym,"",u.secType,u.conId) if c.exchange=="SMART"]
    today=dt.date.today(); exps={}
    for c in ch:
        for e in c.expirations:
            d=(dt.datetime.strptime(e,"%Y%m%d").date()-today).days
            if 200<=d<=560: exps[e]=(d,c.tradingClass,set(c.strikes))
    print(sym,"spot",spot,"| scadenze LEAP:",sorted((d,e) for e,(d,_,_) in exps.items()))
    e=min(exps,key=lambda x:abs(exps[x][0]-target)); d,tc,ks=exps[e]
    ks=sorted(k for k in ks if 0.55*spot<=k<=1.1*spot)
    cons=[Option(sym,e,k,"P","SMART",currency="USD",tradingClass=tc) for k in ks]
    ok=[c for c in ib.qualifyContracts(*cons) if c and c.conId]
    ts=[ib.reqMktData(c,"101",False,False) for c in ok]; ib.sleep(8)
    print(f"scadenza {e} ({d} gg), classe {tc}\nstrike bid ask mid IV delta OI")
    for x in ts:
        g=x.modelGreeks; b,a=clean(x.bid),clean(x.ask)
        if b is None or a is None: continue
        print(f"{x.contract.strike:6.1f} {b:6.2f} {a:6.2f} {(a+b)/2:6.2f} {(g.impliedVol*100 if g else float('nan')):5.1f} {(g.delta if g else float('nan')):+.3f} {clean(x.putOpenInterest)}")
    for c in ok: ib.cancelMktData(c)
finally: ib.disconnect()
