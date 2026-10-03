"""Azioni migliorative su ogni posizione aperta (SOLA LETTURA, nessun ordine).

A ogni giro della dashboard ogni gruppo di `portfolio_map.json` viene passato agli
"esaminatori" qui sotto, uno per regola del Piano Finale. Ognuno risponde con zero o
piu' azioni, e ogni azione porta con se' la situazione PRIMA e DOPO l'aggiustamento
(massima perdita, massimo profitto, cassa, pareggio) piu' l'ordine esatto da inserire.

Le regole esaminate:
  1. calendar  -> trasformazione DC Time Machine (delegata a transform.py)
  2. strutture a debito -> trasformazione senza rischio alzando la gamba lunga
     (Piano Finale, riga aggiunta il 30/09/2026)
  3. spread a credito   -> presa di profitto al 50% del credito
  4. qualunque corta    -> regola dei 21 DTE (chiudere o rollare)
  5. ZEBRA              -> reset quando il delta delle lunghe scende verso 0,50-0,60

NON sono previsti stop loss: regola permanente dell'utente. Qui dentro
non esiste nessuna azione innescata da un livello di prezzo avverso.

Nota sui prezzi: si valuta sempre a prezzi ESEGUIBILI (si vende al denaro, si compra
alla lettera), mai a meta' spread. Il mid viene riportato a parte come scenario migliore.
"""
from __future__ import annotations

import datetime as dt
import json

from ib_async import Option

import deals
from ib_common import DATA, clean

# urgenze, in ordine di evidenza sulla pagina
PRONTA, VICINA, INFO = "pronta", "vicina", "info"

# indici a regolamento in contanti e di stile europeo: niente esercizio anticipato.
# Tutto il resto (azioni USA) e' americano e si puo' vedere assegnato in qualunque momento.
INDICI = ("ESTX50", "SPX", "XSP", "VIX", "RUT", "NDX", "DJX")

# passi di strike da provare per la gamba lunga da alzare, dal piu' largo al piu' fitto
PASSI = (10, 5, 2.5, 1)
MAX_CANDIDATI = 6


# ---------------------------------------------------------------- utilita'

def _d(e):
    return dt.datetime.strptime(e, "%Y%m%d").date()


def _dte(e):
    return (_d(e) - dt.date.today()).days


def _opt_legs(g):
    return [l for l in g["legs"] if l["secType"] == "OPT"]


def _cassa(legs):
    """Cassa netta del gruppo: positiva = debito pagato, negativa = credito incassato.
    avgCost di IBKR e' per contratto e include gia' il moltiplicatore."""
    return sum(l["position"] * l["avgCost"] for l in legs)


def _estremi(legs):
    """(max profitto, max perdita, stimato) con la stessa funzione usata dalla tabella."""
    try:
        return deals.extremes(legs)
    except Exception:
        return "n/d", "n/d", False


def _pareggio(legs):
    try:
        res = deals.breakevens(legs)
    except Exception:
        return None
    for sym, bes, d in res:
        if bes:
            return bes[0] if len(bes) == 1 else (bes[0], bes[-1])
    return None


def _iv_rif(legs):
    """Volatilita' di riferimento di una posizione: quella della gamba con lo strike piu' vicino
    al prezzo. Si calcola UNA VOLTA sulle gambe in essere e si passa a tutte le varianti, cosi'
    il confronto fra alternative e' fatto con lo stesso metro."""
    S = next((l.get("und") for l in legs if l.get("und")), None)
    con_iv = [l for l in legs if l.get("iv")]
    if not S or not con_iv:
        return None
    return min(con_iv, key=lambda l: abs(l["strike"] - S))["iv"]


def _foto(legs, offset=0.0, iv=None):
    """Riepilogo di una struttura: cassa, estremi, pareggio. `offset` e' l'utile gia'
    realizzato chiudendo delle gambe, che va sommato a entrambi gli estremi.

    Il pareggio va spostato ANCHE LUI dall'offset, non solo gli estremi: una perdita gia'
    realizzata chiudendo la vecchia gamba e' denaro uscito che il titolo deve recuperare.
    Senza questa correzione il pareggio risultava piu' basso del vero e la manovra sembrava
    piu' conveniente di quanto sia (trovato confrontando la colonna della
    dashboard con il conto fatto a mano su un caso reale: 242,72 contro 244,66)."""
    mx, mn, est = _estremi(legs)
    add = lambda v: v if isinstance(v, str) or v is None else v + offset
    # il pareggio si cerca dove il P/L totale e' zero: si carica l'offset sul costo di
    # una gamba, cosi la base di costo complessiva e' quella vera
    per_pareggio = [dict(l) for l in legs]
    if offset and per_pareggio:
        prima = next((l for l in per_pareggio if l["position"]), None)
        if prima is not None:
            prima["avgCost"] = prima["avgCost"] - offset / prima["position"]
    # prob = con la volatilita implicita (prudente per chi vende premio)
    # ev   = con la volatilita realizzata ATTESA, perche' alla implicita sarebbe zero per
    #        costruzione e non distinguerebbe un'alternativa dall'altra
    try:
        _, prob = deals.misure(per_pareggio, iv=iv)
    except Exception:
        prob = None
    try:
        base = iv or _iv_rif(per_pareggio)
        vol, fonte = _vol_attesa(per_pareggio, base)
        ev, _ = deals.misure(per_pareggio, iv=base, vol=vol) if vol else (None, None)
    except Exception:
        ev, fonte = None, None
    rr = None
    if isinstance(mx, (int, float)) and isinstance(mn, (int, float)) and mn < 0:
        rr = (mx + offset) / abs(mn + offset) if (mn + offset) < 0 else None
    return dict(cassa=_cassa(legs) - offset, max_profitto=add(mx), max_perdita=add(mn),
                pareggio=_pareggio(per_pareggio), prob=prob, ev=ev, ev_fonte=fonte,
                rr=rr, stimato=est)


def _quote(ib, contracts, wait=5, greche=False):
    """Una sola attesa per tutti i contratti: le richieste si accavallano, non si sommano."""
    # lo stesso contratto puo' comparire in piu' varianti (lo strike lungo di una e' il corto
    # di un'altra): chiederlo due volte fa fallire la cancellazione della seconda
    visti, unici = set(), []
    for c in contracts:
        if c.conId not in visti:
            visti.add(c.conId)
            unici.append(c)
    contracts = unici
    if not contracts:
        return {}
    ts = []
    for c in contracts:
        ts.append((c, ib.reqMktData(c, "", False, False)))
    ib.sleep(wait)
    out = {}
    for c, t in ts:
        b, a = clean(t.bid), clean(t.ask)
        ok = b is not None and a is not None and a > 0 and b >= 0
        g = t.modelGreeks if greche else None
        out[c.conId] = dict(bid=b if ok else None, ask=a if ok else None,
                            mid=(b + a) / 2 if ok else None,
                            delta=g.delta if g else None, iv=g.impliedVol if g else None)
        ib.cancelMktData(c)
    return out


_STRIKES = {}


def _catena(ib, modello, mult, currency):
    """Contratti realmente quotati su quella scadenza e quel tipo, chiesti una volta sola.

    Si usa reqContractDetails con lo strike lasciato a zero (carattere jolly): torna la lista
    esatta. reqSecDefOptParams invece restituisce l'UNIONE degli strike di tutte le scadenze,
    e su una scadenza lontana come Mar'27 la maggior parte di quegli strike non esiste: si
    finiva per interrogarne una decina inesistenti, un errore per ognuno nei log."""
    sym, exp, right = modello["symbol"], modello["expiry"], modello["right"]
    if (sym, exp, right) in _STRIKES:
        return _STRIKES[(sym, exp, right)]
    out = []
    try:
        c = Option(sym, exp, 0, right, "SMART" if currency != "EUR" else "EUREX",
                   multiplier=str(int(mult)) if mult == int(mult) else str(mult), currency=currency)
        out = sorted((cd.contract for cd in ib.reqContractDetails(c)), key=lambda x: x.strike)
    except Exception:
        out = []
    # un risultato vuoto NON si mette in cache: durante un calo di connessione IBKR la
    # richiesta torna vuota, e memorizzarla farebbe saltare tutti i controlli successivi
    # dello stesso giro anche dopo che la linea e' tornata (visto il 01/10/2026)
    if out:
        _STRIKES[(sym, exp, right)] = out
    return out


