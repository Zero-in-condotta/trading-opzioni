"""MODELLO per inviare UN SOLO ordine (singola gamba o combo) a IBKR. Si copia, si compilano i parametri
in cima e si lancia. NON e' uno strumento generico da riga di comando: il contratto sta scritto NEL CODICE,
di proposito (un ordine non deve poter cambiare per un errore di digitazione o di interpretazione).

REGOLE D'USO (vedi docs/RICOSTRUZIONE.md e CLAUDE.md)
  * Si usa SOLO quando l'utente scrive esplicitamente "esegui su tws" riferito a un'operazione precisa.
    Mai di iniziativa, mai oltre il limite dell'istruzione.
  * Prima di inviare si controlla il Piano e si dicono all'utente le eventuali deroghe.
  * Dopo l'invio: verificare le posizioni sul conto, mappare la struttura in data/portfolio_map.json,
    ricaricare la dashboard, scrivere nel registro.
  * ib_common.connect() resta readonly=True per tutti gli altri script: la scrittura vive SOLO nei file derivati
    da questo modello. In TWS serve disattivare "Read-Only API" (Configurazione globale > API).

SICUREZZE, in sequenza
  1. i contratti sono fissi nel codice, qualificati e verificati con assert (strike, tipo, scadenza, moltiplicatore)
  2. il sottostante non si e' mosso oltre la tolleranza rispetto al momento in cui e' stata calcolata la proposta
  3. quotazioni live: il credito/debito ESEGUIBILE (si vende al bid, si compra all'ask) si confronta col limite;
     se il limite non e' raggiungibile l'ordine resta DAY, non si insegue il prezzo
  4. whatIfOrder sul margine con soglia. ATTENZIONE: sui combo spesso restituisce 0 e commissione non valida:
     la copertura va ragionata sulla struttura, non fidandosi di questo numero
  5. senza --confirm non si trasmette nulla (prova a secco)

Uso:  uv run python send_order_TEMPLATE.py            (prova a secco)
      uv run python send_order_TEMPLATE.py --confirm  (trasmette)
"""
from __future__ import annotations
import argparse, sys
from ib_async import IB, Option, Contract, ComboLeg, LimitOrder, Index, Stock

