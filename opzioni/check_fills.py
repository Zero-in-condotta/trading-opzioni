"""Esecuzioni di oggi e saldi per valuta (SOLA LETTURA). Non stampa il numero di conto."""
from ib_async import ExecutionFilter
from ib_common import connect
ib=connect("127.0.0.1",7496,105)
try:
    fills=ib.reqExecutions(ExecutionFilter())
    for f in fills:
        c=f.contract
        cr=f.commissionReport
        print(f"{f.time} {c.symbol} {c.secType} {c.lastTradeDateOrContractMonth} {c.strike} {c.right} {f.execution.side} x{f.execution.shares} @ {f.execution.price} | commissione {cr.commission if cr else 'n/d'} {cr.currency if cr else ''}")
    for v in ib.accountValues():
        if v.tag in ("CashBalance","SettledCash","UnrealizedPnL","RealizedPnL") and v.currency in ("USD","BASE","EUR"):
            print("CONTO",v.tag,v.currency,v.value)
finally:
    ib.disconnect()