def _candidati(ib, modello, k_da, k_a, mult, currency):
    """Contratti quotati agli strike fra k_da e k_a (estremi esclusi). Si tengono quelli piu'
    vicini alla corta: e' li' che il credito della roll-up e' piu' alto."""
    cands = [c for c in _catena(ib, modello, mult, currency) if k_da < c.strike < k_a]
    return cands[-MAX_CANDIDATI:] if len(cands) >= 2 else []


_SCAD = {}


def _sottostante(sym, currency):
    from ib_async import Index, Stock
    if sym in INDICI:
        return Index(sym, "EUREX" if sym == "ESTX50" else "CBOE")
    return Stock(sym, "SMART", currency)


def _mensile(e):
    """Scadenza mensile standard: terzo venerdi' del mese. Sulle weekly ESTX50 l'open interest
    e' strutturalmente misero (1-40 contratti contro migliaia): scoperto il 18/09/2026, e per
    questo un roll va proposto prima sulle mensili."""
    d = _d(e)
    return d.weekday() == 4 and 15 <= d.day <= 21


def _scadenze(ib, sym, currency, dopo_di):
    """Scadenze disponibili successive a `dopo_di`, mensili per prime."""
    if (sym, currency) not in _SCAD:
        exps = set()
        try:
            und = _sottostante(sym, currency)
            ib.qualifyContracts(und)
            for ch in ib.reqSecDefOptParams(sym, "", und.secType, und.conId):
                exps |= set(ch.expirations)
        except Exception:
            exps = set()
        _SCAD[(sym, currency)] = sorted(exps)
    dopo = [e for e in _SCAD[(sym, currency)] if e > dopo_di]
    mensili = [e for e in dopo if _mensile(e)]
    return (mensili[:2] or dopo[:2])


# soglie di delta sulla gamba corta come innesco di aggiustamento (metodo Option Alpha,
# sintesi in Risorse/Aggiustamenti - competenze dai video). Entrando a 15 delta, 30 significa
# che la probabilita' di perdere su quel lato e' RADDOPPIATA. Il delta e' preferibile alla
# distanza percentuale perche' tiene dentro volatilita' e tempo residuo, e si inasprisce da
# solo avvicinandosi la scadenza.
DELTA_ALLERTA, DELTA_AZIONE = 0.30, 0.40

# RIPIEGO: frazione dell'implicita da usare come volatilita' attesa SOLO quando non si ha la
# volatilita' realmente realizzata dal sottostante. Dove c'e' la misura, si usa quella.
# Il 02/10/2026 la misura ha smentito questa stima su ESTX50: realizzata 13,9% contro implicita
# 19,9%, cioe' un rapporto di 0,70 e non 0,85, e con 0,85 il valore atteso dei bull put usciva
# negativo quando con la volatilita' vera e' positivo.
# Serve perche' CON la volatilita implicita il valore atteso e' zero per costruzione: e'
# esattamente cio' che il mercato prezza. Il vantaggio del venditore di premio, se esiste, sta
# nella differenza fra implicita e realizzata (Risorse/Come si calcola se un trade ha edge).
# Ancoraggio storico dal vault: su SPX mensile IV media circa 19,8% contro RV circa 16,3%,
# cioe' un rapporto di circa 0,82. Qui si usa 0,85, leggermente piu' prudente.
# E' UN'IPOTESI, non una legge: cambiarla cambia la classifica delle alternative.
FATTORE_RV = 0.85

# Volatilita' realizzata misurata, per sottostante. La scrive scan_vrp.py (che gira con
# yfinance); actions.py la legge soltanto, perche' la dashboard non ha yfinance fra le
# dipendenze e non deve dipendere dalla rete a ogni giro.
_RV_FILE = DATA / "rv_storiche.json"
_RV_CACHE = {}


def _rv_reale(sym):
    """Volatilita' realizzata dal sottostante, se misurata di recente. None altrimenti."""
    if not _RV_CACHE:
        try:
            d = json.loads(_RV_FILE.read_text())
            agg = dt.date.fromisoformat(d.get("aggiornato", "1970-01-01"))
            if (dt.date.today() - agg).days <= 7:
                _RV_CACHE.update(d.get("rv", {}))
            _RV_CACHE["_letto"] = True
        except Exception:
            _RV_CACHE["_letto"] = True
    v = _RV_CACHE.get(sym)
    return float(v) if isinstance(v, (int, float)) else None


def _vol_attesa(legs, iv):
    """(volatilita' da usare per il valore atteso, da dove viene)."""
    sym = legs[0]["symbol"] if legs else None
    r = _rv_reale(sym) if sym else None
    if r:
        return r, "misurata"
    return (iv * FATTORE_RV, "stimata") if iv else (None, None)


def _trigger_delta(corta):
    """(delta assoluto della corta, etichetta del trigger) oppure (None, '')."""
    d = corta.get("delta")
    if d is None:
        return None, ""
    d = abs(d)
    if d >= DELTA_AZIONE:
        return d, f"corta a {d:.2f} delta: oltre la soglia 40, il mercato si e' mosso molto contro"
    if d >= DELTA_ALLERTA:
        return d, (f"corta a {d:.2f} delta: superata la soglia 30, cioe' la probabilita' di perdere "
                   "su questo lato e' raddoppiata rispetto all'ingresso")
    return d, (f"corta a {d:.2f} delta, sotto la soglia 30: **non e' il delta a far comparire "
               "questa azione**, ma la scadenza vicina. Il mercato non si e' ancora mosso "
               "abbastanza contro di te per giustificare un aggiustamento difensivo")


def _regole(credito=None, size_invariata=None, lato_perdente=None, tempo_esteso=None):
    """Le quattro regole di aggiustamento del metodo, verificate sulla singola proposta.

    1 non muovere il lato in perdita  2 sempre un credito netto
    3 mai aumentare la dimensione     4 estendere la scadenza quando possibile
    Ogni voce e' (testo, esito) con esito in 'si' / 'no' / 'na'. Serve a far vedere subito se
    una proposta rispetta il metodo o se e' una deroga consapevole."""
    v = []
    if lato_perdente is not None:
        v.append(("Non muove il lato in perdita", "si" if lato_perdente else "no"))
    if credito is not None:
        v.append(("Si fa a credito netto", "si" if credito else "no"))
    if size_invariata is not None:
        v.append(("Dimensione invariata", "si" if size_invariata else "no"))
    if tempo_esteso is not None:
        v.append(("Estende la scadenza", "si" if tempo_esteso else "na"))
    return v


def _cuscinetto(corta, spot):
    """Quanto spazio resta prima che la corta vada dentro il prezzo, in frazione dello strike.

    Positivo = fuori dal prezzo (sicura), NEGATIVO = gia' dentro il prezzo (nei guai). Va
    misurato con il segno: per una put venduta il sottostante 6% SOPRA lo strike e 6% SOTTO
    sono situazioni opposte, e una distanza in valore assoluto le confonde."""
    if spot is None:
        return None
    return ((spot - corta["strike"]) if corta["right"] == "P" else (corta["strike"] - spot)) / corta["strike"]


def _al_prezzo(contratti, q, verso):
    """Prezzo eseguibile di una gamba: si vende al denaro, si compra alla lettera."""
    px = q.get(contratti.conId) or {}
    return px.get("bid") if verso < 0 else px.get("ask")


# ---------------------------------------------------------------- esaminatori

