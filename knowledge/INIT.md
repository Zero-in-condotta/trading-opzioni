# INIT - Trading in opzioni con Claude (versione per il repository)

> File di ingresso della conoscenza del progetto. Leggerlo per intero, poi [PIANO.md](PIANO.md) (regole) e [METODI_OPERATIVI.md](METODI_OPERATIVI.md) (come si valuta e si aggiusta). Per ricreare l'ambiente su un nuovo computer: [../docs/RICOSTRUZIONE.md](../docs/RICOSTRUZIONE.md). Questa versione **non contiene** posizioni, saldi, storico dei trade né dati personali: quelli vivono nei file locali non versionati (`opzioni/data/`) e nel registro privato dell'utente.

## Che cos'e

Un sistema per gestire un portafoglio di opzioni su Interactive Brokers (IBKR): codice in sola lettura per leggere conto, catene e prezzi, una dashboard locale, una ricerca di occasioni con valore atteso statistico, un motore di aggiustamenti, e una base di conoscenza (regole, metodi, didattica). Claude e' il **mentore e gestore esperto**; l'utente e' lo studente che deve diventare **completamente indipendente**. Obiettivo: rendimento dal premio venduto **senza rischi insensati**, con protezione dal crash sempre attiva.

## Ruolo di Claude (regole di comportamento)

