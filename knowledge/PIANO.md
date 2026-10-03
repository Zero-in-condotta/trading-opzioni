# Piano — Portafoglio Opzioni (versione unica, compattata il 03/10/2026)

> Piano di gestione di un portafoglio di opzioni su indici (numerazione §1-13). **Le regole sono indicazioni, non vincoli**: Claude ricorda cosa suggerisce il Piano e poi decide sulla situazione; fanno eccezione le regole dichiarate "permanenti" dall'utente (sotto). Non è consulenza finanziaria: i numeri sono stime, le opzioni possono perdere in fretta più di quanto dicano i modelli.

## Regole permanenti dell'utente (prevalgono su tutto)
- **Niente stop loss** (28/09/2026): le perdite si gestiscono aggiustando la posizione, mai con un livello di prezzo. Il **dimensionamento è quindi l'unico controllo del rischio**: la perdita massima di ogni struttura è la perdita davvero attesa nel caso peggiore.
- **Esecuzione**: Claude invia ordini via API **solo quando l'utente scrive "esegui su tws"** su un'operazione precisa (limite e quantità sono quelli dell'istruzione); dopo ogni ordine mappa la struttura e ricarica la dashboard. Dettaglio in [Init](INIT.md).
- **Scadenze**: di default le mensili; le weekly (venerdì entro 45 giorni) solo se l'open interest misurato supera la soglia (sulle weekly di ESTX50 e XSP è spesso sottile). Rilassato il 03/10/2026 su richiesta dell'utente (prima: solo mensili).
- **Rischio non definito**: ammesso solo nelle eccezioni dichiarate dall'utente e mai senza conferma specifica.

## 1. Filosofia e obiettivi
**Edge**: il *variance risk premium* — la volatilità implicita supera in media quella che si realizza. Si vende volatilità **solo in una forma che sopravvive allo scenario peggiore plausibile**. L'edge non è automatico: va **misurato per ogni trade** (vedi §3, filtro sull'edge).

| Obiettivo | Valore |
|---|---|
| Capitale | NAV del conto, leva 2:1, margine ≤ 50% del NAV |
| Rendimento atteso | 12–20% annuo netto; il 3% mensile è un target **lordo di premio**, non netto |
| Drawdown | **allerta −8%**, **kill-switch −15%**: entrambi **solo avvisi** (dashboard), l'eventuale chiusura la decide l'utente |
| Stress S3 (crash −20%) | non peggiore di −15% del NAV |