def _da_transform(groups, transf_res):
    """Riusa il risultato di transform.py e lo attacca al gruppo giusto della mappa."""
    out = []
    for r in transf_res or []:
        chiavi = set()
        for c in r["group"]:
            for k in ("short", "long"):
                ct = c[k].contract
                chiavi.add((ct.symbol, ct.lastTradeDateOrContractMonth, ct.strike, ct.right))
        gid = None
        for g_id, g in groups.items():
            if chiavi <= {(l["symbol"], l["expiry"], l["strike"], l["right"]) for l in g["legs"]}:
                gid = g_id
                break
        if gid is None:
            continue
        g = groups[gid]
        righe = [x for x in r["rows"] if x.get("mid") is not None]
        if not righe:
            continue
        # ESEGUIBILI = si incassa il credito necessario ai prezzi denaro/lettera di adesso.
        # Quelle che funzionano solo a meta' spread NON sono pronte: e' la differenza che
        # faceva scattare avvisi su trasformazioni che poi al momento di mandare l'ordine
        # non si riempivano (segnalato dall'utente il 01/10/2026).
        eseguibili = [x for x in righe if x["ok_nat"]]
        al_mid = [x for x in righe if x["ok_mid"] and not x["ok_nat"]]
        # fra le ali possibili si prende la piu' larga: stesso pavimento, tetto piu' alto
        b = (max(eseguibili, key=lambda x: x["w"]) if eseguibili else
             max(al_mid, key=lambda x: x["w"]) if al_mid else
             min(righe, key=lambda x: x["gap"]))
        cur = r["cur"]
        qty, mult = r["qty"], r["mult"]
        ivr = _iv_rif(_opt_legs(g))
        prima = _foto(_opt_legs(g), iv=ivr)
        ordine = []
        add_legs = []
        # prezzi eseguibili: la lunga si vende al denaro, l'ala si compra alla lettera
        lq = r.get("longs_q") or []
        wq = b.get("wings_q") or []
        # si ricostruisce anche la struttura RISULTANTE, per poterne calcolare pareggio e
        # probabilita' con lo stesso motore di tutte le altre azioni
        und = next((l.get("und") for l in _opt_legs(g) if l.get("und")), None)
        nuove, realizzato = [], 0.0
        for n, (c, wing) in enumerate(zip(r["group"], b.get("wings", []))):
            S_, L_ = c["short"], c["long"]
            sc, L = S_.contract, L_.contract
            ordine.append(f"VENDI {qty:g} {L.right} {L.strike:g} {L.lastTradeDateOrContractMonth}")
            ordine.append(f"COMPRA {qty:g} {wing.right} {wing.strike:g} {wing.lastTradeDateOrContractMonth}")
            lbid = (lq[n] or {}).get("bid") if n < len(lq) else None
            wask = (wq[n] or {}).get("ask") if n < len(wq) else None
            add_legs.append(dict(expiry=L.lastTradeDateOrContractMonth, strike=L.strike,
                                 right=L.right, qty=-qty, mult=mult, price=lbid))
            add_legs.append(dict(expiry=wing.lastTradeDateOrContractMonth, strike=wing.strike,
                                 right=wing.right, qty=qty, mult=mult, price=wask))
            iv_s = next((l.get("iv") for l in _opt_legs(g)
                         if l["strike"] == sc.strike and l["right"] == sc.right
                         and l["expiry"] == sc.lastTradeDateOrContractMonth), None)
            if nuove is None or lbid is None or wask is None:
                nuove = None
                continue
            comune = dict(symbol=sc.symbol, secType="OPT", mult=mult, currency=cur,
                          iv=iv_s, und=und)
            nuove.append(dict(comune, expiry=sc.lastTradeDateOrContractMonth, strike=sc.strike,
                              right=sc.right, position=S_.position, avgCost=S_.averageCost))
            nuove.append(dict(comune, expiry=wing.lastTradeDateOrContractMonth, strike=wing.strike,
                              right=wing.right, position=qty, avgCost=wask * mult))
            realizzato += L_.position * (lbid * mult - L_.averageCost)
        pronta, solo_mid = bool(eseguibili), bool(al_mid) and not eseguibili
        if pronta:
            titolo = f"Trasformabile ORA senza rischio (ali da {b['w']:g})"
            perche = ("Vendendo la gamba lunga e comprando l'ala sulla scadenza corta il calendar diventa "
                      "uno spread verticale. Il credito incassato copre l'intera larghezza dell'ala, quindi "
                      "da quel momento il caso peggiore e' positivo: la posizione non puo' piu' perdere. "
                      "Il credito necessario si incassa ai prezzi denaro/lettera di adesso.")
        elif solo_mid:
            titolo = f"Trasformabile solo a meta' spread (ali da {b['w']:g})"
            perche = ("Il credito basta al prezzo medio, ma non ai prezzi eseguibili di adesso: mettendo "
                      "l'ordine al limite necessario resteresti in coda senza riempimento. Si guarda, non "
                      "si manda. Diventa eseguibile quando lo spread denaro-lettera si stringe o quando il "
                      "calendar guadagna ancora un po'.")
        else:
            titolo = f"Trasformazione senza rischio (ali da {b['w']:g})"
            perche = (f"Mancano {b['gap']:,.0f} {cur} di credito perche' il caso peggiore arrivi a zero. "
                      "Quello che la muove e' il rapporto fra la volatilita implicita della corta e quella "
                      "della lunga che rientra, non il prezzo del sottostante.")
        note = []
        # commissioni di un combo a due gambe su un contratto: circa 1,30-2,00. La soglia era
        # 25 e faceva scattare l'avviso anche su un pavimento di 21, che invece le copre dieci
        # volte: alzava un allarme falso proprio sul caso buono (corretto il 01/10/2026)
        if (pronta or solo_mid) and b["floor"] < 10 * r["qty"]:
            note.append("Il pavimento e' dello stesso ordine delle commissioni di un ordine a due "
                        "gambe: a costi veri questa trasformazione non e' davvero senza rischio.")
        if (pronta or solo_mid) and r["close_now"] is not None and r["close_now"] > b["floor"]:
            note.append(f"Chiudendo e basta incasseresti {r['close_now']:+,.0f} {cur} certi, piu' del "
                        f"pavimento garantito dalla trasformazione ({b['floor']:+,.0f} {cur}): "
                        "trasformare conviene solo se credi nello scenario migliore.")
        out.append(dict(
            gid=gid, nome=g["nome"], kind="trasforma_calendar", cur=cur,
            titolo=titolo, stato=PRONTA if pronta else VICINA, perche=perche,
            ordine=" + ".join(ordine) + (f", limite: credito >= {b['need']:.2f}" if b.get("need") else ""),
            prima=prima,
            # dopo la trasformazione la cassa si rovescia: hai pagato il debito e incassato il
            # credito, quindi in tasca resta (credito - debito) = il tetto della struttura nuova.
            # Pavimento e tetto restano quelli di transform.py (sono la verita' di riferimento);
            # pareggio e probabilita' arrivano dalla struttura risultante ricostruita qui.
            dopo=dict(_foto(nuove, offset=realizzato, iv=ivr) if nuove else
                      dict(pareggio=None, prob=None, stimato=False),
                      cassa=-b["top"], max_perdita=b["floor"], max_profitto=b["top"]),
            chiudendo_ora=r["close_now"], add_legs=add_legs, nota=" ".join(note),
        ))
    return out


