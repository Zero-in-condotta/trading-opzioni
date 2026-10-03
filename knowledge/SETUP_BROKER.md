# Setup e Watchlist

## Broker
- **IBKR**, conto a margine, permessi opzioni attivi.
- **TWS installato in locale** (Trader Workstation Mosaic, Linux). I permessi di trading (opzioni US, futures CFE, derivati Eurex) si gestiscono dal **Client Portal web** (Account Management → Trading Permissions), non da TWS — il conto paper eredita di norma i permessi del conto live collegato.
- **Piattaforma di uso quotidiano: IBKR Desktop** (deciso il 16/09/2026, TWS giudicato troppo complesso per l'uso di tutti i giorni). Stesso conto, stessi dati/posizioni di TWS — ma **il Risk Navigator (scenari personalizzati prezzo+volatilità, usato per lo stress test S1-S5 del Piano §5.3) è specifico di TWS, non esiste in IBKR Desktop.** Schema operativo: Lun-Gio routine su IBKR Desktop, **venerdì aprire TWS solo per lo stress test settimanale** (scenari già salvati, vedi sotto). **IV Rank/IV Percentile confermate presenti anche su Desktop** (13/26/52 settimane — usare 52) il 16/09/2026: non serve più aspettare il venerdì per quella lettura, disponibile ogni sera direttamente su Desktop.

## Abbonamenti dati (market data)

Attivati il 16/09/2026 su Client Portal → Settings → Market Data Subscriptions (necessari per operare con dati real-time, non delayed — dettaglio del perché in conversazione, qui solo il riepilogo):

| Bundle | Copre | Prezzo | Waiver |
|---|---|---|---|
| US Securities Snapshot and Futures Value Bundle (NP,L1) | Prerequisito USA | $10/mese | a $30 commissioni/mese |
| US Equity and Options Add-On Streaming Bundle (NP) | OPRA — opzioni XSP/SPX/VIX | $4.50/mese | incluso nel prerequisito sopra |
| CBOE Streaming Market Indexes (NP) | VIX/VIX3M livello | $3.50/mese | — |
| Europe Equity and Derivative Display Value Bundle (NP) | Eurex Retail Europe — OESX | €12.50/mese | a $35 commissioni/mese |
| STOXX DAX Volatility Index (NP) | V2TX | €6.00/mese | — |
| STOXX Index Real-Time Data (NP) | ESTX50 | €3.00/mese | — |

**Alternativa gratuita per VIX/VIX3M nei giorni normali:** Yahoo Finance (`^VIX`, `^VIX3M`) mostra dati **delayed 15-20 minuti** (etichettati "Chicago Options - Delayed Quote", non real-time nonostante possa sembrarlo), verificato il 16/09/2026. Per la lettura serale del regime (21:00, mercato aperto da ore) il ritardo è irrilevante — può sostituire il bundle "CBOE Streaming Market Indexes" ($3.50/mese) nei giorni di regime 1-2. **Nei giorni di regime elevato/crisi**, quando la Routine Operativa impone controlli intraday ogni volta che il VIX si muove >20%, il ritardo torna rilevante: lì conviene restare su IBKR come fonte primaria.

## Estensione futura: FTSE MIB e DAX (17/09/2026)

Aggiunti `FTMIB` e `DAX` alla watchlist come possibili terzo/quarto indice per diversificare (Piano §3, mai ancora usati operativamente). Verificato lo stesso giorno:
- **DAX** (EUREX, "DAX 40 Index") → **ipotesi iniziale sbagliata**, corretta il 17/09/2026: **non basta l'abbonamento Eurex/STOXX già attivo**. Confermato dal supporto IBKR (IBot): *"The DAX 30 Index and STOXX indices require separate market data subscriptions. STOXX real-time data does not include the DAX index."* Serve il bundle **"German ETF's and Indices"** — attivato lo stesso giorno (Client Portal → User menu → Settings → Trading Platform → Market Data Subscriptions → icona ingranaggio → spuntare "German ETF's and Indices" → Continue), copre **indice DAX + VDAX-NEW** (equivalente tedesco di V2TX), solo lettura di regime.
  - **Se dopo l'attivazione il quote resta "delayed":** probabile routing **Stock (SMART)** sulla riga watchlist, che richiede l'abbonamento su *tutte* le borse dove il prodotto è quotato. Fix: creare la riga come **Stock (Directed)** invece di SMART, scegliendo la borsa specifica (Xetra) dove hai davvero l'abbonamento.
  - **Per operare davvero su ODAX (le opzioni, non solo leggere l'indice):** serve **Eurex Core / Eurex Retail Europe** — quasi certamente già attivo, è lo stesso bundle ("Europe Equity and Derivative Display Value Bundle") usato oggi per OESX. "German ETF's and Indices" serve solo a leggere il regime (indice + VDAX-NEW), non abilita da solo il trading delle opzioni.
- **FTMIB** → **limite strutturale, non un abbonamento mancante** (verificato il 17/09/2026 con IBot di supporto IBKR): *"We do not make available real-time index levels for the FTSE MIB 40 index. This is due to the disproportionately high licensing fees that FTSE would require."* Vale anche per il FTSE 100. **Non attivabile in nessun modo su IBKR** — solo dati ritardati disponibili, insufficienti per operare secondo questo Piano (stessa logica del limite su VSTOXX/FVS, Piano §3). **FTSE MIB/MIBO è di fatto escluso dagli strumenti utilizzabili**, non solo "da configurare".

## Watchlist

**Solo due strumenti sono operativi** (su di loro apriamo davvero ordini — core e hedge insieme): **XSP** e **ESTX50/OESX**. Gli altri tre (VIX, VIX3M, V2TX) sono **segnali**, non si opera mai direttamente su di loro — servono solo a classificare il regime (Piano §4) e decidere quando/quanto muoversi sui due strumenti operativi. Deciso il 16/09/2026: l'hedge (bucket C) si fa con put OTM dirette su XSP ed ESTX50 stessi, non con derivati su VIX/VSTOXX.

Come cercarli in TWS: barra di ricerca simbolo, digitare il codice, filtrare per tipo **Indice (IND)** e la borsa indicata.

| Cosa | Simbolo su TWS | Borsa | Note |
|---|---|---|---|
| VIX | `VIX` | CBOE | Indice, SecType IND, USD. Futures: `VX` (standard) o `VXM` (mini, 1/10) su CFE |
| VIX3M | `VIX3M` | CBOE | Stesso indice, scadenza a 93 giorni invece di 30 |
| VSTOXX | `V2TX` (o cercare "VSTOXX") | Eurex | Indice ufficiale STOXX, ISIN DE000A0C3QF1 — utile solo come **lettura del livello** per il regime (Piano §4). **Verificato il 16/09/2026: il future `FVS` NON è disponibile su IBKR** (né per simbolo né cercando "VSTOXX" per nome, né in TWS né in Desktop) — non è negoziabile tramite questo broker. L'hedge sulla parte EUR (Piano §3) si fa con **put OTM dirette su `ESTX50`**, non con VSTOXX |
| XSP | `XSP` | CBOE | Opzioni mini-SPX, cash-settled |
| Euro Stoxx 50 | `ESTX50` (indice) / opzioni `OESX` | Eurex | — |

Verificare sempre in TWS che il contratto selezionato abbia SecType ed exchange corretti prima di aggiungerlo alla watchlist — più simboli omonimi possono comparire su borse diverse.

### Colonne IV Rank / IV Percentile

TWS le calcola **nativamente**, non serve costruire uno storico proprio (corregge quanto ipotizzato inizialmente in Automazione): passare il mouse sull'intestazione di una colonna esistente della watchlist finché non compare **"Insert Column"**, cercare **IV Rank** / **IV Percentile** e scegliere la finestra a **52 settimane** (coerente con Piano §4, che parla di IV Rank sull'ultimo anno).

## Greche

- **Per singolo strike:** tasto destro sul simbolo → **Option Chain**, oppure aprire **OptionTrader** — colonne Delta/Gamma/Theta/Vega presenti di default per ogni riga.
- **Aggregate di portafoglio (per il confronto con i limiti Piano §5.2):** pannello **Portfolio** → widget **Greeks**, con **beta weighting** attivato verso SPX per le posizioni USD. La parte EUR (Euro Stoxx 50) va letta separatamente: il beta weighting pesa verso un solo riferimento per volta.

## Alert di prezzo

Su `VIX`: tasto destro nella watchlist → **Create Alert** → tipo **% Change**, soglia **20%** — è il trigger che nel Piano §4 impone una rivalutazione intraday del regime (vedi anche [Piano §9 (routine)](PIANO.md) §2). Alert equivalenti da impostare su ogni posizione aperta per stop/take profit (non uno stop automatico — vedi sotto).

## Risk Navigator (IBKR TWS)

Scenari S1–S5 da impostare e rilanciare ogni venerdì (dettaglio nel [Piano](PIANO.md) §5.3):

| Scenario | Indice | VIX |
|---|---|---|
| S1 – Correzione | −5% | +8 punti |
| S2 – Shock | −10% | +20 punti |
| S3 – Crash | −20% | +40 punti |
| S4 – Gap weekend | −7% apertura | +15 punti |
| S5 – Rally violento | +7% | −6 punti |

**Come crearli:** aprire Risk Navigator (tasto destro sull'account, o menu Analytics) → menu **View → Custom Scenario**. Permette di impostare contemporaneamente la variazione del sottostante (Explicit Value / Change / % Change) **e** la variazione della volatilità implicita — necessario per replicare shock di prezzo e VIX insieme, mai separati (Piano §5.3). Salvare ciascuno dei cinque come scenario nominato, così il venerdì si richiamano invece di reinserirli ogni volta.

## Calendario macro

**[ForexFactory Calendar](https://www.forexfactory.com/calendar)** (deciso il 16/09/2026) — fonte per il controllo macro della routine (Piano §6, [Piano §9 (routine)](PIANO.md) §3.7): FOMC, BCE, CPI e altri eventi binari prima di ogni apertura a size piena.

## Weekly vs mensili: differenza di liquidità scoperta il 18/09/2026

Su ESTX50 (OESX), le scadenze **weekly** (marcate con una piccola "W" accanto a OESX nella chain di TWS) hanno **Open Interest strutturalmente molto più basso** delle mensili standard (terzo venerdì del mese, senza "W") — verificato con numeri reali: weekly Nov06'26 con OI di 1-40 contratti sulla fascia delta 10-20, contro le **migliaia** (es. 6,53K) della mensile Nov20'26 sugli stessi ordini di grandezza di strike. Lo spread bid-ask da solo non lo segnala (su Nov06 era comunque ~4%, sotto soglia) — serve controllare l'OI esplicitamente, i due filtri raccontano storie diverse.

**Regola pratica adottata da qui in avanti:** preferire le scadenze mensili standard quando possibile, anche a costo di uscire di qualche giorno dal range 30-60 DTE (entro un margine ragionevole, es. 3-5 giorni) — usare una weekly solo se non c'è alternativa mensile vicina e si accetta esplicitamente il rischio di OI basso come deroga.

## Esecuzione ordini (note pratiche da IBKR/TWS)
- Spread: OptionTrader o Strategy Builder, sempre come **combo** con limite sul credito netto — mai gambe separate.
- Take profit: ordine GTC al 50% del credito, inserito subito dopo l'apertura.
- Stop: alert di prezzo sullo spread o sul sottostante, non uno stop automatico (rischio di fill assurdo su gap o spread bid-ask allargato).
- **Cambiare il prezzo di un ordine in attesa (17/09/2026, dopo un incidente reale):** dopo aver modificato il limite (es. da -5,40 a -5,20 per favorire il fill), **verificare nel pannello Orders che l'ordine precedente risulti esplicitamente cancellato**, non solo che il nuovo sia stato inviato — su IBKR Desktop una modifica di prezzo può creare un ordine parallelo invece di sostituire quello esistente. Se entrambi restano attivi e si riempiono entrambi, la posizione raddoppia senza che sia una scelta consapevole (e' successo davvero: lo stesso spread riempito due volte, a due prezzi diversi, a un'ora di distanza).