## 2. Architettura (bucket)
| Bucket | Funzione | Strutture ammesse |
|---|---|---|
| **A. Core** | raccogliere il premio | put credit spread, bear call, iron condor su indici (XSP/SPX/ESTX50), 30-85 DTE, corta a delta 15-25 |
| **B. Tattico** | view e diversificazione dalla sola short vega | calendar (anche double), debit spread, broken wing butterfly, ZEBRA |
| **C. Protezione di coda** | guadagnare nei crash | put OTM lunghe su indice (es. su ESTX50 e XSP, scadenze a 3-4 mesi) |
| **D. Liquidità** | buffer di margine | ≥ 35% del NAV in T-bill/monetari |
| **Satelliti su singoli titoli** | eccezioni | ≤ 1,5% NAV ciascuno, aggregato ≤ 8% (budget da definire dall'utente) |

- **Il portafoglio non deve mai essere "puramente short volatilità"**: B e C compensano la vega negativa di A.
- **Beta**: tetto 0,75 (fascia di comfort 0,5-0,75) **se e quando** si riattiva il motore beta; oggi **sospeso**.
- **In valutazione, non attivi**: sacca azionaria 20% NAV con collar a 3 mesi (costo netto ~0, pavimento −5/−7%, tetto +6/+10%; vincolo dei lotti da 100); covered call mensile (nessun vantaggio statistico) e PMCC (EV negativo): scartati ai prezzi del 21/09. Earnings butterfly 1 DTE: **prova reale solo a 0,25% × 3 posizioni**, poi 0,5% × 5 dopo 20 operazioni con riempimenti verificati; mai prima di averla scritta con i criteri (Fase 2). Momentum-Skew e put nude su indici: abbandonati / futuri (dopo 6-12 mesi di journal).

## 3. Universo e filtri
- **Indici cash-settled**: ESTX50 (Eurex, moltiplicatore 10), SPX/XSP, VIX per l'hedge; **mini-indici**: XSP = SPX/10, XND = NDX/100, MRUT = RUT/10, DJX = DJIA/100 — la volatilità storica si prende dall'indice **padre**, la catena dal mini-indice (XND senza dati live: non operabile). **Non negoziabili su IBKR**: VSTOXX/future FVS (l'hedge EUR usa put dirette su ESTX50) e FTSE MIB (dati in tempo reale non disponibili). DAX richiede un abbonamento dati a parte.
- **Singoli titoli**: solo come satelliti, rischio definito di norma; gli **earnings sono ammessi** (decisione 20/09) ma solo con la regola scritta e dopo la prova reale.
- **Come lavora oggi la ricerca di occasioni (03/10/2026)**: orizzonte **7-200 giorni** (mensili sempre, **weekly = solo venerdì entro 45 giorni**), universo libero (indici, mini-indici, azioni/ETF USA; elenchi `@ETF`, `@INDICI`, `@TOP`). **Valore atteso prudente = il minore fra** (a) quello con la volatilità **prevista** dal modello HAR (non più il massimo delle ultime tre finestre) e (b) quello sulla **distribuzione storica** a quell'orizzonte (bootstrap di 20-25 anni di rendimenti sovrapposti, crisi incluse, martingalizzato al 2%): la log-normale piatta non è più l'unico metro. **Ordinamento: valore atteso per unità di rischio** (default) o assoluto. **Filtri del Piano obbligatori** (con interruttore per vederli comunque): margine di volatilità ≥ +2, spread ≤ 10%, open interest ≥ 100 (se ignoto, escluso), niente utili prima della scadenza per le strutture a credito. **Famiglie**: bull put, bear call, iron condor, iron butterfly, calendar (anche con più scadenze lunghe, 14-90 giorni di scarto), double calendar/diagonal, broken wing butterfly, **put backspread 1×2** (rischio definito, positivo nei crolli), verticali a debito, **put ratio 1×2 dimensionati sulla perdita a −20% al netto delle protezioni** (non più a sottostante zero); screener del **forward factor** su molti ticker e **box spread** su SPX/XSP (rendimento implicito della liquidità, da confrontare con i T-bill). **Esclusi, perché il vincolo resta**: rischio non definito (jade lizard, risk reversal, strangle, call ratio) e stop loss. **Non implementati**: opzioni su VIX (richiedono il future della stessa scadenza e un modello di volatilità della volatilità: il modello attuale darebbe numeri sbagliati). I **calendar pre-utili** sono segnalati (data degli utili e scadenza) ma non ancora preferiti dal ranking.
- **Filtro sull'edge (per ogni nuovo trade)**: **volatilità di pareggio − volatilità realizzata ≥ +2 punti**, spread denaro-lettera delle gambe ≤ 10%, **open interest ≥ 100**, e valore atteso positivo calcolato con la volatilità **realmente realizzata** (non con l'implicita, dove vale zero per costruzione). Metodo in [Come si calcola se un trade ha edge](METODI_OPERATIVI.md). Calendar nuovi: forward factor ≥ +0,20.
- Un valore atteso "positivo" per colpa dello skew (strutture a debito già positive alla IV implicita) o per una coda dimenticata (put ratio: perdita a sottostante zero) **non è un'occasione**.

## 4. Regimi di volatilità (guida)
Tre indicatori: livello VIX/VSTOXX, rapporto VIX/VIX3M (< 0,9 contango sano; > 1,0 stress), **IV Rank a 13 settimane** come riferimento primario (controllare anche 26 e 52: se divergono molto, capire perché).

| Regime | Condizioni | Core (A) | Tattico (B) | Hedge (C) |
|---|---|---|---|---|
| 1 Compressione | VIX < 14, IVR < 20 | 50% della size | ↑ calendar/debit | comprare coda a prezzo di saldo |
| 2 Normale | VIX 14-22 | 100% | normale | budget standard |
| 3 Elevato | VIX 22-30 | 60-75%, ali più larghe | ridotto | mantenere |
| 4 Crisi | VIX > 30 o VIX/VIX3M > 1 | **nessuna nuova short vega** | solo long vega a rischio definito | monetizzare (§7.3) |
| 5 Post-picco | VIX −25% dal massimo, contango | rientro a terzi (§8) | normale | ricostruire |

IVR 13 sett. ≥ 30 → core; < 30 → calendar/diagonal; view direzionale → debit spread; sempre hedge attivo.

## 5. Limiti di rischio
### 5.1 Dimensionamento
- **Perdita massima per trade: 1-2% NAV** (struttura a rischio definito = larghezza − credito); **mai proporre oltre il 2% NAV** (la dashboard nasconde queste occasioni). Satellite: ≤ 1,5%.
- **Concentrazione**: perdita massima aggregata per **(sottostante, mese di scadenza più vicina)** con avviso al **2% NAV** (scelta di Claude, non del Piano); le perdite massime si sommano. Buying power ≤ 5% NAV per posizione; strumenti correlati (SPX, NDX, ESTX50) come un blocco nelle crisi.
- **Margine ≤ 50%** (normale), deve restare < 70% anche dopo un raddoppio dei requisiti.
### 5.2 Greche (beta-weighted)
Delta: perdita ≤ 1% NAV per −3% dell'indice · Vega: ≤ 6% NAV per +10 punti di VIX · Gamma: ≤ 20% del rischio in posizioni < 14 DTE · Theta: 0,05-0,15% NAV/giorno (oltre si vende troppa coda).
### 5.3 Stress test settimanale — **manuale su TWS Risk Navigator, una volta a settimana** (deciso 03/10/2026)
Prezzo e volatilità insieme. Limiti: **S1 −5%/+8 → ≤ 6%** · **S2 −10%/+20 → ≤ 12%** · **S3 −20%/+40 → ≤ 15%** · **S4 gap −7%/+15 → ≤ 10%** · **S5 +7%/−6 → ≤ 5%** del NAV. Se uno scenario viola il limite si riduce prima di aprire qualunque cosa. Metodo e storico in [Valutazione Copertura e Rischio](METODI_OPERATIVI.md). Il pannello della dashboard è solo una stima grezza (cambio fisso 1,153, volatilità e beta fissi; una versione migliorata è in `~/CODE/opzioni/stress_v2.py`, non collegata).

## 6. Gestione delle posizioni
**Ingresso (A)**: 30-85 DTE, corta a delta 15-25, laddering (≤ 30% del rischio A su una scadenza), niente apertura con size piena prima di eventi macro binari (FOMC, BCE, CPI).
**Uscita**: **take profit 50% del credito**; **chiusura/roll della corta a 21 DTE**; **nessuno stop**. Calendar: chiudere la corta prima dei 21 DTE oppure **trasformarli senza rischio**.
**Aggiustamenti (sono il sistema di controllo del rischio)**:
- **Trasformazione senza rischio** (DC Time Machine): calendar → credit spread verticale quando credito ≥ debito + larghezza ali; strutture a debito (ZEBRA, bull call) → **alzare la gamba lunga** appena in profitto (credito incassato ≥ debito). Dettaglio in [DC Time Machine](METODI_OPERATIVI.md).
- **Roll a credito** a strike fermi sulle due mensili successive; **mai a debito**. Funziona su scadenze corte (il premio cresce come √T), non su scadenze lunghe.
- **Legging out**: ricomprare la corta quando vale < 0,10 o < 5% del credito (libera margine, lascia la lunga gratis).
- **Difesa laterale** (iron condor sul lato sotto pressione): solo discrezionale, abbassa la perdita massima ma alza la probabilità di perdere.
- **Quattro regole** (da [Aggiustamenti](METODI_OPERATIVI.md)): non muovere il lato in perdita, sempre credito netto, mai aumentare la dimensione, estendere la scadenza a strike fermi. **Entrare bene vale cento volte aggiustare bene.**
- **Scartati, da non riproporre**: rollare la gamba lunga verso il basso in un credit spread, raddoppiare su un perdente (mediare), trasformare rischio definito in indefinito.
- **Assegnazione anticipata** (titoli: opzioni americane): controllare corte dentro il prezzo con estrinseco < dividendo; gli indici (ESTX50, XSP, SPX) sono europei e immuni.
- Le **probabilità** mostrate sono risk-neutral (stima prudente per chi vende premio); vanno lette insieme alla perdita massima e al pareggio, non da sole.

## 7. Contenimento dei picchi di volatilità
**7.1 Difese strutturali** sempre attive: rischio definito, protezione di coda permanente, liquidità ≥ 35%, laddering, vega nei limiti.
**7.2 Circuit breaker** (guida, tranne i due avvisi della dashboard): L1 allerta (VIX +25% in 1-2 sedute, VIX/VIX3M > 0,95): stop a nuova short vega, chiudere corte a delta > 30 · L2 difesa (VIX > 30, drawdown −8% = avviso): ridurre la vega del 50%, margine < 40%, monetizzare 1/3 dell'hedge · L3 (VIX > 40, drawdown −12%): azzerare la short vega, monetizzare il secondo terzo · **kill-switch −15% (avviso)**: valutare la chiusura di tutto tranne l'hedge e uno stop operativo di 30 giorni con revisione scritta.
**7.3 Monetizzazione dell'hedge**: un terzo quando vale **3× il costo** o al L2; un terzo al L3 o VIX raddoppiato; l'ultimo terzo fino a scadenza. **Rinnovo a 30 giorni dalla scadenza** ; budget 3-5% NAV annuo.
**7.4 Comportamento in crisi**: niente raddoppi per mediare, niente roll difensivi in regime 4, ordini limite al mid o poco peggio (gli spread si allargano 3-5 volte), decisioni sulla checklist e non sulle notizie.
**La protezione non cresce da sola con il libro**: va rifatto lo stress test (con e senza protezioni) ogni volta che il core aumenta; con un core piccolo le protezioni coprono ampiamente, con uno grande no.

## 8. Rientro dopo la crisi
Tutte: VIX sceso ≥ 25% dal massimo, VIX/VIX3M < 0,95 per 2 sedute, stress S3 rispettato. Size a terzi, uno ogni 5-7 sedute, solo rischio definito il primo, DTE 45-60, ali più larghe.

## 9. Routine
**Tutto su richiesta, niente timer** (eccezioni dichiarate: dashboard ogni 10 minuti e ricerca programmata ESTX50/XSP alle 16:00; vedi [Init](INIT.md)).
- **"fai la serata"**: `evening.py` (sola lettura) → avvisi, regime, portafoglio, edge. **"fai apertura europea/USA"**: `session_scan.py eu|us`. **Ricerca per ticker**: campo "Cerca occasioni" nella dashboard.
- **Giornaliera**: regime, trigger, greche vs limiti, posizioni a take profit / 21 DTE / trasformabili (riquadro **Azioni migliorative**), calendario macro (ForexFactory).
- **Settimanale (venerdì)**: **stress test manuale su TWS Risk Navigator** (richiede TWS, non IBKR Desktop), distribuzione del rischio per scadenza e sottostante, stato e costo dell'hedge, journal.
- **Mensile**: KPI e attribuzione P&L, violazioni, ricalibrazione **mai durante un drawdown**.
- Dopo i cambi dell'ora legale (fine ottobre/marzo) verificare gli orari di chiusura USA rispetto a quelli italiani.

## 10. Journal e KPI
Una scheda per trade in Journal: data, sottostante, struttura, DTE, delta, regime, motivazione, credito/debito, rischio max, uscita, P&L, regola rispettata sì/no. Soglie d'attenzione: win rate A < 65% su 50+ trade · perdita media/guadagno medio > 2,5 · expectancy negativa su 3 mesi · Sortino < 1 · costo hedge > 5% NAV annuo · violazioni > 2 al mese · delta che spiega più del theta = trading direzionale mascherato.

## 11. Fiscale (Italia, da verificare)
Redditi diversi al 26%; le minusvalenze (hedge compreso) compensabili entro 4 anni con redditi diversi, **non** con proventi di ETF/UCITS (redditi di capitale). Verificare con un commercialista il regime dichiarativo (anche per un conto estero) prima di operare su larga scala; verificare VIX options e strumenti USA.

## 12. Checklist pre-trade
- [ ] edge: margine di volatilità ≥ +2, spread ≤ 10%, open interest ≥ 100, valore atteso positivo
- [ ] perdita massima ≤ 2% NAV e concentrazione (sottostante, mese) sotto il 2%
- [ ] nessun avviso di drawdown attivo; regime compatibile
- [ ] greche di portafoglio nei limiti anche dopo il trade; stress S2 e S4 rispettati
- [ ] nessun earnings/evento binario prima della scadenza (singoli titoli)
- [ ] piano di uscita scritto: take profit 50%, 21 DTE, aggiustamenti possibili; **niente stop**
- [ ] hedge di coda attivo
- [ ] eventuali **deroghe dette all'utente prima di eseguire**, mai in silenzio

## 13. Principi finali
1. La sopravvivenza viene prima del rendimento (−50% richiede +100%).
2. Il premio incassato non è un guadagno finché la posizione non è chiusa.
3. Le regole si cambiano a mercato calmo, mai durante la crisi.
4. L'hedge che "costa sempre" è il motivo per cui si può continuare a vendere premio.
5. Nessun modello cattura tutto: lasciare un margine per l'evento che nessuno scenario aveva previsto. Un edge misurato su un modello (log-normale piatto) **sovrastima le put lontane**: diffidare dei margini più alti.