def _alza_la_lunga(ib, groups):
    """Strutture a debito (verticale a debito, ZEBRA): si alza la gamba lunga finche' il
    credito incassato copre il debito pagato. Piano Finale, regola del 30/09/2026."""
    azioni = []
    for gid, g in groups.items():
        legs = _opt_legs(g)
        if len(legs) < 2 or len({l["symbol"] for l in legs}) > 1 or len({l["expiry"] for l in legs}) > 1:
            continue
        lunghe = [l for l in legs if l["position"] > 0]
        corte = [l for l in legs if l["position"] < 0]
        if not lunghe or not corte or len({l["right"] for l in legs}) > 1:
            continue
        debito = _cassa(legs)
        if debito <= 0:                       # a credito: non e' questa la regola
            continue
        if len({l["strike"] for l in lunghe}) > 1:
            continue
        lunga = lunghe[0]
        k_corta = min(l["strike"] for l in corte)
        if lunga["right"] != "C" or lunga["strike"] >= k_corta:
            continue
        if _dte(lunga["expiry"]) < 21:        # troppo tardi per rimaneggiare
            continue
        qty = lunga["position"]
        mult = lunga.get("mult") or 100
        ivr = _iv_rif(legs)
        cands = _candidati(ib, lunga, lunga["strike"], k_corta, mult, lunga["currency"])
        if not cands:
            continue
        q = _quote(ib, cands)
        bid_vecchia = lunga.get("bid")
        if bid_vecchia is None:
            continue
        righe = []
        for c in cands:
            px = q.get(c.conId) or {}
            if px.get("ask") is None:
                continue
            # si vende la lunga al denaro e si ricompra piu' in alto alla lettera
            credito = (bid_vecchia - px["ask"]) * mult * qty
            credito_mid = (bid_vecchia - px["mid"]) * mult * qty if px.get("mid") else None
            realizzato = (bid_vecchia * mult - lunga["avgCost"]) * qty
            nuove = [dict(l) for l in corte] + [dict(lunga, strike=c.strike,
                                                     avgCost=px["ask"] * mult,
                                                     iv=lunga.get("iv"), und=lunga.get("und"))]
            dopo = _foto(nuove, offset=realizzato, iv=ivr)
            righe.append(dict(strike=c.strike, credito=credito, credito_mid=credito_mid,
                              dopo=dopo, ask=px["ask"]))
        if not righe:
            continue
        num = lambda v: v if isinstance(v, (int, float)) else None
        # senza rischio = la peggiore delle ipotesi non e' piu' negativa
        ok = [r for r in righe if (num(r["dopo"]["max_perdita"]) or -1) >= 0]
        # Una roll-up che porta il PROFITTO massimo sotto zero perde in ogni scenario: alzando
        # troppo la lunga lo spread diventa piu' stretto di quanto hai sborsato. Va tolta dalle
        # candidate, altrimenti il criterio "pavimento piu' alto" la sceglie proprio perche'
        # perde poco (caso reale: alzare la lunga di troppo dava peggiore -2.143 e
        # MIGLIORE -143, cioe' una perdita garantita, e veniva proposta come la piu' conveniente).
        sensate = [r for r in righe if (num(r["dopo"]["max_profitto"]) or -1) > 0]
        # fra quelle senza rischio si tiene la piu' alta (meno rialzo regalato),
        # altrimenti la piu' vicina all'obiettivo fra quelle che possono ancora guadagnare
        # "piccoli aggiustamenti incrementali": fra le varianti senza rischio si prende la
        # PIU' BASSA, cioe' il minimo che raggiunge l'obiettivo, non la piu' alta. Alzare piu'
        # del necessario regala rialzo e stringe la struttura fino a togliergli spazio per
        # guadagnare (metodo Option Alpha; su un caso reale il criterio aggressivo
        # proponeva una variante che perdeva in ogni scenario)
        best = (min(ok, key=lambda r: r["strike"]) if ok else
                max(sensate, key=lambda r: num(r["dopo"]["max_perdita"]) or -1e18) if sensate else None)
        if best is None:
            continue
        prima = _foto(legs, iv=ivr)
        cur = lunga["currency"]
        manca = None
        if not ok and isinstance(best["dopo"]["max_perdita"], (int, float)):
            manca = -best["dopo"]["max_perdita"]
        azioni.append(dict(
            gid=gid, nome=g["nome"], kind="trasforma_debito", cur=cur,
            titolo=f"Trasformazione senza rischio: alza la lunga da {lunga['strike']:g} a {best['strike']:g}",
            stato=PRONTA if ok else VICINA,
            perche=(
                "Vendendo le lunghe basse e ricomprandone altrettante piu' in alto si incassa un credito. "
                "Quando quel credito copre il debito pagato all'apertura, il caso peggiore smette di essere "
                "negativo: da li' la posizione non puo' piu' perdere, qualunque cosa faccia il titolo. "
                "In cambio si rinuncia al rialzo fra il vecchio e il nuovo strike."
                if ok else
                f"Oggi la roll-up piu' conveniente lascia ancora {manca:,.0f} {cur} di perdita nel caso "
                f"peggiore. Serve che il titolo salga o che il valore temporale della lunga si rafforzi."
                if manca is not None else
                "Nessuna roll-up porta oggi il caso peggiore a zero."),
            ordine=(f"VENDI {qty:g} C {lunga['strike']:g} {lunga['expiry']} + "
                    f"COMPRA {qty:g} C {best['strike']:g} {lunga['expiry']}, "
                    f"limite: credito >= {(best['credito']/mult/qty):.2f} per unita'"),
            regole=_regole(credito=best["credito"] > 0, size_invariata=True, lato_perdente=True),
            prima=prima, dopo=best["dopo"], chiudendo_ora=None,
            add_legs=[dict(expiry=lunga["expiry"], strike=lunga["strike"], right="C", qty=-qty,
                           mult=mult, price=bid_vecchia),
                      dict(expiry=lunga["expiry"], strike=best["strike"], right="C", qty=qty,
                           mult=mult, price=best["ask"])],
            alternative=[dict(strike=r["strike"], credito=r["credito"],
                              max_perdita=r["dopo"]["max_perdita"], max_profitto=r["dopo"]["max_profitto"],
                              pareggio=r["dopo"].get("pareggio"), prob=r["dopo"].get("prob"),
                              ev=r["dopo"].get("ev"), rr=r["dopo"].get("rr"),
                              etichetta=(f"alza a {r['strike']:g}"
                                         + ("" if (num(r["dopo"]["max_profitto"]) or -1) > 0
                                            else " — perde in ogni caso")))
                         for r in sorted(righe, key=lambda r: r["strike"])],
            nota=("" if ok else
                  "Il pareggio si sposta in alto a ogni gradino: la perdita massima scende, ma il "
                  "titolo deve salire di piu' perche' la posizione guadagni. Guarda la colonna "
                  "Pareggio prima di decidere, non solo la perdita massima."),
        ))
    return azioni


def _spread_a_credito(g):
    """(corta, lunga, qty) se il gruppo e' uno spread verticale a credito, altrimenti None."""
    legs = _opt_legs(g)
    if len(legs) != 2 or len({l["symbol"] for l in legs}) > 1 or len({l["expiry"] for l in legs}) > 1:
        return None
    if len({l["right"] for l in legs}) > 1 or _cassa(legs) >= 0:
        return None
    corte = [l for l in legs if l["position"] < 0]
    lunghe = [l for l in legs if l["position"] > 0]
    if len(corte) != 1 or len(lunghe) != 1 or corte[0]["position"] != -lunghe[0]["position"]:
        return None
    return corte[0], lunghe[0], abs(corte[0]["position"])


