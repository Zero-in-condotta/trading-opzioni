"""Valuta TUTTE le strutture a rischio finito su un sottostante con prezzi reali (SOLA LETTURA):
put spread, call spread, iron condor, iron butterfly, calendar put/call ATM, bull call spread, bear put spread.
Uso: uv run --with yfinance strategies.py SPX XSP ESTX50 AAPL MSFT ...
RV attesa = max(RV10, RV20 close-to-close) + 1 (regola prudente). Margine = RV di pareggio - RV attesa (crediti)."""
import os, sys, json, datetime as dt, numpy as np, yfinance as yf
from math import sqrt, log, exp
from statistics import NormalDist
from ib_async import Index, Stock, Option
from ib_common import connect, clean
N=NormalDist().cdf
U={"SPX":dict(kind="IND",sym="SPX",exch="CBOE",yt="^GSPC",mult=100,cur="USD"),
   "XSP":dict(kind="IND",sym="XSP",exch="CBOE",yt="^GSPC",mult=100,cur="USD"),
   "RUT":dict(kind="IND",sym="RUT",exch="RUSSELL",yt="^RUT",mult=100,cur="USD"),
   "ESTX50":dict(kind="IND",sym="ESTX50",exch="EUREX",yt="^STOXX50E",mult=10,cur="EUR",oexch="EUREX",tclass="OESX")}
EU={"SAP":("SAP.DE","IBIS"),"SIE":("SIE.DE","IBIS"),"ALV":("ALV.DE","IBIS"),"DTE":("DTE.DE","IBIS"),"MUV2":("MUV2.DE","IBIS"),"BAS":("BAS.DE","IBIS"),"BMW":("BMW.DE","IBIS"),"BAYN":("BAYN.DE","IBIS"),"DBK":("DBK.DE","IBIS"),"VOW3":("VOW3.DE","IBIS"),
   "ADS":("ADS.DE","IBIS"),"CON":("CON.DE","IBIS"),"DB1":("DB1.DE","IBIS"),"DHL":("DHL.DE","IBIS"),"EOAN":("EOAN.DE","IBIS"),"FRE":("FRE.DE","IBIS"),"HEI":("HEI.DE","IBIS"),"HEN3":("HEN3.DE","IBIS"),"IFX":("IFX.DE","IBIS"),"MBG":("MBG.DE","IBIS"),
   "MRK":("MRK.DE","IBIS"),"MTX":("MTX.DE","IBIS"),"RWE":("RWE.DE","IBIS"),"RHM":("RHM.DE","IBIS"),"VNA":("VNA.DE","IBIS"),"ZAL":("ZAL.DE","IBIS"),"SHL":("SHL.DE","IBIS"),"ENR":("ENR.DE","IBIS"),"BEI":("BEI.DE","IBIS"),"QIA":("QIA.DE","IBIS"),
   # Francia/Olanda/Italia: coperti dallo stesso abbonamento gia' attivo ("Europe Equity and
   # Derivative Display Value Bundle", copre anche Euronext Basic e Borsa Italiana). Verificato il 22/09/2026.
   "ASML":("ASML.AS","AEB"),"MC":("MC.PA","SBF"),"TTE":("TTE.PA","SBF"),"OR":("OR.PA","SBF"),"AIR":("AIR.PA","SBF"),"AI":("AI.PA","SBF"),"SU":("SU.PA","SBF"),"BNP":("BNP.PA","SBF"),"DG":("DG.PA","SBF"),
   "PHIA":("PHIA.AS","AEB"),"AD":("AD.AS","AEB"),"ISP":("ISP.MI","BVME"),"ENI":("ENI.MI","BVME"),"ENEL":("ENEL.MI","BVME"),"UCG":("UCG.MI","BVME"),"G":("G.MI","BVME")}
# Le 30 piu' liquide tra le 46 disponibili su Eurex/Euronext/Borsa Italiana (scelte per capitalizzazione/
# peso in EuroStoxx50; VNA e SHL escluse perche' oggi senza scadenza 24-46 gg). Selezione del 22/09/2026.
EU_LIST=["ASML","SAP","MC","TTE","SIE","ALV","SU","OR","AI","AIR","DTE","MUV2","BAS","BNP","ISP","ENEL","ENI",
   "ADS","DG","MBG","IFX","DB1","VOW3","PHIA","AD","UCG","G","BMW","DBK","RWE"]
