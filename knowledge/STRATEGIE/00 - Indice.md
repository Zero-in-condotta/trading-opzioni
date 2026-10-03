# Indice — Strategie di Trading con Opzioni

Guida completa a 35 moduli su opzioni: le 5 greche, 25 strategie (dalla più semplice alla più esotica) e 5 tecniche di aggiustamento/gestione della posizione. Ogni file è autosufficiente, con grafico payoff, esempio numerico, casi d'uso, pericoli e capitale richiesto — puoi aprirli in qualunque ordine, ma se non hai mai visto un'opzione in vita tua, l'ordine numerico qui sotto è pensato per essere letto in sequenza almeno la prima volta.

Non ripete da zero quello che c'è già in [Manuale](../MANUALE.md) (la filosofia generale del progetto, il core, le protezioni) — qui trovi il dettaglio pratico di ogni singola struttura, con più esempi e più profondità di quanta il Manuale ne dedichi a ciascuna. Per le greche di base c'è anche [Glossario — Delta](../GLOSSARIO.md), linkato dal file 01 qui sotto.

## 1. Le Greche — il linguaggio con cui si legge un'opzione

| # | File | Cosa misura |
|---|---|---|
| 01 | [01 - Delta](01%20-%20Delta.md) | sensibilità al prezzo del sottostante / probabilità implicita di ITM |
| 02 | [02 - Gamma](02%20-%20Gamma.md) | quanto velocemente cambia il Delta |
| 03 | [03 - Theta](03%20-%20Theta.md) | decadimento temporale giornaliero |
| 04 | [04 - Vega](04%20-%20Vega.md) | sensibilità alla volatilità implicita |
| 05 | [05 - Rho](05%20-%20Rho.md) | sensibilità ai tassi d'interesse |

## 2. Strategie direzionali semplici — una gamba sola

| # | File | View | Rischio |
|---|---|---|---|
| 06 | [06 - Long Call](06%20-%20Long%20Call.md) | rialzista | limitato al premio |
| 07 | [07 - Long Put](07%20-%20Long%20Put.md) | ribassista | limitato al premio |
| 08 | [08 - Short Call (Naked Call)](08%20-%20Short%20Call%20%28Naked%20Call%29.md) | ribassista/neutrale | **illimitato** |
| 09 | [09 - Short Put (Naked Put)](09%20-%20Short%20Put%20%28Naked%20Put%29.md) | rialzista/neutrale | ampio, fino a strike×100 |

## 3. Reddito e copertura di portafoglio

| # | File | Idea centrale |
|---|---|---|
| 10 | [10 - Covered Call](10%20-%20Covered%20Call.md) | affitti le azioni che possiedi per un premio |
| 11 | [11 - Cash-Secured Put (CSP)](11%20-%20Cash-Secured%20Put%20%28CSP%29.md) | vieni pagato per aspettare di comprare a sconto |
| 12 | [12 - Poor Man's Covered Call (PMCC)](12%20-%20Poor%20Man's%20Covered%20Call%20%28PMCC%29.md) | Covered Call con molto meno capitale, via LEAPS |
| 13 | [13 - Protective Put (Married Put)](13%20-%20Protective%20Put%20%28Married%20Put%29.md) | assicurazione diretta su azioni possedute |
| 14 | [14 - Collar](14%20-%20Collar.md) | assicurazione quasi gratuita, in cambio del rialzo |

## 4. Spread verticali — due gambe, rischio definito

| # | File | Tipo | View |
|---|---|---|---|
| 15 | [15 - Bull Call Spread](15%20-%20Bull%20Call%20Spread.md) | debit | rialzista |
| 16 | [16 - Bear Put Spread](16%20-%20Bear%20Put%20Spread.md) | debit | ribassista |
| 17 | [17 - Bull Put Spread](17%20-%20Bull%20Put%20Spread.md) | credit | rialzista/neutrale — **il core del piano dell'utente** |
| 18 | [18 - Bear Call Spread](18%20-%20Bear%20Call%20Spread.md) | credit | ribassista/neutrale — **il core del piano dell'utente** |

## 5. Strategie di volatilità e neutrali

| # | File | View | Rischio |
|---|---|---|---|
| 19 | [19 - Long Straddle](19%20-%20Long%20Straddle.md) | volatilità in salita, direzione ignota | limitato al premio |
| 20 | [20 - Short Straddle](20%20-%20Short%20Straddle.md) | mercato fermo | **ampio/illimitato** |
| 21 | [21 - Long Strangle](21%20-%20Long%20Strangle.md) | volatilità in salita, più economico dello straddle | limitato al premio |
| 22 | [22 - Short Strangle](22%20-%20Short%20Strangle.md) | mercato in un range | **ampio/illimitato** |
| 23 | [23 - Iron Condor](23%20-%20Iron%20Condor.md) | lateralità, rischio definito | definito |
| 24 | [24 - Iron Butterfly](24%20-%20Iron%20Butterfly.md) | lateralità stretta, credito più alto | definito |