def _roll_out(ib, groups):
    """Spread a credito: chiudere questa scadenza e riaprire sulla successiva, A CREDITO.

    Senza stop loss questa e' la difesa principale di un bucket A minacciato. La regola che
    conta e' una sola: un roll buono si fa a credito netto. Se per allontanarti devi pagare,
    stai rimandando il problema, non gestendolo (modulo 31 del vault)."""
    azioni = []
    for gid, g in groups.items():
        sp = _spread_a_credito(g)
        if not sp:
            continue
        corta, lunga, qty = sp
        mult = corta.get("mult") or 100
        dte = _dte(corta["expiry"])
        und = corta.get("und")
        # si guarda solo quando serve: sotto i 28 giorni, o con la corta minacciata.
        # Scandire tutte le scadenze a ogni giro costerebbe tempo per niente.
        cusc = _cuscinetto(corta, und)
        d_corta, trig = _trigger_delta(corta)
        # innesco: delta della corta oltre 30 (metodo Option Alpha) oppure cuscinetto sottile,
        # oppure la scadenza vicina che costringe comunque a decidere
        minacciata = (d_corta is not None and d_corta >= DELTA_ALLERTA) or (cusc is not None and cusc <= 0.03)
        if dte > 28 and not minacciata:
            continue
        if corta.get("ask") is None or lunga.get("bid") is None:
            continue
        ivr = _iv_rif(_opt_legs(g))
        larghezza = abs(corta["strike"] - lunga["strike"])
        passo = larghezza                      # si prova anche un roll piu' lontano di una larghezza
        varianti = []
        for e in _scadenze(ib, corta["symbol"], corta["currency"], corta["expiry"]):
            quotati = {c.strike: c for c in _catena(ib, dict(corta, expiry=e), mult, corta["currency"])}
            for giu in (0, passo):
                ks = corta["strike"] - giu if corta["right"] == "P" else corta["strike"] + giu
                kl = lunga["strike"] - giu if corta["right"] == "P" else lunga["strike"] + giu
                if ks in quotati and kl in quotati:
                    varianti.append(dict(exp=e, ks=ks, kl=kl, c_corta=quotati[ks], c_lunga=quotati[kl],
                                         giu=giu))
        if not varianti:
            continue
        q = _quote(ib, [c for v in varianti for c in (v["c_corta"], v["c_lunga"])])
        # costo di chiusura della posizione attuale: si ricompra la corta alla lettera,
        # si vende la lunga al denaro
        costo_chiusura = (corta["ask"] - lunga["bid"]) * mult * qty
        realizzato = (corta["position"] * (corta["ask"] * mult - corta["avgCost"])
                      + lunga["position"] * (lunga["bid"] * mult - lunga["avgCost"]))
        righe = []
        for v in varianti:
            b = _al_prezzo(v["c_corta"], q, -1)
            a = _al_prezzo(v["c_lunga"], q, +1)
            if b is None or a is None:
                continue
            credito_nuovo = (b - a) * mult * qty
            netto = credito_nuovo - costo_chiusura
            nuove = [dict(corta, expiry=v["exp"], strike=v["ks"], avgCost=b * mult),
                     dict(lunga, expiry=v["exp"], strike=v["kl"], avgCost=a * mult)]
            righe.append(dict(v, netto=netto, dopo=_foto(nuove, offset=realizzato, iv=ivr),
                              credito_nuovo=credito_nuovo, b=b, a=a))
        if not righe:
            continue
        a_credito = [r for r in righe if r["netto"] >= 0]
        # Il roll A STRIKE FERMI sulla scadenza successiva e' la Regola 4 del metodo (estendere
        # la scadenza a credito). Spostare gli strike nella direzione opposta al movimento
        # significa invece MUOVERE IL LATO IN PERDITA, che la Regola 1 vieta: realizza la
        # perdita e la ricompone piu' avanti. Quindi fra i roll a credito si preferisce quello
        # a strike fermi, e solo in sua assenza si mostra l'altro, marcato come deroga.
        fermi = [r for r in a_credito if not r["giu"]]
        best = (max(fermi, key=lambda r: r["netto"]) if fermi
                else max(a_credito, key=lambda r: r["netto"]) if a_credito
                else max(righe, key=lambda r: r["netto"]))
        cur = corta["currency"]
        verso = "piu' in basso" if corta["right"] == "P" else "piu' in alto"
        azioni.append(dict(
            gid=gid, nome=g["nome"], kind="roll_out", cur=cur,
            titolo=(f"Roll a credito su {_d(best['exp']).strftime('%d/%m/%Y')}"
                    + (f", {verso} di {best['giu']:g} punti" if best["giu"] else ", stessi strike")
                    + (f": +{best['netto']:,.0f} {cur}" if best["netto"] >= 0 else
                       f": costerebbe {-best['netto']:,.0f} {cur}")),
            stato=PRONTA if (a_credito and (dte <= 21 or minacciata)) else VICINA,
            perche=(
                "Chiudere questa scadenza e riaprire la stessa struttura piu' avanti incassando un credito "
                "netto: guadagni tempo e spazio senza mettere altro capitale a rischio. E' la difesa "
                "principale di uno spread a credito, ora che non ci sono stop loss. "
                + ((f"La corta e' gia' dentro il prezzo di {-cusc * 100:.1f}% ({corta['symbol']} a "
                    f"{und:,.2f}): e' il momento in cui questa manovra serve davvero."
                    if cusc < 0 else
                    f"Alla corta restano solo {cusc * 100:.1f}% di cuscinetto ({corta['symbol']} a "
                    f"{und:,.2f}): e' il momento in cui questa manovra serve davvero.")
                   if minacciata and und else
                   f"Mancano {dte} giorni alla scadenza: la regola dei 21 DTE ti costringera' comunque "
                   "a decidere, meglio sapere adesso quanto rende il roll.")
                if a_credito else
                f"Nessuna delle scadenze provate permette un roll a credito: la migliore costerebbe "
                f"{-best['netto']:,.0f} {cur}. **Non si rolla a debito**: pagare per allontanarsi "
                "significa rimandare il problema, non gestirlo. Meglio chiudere o lasciar correre."),
            ordine=(f"COMPRA {qty:g} {corta['right']} {corta['strike']:g} {corta['expiry']} + "
                    f"VENDI {qty:g} {lunga['right']} {lunga['strike']:g} {lunga['expiry']} + "
                    f"VENDI {qty:g} {corta['right']} {best['ks']:g} {best['exp']} + "
                    f"COMPRA {qty:g} {lunga['right']} {best['kl']:g} {best['exp']}, "
                    f"limite: credito netto >= {best['netto'] / mult / qty:.2f} per unita'"),
            # P/L vero chiudendo e basta = credito incassato all'apertura meno il costo di uscita.
            # Non il solo costo di uscita: quello da solo e' sempre negativo e non dice nulla.
            trigger=trig,
            regole=_regole(credito=best["netto"] >= 0, size_invariata=True,
                           lato_perdente=not best["giu"], tempo_esteso=True),
            prima=_foto(_opt_legs(g), iv=ivr), dopo=best["dopo"],
            chiudendo_ora=-_cassa(_opt_legs(g)) - costo_chiusura,
            add_legs=[dict(expiry=corta["expiry"], strike=corta["strike"], right=corta["right"],
                           qty=qty, mult=mult, price=corta["ask"]),
                      dict(expiry=lunga["expiry"], strike=lunga["strike"], right=lunga["right"],
                           qty=-qty, mult=mult, price=lunga["bid"]),
                      dict(expiry=best["exp"], strike=best["ks"], right=corta["right"],
                           qty=-qty, mult=mult, price=best["b"]),
                      dict(expiry=best["exp"], strike=best["kl"], right=lunga["right"],
                           qty=qty, mult=mult, price=best["a"])],
            alternative=[dict(strike=r["ks"], credito=r["netto"],
                              max_perdita=r["dopo"]["max_perdita"], max_profitto=r["dopo"]["max_profitto"],
                              pareggio=r["dopo"].get("pareggio"), prob=r["dopo"].get("prob"),
                              ev=r["dopo"].get("ev"), rr=r["dopo"].get("rr"),
                              etichetta=f"{_d(r['exp']).strftime('%d/%m')} strike {r['ks']:g}")
                         for r in sorted(righe, key=lambda r: (r["exp"], -r["ks"]))],
            nota=(("" if a_credito else "Roll a debito: il Piano non lo prevede. ")
                  + ("Questa variante sposta gli strike nella direzione opposta al movimento, "
                     "cioe' muove il lato in perdita: il metodo lo sconsiglia perche' realizza la "
                     "perdita e la ricompone piu' avanti. Il roll a strike fermi e' la manovra "
                     "conforme." if best["giu"] else "")),
        ))
    return azioni


