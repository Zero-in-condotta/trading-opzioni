"""Stress test del portafoglio (SOLA LETTURA): rivaluta ogni opzione con Black-Scholes a IV corrente e IV stressata.
Scenari immediati (T invariata). Il P&L di scenario = valore stressato - valore corrente con lo stesso modello."""
import datetime as dt, sys
from math import log, sqrt, exp
from statistics import NormalDist
import json
from ib_async import Option, Forex
from ib_common import connect, clean, DATA
INDICI={"ESTX50","XSP","SPX","NDX","XND","RUT","MRUT","DJX","VIX"}
def _carica(f,d):
    try: return json.loads((DATA/f).read_text())
    except Exception: return d
# Protezioni di coda = le gambe del gruppo 'protezioni_coda' di data/portfolio_map.json
HEDGE={(l['symbol'],l['expiry'],float(l['strike']),l['right']) for g in _carica('portfolio_map.json',{'groups':[]})['groups']
       if g['id']=='protezioni_coda' for l in g['legs']}
# Beta dei titoli: data/betas.json (calcola_beta.py); indici e sconosciuti = 1,0
BETA={s:max(v.get('beta_2a') or 1.0,v.get('beta_1a') or 1.0,1.0) for s,v in _carica('betas.json',{}).get('beta',{}).items()}
def eurusd(ib):
    try:
        f=Forex('EURUSD'); ib.qualifyContracts(f); t=ib.reqMktData(f,'',False,False); ib.sleep(3)
        v=next((x for x in (t.marketPrice(),t.last,t.close) if x and x==x and x>0),None); ib.cancelMktData(f)
        if v: return float(v)
    except Exception: pass
    print('ATTENZIONE: cambio EUR/USD non letto da TWS, uso 1,10'); return 1.10
N=NormalDist().cdf
def bs(S,K,T,s,cp,r=0.02):
    if T<=0: return max(S-K,0) if cp=="C" else max(K-S,0)
    d1=(log(S/K)+(r+.5*s*s)*T)/(s*sqrt(T)); d2=d1-s*sqrt(T)
    return S*N(d1)-K*exp(-r*T)*N(d2) if cp=="C" else K*exp(-r*T)*N(-d2)-S*N(-d1)
SCEN=[("S1 correzione",-5,8),("S2 shock",-10,20),("S3 crash",-20,40),("S4 gap weekend",-7,15),("S5 rally violento",7,-6)]
ib=connect("127.0.0.1",7496,140)
try:
    ib.reqMarketDataType(int(sys.argv[1]) if len(sys.argv)>1 else 1)
    port=[p for p in ib.portfolio() if p.contract.secType=="OPT"]
    rows=[]
    tk=[]
    for p in port:
        c=p.contract; ib.qualifyContracts(c); t=ib.reqMktData(c,"",False,False); tk.append((p,t))
    ib.sleep(8)
    today=dt.date.today(); fx=eurusd(ib)
    for p,t in tk:
        c=p.contract; g=t.modelGreeks
        if not g or not g.impliedVol or not g.undPrice: print("dati mancanti per",c.localSymbol); continue
        T=(dt.datetime.strptime(c.lastTradeDateOrContractMonth,"%Y%m%d").date()-today).days/365
        rows.append(dict(sym=c.symbol,cp=c.right,K=c.strike,T=T,iv=g.impliedVol,S=g.undPrice,q=p.position,mult=float(c.multiplier),cur=c.currency,hedge=(p.position>0 and (c.symbol,c.lastTradeDateOrContractMonth,float(c.strike),c.right) in HEDGE)))
    for c in [p.contract for p,t in tk]: ib.cancelMktData(c)
    nav=[float(a.value) for a in ib.accountSummary() if a.tag=="NetLiquidation" and a.currency=="EUR"][0]
    def scen(dm,dv,include_hedge=True,label=""):
        tot=0
        for r in rows:
            if r["hedge"] and not include_hedge: continue
            beta=1.0 if r["sym"] in INDICI else BETA.get(r["sym"],1.0)
            S2=r["S"]*(1+beta*dm/100)
            dvol=dv/100*min(sqrt(30/max(r["T"]*365,7)),2.0)*(1.0 if r["sym"] in INDICI else 0.7)
            v0=bs(r["S"],r["K"],r["T"],r["iv"],r["cp"]); v1=bs(S2,r["K"],r["T"],max(r["iv"]+dvol,0.03),r["cp"])
            pl=r["q"]*r["mult"]*(v1-v0)
            tot+= pl if r["cur"]=="EUR" else pl/fx
        return tot
    print(f"NAV {nav:,.0f} EUR (USD convertiti a {fx:.4f}). Scenari immediati sul portafoglio di opzioni:")
    print(f"{'Scenario':22s} {'CON protezioni':>16s} {'SENZA protezioni':>18s} {'valore delle protezioni':>24s}")
    for name,dm,dv in SCEN:
        a=scen(dm,dv,True); b=scen(dm,dv,False)
        print(f"{name+f' ({dm:+d}%, IV {dv:+d})':22s} {a:9,.0f} EUR ({a/nav*100:+.1f}%) {b:10,.0f} EUR ({b/nav*100:+.1f}%) {a-b:10,.0f} EUR")
finally: ib.disconnect()
