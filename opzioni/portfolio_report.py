"""Riepilogo del portafoglio (SOLA LETTURA): posizioni, valore di mercato, P&L, DTE. Non stampa il numero di conto."""
import datetime as dt
from ib_common import connect, clean
ib=connect("127.0.0.1",7496,111)
try:
    ib.sleep(3)
    port=ib.portfolio()
    today=dt.date.today()
    print(f"Posizioni: {len(port)}")
    for p in sorted(port,key=lambda x:(x.contract.symbol,x.contract.lastTradeDateOrContractMonth,x.contract.strike)):
        c=p.contract
        exp=c.lastTradeDateOrContractMonth
        dte=""
        try: dte=(dt.datetime.strptime(exp,"%Y%m%d").date()-today).days
        except Exception: pass
        print(f"{c.symbol:6s} {c.secType:4s} {exp} {c.strike:>8} {c.right or '':1s} qty {p.position:+.0f} | costo medio {p.averageCost:9.2f} | prezzo {p.marketPrice:9.3f} | valore {p.marketValue:10.2f} {c.currency} | P&L non realizzato {p.unrealizedPNL:9.2f} | DTE {dte}")
    for a in ib.accountSummary():
        if a.tag in ("NetLiquidation","MaintMarginReq","ExcessLiquidity","Cushion","UnrealizedPnL","RealizedPnL"):
            print("CONTO",a.tag,a.value,a.currency)
finally: ib.disconnect()