def _difesa_laterale(ib, groups):
    """Spread a credito minacciato: vendere il verticale opposto sulla stessa scadenza, cioe'
    trasformarlo in iron condor.

    Incassa altro credito e abbassa la perdita massima sul lato che ti sta andando contro, ma
    **tappa il lato finora sicuro**: da li' in poi puoi perdere in due direzioni invece che in
    una. E' una scelta discrezionale, non un automatismo: si propone solo quando lo spread e'
    davvero sotto pressione, con il conto fatto, e decide l'utente."""
    azioni = []
    for gid, g in groups.items():
        sp = _spread_a_credito(g)
        if not sp:
            continue
        corta, lunga, qty = sp
        mult = corta.get("mult") or 100
        spot, dte = corta.get("und"), _dte(corta["expiry"])
        if spot is None or dte < 7:
            continue
        # questa manovra e' una DIFESA: ha senso solo se lo spread e' sotto pressione.
        # Proporla su una posizione tranquilla sarebbe solo rumore, e per di piu' un invito
        # a tappare il lato buono senza motivo.
        cusc = _cuscinetto(corta, spot)
        d_corta, trig = _trigger_delta(corta)
        minacciata = cusc <= 0.03 or (d_corta is not None and d_corta >= DELTA_ALLERTA)
        if cusc > 0.06 and not (d_corta is not None and d_corta >= DELTA_ALLERTA):
            continue
        altro = "C" if corta["right"] == "P" else "P"
        ivr = _iv_rif(_opt_legs(g))
        larghezza = abs(corta["strike"] - lunga["strike"])
        catena = _catena(ib, dict(corta, right=altro), mult, corta["currency"])
        per_strike = {c.strike: c for c in catena}
        # si guarda la fascia dalla parte opposta: le corte utili stanno a 10-25 delta
        if altro == "C":
            corte = [c for c in catena if c.strike > spot * 1.005][:10]
        else:
            corte = [c for c in catena if c.strike < spot * 0.995][-10:]
        coppie = [(c, per_strike.get(c.strike + larghezza if altro == "C" else c.strike - larghezza))
                  for c in corte]
        coppie = [(s, l) for s, l in coppie if l is not None][:8]
        if not coppie:
            continue
        q = _quote(ib, [x for p in coppie for x in p], greche=True)
        righe = []
        for s, l in coppie:
            qs, ql = q.get(s.conId) or {}, q.get(l.conId) or {}
            if qs.get("bid") is None or ql.get("ask") is None or qs.get("delta") is None:
                continue
            d = abs(qs["delta"])
            if not 0.08 <= d <= 0.28:
                continue
            credito = (qs["bid"] - ql["ask"]) * mult * qty
            if credito <= 0:
                continue
            nuove = [dict(l_) for l_ in _opt_legs(g)] + [
                dict(corta, right=altro, strike=s.strike, position=-qty, avgCost=qs["bid"] * mult,
                     iv=qs.get("iv") or corta.get("iv")),
                dict(lunga, right=altro, strike=l.strike, position=qty, avgCost=ql["ask"] * mult,
                     iv=ql.get("iv") or lunga.get("iv"))]
            righe.append(dict(ks=s.strike, kl=l.strike, delta=d, credito=credito,
                              dopo=_foto(nuove, iv=ivr), bid=qs["bid"], ask=ql["ask"]))
        if not righe:
            continue
        # si preferisce la corta vicina a 17 delta, come il core del Piano (15-25 delta)
        best = min(righe, key=lambda r: abs(r["delta"] - 0.17))
        prima = _foto(_opt_legs(g), iv=ivr)
        cur = corta["currency"]
        credito_attuale = -_cassa(_opt_legs(g))
        if best["credito"] < 0.20 * credito_attuale:
            continue                           # troppo poco per valere commissioni e tetto perso
        verso = "al rialzo" if altro == "C" else "al ribasso"
        azioni.append(dict(
            gid=gid, nome=g["nome"], kind="difesa_laterale", cur=cur,
            titolo=(f"Difesa laterale: vendi anche il {'call' if altro == 'C' else 'put'} spread "
                    f"{best['ks']:g}/{best['kl']:g} (+{best['credito']:,.0f} {cur})"),
            stato=VICINA if minacciata else INFO,
            perche=(
                f"Vendendo il verticale opposto sulla stessa scadenza lo spread diventa un iron condor. "
                f"Incassi altri {best['credito']:,.0f} {cur}, che **si sottraggono alla perdita massima** "
                f"del lato che ti sta andando contro: a scadenza solo uno dei due lati puo' perdere, mai "
                f"entrambi. La corta nuova e' a {best['delta']:.2f} di delta, dentro la fascia 15-25 che "
                f"il Piano usa per il core.\n\n"
                f"Il prezzo da pagare: finora {verso} eri tranquillo, da adesso non piu'. La perdita "
                f"massima scende, ma **la probabilita' di finire in perdita sale**, perche' puoi avere "
                f"torto in due direzioni invece che in una. Ha senso se pensi che il mercato resti in un "
                f"intervallo; non ha senso se pensi solo che il ribasso si fermera' e poi riparta forte."),
            ordine=(f"VENDI {qty:g} {altro} {best['ks']:g} {corta['expiry']} + "
                    f"COMPRA {qty:g} {altro} {best['kl']:g} {corta['expiry']}, "
                    f"limite: credito >= {(best['credito'] / mult / qty):.2f} per unita'"),
            trigger=trig,
            # vendere il verticale opposto a parita' di larghezza e numero di contratti non
            # aggiunge rischio (a scadenza un solo lato puo' essere dentro il prezzo) e non
            # tocca il lato in perdita: conforme a tutte e quattro le regole tranne il tempo
            regole=_regole(credito=True, size_invariata=True, lato_perdente=True, tempo_esteso=False),
            prima=prima, dopo=best["dopo"], chiudendo_ora=None,
            add_legs=[dict(expiry=corta["expiry"], strike=best["ks"], right=altro, qty=-qty,
                           mult=mult, price=best["bid"]),
                      dict(expiry=corta["expiry"], strike=best["kl"], right=altro, qty=qty,
                           mult=mult, price=best["ask"])],
            alternative=[dict(strike=r["ks"], credito=r["credito"],
                              max_perdita=r["dopo"]["max_perdita"], max_profitto=r["dopo"]["max_profitto"],
                              pareggio=r["dopo"].get("pareggio"), prob=r["dopo"].get("prob"),
                              ev=r["dopo"].get("ev"), rr=r["dopo"].get("rr"),
                              etichetta=f"corta {r['ks']:g} ({r['delta']:.2f} delta)")
                         for r in sorted(righe, key=lambda r: r["ks"])],
            nota="Scelta discrezionale, non una regola del Piano: il motore fa il conto, la decisione "
                 "resta tua. Da non fare se l'idea e' solo 'il ribasso si fermera'': in quel caso il "
                 "roll a credito e' la mossa giusta, non questa.",
        ))
    return azioni


def _assegnazione(ib, groups):
    """Esercizio anticipato sulle corte americane: il caso classico e' la call corta ITM
    con valore estrinseco minore del dividendo in arrivo. Li' esercitare conviene alla
    controparte e ti ritrovi corto di azioni dall'oggi al domani."""
    azioni = []
    div = {}
    for gid, g in groups.items():
        for l in _opt_legs(g):
            if l["position"] >= 0 or l["symbol"] in INDICI:
                continue
            und, px = l.get("und"), l.get("marketPrice")
            if und is None or px is None:
                continue
            itm = und > l["strike"] if l["right"] == "C" else und < l["strike"]
            if not itm:
                continue
            intrinseco = (und - l["strike"]) if l["right"] == "C" else (l["strike"] - und)
            estrinseco = px - max(intrinseco, 0)
            if l["symbol"] not in div:
                div[l["symbol"]] = _dividendo(ib, l["symbol"], l["currency"])
            d_imp, d_data = div[l["symbol"]]
            rischio, perche = None, ""
            if (l["right"] == "C" and d_imp and d_data and d_data <= _d(l["expiry"])
                    and estrinseco < d_imp):
                rischio = PRONTA
                perche = (f"Call corta {l['strike']:g} dentro il prezzo ({l['symbol']} a {und:,.2f}) con "
                          f"valore estrinseco di {estrinseco:,.2f} contro un dividendo di {d_imp:,.2f} "
                          f"con stacco il {d_data.strftime('%d/%m/%Y')}. Alla controparte conviene "
                          "esercitare prima dello stacco per incassare il dividendo: ti ritroveresti "
                          "corto di azioni e dovresti pagare tu il dividendo.")
            elif estrinseco <= 0.10:
                rischio = VICINA
                perche = (f"Corta {l['right']} {l['strike']:g} dentro il prezzo con valore estrinseco "
                          f"quasi nullo ({estrinseco:,.2f}). Quando l'estrinseco sparisce l'esercizio "
                          "anticipato non costa piu' niente a chi ce l'ha: l'assegnazione puo' arrivare "
                          "in qualunque momento, anche senza dividendo di mezzo.")
            if not rischio:
                continue
            azioni.append(dict(
                gid=gid, nome=g["nome"], kind="assegnazione", cur=l["currency"],
                titolo=f"Rischio di assegnazione anticipata sulla corta {l['right']} {l['strike']:g}",
                stato=rischio, perche=perche,
                ordine=(f"Ricomprare {abs(l['position']):g} {l['right']} {l['strike']:g} {l['expiry']} "
                        "(chiusura della sola corta), oppure rollarla piu' lontana. Decidere prima "
                        "dello stacco, non dopo."),
                prima=_foto(_opt_legs(g)), dopo=None, chiudendo_ora=None, add_legs=[],
                nota="Opzioni americane su azione: l'esercizio puo' arrivare in qualunque giorno, "
                     "non solo a scadenza. Gli indici del libro (ESTX50, XSP, SPX) sono europei "
                     "a regolamento in contanti e non hanno questo problema.",
            ))
    return azioni