# ======================= PARAMETRI DA COMPILARE (esempio: iron condor su ESTX50) =======================
SOTTOSTANTE = "ESTX50"          # simbolo del sottostante
TIPO_SOTTOSTANTE = "INDEX"      # "INDEX" oppure "STOCK"
BORSA_INDICE = "EUREX"          # borsa dell'indice (XND: "NASDAQ", MRUT: "RUSSELL"); per STOCK si usa SMART
BORSA_OPZIONI = "EUREX"         # borsa delle opzioni ("SMART" per le USA, "EUREX" per ESTX50)
VALUTA = "EUR"
CLASSE_NEGOZIAZIONE = "OESX"    # tradingClass del contratto (ESTX50: OESX; XSP: XSP; titoli USA: il simbolo)
SCADENZA = "20261120"           # AAAAMMGG
MOLTIPLICATORE = 10             # ESTX50 = 10, opzioni USA = 100. Sempre verificato con assert sul contratto.
# Gambe nel verso del PACCHETTO: (strike, tipo "P"/"C", rapporto, azione "BUY"/"SELL")
GAMBE = [(6525.0, "C", 1, "SELL"), (6625.0, "C", 1, "BUY"), (5750.0, "P", 1, "BUY"), (5850.0, "P", 1, "SELL")]
LATO = "credito"                # "credito" (incassi) oppure "debito" (paghi)
LIMITE = 290.0                  # in valuta, per l'intero pacchetto (es. 290 = 290 EUR di credito minimo)
QUANTITA = 1
MARGINE_MAX = 1000.0            # in valuta: sopra questa cifra si annulla (rischio massimo noto + margine di tolleranza)
SPOT_PROPOSTA = 6213.15         # prezzo del sottostante quando e' stata calcolata la proposta
TOLLERANZA_SPOT = 0.01          # 1%: oltre, la proposta e' scaduta
CLIENT_ID = 131
PORTA = 7496
# ======================================================================================================


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true", help="trasmette davvero")
    ap.add_argument("--port", type=int, default=PORTA)
    a = ap.parse_args()
    ib = IB()
    ib.connect("127.0.0.1", a.port, clientId=CLIENT_ID, readonly=False, timeout=15)
    try:
        ib.reqMarketDataType(1)
        # 1-2. sottostante e tolleranza
        und = (Index(SOTTOSTANTE, BORSA_INDICE, VALUTA) if TIPO_SOTTOSTANTE == "INDEX" else Stock(SOTTOSTANTE, "SMART", VALUTA))
        und = ib.qualifyContracts(und)[0]
        tu = ib.reqMktData(und, "", False, False); ib.sleep(3)
        spot = tu.last if tu.last == tu.last and tu.last else tu.close
        print(f"{SOTTOSTANTE} ora {spot} (proposta {SPOT_PROPOSTA})")
        if not spot or abs(spot / SPOT_PROPOSTA - 1) > TOLLERANZA_SPOT:
            print("ABORT: sottostante oltre la tolleranza o senza prezzo: la proposta e' scaduta"); return 1
        # 1. contratti
        legs, ticks = [], []
        for k, r, ratio, act in GAMBE:
            q = ib.reqContractDetails(Option(SOTTOSTANTE, SCADENZA, k, r, BORSA_OPZIONI, currency=VALUTA))
            cs = [d.contract for d in q if d.contract.tradingClass == CLASSE_NEGOZIAZIONE
                  and d.contract.lastTradeDateOrContractMonth == SCADENZA]
            if len(cs) != 1:
                print(f"ABORT: contratto {k}{r} non univoco ({len(cs)})"); return 1
            c = cs[0]
            assert (c.right, c.strike) == (r, k) and float(c.multiplier) == MOLTIPLICATORE, "contratto diverso da quello atteso"
            legs.append((c, ratio, act)); ticks.append(ib.reqMktData(c, "101", False, False))
        ib.sleep(6)
        # 3. credito/debito eseguibile
        netto = 0.0                       # >0 credito, <0 debito
        for (c, ratio, act), t in zip(legs, ticks):
            oi = t.callOpenInterest if c.right == "C" else t.putOpenInterest
            print(f"  {act:4s} x{ratio} {c.strike:g}{c.right}  bid {t.bid} ask {t.ask}  OI {oi}")
            if not (t.bid and t.ask and t.bid > 0 and t.ask > 0):
                print("ABORT: quotazione assente su una gamba"); return 1
            netto += (t.bid if act == "SELL" else -t.ask) * ratio * MOLTIPLICATORE
        print(f"Netto eseguibile (bid/ask) = {netto:+.0f} {VALUTA} (credito positivo); limite {LATO} {LIMITE:.0f}")
        bag = Contract(symbol=SOTTOSTANTE, secType="BAG", currency=VALUTA, exchange=BORSA_OPZIONI)
        bag.comboLegs = [ComboLeg(conId=c.conId, ratio=ratio, action=act, exchange=BORSA_OPZIONI) for c, ratio, act in legs]
        # Pacchetto "nel verso dell'incasso": ordine BUY con prezzo NEGATIVO per un credito, POSITIVO per un debito
        prezzo = (-LIMITE if LATO == "credito" else LIMITE) / MOLTIPLICATORE
        o = LimitOrder("BUY", QUANTITA, round(prezzo, 2), tif="DAY", transmit=True)
        # 4. margine
        w = ib.whatIfOrder(bag, o)
        init_ch = float(w.initMarginChange or 0)
        print(f"whatIf -> margine iniziale {init_ch:+.0f}, commissione {w.commission}")
        if init_ch > MARGINE_MAX:
            print("ABORT: margine oltre la soglia, struttura non conforme a quella attesa"); return 1
        print(f"ORDINE: pacchetto x{QUANTITA} @ {LATO} {LIMITE:.0f} {VALUTA} (LMT {o.lmtPrice}) DAY")
        raggiungibile = netto >= LIMITE - 0.5 if LATO == "credito" else -netto <= LIMITE + 0.5
        if not raggiungibile:
            print("NOTA: il limite non e' raggiungibile ai prezzi di adesso: l'ordine resterebbe in attesa nello spread.")
        # 5. conferma
        if not a.confirm:
            print("--confirm assente: NIENTE E' STATO TRASMESSO."); return 0
        tr = ib.placeOrder(bag, o); print(f"Trasmesso. orderId {tr.order.orderId}")
        for _ in range(20):
            ib.sleep(3); print(f"  stato: {tr.orderStatus.status} riempiti {tr.orderStatus.filled}/{QUANTITA} medio {tr.orderStatus.avgFillPrice}")
            if tr.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
                break
        print(f"ESITO: {tr.orderStatus.status}, prezzo medio {tr.orderStatus.avgFillPrice}")
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