- **Non limitarsi a eseguire.** Ogni scelta (strike, scadenza, dimensione, trigger) va spiegata nel merito e agganciata al Piano e a un caso concreto: prima la teoria, poi la pratica.
- **Verificare la comprensione** con domande di ripasso quando si introduce un concetto nuovo (greche, regimi di volatilita', struttura a termine, valore atteso).
- **Accuratezza numerica non negoziabile**: prezzi, greche, margini si verificano (mai a memoria). Non e' consulenza finanziaria: ribadirlo quando si parla di rendimenti e dimensioni.
- **Le regole del Piano sono indicazioni, non vincoli.** Claude ricorda cosa suggerisce il Piano e poi decide sulla situazione; fanno eccezione le regole che l'utente ha dichiarato "permanenti" (sotto).
- **Dire all'utente se una richiesta viola il Piano prima di eseguirla**: mai eseguire una deroga in silenzio.
- Distinguere sempre **fatto da inferenza**; quando un calcolo dipende da un'ipotesi, dichiararla.
- Concetti riutilizzabili -> glossario; decisioni nuove -> registrarle subito nei documenti di conoscenza.
- Lingua: italiano. Niente emoji nelle note.

## Regole permanenti (dichiarate dall'utente, prevalgono su tutto)

1. **Niente stop loss.** Le perdite si gestiscono aggiustando la posizione, mai con un livello di prezzo. Conseguenza: il **dimensionamento e' l'unico controllo del rischio** e la perdita massima di una struttura e' la perdita realmente attesa nel caso peggiore (tetti: perdita massima per trade 1-2% del NAV; mai proporre oltre il 2%).
2. **Esecuzione via API solo su "esegui su tws".** Claude invia un ordine **soltanto** quando l'utente lo scrive esplicitamente riferito a un'operazione precisa (di norma l'istruzione copiata dalla dashboard). Mai di iniziativa, mai oltre il limite dell'istruzione, mai chiudere o aggiustare senza richiesta.
3. **Subito dopo ogni ordine eseguito**: verificare le posizioni sul conto, mappare la struttura in `opzioni/data/portfolio_map.json`, ricaricare la dashboard e controllare che "Posizioni non mappate" sia vuoto, scrivere nel registro.
4. **Rischio non definito escluso** (niente strangle/straddle venduti, call ratio, jade lizard, risk reversal) salvo eccezioni dichiarate e mai senza conferma specifica.
5. **Scadenze**: di default le mensili; le weekly (venerdi' entro 45 giorni) solo se l'open interest misurato lo permette.
6. **Nessun automatismo non richiesto**: i servizi pianificati sono solo la dashboard (ogni 10 minuti), il server locale (sempre attivo) e la ricerca programmata di ESTX50/XSP alle 16:00 nei giorni di mercato. Tutto il resto parte **su richiesta**.
7. **Lo stress test settimanale lo fa l'utente a mano** su TWS (Risk Navigator); il pannello della dashboard e' solo una stima grezza.

## Esecuzione via API: come funziona e perche' e' sicura

- `ib_common.connect()` apre **sempre** `readonly=True`: tutti gli script di analisi non possono inviare ordini nemmeno per errore.
- La scrittura vive **solo** in file derivati da `opzioni/send_order_TEMPLATE.py`, uno per ordine, con il **contratto scritto nel codice** (non da riga di comando). Sicurezze in sequenza: contratti qualificati e verificati con assert; sottostante entro tolleranza rispetto al momento della proposta; credito/debito **eseguibile** (bid/ask) confrontato col limite (se non raggiungibile l'ordine resta DAY, **non si insegue il prezzo**); `whatIfOrder` sul margine (**inaffidabile sui combo**: spesso 0 e commissione non valida, la copertura va ragionata sulla struttura); conferma esplicita `--confirm`.
- In TWS serve disattivare "Read-Only API" solo se si intende davvero inviare ordini da qui.

## Strumenti (vedi docs/RICOSTRUZIONE.md per il dettaglio)

- **Dashboard** (`dashboard.py`, ogni 10 minuti): grafico P/L; **Posizioni per mese di scadenza** (righe collassabili con totali, Vega e Theta in EUR); concentrazione per sottostante e mese; **Occasioni sul mercato** (ricerca asincrona per ticker con risultati a flusso); **Azioni migliorative** (motore `actions.py`: trasformazioni senza rischio, presa di profitto al 50%, regola dei 21 DTE, roll a credito, rischio di assegnazione, protezioni di coda, legging out, difesa laterale; con situazione prima/dopo, probabilita', valore atteso e "non fare nulla" in classifica). Il P/L mostrato e' a meta' spread e **non e' incassabile**: la colonna "Chiudendo ora" e' quella vera. Tutti gli importi sono in EUR al cambio di TWS del momento; fanno eccezione il limite d'ordine nelle istruzioni copiabili e i prezzi unitari delle opzioni.
- **Server locale** (`chain_builder.py`, porta 8770, servizio sempre attivo): costruttore di strategie con payoff, serve la dashboard (`/dashboard.html`) e gli endpoint `/api/occasioni*`. **Aprire la dashboard da `http://127.0.0.1:8770/dashboard.html`.**
- **Ricerca di occasioni** (`scan_ticker.py`, `scan_vrp.py`, `scan_ff.py`, `scan_box.py`, `stat_ev.py`, `occ_render.py`): orizzonte 7-200 giorni, mensili e weekly; valore atteso **prudente** = il minore fra quello con la volatilita' prevista (HAR) e quello sulla distribuzione storica (bootstrap, crisi incluse); filtri del Piano obbligatori (con interruttore); ordinamento per valore atteso diviso rischio; screener del forward factor; box spread. La dashboard **non ricalcola mai le occasioni al caricamento**.
- **Su richiesta**: "fai la serata" -> `evening.py`; "fai apertura europea/USA" -> `session_scan.py eu|us`; `transform.py`; `stress.py` (grezzo) e `stress_v2.py` (non collegato); `calcola_beta.py`.
- **Dati locali** (`opzioni/data/`, mai nel repository): `portfolio_map.json` (mappa dei gruppi), `stato_portafoglio.json`, `price_rules.json`, `occasioni/`, `nav_history.csv` (picco del drawdown: versamenti e prelievi lo falsano e vanno corretti a mano), `betas.json`, `dashboard.html`.
- **Notifiche di sistema**: solo per il drawdown (allerta -8%, kill-switch -15%) via `notify-send`; sono **avvisi**, la chiusura la decide l'utente.

## Lezioni che restano valide

- **Entrare bene vale cento volte aggiustare bene.** Un aggiustamento non salva una posizione nata troppo grande; il filtro sull'edge conta piu' del motore delle azioni.
- **Valore atteso con la volatilita' realizzata, non con l'implicita** (con l'implicita vale zero per costruzione). La **volatilita' di pareggio** e' una proprieta' del trade. Con la log-normale piatta i margini sulle put lontane sono **sovrastimati**; un valore atteso positivo gia' alla IV implicita e' skew, non un'occasione; una coda dimenticata (put ratio: perdita a sottostante zero) rende positivo un valore atteso che non lo e'.
- **Il cuscinetto della corta si misura con il segno** (sopra/sotto il prezzo non sono la stessa cosa). Rollare in basso un credit spread non e' per forza una trappola: scambia probabilita' con dimensione della perdita.
- **P/L a meta' spread != incassabile**; spread denaro-lettera e commissioni a ogni manovra.
- Le opzioni **ESTX50 hanno moltiplicatore 10**, quelle USA 100: sempre leggere `contract.multiplier`.
- `reqSecDefOptParams` restituisce l'unione degli strike di tutte le scadenze: per quelli veri `reqContractDetails`. **Eurex chiude alle 17:30 (ora italiana)**: dopo non ci sono quotazioni ESTX50.
- **Mini-indici**: XSP = SPX/10, XND = NDX/100, MRUT = RUT/10, DJX = DJIA/100. Volatilita' storica dall'indice padre, catena dal mini; l'indice puo' stare su una borsa diversa dalle opzioni (XND: NASDAQ; MRUT: RUSSELL). XND senza abbonamento Nasdaq non ha quotazioni.
- A mercato chiuso (dati congelati) TWS **non fornisce le greche**: lo scanner non produce righe. I test offline (`tests/`) servono a verificare il codice senza TWS.
- Con TWS `reqMarketDataType`: 1 live, 2 congelati, 3 ritardati, 4 ritardati congelati. Per decisioni operative solo dati live.

## Struttura del repository

```
opzioni/        codice (script, dashboard, server)             config.example/  modelli dei file di configurazione
knowledge/      questa conoscenza (regole, metodi, didattica)   systemd/         servizi e timer (modelli)
docs/           RICOSTRUZIONE.md e altri documenti              scripts/         installazione e verifica
tests/          test offline (mercato simulato, nessun TWS)
```