EU_LIST_ALL=list(EU); US_LIST=["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","AMD","NFLX","JPM","BAC","WFC","GS","MS","XOM","CVX","WMT","COST","HD","MU","INTC","ORCL","CRM","ADBE","DIS","NKE","V","MA","UNH","PFE","KO"]
# Alcuni titoli hanno su Eurex la classe delle opzioni a scadenza settimanale/mensile ravvicinata
# sotto un ticker diverso dall'azione (spesso storico): senza questo alias si trova solo la classe
# "E" a scadenze lunghe (24/59/87 gg) e si perde la finestra 24-46 giorni. Verificato il 22/09/2026.
TCLASS_ALIAS={"BAYN":"BAY","DHL":"DPW","EOAN":"EOA","MBG":"DAI","VOW3":"VO3",
   "ASML":"ASM","MC":"MOH","TTE":"TOTB","OR":"LOR","AIR":"EAD","AI":"AIR","SU":"SND","DG":"SQU",
   "ISP":"IES5","ENI":"ENT5","ENEL":"ENL5","UCG":"CRI5","G":"ASG5","PHIA":"PHI1","AD":"AHO"}
def cfg(n):
    if n in EU: return dict(kind="STK",sym=n,exch=EU[n][1],yt=EU[n][0],mult=100,cur="EUR",oexch="EUREX",tclass=TCLASS_ALIAS.get(n,n))
    return U.get(n,dict(kind="STK",sym=n,exch="SMART",yt=n,mult=100,cur="USD"))
def chunks(s,n):
    for i in range(0,len(s),n): yield s[i:i+n]
def bs(S,K,T,s,cp):
    d1=(log(S/K)+.5*s*s*T)/(s*sqrt(T)); d2=d1-s*sqrt(T)
    c=S*N(d1)-K*N(d2); return c if cp=="C" else c-S+K
def rvs(yt):
    c=yf.download(yt,period="1y",progress=False,auto_adjust=False)["Close"].squeeze().dropna(); r=np.log(c).diff().dropna()
    return {n:r.iloc[-n:].std()*sqrt(252)*100 for n in (10,20,60)}
def earn(yt):
    try:
        cal=yf.Ticker(yt).calendar; e=cal.get("Earnings Date") if isinstance(cal,dict) else None
        if e: e=e[0] if isinstance(e,(list,tuple)) else e; return e if isinstance(e,dt.date) else e.date()
    except Exception: pass
def fetch(ib,c,und,spot,exp,tc,exch,strikes):
    cons=[Option(c["sym"],exp,k,r,exch,currency=c["cur"],tradingClass=tc) for k in strikes for r in ("P","C")]
    ok=[]
    for blk in chunks(cons,50): ok+=[x for x in ib.qualifyContracts(*blk) if x and x.conId]
    out={}
    for blk in chunks(ok,80):
        ts=[ib.reqMktData(x,"101",False,False) for x in blk]; ib.sleep(6)
        for x in ts:
            g=x.modelGreeks; b,a=clean(x.bid),clean(x.ask)
            if b is not None and a is not None and a>0 and g and g.impliedVol and g.delta:
                out[(x.contract.right,x.contract.strike)]=dict(bid=b,ask=a,iv=g.impliedVol,delta=g.delta,oi=clean(x.putOpenInterest if x.contract.right=="P" else x.callOpenInterest))
        for x in blk: ib.cancelMktData(x)
    return out