def _dividendo(ib, sym, currency):
    """(importo, data di stacco) del prossimo dividendo, dal tick 456 di IBKR. Niente a memoria."""
    try:
        und = _sottostante(sym, currency)
        ib.qualifyContracts(und)
        t = ib.reqMktData(und, "456", False, False)
        ib.sleep(4)
        d = t.dividends
        ib.cancelMktData(und)
        if d and d.nextAmount and d.nextDate:
            return float(d.nextAmount), d.nextDate
    except Exception:
        pass
    return None, None


def _protezioni(ib, groups):
    """Le due regole gia' scritte nel Piano Finale sulle protezioni di coda, finora senza codice:
    monetizzare un terzo quando valgono 3x il costo, e rollarle a 30 giorni dalla scadenza."""
    azioni = []
    for gid, g in groups.items():
        if "protezion" not in g["nome"].lower():
            continue
        for l in _opt_legs(g):
            if l["position"] <= 0 or l.get("bid") is None:
                continue
            qty = l["position"]
            costo = l["avgCost"] * qty
            valore = l["bid"] * (l.get("mult") or 100) * qty
            cur, dte = l["currency"], _dte(l["expiry"])
            if costo > 0 and valore >= 3 * costo:
                azioni.append(dict(
                    gid=gid, nome=g["nome"], kind="monetizza_hedge", cur=cur,
                    titolo=f"Monetizzare un terzo: la protezione vale {valore / costo:.1f}x il costo",
                    stato=PRONTA,
                    perche=("Regola del Piano Finale: quando una protezione vale tre volte quello che "
                            "l'hai pagata, se ne vende un terzo. Incassi il profitto nel momento in cui "
                            "l'assicurazione e' cara, e resti coperto con il resto. Se non la monetizzi "
                            "mai, in un crash la vedi salire e poi tornare indietro senza aver preso nulla."),
                    ordine=f"VENDERE un terzo della posizione ({max(1, round(qty / 3)):g} su {qty:g}) "
                           f"{l['right']} {l['strike']:g} {l['expiry']}",
                    prima=_foto(_opt_legs(g)), dopo=None,
                    chiudendo_ora=valore - costo, add_legs=[], nota=""))
            if 0 <= dte <= 30:
                azioni.append(dict(
                    gid=gid, nome=g["nome"], kind="rinnova_hedge", cur=cur,
                    titolo=f"Rinnovare la protezione: mancano {dte} giorni alla scadenza",
                    stato=PRONTA,
                    perche=("Regola del Piano Finale: le protezioni si rollano a 30 giorni dalla scadenza. "
                            "Sotto quella soglia il valore temporale residuo evapora in fretta e la "
                            "copertura diventa cara in rapporto a quello che protegge. Il libro non deve "
                            "mai restare scoperto: si apre la nuova prima di chiudere la vecchia."),
                    ordine=f"Rollare {qty:g} {l['right']} {l['strike']:g} dal {l['expiry']} "
                           "alla scadenza successiva, strike ricalibrato sul livello attuale dell'indice.",
                    prima=_foto(_opt_legs(g)), dopo=None, chiudendo_ora=valore - costo,
                    add_legs=[], nota=""))
    return azioni


def _legging_out(groups):
    """Corta a pochi centesimi: ricomprarla libera margine, toglie il rischio di assegnazione
    e lascia la gamba lunga gratis, che diventa una protezione a costo zero."""
    out = []
    for gid, g in groups.items():
        sp = _spread_a_credito(g)
        if not sp:
            continue
        corta, lunga, qty = sp
        if corta.get("ask") is None or _dte(corta["expiry"]) < 5:
            continue
        credito = -_cassa(_opt_legs(g))
        mult = corta.get("mult") or 100
        costo = corta["ask"] * mult * qty
        if corta["ask"] > 0.10 and costo > 0.05 * credito:
            continue
        realizzato = corta["position"] * (corta["ask"] * mult - corta["avgCost"])
        resta = [dict(lunga)]
        out.append(dict(
            gid=gid, nome=g["nome"], kind="legging_out", cur=corta["currency"],
            titolo=f"Ricomprare la corta {corta['strike']:g} a {corta['ask']:.2f}: la lunga resta gratis",
            stato=PRONTA,
            perche=(f"La corta costa {costo:,.0f} {corta['currency']} per essere ricomprata, cioe' "
                    f"{costo / credito * 100:.0f}% del credito incassato: quasi tutto il premio e' gia' "
                    "tuo. Ricomprandola liberi il margine che lo spread blocca, togli ogni rischio di "
                    "assegnazione, e ti resta la gamba lunga **pagata dal credito gia' incassato**: una "
                    "protezione di coda a costo zero fino a scadenza, che in un crollo vale molto."),
            ordine=f"COMPRA {qty:g} {corta['right']} {corta['strike']:g} {corta['expiry']} "
                   "(chiusura della sola corta, la lunga resta aperta)",
            prima=_foto(_opt_legs(g), iv=_iv_rif(_opt_legs(g))),
            dopo=_foto(resta, offset=realizzato, iv=_iv_rif(_opt_legs(g))),
            chiudendo_ora=None,
            add_legs=[dict(expiry=corta["expiry"], strike=corta["strike"], right=corta["right"],
                           qty=qty, mult=mult, price=corta["ask"])],
            nota="", ))
    return out


