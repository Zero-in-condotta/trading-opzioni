# Trading in opzioni su Interactive Brokers - strumenti e conoscenza

Strumenti in **sola lettura** (piu' un modello per gli ordini) per gestire un portafoglio di opzioni su indici e titoli tramite l'API locale di Trader Workstation (TWS), e la conoscenza che li accompagna.

- **Dashboard** del portafoglio: P/L, greche, pareggi, concentrazione, posizioni raggruppate per mese di scadenza, tutto in EUR.
- **Ricerca di occasioni** per ticker a flusso (indici, mini-indici, azioni, ETF): costruisce verticali, iron condor, calendar, double calendar/diagonal, broken wing butterfly, backspread e altro, e li valuta con un **valore atteso statistico** (previsione di volatilita' HAR + bootstrap della storia reale, crisi incluse) a **prezzi eseguibili**. Screener del forward factor e box spread.
- **Azioni migliorative** per ogni posizione: trasformazione dei calendar senza rischio, presa di profitto, regola dei 21 DTE, roll a credito, rischio di assegnazione anticipata, protezioni di coda.
- **Costruttore di strategie** con payoff, greche e Monte Carlo sulla catena reale.
- **Conoscenza** (`knowledge/`): piano di rischio, metodi di valutazione e di aggiustamento, manuale, glossario e 35 moduli sulle strategie.

> **Non e' consulenza finanziaria.** Le opzioni possono perdere in fretta piu' di quanto prevedano i modelli. Il codice non invia ordini, salvo il modello `opzioni/send_order_TEMPLATE.py` che va compilato ed eseguito esplicitamente.

## Avvio rapido

Prerequisiti: Linux con systemd, `uv`, TWS installato e con l'API attiva (porta 7496). Dettagli completi in **[docs/RICOSTRUZIONE.md](docs/RICOSTRUZIONE.md)**.

```bash
git clone <URL> ~/CODE/trading-opzioni && cd ~/CODE/trading-opzioni
scripts/install.sh --abilita
scripts/verifica_ambiente.sh
xdg-open http://127.0.0.1:8770/dashboard.html
```

Senza TWS si possono comunque provare i calcoli: `uv run python tests/test_offline.py`.

## Struttura

| Cartella | Contenuto |
|---|---|
| `opzioni/` | il codice (script, dashboard, server locale) |
| `knowledge/` | regole, metodi, didattica |
| `docs/` | documentazione, in particolare la guida di ricostruzione |
| `config.example/` | modelli dei file di configurazione (la configurazione vera sta in `opzioni/data/`, non versionata) |
| `systemd/` | servizi e timer (modelli) |
| `scripts/` | installazione e verifica |
| `tests/` | test offline con un mercato simulato |

## Dati privati

`opzioni/data/`, `opzioni/logs/` e i file `*.env` sono ignorati da git: contengono posizioni, saldi e token e **non vanno mai pubblicati**. Se lavori con Claude Code, `CLAUDE.md` contiene le regole di condotta.

## Per Claude Code

Leggi `CLAUDE.md`, poi `docs/RICOSTRUZIONE.md` e `knowledge/INIT.md`.