## 6. Spread avanzati e asimmetrici

| # | File | Nota |
|---|---|---|
| 25 | [25 - Butterfly Spread](25%20-%20Butterfly%20Spread.md) | come l'Iron Butterfly ma con debit invece di credit |
| 26 | [26 - Condor Spread](26%20-%20Condor%20Spread.md) | come la Butterfly ma con zona di profitto più ampia |
| 27 | [27 - Ratio Call Spread](27%20-%20Ratio%20Call%20Spread.md) | direzionale rialzista moderato, una gamba scoperta |
| 28 | [28 - Ratio Put Spread](28%20-%20Ratio%20Put%20Spread.md) | direzionale ribassista moderato, una gamba scoperta |
| 29 | [29 - Calendar Spread](29%20-%20Calendar%20Spread.md) | sfrutta il differente decadimento tra scadenze — **aperto oggi nel progetto** |
| 30 | [30 - Diagonal Spread](30%20-%20Diagonal%20Spread.md) | come il Calendar ma con strike diversi |
| 35 | [35 - ZEBRA](35%20-%20ZEBRA.md) | replica ~100 azioni sintetiche con poco capitale e quasi zero valore estrinseco — rischio definito, a differenza del Ratio Call Spread |

## 7. Tecniche di aggiustamento e gestione

| # | File | Quando si usa |
|---|---|---|
| 31 | [31 - Rolling](31%20-%20Rolling.md) | spostare scadenza e/o strike di una posizione minacciata |
| 32 | [32 - Inversione e Trasformazione](32%20-%20Inversione%20e%20Trasformazione.md) | trasformare rischio illimitato in rischio definito al volo |
| 33 | [33 - Delta Hedging](33%20-%20Delta%20Hedging.md) | neutralizzare il Delta di portafoglio con il sottostante |
| 34 | [34 - Take Profit e Stop Loss Meccanico](34%20-%20Take%20Profit%20e%20Stop%20Loss%20Meccanico.md) | le due regole meccaniche di uscita — la base di tutto |

## Matrice di confronto rapido

| Strategia | Prospettiva | Rischio | Impatto Theta | Complessità |
|---|---|---|---|---|
| Covered Call / CSP | Moderatamente rialzista | Moderato / elevato | Favorevole (+) | Bassa |
| Credit Spread (Bull Put / Bear Call) | Direzionale / neutrale | Definito | Favorevole (+) | Media |
| Iron Condor / Iron Butterfly | Neutrale (lateralità) | Definito | Molto favorevole (+) | Media |
| Calendar / Diagonal | Neutrale / direzionale | Definito | Favorevole (+) | Alta |
| Long Straddle / Strangle | Alta volatilità attesa | Limitato al premio | Sfavorevole (−) | Media |
| Short Straddle / Strangle (nudi) | Bassa volatilità attesa | **Ampio/illimitato** | Molto favorevole (+) | Media-alta |
| Aggiustamento (Rolling, Inversione) | Gestione del rischio | Variabile | Dipende dalla manovra | Alta |

## Come si collega al progetto reale

Il Piano ([Piano](../PIANO.md)) usa attivamente solo un sottoinsieme di questi 34 moduli: [17 - Bull Put Spread](17%20-%20Bull%20Put%20Spread.md) e [18 - Bear Call Spread](18%20-%20Bear%20Call%20Spread.md) come core (bucket A), [07 - Long Put](07%20-%20Long%20Put.md) come hedge di coda (bucket C), [29 - Calendar Spread](29%20-%20Calendar%20Spread.md) come satellite in regime di IV compressa (bucket B), e le regole di [34 - Take Profit e Stop Loss Meccanico](34%20-%20Take%20Profit%20e%20Stop%20Loss%20Meccanico.md) come disciplina di uscita su tutto. Gli altri 29 moduli non sono "inutili" — sono il resto della mappa: utili per capire il perché delle scelte fatte, riconoscere alternative quando le condizioni di mercato cambiano, e avere un vocabolario comune per parlare di opzioni in generale, dentro o fuori da questo progetto specifico.

## Nota sui numeri usati negli esempi

Ogni file dichiara esplicitamente quando i numeri (strike, premi, prezzi) sono un esempio didattico arrotondato e non una quotazione reale verificata — è la norma in questa cartella, a differenza delle operazioni reali del progetto (in `Journal/` e `Primo Trade.md`), dove vale invece la regola di accuratezza numerica non negoziabile di [Init](../INIT.md).