RES=[]
def analyse(ib,name):
    RES.clear(); c=cfg(name)
    und=Index(c["sym"],c["exch"]) if c["kind"]=="IND" else Stock(c["sym"],c["exch"] if c.get("oexch") else "SMART",c["cur"])
    ib.qualifyContracts(und)
    t=ib.reqMktData(und,"",False,False); ib.sleep(3); spot=clean(t.last) or clean(t.marketPrice()) or clean(t.close); ib.cancelMktData(und)
    if not spot:
        try:
            spot=float(yf.download(c["yt"],period="5d",progress=False,auto_adjust=False)["Close"].squeeze().dropna().iloc[-1]); print(name,"(prezzo dal Yahoo: nessun dato IBKR sul sottostante)")
        except Exception: print(name,": senza prezzo"); return
    chains=ib.reqSecDefOptParams(c["sym"],"",und.secType,und.conId)
    today=dt.date.today(); near=far=None
    for ch in chains:
        if c.get("tclass") and ch.tradingClass!=c["tclass"]: continue
        if c["kind"]=="STK" and ch.exchange!=c.get("oexch","SMART"): continue
        if c["kind"]=="IND" and not c.get("tclass") and ch.exchange not in ("SMART","CBOE","RUSSELL"): continue
        for e in ch.expirations:
            d=(dt.datetime.strptime(e,"%Y%m%d").date()-today).days
            if 24<=d<=46 and (near is None or abs(d-33)<abs(near[0]-33)): near=(d,e,ch.tradingClass,ch.exchange,set(ch.strikes))
            if 55<=d<=95 and (far is None or abs(d-70)<abs(far[0]-70)): far=(d,e,ch.tradingClass,ch.exchange,set(ch.strikes))
    if not near: print(name,": nessuna scadenza 28-46 gg"); return
    d1,e1,tc,ex,ks=near; T1=d1/365
    kn=sorted(k for k in ks if 0.86*spot<=k<=1.14*spot)
    data=fetch(ib,c,und,spot,e1,tc,c["oexch"] if c.get("oexch") else "SMART",kn)
    P={k:v for (r,k),v in data.items() if r=="P"}; C={k:v for (r,k),v in data.items() if r=="C"}
    if len(P)<6 or len(C)<6: print(name,": catena incompleta (pochi dati)"); return
    rv=rvs(c["yt"]); ex_rv=max(rv[10],rv[20])+1; ed=earn(c["yt"]); mult=c["mult"]
    flag=""
    if ed and ed<=dt.datetime.strptime(e1,"%Y%m%d").date() and c["kind"]=="STK": flag=f"  ATTENZIONE: utili {ed} prima della scadenza"
    print(f"\n=== {name} ({c['cur']}): spot {spot:,.2f} | scadenza {e1} ({d1} gg) | RV c2c 10/20/60 {rv[10]:.0f}/{rv[20]:.0f}/{rv[60]:.0f}% -> attesa {ex_rv:.1f}% | IV put 16Δ ~ {min(P.values(),key=lambda v:abs(abs(v['delta'])-.16))['iv']*100:.1f}%, call 16Δ ~ {min(C.values(),key=lambda v:abs(abs(v['delta'])-.16))['iv']*100:.1f}%{flag}")
    z=np.random.default_rng(1).standard_normal(300000)
    def price(book,k,side):  # side +1 compro (pago ask), -1 vendo (incasso bid)
        v=book[k]; return v["ask"] if side>0 else v["bid"], (v["ask"]+v["bid"])/2
    def structure(legs):  # legs: (book,k,qty)
        nat=sum(-q*(price(b,k,q)[0]) for b,k,q in legs); mid=sum(-q*price(b,k,q)[1] for b,k,q in legs)
        lim=nat+0.4*(mid-nat); return nat,mid,lim
    def payoff(F,legs): return sum(q*(np.maximum(F-k,0) if b is C else np.maximum(k-F,0)) for b,k,q in legs)
    def ev(legs,cr,sig):
        F=spot*np.exp(-0.5*sig*sig*T1+sig*sqrt(T1)*z); return (cr+payoff(F,legs)).mean()   # q>0 = pago; payoff dei long positivo
    def be(legs,cr):
        lo,hi=0.03,2.0
        for _ in range(35):
            m=(lo+hi)/2
            if ev(legs,cr,m)>0: lo=m
            else: hi=m
        return (lo+hi)/2*100
    def show(nm,legs,desc,credit=True,width=None):
        nat,mid,lim=structure(legs); cr=lim if credit else lim
        # per crediti lim>0 (incasso); per debiti lim<0
        e=ev(legs,cr,ex_rv/100)*mult; e_lo=ev(legs,cr,0.8*ex_rv/100)*mult; e_hi=ev(legs,cr,1.2*ex_rv/100)*mult
        sp=[round((book[k]["ask"]-book[k]["bid"])/((book[k]["ask"]+book[k]["bid"])/2)*100,1) for book,k,q in legs]
        oi=[book[k]["oi"] for book,k,q in legs]
        risk=(width-lim)*mult if (credit and width) else (-lim*mult if not credit else None)
        b=be(legs,cr)
        m=b-ex_rv if credit else None
        s=f"  {nm:22s} {desc:36s} {'credito' if credit else 'debito'} {abs(lim):8.2f} (nat {nat:.2f} / mid {mid:.2f})"
        if risk: s+=f" | rischio {risk:9,.0f} | EV {e:+8.0f} (RV -20%: {e_lo:+.0f}, +20%: {e_hi:+.0f}) {c['cur']}"
        if credit: s+=f" | RV pareggio {b:5.1f} (margine {m:+.1f})"
        s+=f" | spread% {sp} OI {oi}"
        print(s)
        RES.append(dict(nm=nm,ev=round(float(e),1),lo=round(float(e_lo),1),hi=round(float(e_hi),1),margin=(None if m is None else round(float(m),1)),risk=(None if risk is None else round(float(risk),0)),sp=max(sp),oi=[None if o is None else float(o) for o in oi]))
        return None
    wid=0.01*spot
    kp=min(P,key=lambda k:abs(abs(P[k]['delta'])-.16)); kc=min(C,key=lambda k:abs(abs(C[k]['delta'])-.16))
    lp=min([k for k in P if k<kp],key=lambda k:abs(k-(kp-wid)),default=None); lc=min([k for k in C if k>kc],key=lambda k:abs(k-(kc+wid)),default=None)
    if lp and (kp-lp)>1.7*wid: lp=None
    if lc and (lc-kc)>1.7*wid: lc=None
    if lp: show("Put spread",[(P,kp,-1),(P,lp,+1)],f"{lp:g}/{kp:g} (corta {abs(P[kp]['delta']):.2f}Δ)",True,kp-lp)
    if lc: show("Call spread",[(C,kc,-1),(C,lc,+1)],f"{kc:g}/{lc:g} (corta {C[kc]['delta']:.2f}Δ)",True,lc-kc)
    if lp and lc: show("Iron condor",[(P,kp,-1),(P,lp,+1),(C,kc,-1),(C,lc,+1)],f"{lp:g}/{kp:g}+{kc:g}/{lc:g}",True,max(kp-lp,lc-kc))
    atm=min(set(P)&set(C),key=lambda k:abs(k-spot)); wb=0.04*spot
    lw=min([k for k in P if k<atm],key=lambda k:abs(k-(atm-wb)),default=None); uw=min([k for k in C if k>atm],key=lambda k:abs(k-(atm+wb)),default=None)
    if lw and uw and (atm-lw)<=1.7*wb and (uw-atm)<=1.7*wb: show("Iron butterfly",[(P,atm,-1),(C,atm,-1),(P,lw,+1),(C,uw,+1)],f"{lw:g}/{atm:g}/{uw:g}",True,max(atm-lw,uw-atm))
    # spread a debito direzionali
    ka=min(C,key=lambda k:abs(C[k]['delta']-.50)); kb=min(C,key=lambda k:abs(C[k]['delta']-.25))
    if kb>ka: show("Bull call spread",[(C,ka,+1),(C,kb,-1)],f"{ka:g}/{kb:g}",False)
    pa=min(P,key=lambda k:abs(abs(P[k]['delta'])-.50)); pb=min(P,key=lambda k:abs(abs(P[k]['delta'])-.25))
    if pb<pa: show("Bear put spread",[(P,pa,+1),(P,pb,-1)],f"{pa:g}/{pb:g}",False)
    # calendar ATM (put e call): corta near, lunga far stesso strike
    if far:
        d2,e2,tc2,ex2,ks2=far; T2=d2/365
        kf=sorted(k for k in ks2 if abs(k-spot)/spot<=0.03)
        fd=fetch(ib,c,und,spot,e2,tc2,c["oexch"] if c.get("oexch") else "SMART",kf)
        for r,book in (("P",P),("C",C)):
            fb={k:v for (rr,k),v in fd.items() if rr==r}
            common=[k for k in fb if k in book]
            if not common: continue
            k=min(common,key=lambda x:abs(x-spot)); s1=book[k]; s2=fb[k]
            debit=s2["ask"]-s1["bid"]; midd=(s2["ask"]+s2["bid"])/2-(s1["ask"]+s1["bid"])/2; lim=debit-0.4*(debit-midd)
            fwd=sqrt(max((s2["iv"]**2*T2-s1["iv"]**2*T1)/(T2-T1),1e-6)); ff=s1["iv"]/fwd-1
            # simulazione a 2 stadi: prezzo a T1 con vol reale; poi valore della lunga con vol = IV lunga (ipotesi A) o forward (ipotesi B)
            F=spot*np.exp(-0.5*(ex_rv/100)**2*T1+(ex_rv/100)*sqrt(T1)*z)
            corta=np.maximum(F-k,0) if r=="C" else np.maximum(k-F,0)
            def lunga(sig): 
                return np.array([bs(f,k,T2-T1,sig,r) for f in F[:20000]])
            eA=(lunga(s2["iv"]).mean()-corta[:20000].mean()-lim)*mult; eB=(lunga(fwd).mean()-corta[:20000].mean()-lim)*mult
            print(f"  Calendar {'put' if r=='P' else 'call'} ATM {k:g} ({e1} corta / {e2} lunga): debito {lim:.2f} (nat {debit:.2f}) rischio {lim*mult:8,.0f} | IV corta {s1['iv']*100:.1f}% lunga {s2['iv']*100:.1f}% forward {fwd*100:.1f}% forward factor {ff:+.2f} | EV se la lunga mantiene IV {eA:+.0f} / vale la forward {eB:+.0f} {c['cur']} | spread% {round((s1['ask']-s1['bid'])/((s1['ask']+s1['bid'])/2)*100,1)}/{round((s2['ask']-s2['bid'])/((s2['ask']+s2['bid'])/2)*100,1)} OI {s1['oi']}/{s2['oi']}")
def _fin(name,c,ex_rv=None): pass
if __name__=="__main__":
    ib=connect("127.0.0.1",7496,130)
    try:
        ib.reqMarketDataType(int(os.environ.get('MDT','1')))
        for n in sys.argv[1:]:
            try:
                analyse(ib,n); print('SUMMARY|'+n+'|'+json.dumps(RES),flush=True)
            except Exception as ex: print(n,'errore:',str(ex)[:100],flush=True)
    finally: ib.disconnect()
