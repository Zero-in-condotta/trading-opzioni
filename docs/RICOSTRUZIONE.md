# Ricostruzione dell'ambiente su un nuovo computer

> **Per Claude (e per chi lo rifa a mano).** Questo documento basta, insieme al repository, per ricreare da zero l'ambiente di lavoro: codice, servizi, configurazione, regole di comportamento. Va letto **per intero** prima di toccare qualcosa. Le persone fanno una sola cosa prima di lanciare Claude: **installare e configurare TWS** (sezione 3). Tutto il resto lo fa Claude seguendo le sezioni 4-9.
>
> Cosa **non** c'e' nel repository, volutamente: posizioni, saldi, storico del conto e dei trade, mappa del portafoglio, regole di prezzo personali, token (Flex, API di terzi), numeri di conto. Vedi sezione 10 per come ricostruire quello che serve.

## 0. Come usare questo documento

1. Leggi la sezione 1 (che cosa e'), poi la 2 (cosa serve) e la 3 (TWS).
2. Esegui l'installazione (4), la configurazione (5), l'avvio dei servizi (6), la verifica (7).
3. Leggi **`CLAUDE.md`** (regole di condotta) e **`knowledge/INIT.md`, `knowledge/PIANO.md`, `knowledge/METODI_OPERATIVI.md`**: sono la conoscenza con cui si opera.
4. Tieni a portata la sezione 9 (problemi noti): ogni voce e' un errore gia' fatto una volta.

## 1. Che cosa e' questo sistema

Un insieme di strumenti che parlano con **Trader Workstation (TWS)** di Interactive Brokers attraverso la sua API locale e producono: una **dashboard** del portafoglio di opzioni, una **ricerca di occasioni** con valore atteso statistico, un **motore di aggiustamenti**, un **costruttore di strategie**. E' **in sola lettura** (nessun ordine parte per errore); gli ordini si inviano solo con un modello dedicato, su richiesta esplicita.

```
                          +-----------------------+
   Yahoo Finance  ------> |  stat_ev.py (HAR,     |
   (storico, via          |  bootstrap, EV)       |
    yfinance)             +-----------+-----------+
                                      |
   TWS (127.0.0.1:7496) <--> ib_async |  scan_vrp.py / scan_ticker.py / scan_ff.py / scan_box.py
        ^                              v
        |                      occ_render.py (filtri, schede)
        |                              ^
        +--- dashboard.py ------> data/dashboard.html + data/stato_portafoglio.json
        |        (ogni 10 minuti)           ^
        +--- chain_builder.py  (server 8770, sempre attivo) -- serve la dashboard e /api/*
                  ^ lancia in processi separati: scan_ticker / scan_ff / scan_box
```

Flussi principali:
- **Dashboard**: `dashboard.py` legge il conto da TWS, applica la mappa `data/portfolio_map.json` (che raggruppa le gambe in strutture), calcola P/L, greche, pareggi, concentrazione, azioni migliorative (`actions.py`), scrive `data/dashboard.html` e `data/stato_portafoglio.json`. Un timer systemd la rilancia ogni 10 minuti (dura circa 4 minuti).
- **Server locale**: `chain_builder.py` (porta 8770) e' sempre attivo: serve la dashboard da `http://127.0.0.1:8770/dashboard.html`, il costruttore di strategie e gli endpoint della ricerca di occasioni.
- **Ricerca di occasioni**: dalla pagina si scrive un ticker e si preme "Cerca occasioni": il server avvia `scan_ticker.py` in un **processo a parte** che scrive `data/occasioni/<job>.json` a ogni scadenza analizzata; la pagina interroga `/api/occasioni` ogni 3 secondi e mostra le righe man mano. Ricaricare la pagina non interrompe nulla. La dashboard **non ricalcola mai** le occasioni al caricamento.

## 2. Requisiti

### Sistema (testato su CachyOS / Arch Linux, systemd, fish)
| Cosa | Perche' | Come |
|---|---|---|
| `git` | clone del repository | `sudo pacman -S git` |
| `uv` | gestisce Python e le dipendenze | `sudo pacman -S uv` oppure https://docs.astral.sh/uv/ |
| Python >= 3.11 | richiesto dal progetto (sviluppato con 3.14) | lo installa `uv` da solo |
| systemd (utente) | servizi e timer | gia' presente su Arch |
| `libnotify` (`notify-send`) | avvisi di drawdown sul desktop (facoltativo) | `sudo pacman -S libnotify` |
| `chromium` | screenshot di verifica con `--headless` (facoltativo) | `sudo pacman -S chromium` |
| `node` | controllo sintattico del JavaScript delle pagine (facoltativo) | `sudo pacman -S nodejs` |
| Fuso orario | i timer usano l'ora locale: la ricerca delle 16:00 e' pensata per l'Italia | `timedatectl set-timezone Europe/Rome` |
| Accesso a Internet | `yfinance` scarica gli storici da Yahoo (volatilita' realizzata, HAR, bootstrap, beta) | - |

### Account e dati di mercato (IBKR)
- Conto IBKR con **permessi opzioni** (livello adatto agli spread) e sugli indici usati: opzioni USA su indici (XSP/SPX), derivati **Eurex** (ESTX50/OESX). I permessi si gestiscono dal **Client Portal web** (Account Management > Trading Permissions), non da TWS.
- **Abbonamenti dati in tempo reale** (senza, TWS da' dati ritardati e **le greche non arrivano**): vedi `knowledge/SETUP_BROKER.md` (bundle OPRA per XSP/SPX/VIX, Eurex retail per OESX, STOXX per ESTX50, ecc.). Nota: **XND e NDX non hanno quotazioni** senza il pacchetto dati Nasdaq.
- Il sistema usa la valuta base **EUR**: i cambi verso altre valute si leggono da TWS (`Forex EURxxx`).

### Servizi esterni (tutti facoltativi tranne Yahoo)
- **Yahoo Finance** (via `yfinance`): necessario per gli storici. Nessuna chiave.
- **Flex Query IBKR** (solo `pnl_history.py`, storico del P/L): token in `~/.config/opzioni/flex.env` (modello in `config.example/flex.env.example`).
- **Massive (ex Polygon)** (solo `massive_probe.py`): variabile d'ambiente `MASSIVE_API_KEY`. Non serve al resto.
- **OQuants / ORATS** (analytics e backtest a pagamento): usati per validare idee, **non** sono richiesti dal codice.

## 3. TWS: installazione e impostazioni (lo fa l'utente prima di tutto il resto)

1. Installare **Trader Workstation** (versione "Mosaic" per Linux, dal sito IBKR) e accedere al **conto reale** (il conto paper puo' avere dati di mercato inaffidabili).
2. **Configurazione globale > API > Impostazioni**:
   - attivare **Enable ActiveX and Socket Clients**;
   - **porta 7496** (conto reale; 7497 per il paper);
   - **Trusted IPs: 127.0.0.1** e consentire solo connessioni locali;
   - lasciare attivo **Read-Only API** (gli script di analisi sono comunque `readonly=True`); **disattivarlo solo** se si vogliono inviare ordini con `send_order_TEMPLATE.py`;
   - disattivare i messaggi di conferma che bloccano le connessioni API, se TWS li mostra.
3. **Mantenere TWS aperto e connesso**: TWS fa un logout/riavvio giornaliero (impostabile in Configurazione > Lock and Exit). Quando e' chiuso, dashboard e ricerche falliscono con `TimeoutError`: e' normale e si risolve da solo alla riapertura (il server e il timer riprovano).
4. Per lo **stress test settimanale** serve TWS (il **Risk Navigator** non esiste in IBKR Desktop): scenari personalizzati prezzo+volatilita' insieme (vedi `knowledge/SETUP_BROKER.md`).
5. Provare dalla riga di comando: `uv run opzioni/check_connection.py --port 7496` (dopo l'installazione, sezione 4).

### Identificativi di connessione (clientId) usati
Ogni connessione API ha un `clientId` che deve essere **unico fra le connessioni contemporanee**. Quelli usati:

| clientId | Script | clientId | Script |
|---|---|---|---|
| 101 | `check_connection.py` | 140 | `stress.py`, `stress_v2.py`, `double_diagonal.py` |
| 105 | `check_fills.py` | 143 | `intraday_rv.py`, `pnl_history.py` |
| 106 | `stage2_stock.py` | 150 | `chain_builder.py` (server), `collar.py` |
| 107 | `stage2_index.py` | 151 | `pmcc.py` |
| 109 | `stage2_batch.py` | 170 | `dashboard.py` |
| 110 | `leap_chain.py`, `leap_calls.py` | 177 | `actions.py` (da solo) |
| 111 | `portfolio_report.py` | 196-197 | `scan_vrp.py` (scansione, `--posizioni`) |
| 112 | `mappa_da_posizioni.py` | 230 | `scan_ticker.py` |
| 120 | `evening.py` | 231 | `scan_ff.py` |
| 130 | `strategies.py` | 232 | `scan_box.py` |
| 131 | `transform.py`, `send_order_TEMPLATE.py` | 132 | `double_calendar.py` |

Se due script con lo stesso id girano insieme, il secondo fallisce o disconnette il primo. Sono in uso **contemporaneamente** in condizioni normali: 150 (server), 170 (dashboard) e, durante una ricerca, 230/231/232; `stress.py` (140) parte dalla dashboard.

## 4. Installazione

```bash
git clone <URL-del-repository> ~/CODE/trading-opzioni
cd ~/CODE/trading-opzioni
scripts/install.sh            # uv sync, cartelle locali, configurazioni di esempio, servizi systemd (non abilitati)
```

`scripts/install.sh` fa, nell'ordine: `uv sync` (crea `.venv` con le dipendenze di `pyproject.toml`/`uv.lock`); crea `opzioni/data/` e `opzioni/logs/`; **copia i modelli** `config.example/portfolio_map.example.json` e `price_rules.example.json` in `opzioni/data/` se mancano (**vanno sostituiti** con i dati veri, sezione 10); installa in `~/.config/systemd/user/` i servizi `opzioni-*` sostituendo il percorso del repository.

I servizi installati (da `systemd/`):

| Unita' | Tipo | Cosa fa |
|---|---|---|
| `opzioni-chain-builder.service` | simple, `Restart=always` | server locale sulla porta 8770 (dashboard, costruttore, API delle ricerche) |
| `opzioni-dashboard.service` + `.timer` | oneshot, ogni 10 minuti | rigenera `data/dashboard.html` |
| `opzioni-opportunita.service` + `.timer` | oneshot, lun-ven 16:00, riprova se TWS non risponde | ricerca programmata su ESTX50 e XSP (`data/opportunita.json`), mostrata finche' non si fa una ricerca dalla pagina |

Abilitarli **dopo** che TWS e' pronto: `scripts/install.sh --abilita` (equivale a `systemctl --user enable --now opzioni-chain-builder.service opzioni-dashboard.timer opzioni-opportunita.timer`).

## 5. Configurazione dei dati locali

Tutto cio' che e' personale sta in `opzioni/data/` (ignorato da git). Formati:

### `portfolio_map.json` (la mappa dei gruppi) — il file piu' importante
```json
{"groups": [
  {"id": "xsp_bull_put_1", "nome": "XSP - Bull Put 740/730 (Nov20)", "note": "...",
   "legs": [{"symbol": "XSP", "expiry": "20261120", "strike": 730.0, "right": "P"},
            {"symbol": "XSP", "expiry": "20261120", "strike": 740.0, "right": "P"}]}]}
```
- **Un gruppo = una operazione** (una struttura). Le gambe si identificano per simbolo, scadenza `AAAAMMGG`, strike e tipo `P`/`C`. Una posizione sul conto non presente in nessun gruppo compare come **"non mappata"** nella dashboard (mai nascosta).
- Se una gamba e' **condivisa** fra due strutture (stesso strike aperto due volte) si indica `"qty"` con segno in ciascun gruppo; la somma deve fare la posizione sul conto. Il codice divide la posizione fra i gruppi.
- Il gruppo con id **`protezioni_coda`** (o con "protezion" nel nome) e' trattato come protezione: escluso dalle concentrazioni e dai totali "senza protezioni".
- Se manca la mappa: `uv run opzioni/mappa_da_posizioni.py` ne scrive una **bozza** da riunire in strutture (sezione 10).

### `price_rules.json`
Regole di **presa di profitto** o uscita pianificata a un livello di prezzo (mai stop loss): `{"rules": [{"symbol","above","since","action","orders":[...]}]}`. Avviso solo in dashboard.

### Altri file (generati)
`stato_portafoglio.json` (dashboard), `dashboard.html` (dashboard), `opportunita.json` (ricerca programmata), `occasioni/`, `occasioni_ff/`, `occasioni_box/` (ricerche dalla pagina), `rv_storiche.json` (volatilita' realizzate misurate, lette da `actions.py`), `betas.json` (`calcola_beta.py`), `nav_history.csv` (picco del NAV per il drawdown: **versamenti e prelievi lo falsano e vanno corretti a mano**), `pnl_daily.csv` (storico del P/L), `serate/` (rapporti serali, o `OPZIONI_NOTE_DIR`), `reports/`, `raw/` (catene in Parquet).

### Variabili d'ambiente e file fuori dal repository
- `OPZIONI_NOTE_DIR`: dove `evening.py` scrive i rapporti serali (default `opzioni/data/serate`).
- `~/.config/opzioni/flex.env`: `FLEX_TOKEN`, `FLEX_QUERY_ID` (solo `pnl_history.py`), permessi 600.
- `MASSIVE_API_KEY` (solo `massive_probe.py`).

## 6. Avvio e uso quotidiano

```bash
systemctl --user enable --now opzioni-chain-builder.service opzioni-dashboard.timer opzioni-opportunita.timer
systemctl --user start opzioni-dashboard.service      # rigenera subito la dashboard (~4 minuti)
xdg-open http://127.0.0.1:8770/dashboard.html          # aprire SEMPRE da qui (non da file://)
```

Su richiesta (nessun timer): `uv run opzioni/evening.py` (serata: avvisi, regime, portafoglio, edge), `uv run opzioni/session_scan.py eu|us` (scansione ad apertura di sessione), `uv run opzioni/transform.py`, ricerche per ticker dalla pagina ("Cerca occasioni", "Cerca calendar", "Box spread").

Procedura **dopo ogni ordine eseguito** (regola permanente): verificare le posizioni sul conto, **aggiornare `portfolio_map.json`**, rigenerare la dashboard e controllare che "Posizioni non mappate" sia vuoto, scrivere nel registro.

Verifica visiva di una modifica alla pagina (senza aprire il browser): `chromium --headless=new --no-sandbox --disable-gpu --window-size=1500,2600 --virtual-time-budget=20000 --screenshot=/tmp/p.png http://127.0.0.1:8770/dashboard.html`.

## 7. Verifica dell'ambiente

```bash
scripts/verifica_ambiente.sh
```
Controlla: `uv`, systemd, porta 7496 in ascolto, **compilazione di tutti gli script**, **test offline** (`tests/test_offline.py`, mercato simulato: nessun TWS e nessuna rete) e la **connessione a TWS** con lettura dei prezzi (`check_connection.py`). I test offline si possono lanciare da soli anche senza TWS: `uv run python tests/test_offline.py` (circa 25 secondi).

Controlli da fare **a mano a mercato aperto** (con TWS e abbonamenti attivi), perche' i test offline non li coprono:
1. `uv run opzioni/check_connection.py --port 7496` legge SPX, XSP, VIX.
2. Una ricerca per un ticker dalla pagina produce righe e mostra EV, 5° percentile, P/L a -20%.
3. La dashboard mostra le posizioni mappate e, sopra la tabella, la concentrazione per (sottostante, mese).
4. Lo screener del forward factor e i box spread restituiscono righe (richiedono greche live).
5. Le **weekly** (piu' classi di negoziazione per scadenza) e le **date degli utili** (da Yahoo) non sono coperte dai test.

## 8. Mappa del codice (cartella `opzioni/`)

| File | Scopo | Ordini? |
|---|---|---|
| `ib_common.py` | connessione (`readonly=True`), percorsi, utilita' | no |
| `deals.py` | nome della struttura, max profitto/perdita, pareggi, P/L a livello, probabilita' di utile (log-normale) | no |
| `stat_ev.py` | **valore atteso statistico**: previsione HAR, bootstrap storico, `EvLN` (valore atteso e volatilita' di pareggio vettoriali) | no |
| `scan_vrp.py` | **motore di costruzione** delle strutture e scansione a riga di comando (ESTX50/XSP); contiene `analizza()` usata da tutte le ricerche | no |
| `scan_ticker.py` | ricerca per ticker a flusso (processo lanciato dal server) | no |
| `scan_ff.py` | screener del forward factor su molti ticker | no |
| `scan_box.py` | box spread su SPX/XSP (rendimento implicito della liquidita') | no |
| `occ_render.py` | filtri, ordinamento e HTML delle schede di occasione | no |
| `dashboard.py` | genera la dashboard e lo stato del portafoglio | no |
| `actions.py` | **azioni migliorative** per ogni posizione | no |
| `transform.py` | trasformazione dei calendar in spread senza rischio | no |
| `chain_builder.py` | server locale (porta 8770), costruttore di strategie, API | no |
| `evening.py`, `session_scan.py`, `strategies.py` | serata e scansioni su richiesta | no |
| `stress.py`, `stress_v2.py` | stress test (grezzo / migliorato, non collegato) | no |
| `calcola_beta.py` | beta dei titoli (scrive `data/betas.json`) | no |
| `pnl_history.py`, `pnl_chart.py` | storico del P/L da Flex Query | no |
| `index_machine.py` | stagionalita di un sottostante (ricostruzione di uno strumento pubblico) | no |
| `collar.py`, `pmcc.py`, `double_calendar.py`, `double_diagonal.py`, `intraday_rv.py`, `leap_*.py`, `stage2_*.py`, `collect_chain.py` | studi e strumenti di ricerca | no |
| `check_connection.py`, `check_fills.py`, `check_position.py`, `portfolio_report.py`, `mappa_da_posizioni.py` | verifiche e riepiloghi | no |
| `massive_probe.py` | sonda per un servizio dati a pagamento (facoltativo) | no |
| `send_order_TEMPLATE.py` | **modello** per l'invio di un ordine: da copiare e compilare | **si'** |

### Convenzioni del codice
- Tutto e' **sola lettura** salvo i derivati di `send_order_TEMPLATE.py`.
- Il **moltiplicatore** si legge sempre da `contract.multiplier` (ESTX50 = 10, USA = 100).
- Prezzi **eseguibili** (si vende al bid, si compra all'ask) per ogni valutazione di convenienza.
- Gli importi mostrati in dashboard sono in **EUR**; il limite d'ordine nelle istruzioni copiabili resta nella valuta del contratto.
- Il **cuscinetto** di una corta si misura **con il segno**.

## 9. Problemi noti e trappole (ognuno e' successo)

| Sintomo | Causa | Rimedio |
|---|---|---|
| `TimeoutError` alla connessione, `Error 504` | TWS chiuso, non loggato o API disattivata | riaprire TWS; i servizi riprovano da soli |
| `Error 354 / 10090: market data not subscribed` | manca l'abbonamento in tempo reale per quello strumento (es. XND, NDX) | attivare l'abbonamento; **non** usare dati ritardati per decidere (prezzi non eseguibili) |
| Nessuna riga dallo scanner a mercato chiuso | con dati congelati (`reqMarketDataType` 2) TWS non da' le greche (IV, delta) | provare a mercato aperto; per il solo codice usare `tests/` |
| ESTX50 senza quotazioni | Eurex chiude alle 17:30 (ora italiana) | normale |
| Il servizio `opzioni-dashboard.service` non riparte con `systemctl start` | un `oneshot` gia' attivo ignora un secondo `start` (la pagina resta quella vecchia) | aspettare la fine (`systemctl --user is-active`), poi riavviare |
| `pkill -f nome` uccide la propria shell | `-f` confronta tutta la riga di comando, che contiene il nome | usare il PID, o un pattern fra parentesi `[n]ome` in un comando separato |
| `uv run` stampa `VIRTUAL_ENV ... does not match` | variabile d'ambiente di un altro ambiente attivo | ignorabile, oppure `deactivate` |
| Dashboard con importi in dollari | pagina non ricaricata o `eur_testo` non applicato | rigenerare; gli importi in USD restano solo nei limiti d'ordine |
| "Posizioni non mappate" | ordine eseguito ma `portfolio_map.json` non aggiornato | aggiungere il gruppo, rigenerare |
| Picco del drawdown sbagliato | versamento/prelievo in `nav_history.csv` | correggere a mano la riga del picco |
| `whatIfOrder` restituisce 0 e commissione 1.79e+308 sui combo | comportamento di TWS sui BAG | ragionare sulla copertura della struttura, non fidarsi del numero |
| Combo a credito non accettato | il prezzo di un credito su un pacchetto BUY e' **negativo** | vedi `send_order_TEMPLATE.py` |
| Ordine "modificato" che raddoppia la posizione | su IBKR Desktop una modifica puo' creare un ordine parallelo | verificare che il precedente risulti **cancellato** |
| Strike dispari XSP con open interest sottile | liquidita' | controllare l'OI (filtro >= 100) prima di ordinare |

## 10. Cosa ricostruire a mano e come (dati NON nel repository)

1. **Mappa del portafoglio**: le posizioni sono su IBKR, il raggruppamento in strutture no. `uv run opzioni/mappa_da_posizioni.py` scrive `opzioni/data/portfolio_map.draft.json` (un gruppo per sottostante e scadenza, da riunire). Riunire le gambe in strutture insieme all'utente, dare i nomi, usare `qty` per le gambe condivise, ridenominare in `portfolio_map.json`, rigenerare la dashboard e arrivare a **zero posizioni non mappate**.
2. **Picco del NAV** (`nav_history.csv`): se non si ripristina, riparte da oggi (il picco del drawdown si azzera). Per mantenerlo, copiare il file dal vecchio computer (canale privato).
3. **Regole di prezzo** (`price_rules.json`): le decide l'utente.
4. **Storico del P/L** (`pnl_daily.csv`): si ricostruisce con la Flex Query (`~/.config/opzioni/flex.env`) e `pnl_history.py`, oppure si copia.
5. **Registro dei trade e note personali** (Journal, appunti): non appartengono al repository. Se l'utente usa un vault di note, ricrearlo a parte; `knowledge/` e' pensato per esserci copiato dentro come area "Info per Claude" e "Didattica".
6. **Beta dei titoli**: `uv run opzioni/calcola_beta.py AAPL MSFT` (default: i titoli presenti nella mappa).
7. **Posizioni aperte da riconoscere**: dopo la mappa, confrontare gruppi e conto con `uv run opzioni/portfolio_report.py`.

Se si **migra** da un vecchio computer, copiare **fuori da GitHub** (chiavetta cifrata, `scp`, rsync su rete fidata): `opzioni/data/portfolio_map.json`, `price_rules.json`, `nav_history.csv`, `pnl_daily.csv`, `betas.json`, e `~/.config/opzioni/flex.env`.

## 11. Regole di condotta di Claude (sintesi; il testo completo e' in `CLAUDE.md` e `knowledge/INIT.md`)

- **Mai inviare ordini di iniziativa.** Solo su "esegui su tws" riferito a un'operazione precisa; dopo l'invio: verifica, mappa, ricarica la dashboard.
- **Niente stop loss**: le perdite si gestiscono con aggiustamenti. Il dimensionamento (perdita massima <= 2% NAV per trade) e' l'unico controllo del rischio.
- **Dire le deroghe prima di eseguire**; le regole del Piano sono indicazioni, non vincoli.
- **Rischio non definito escluso**; scadenze mensili di default.
- **Non pubblicare dati privati**: `opzioni/data/`, `logs/`, `*.env` sono in `.gitignore`; prima di ogni commit controllare che non ci siano posizioni, saldi, token o numeri di conto. Non fare commit o push senza richiesta esplicita.
- Italiano, niente emoji nelle note; spiegare il perche' e verificare la comprensione (ruolo di mentore).

## 12. Checklist finale

- [ ] TWS installato, loggato, API attiva sulla 7496, "Trusted IPs" = 127.0.0.1
- [ ] `scripts/install.sh` eseguito senza errori
- [ ] `scripts/verifica_ambiente.sh` -> TUTTO OK
- [ ] `opzioni/data/portfolio_map.json` ricostruita; "Posizioni non mappate" vuoto
- [ ] servizi abilitati (`systemctl --user status opzioni-chain-builder.service`)
- [ ] `http://127.0.0.1:8770/dashboard.html` mostra le posizioni
- [ ] una ricerca di prova (es. `XSP`) produce righe a mercato aperto
- [ ] letti `CLAUDE.md`, `knowledge/INIT.md`, `knowledge/PIANO.md`