def _presa_di_profitto(groups):
    """Spread a credito: si chiude al 50% del credito massimo (Piano, bucket A)."""
    out = []
    for gid, g in groups.items():
        legs = _opt_legs(g)
        if not legs or len({l["expiry"] for l in legs}) > 1:
            continue
        cassa = _cassa(legs)
        if cassa >= 0:                       # non e' a credito
            continue
        credito = -cassa
        ev, ok = 0.0, True
        for l in legs:
            px = l.get("bid") if l["position"] > 0 else l.get("ask")
            if px is None:
                ok = False
                break
            ev += px * (l.get("mult") or 100) * l["position"]
        if not ok:
            continue
        utile = ev - cassa                   # P/L chiudendo ora, a prezzi eseguibili
        quota = utile / credito if credito else 0
        if quota < 0.40:                     # sotto il 40% non vale nemmeno segnalarlo
            continue
        prima = _foto(legs)
        cur = legs[0]["currency"]
        # con piu' di un contratto non si e' obbligati a scegliere fra tutto e niente:
        # chiuderne meta' incassa la parte certa e lascia correre il resto
        qty = max(abs(l["position"]) for l in legs)
        parziale = ""
        if qty >= 2 and quota >= 0.50:
            meta = int(qty // 2)
            parziale = (f"Con {qty:g} contratti puoi anche chiuderne solo {meta:g}: incassi "
                        f"{utile * meta / qty:+,.0f} {cur} certi e lasci correre il resto, che resta "
                        f"soggetto alla regola dei 21 DTE.")
        out.append(dict(
            gid=gid, nome=g["nome"], kind="take_profit", cur=cur, parziale=parziale,
            titolo=f"Presa di profitto: {quota*100:.0f}% del credito gia' maturato",
            stato=PRONTA if quota >= 0.50 else VICINA,
            perche=("Il Piano chiude gli spread a credito al 50% del credito massimo: gli ultimi 50% "
                    "arrivano piu' lentamente e si corrono per intero i rischi di coda nel frattempo. "
                    f"Chiudendo adesso a prezzi eseguibili incassi {utile:+,.0f} {cur} su un credito "
                    f"massimo di {credito:,.0f} {cur}."
                    if quota >= 0.50 else
                    f"Siamo al {quota*100:.0f}%: la soglia del Piano e' il 50%. Manca poco."),
            ordine=" + ".join(
                f"{'VENDI' if l['position'] > 0 else 'COMPRA'} {abs(l['position']):g} {l['right']} "
                f"{l['strike']:g} {l['expiry']}" for l in legs) + " (chiusura)",
            prima=prima,
            dopo=dict(cassa=0.0, max_perdita=utile, max_profitto=utile, pareggio=None, stimato=False),
            chiudendo_ora=utile,
            add_legs=[dict(expiry=l["expiry"], strike=l["strike"], right=l["right"],
                           qty=-l["position"], mult=l.get("mult") or 100,
                           price=(l.get("bid") if l["position"] > 0 else l.get("ask"))) for l in legs],
            nota="",
        ))
    return out


def _ventuno_dte(groups):
    """Regola dei 21 giorni sulla gamba corta: si chiude o si rolla, mai si tiene fino in fondo."""
    out = []
    for gid, g in groups.items():
        legs = _opt_legs(g)
        corte = [l for l in legs if l["position"] < 0]
        if not corte:
            continue
        e = min(l["expiry"] for l in corte)
        d = _dte(e)
        if d > 28 or d < 0:
            continue
        cur = legs[0]["currency"]
        prima = _foto(legs)
        gamma_corte = ", ".join(f"{abs(l['position']):g} {l['right']} {l['strike']:g}"
                                for l in corte if l["expiry"] == e)
        out.append(dict(
            gid=gid, nome=g["nome"], kind="ventuno_dte", cur=cur,
            titolo=f"Regola dei 21 DTE: mancano {d} giorni sulla corta ({_d(e).strftime('%d/%m/%Y')})",
            stato=PRONTA if d <= 21 else VICINA,
            perche=("Sotto i 21 giorni il gamma della corta esplode: un movimento piccolo del sottostante "
                    "muove la posizione molto piu' di prima, e il premio rimasto non ripaga quel rischio. "
                    "Il Piano chiude o rolla, non porta a scadenza. Non e' uno stop: e' una regola di tempo."
                    if d <= 21 else
                    f"Fra {d - 21} giorn{'o' if d - 21 == 1 else 'i'} scatta la regola dei 21 DTE su "
                    "questa corta. Vale la pena decidere adesso se chiudere, rollare o lasciar correre, "
                    "non all'ultimo giorno."),
            ordine=f"Chiudere il gruppo oppure rollare in avanti le corte ({gamma_corte}) "
                   f"sulla scadenza successiva, a credito.",
            prima=prima, dopo=None, chiudendo_ora=None, add_legs=[], nota="",
        ))
    return out


def _reset_zebra(groups):
    """ZEBRA: si ricentra quando il delta delle lunghe scende verso 0,50-0,60."""
    out = []
    for gid, g in groups.items():
        legs = _opt_legs(g)
        if not legs or not deals.name(legs).startswith("ZEBRA"):
            continue
        lunghe = [l for l in legs if l["position"] > 0 and l.get("delta") is not None]
        if not lunghe:
            continue
        d_lunga = sum(l["delta"] for l in lunghe) / len(lunghe)
        if d_lunga > 0.62:
            continue
        e = min(l["expiry"] for l in legs)
        if _dte(e) < 21:
            continue
        prima = _foto(legs)
        out.append(dict(
            gid=gid, nome=g["nome"], kind="reset_zebra", cur=legs[0]["currency"],
            titolo=f"Reset ZEBRA: delta delle lunghe a {d_lunga:.2f}",
            stato=PRONTA if d_lunga <= 0.55 else VICINA,
            perche=("Lo ZEBRA replica 100 azioni finche' le lunghe stanno intorno a 0,70-0,75 di delta. "
                    "Sceso il delta, la struttura smette di seguire il titolo al rialzo pur continuando "
                    "a pagare valore temporale. Il reset chiude tutto e riapre centrato sul prezzo di oggi."),
            ordine="Chiudere lo ZEBRA e riaprirlo centrato sul prezzo attuale "
                   "(lunghe a delta 0,70-0,75, corta a delta circa 0,50, rapporto 2:1).",
            prima=prima, dopo=None, chiudendo_ora=None, add_legs=[], nota="",
        ))
    return out


# ---------------------------------------------------------------- ingresso

def check(ib, groups, transf_res=None):
    """Tutte le azioni migliorative trovate, le piu' urgenti per prime."""
    azioni = []
    for f, args in ((_da_transform, (groups, transf_res)), (_alza_la_lunga, (ib, groups)),
                    (_roll_out, (ib, groups)), (_difesa_laterale, (ib, groups)),
                    (_assegnazione, (ib, groups)),
                    (_protezioni, (ib, groups)), (_legging_out, (groups,)),
                    (_presa_di_profitto, (groups,)), (_ventuno_dte, (groups,)),
                    (_reset_zebra, (groups,))):
        try:
            azioni += f(*args)
        except Exception as e:
            azioni.append(dict(gid=None, nome="(controllo non riuscito)", kind="errore", cur="",
                               titolo=f"{f.__name__}: {e}", stato=INFO, perche="", ordine="",
                               prima=None, dopo=None, chiudendo_ora=None, add_legs=[], nota=""))
    # fra le alternative di una stessa azione si segnala quella con il VALORE ATTESO piu' alto:
    # e' l'unico numero che mette insieme probabilita' e dimensione dei guadagni e delle perdite.
    # Una probabilita' alta con un payoff sbilanciato puo' avere valore atteso peggiore di una
    # probabilita' bassa ben pagata, ed e' il motivo per cui non si classifica sulla sola
    # probabilita' ne' sul solo rapporto rendimento/rischio.
    for a in azioni:
        alt = [x for x in (a.get("alternative") or []) if x.get("ev") is not None]
        if len(alt) < 2:
            continue
        best = max(alt, key=lambda x: x["ev"])
        # NON FARE NULLA e' una delle opzioni, e va messa nella stessa classifica. Senza questo
        # confronto si finisce per etichettare "la migliore" il meno peggio di un insieme che e'
        # tutto peggiore dell'inazione: successo davvero il 02/10/2026 sul bull put ESTX50, dove
        # lasciare stare valeva -45 e il miglior roll -101.
        base = (a.get("prima") or {}).get("ev")
        if base is not None and base >= best["ev"]:
            a["consiglio"] = dict(etichetta="non fare nulla", ev=base, inazione=True,
                                  rr=(a.get("prima") or {}).get("rr"),
                                  prob=(a.get("prima") or {}).get("prob"),
                                  meglio_di=best)
        else:
            best["consigliata"] = True
            a["consiglio"] = best
    ordine = {PRONTA: 0, VICINA: 1, INFO: 2}
    azioni.sort(key=lambda a: (ordine.get(a["stato"], 3), a["nome"]))
    return azioni


if __name__ == "__main__":
    import transform
    from ib_common import connect
    import dashboard

    ib = connect("127.0.0.1", 7496, 177)
    try:
        ib.reqMarketDataType(1)
        groups, unmapped, *_ = dashboard.build_data(ib)
        _, _, t_res = transform.check(ib)
        az = check(ib, groups, t_res)
    finally:
        ib.disconnect()
    for a in az:
        print(f"[{a['stato'].upper()}] {a['nome']} - {a['titolo']}")
        if a.get("prima") and a.get("dopo"):
            p, d = a["prima"], a["dopo"]
            f = lambda v: "illimitato" if v is None else (v if isinstance(v, str) else f"{v:+,.0f}")
            print(f"    prima: perdita max {f(p['max_perdita'])}, profitto max {f(p['max_profitto'])}")
            print(f"    dopo : perdita max {f(d['max_perdita'])}, profitto max {f(d['max_profitto'])}")
        if a.get("ordine"):
            print(f"    ordine: {a['ordine']}")
    if not az:
        print("Nessuna azione migliorativa.")
