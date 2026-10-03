# Metodi operativi

> I metodi con cui Claude e l'utente valutano un trade e gestiscono le posizioni: calcolo dell'edge, aggiustamenti, trasformazione dei calendar senza rischio, valutazione della copertura. Sono qui accorpati perché si usano insieme; ognuno è una sezione autonoma. Per le **regole** vale il [Piano](PIANO.md); per la spiegazione didattica dei criteri dello scanner la [Guida](GUIDA_SCANNER.md). Lo storico degli stress test reali è nei Logs.

## Indice

- [[#Come si calcola se un trade ha edge]]
- [[#Aggiustamenti - competenze dai video]]
- [[#DC Time Machine - trasformazione dei calendar]]
- [[#Valutazione della copertura e del rischio]]

## Come si calcola se un trade ha edge

> Documento del 21/09/2026, richiesto dall'utente. Spiega il metodo che ho usato per dare i verdetti "edge marginale, non entrare" sul condor XSP e SPX del 21/09. Esempio numerico: catene reali lette dall'API di IBKR (sola lettura) il 21/09/2026 alle 17:46, scadenza 23/10/2026. Contesto: Studio statistico, Guida VRP, sintesi di Bennett. Codice: `~/CODE/opzioni/` (`collect_chain.py`). **Non è consulenza finanziaria: i numeri sono stime da un modello semplificato.**

### 0. Che cosa vuol dire "edge"
**Edge = valore atteso positivo, dopo i costi, rispetto al rischio che si prende.** Cioè: se ripetessi lo stesso trade molte volte alle stesse condizioni, la media dei risultati sarebbe positiva.

Tre cose che NON bastano da sole:
- **La probabilità di vincere.** Un condor con 85% di probabilità di profitto può avere valore atteso negativo, perché il 15% delle volte perde molto più di quanto guadagna nell'85%.
- **Un backtest positivo** su un periodo corto o su un solo indice.
- **"Il premio è alto"**: un premio alto spesso compensa un rischio reale.

### 1. Il metodo in otto passi

#### Passo 1. Prendere i prezzi veri (non quelli teorici)
Da TWS via API, per ogni gamba: **bid, ask, IV, delta, open interest, volume**. Poi per la struttura tre prezzi:
- **Credito naturale** = quello che incassi se accetti il prezzo peggiore (vendi al bid, compri all'ask).
- **Credito medio** = a metà tra i due.
- **Limite ragionevole** = naturale + 40% della differenza con il medio. È un'ipotesi mia sul prezzo a cui si viene realmente eseguiti (con spread larghi il medio non si ottiene quasi mai).

Esempio SPX (ali 50): naturale **8,20**, medio **8,75**, limite **8,42** punti (842 $ per contratto).

#### Passo 2. Descrivere la struttura
| Grandezza | Formula | Esempio SPX 7300/7350 + 8050/8100 |
|---|---|---|
| Guadagno massimo | credito × 100 | **842 $** |
| Perdita massima | (larghezza − credito) × 100 | (50 − 8,42) × 100 = **4.158 $** |
| Breakeven basso | strike put corta − credito | 7350 − 8,42 = **7.341,6** |
| Breakeven alto | strike call corta + credito | 8050 + 8,42 = **8.058,4** |
| Credito/larghezza | credito ÷ larghezza | **17%** |

!edge-payoff-condor-spx.png

**Rischio/rendimento:** rischio 4.158 contro guadagno 842: servono almeno **83% di probabilità di profitto** per pareggiare (4.158 ÷ (4.158 + 842) = 0,83, semplificando).

#### Passo 3. Scegliere la volatilità con cui simulare
Qui sta la scelta più importante. Ci sono due volatilità:
- **IV (implicita):** quella che il mercato prezza oggi (VIX 14,75%; la IV delle singole gambe è tra 10,9% e 16,5%).
- **RV (realizzata) attesa:** quella che pensi si realizzerà davvero nei 32 giorni.

**Per calcolare l'edge si usa la RV attesa, non l'IV**: se il mercato fosse esatto, la IV darebbe valore atteso zero per definizione. Il vantaggio del venditore, se c'è, è la differenza tra le due (la VRP).

**Come stimo la RV attesa (regola pratica mia, non una legge):**
1. Calcolo la RV a 10 e a 20 giorni dai rendimenti giornalieri: RV = deviazione standard × √252. Il 21/09: **RV10 = 11,1%, RV20 = 9,8%**.
2. Prendo il valore più alto e ci aggiungo 1-2 punti di prudenza, perché la volatilità sale più in fretta di quanto scende.
3. Confronto con la storia: negli ultimi 26 anni la RV mensile media è circa 15-16% (IV media circa 19,8%, VIX − RV circa 3,5 punti).

#### Passo 4. Simulare i prezzi a scadenza
Assumo che il prezzo a scadenza sia **log-normale**: 

`S_T = S × exp(−0,5·σ²·T + σ·√T·Z)`, con Z estratto da una normale standard, T = 32/365, σ = la volatilità scelta al passo 3. Estraggo un milione di valori di Z.

#### Passo 5. Calcolare il valore atteso
Per ogni prezzo simulato calcolo il profitto del condor a scadenza: `credito − perdita_put_spread − perdita_call_spread`. Il **valore atteso** è la media su tutti i prezzi. In % del rischio: valore atteso ÷ perdita massima.

Risultato (limite 8,42, SPX ali 50):
| Volatilità reale | Valore atteso per contratto | In % del rischio |
|---|---|---|
| 10% | **+306 $** | +7,4% |
| 11% | +135 $ | +3,2% |
| 13% | **−212 $** | −5,1% |
| 14,75% (IV di oggi) | −510 $ | −12,3% |

#### Passo 6. Trovare la RV di pareggio
È la volatilità reale alla quale il valore atteso è zero. Si legge dal grafico (o si cerca il punto in cui la curva incrocia lo zero):

!edge-valore-atteso-contro-volatilita.png

- **SPX ali 50: 11,8%**; **XSP ali 8: 11,2%**.
- **La regola di ingresso che propongo:** entrare solo se **RV di pareggio − RV attesa ≥ 2 punti**. Oggi: 11,8 − circa 11 = 0,8 punti. **Margine insufficiente.**

#### Passo 7. Guardare da dove viene il premio (IV per lato, skew)
Uso l'IV di ciascuna gamba corta:
- **Put a 7375:** IV **16,5%** contro RV 10-11%: qui c'è VRP (5-6 punti).
- **Call a 8050:** IV **10,9%** contro RV 10-11%: **quasi nessuna VRP**.

Vendere il lato call espone al rialzo senza essere pagati: è la ragione per cui il credito reale del condor è circa il **55%** di quello teorico dello studio.

Poi la **liquidità:** spread di ogni gamba in % del prezzo medio (SPX 1-3%, XSP 4-9%) e **open interest** (Piano: almeno 500; SPX 244-690, XSP 3-118). Se le gambe non sono liquide, il prezzo che vedi non è quello a cui ti eseguono e l'edge sparisce.

#### Passo 8. Dimensione e decisione
- **Perdita massima in % del NAV:** confrontarla con il limite di 1-2% per trade; se la struttura è troppo grande, restringere le ali o aspettare che il NAV cresca.
- **Decisione:** edge positivo solo se **valore atteso > 0 con RV attesa prudente**, **margine di almeno 2 punti** sulla RV di pareggio, liquidità sufficiente e dimensione nei limiti. Altrimenti si aspetta.

### 2. Cosa guardo ogni volta (checklist rapida)
1. Credito naturale, medio, limite → credito/larghezza.
2. Perdita massima, guadagno massimo, breakeven.
3. RV10, RV20 e VIX (la RV attesa è la più prudente + 1-2).
4. Valore atteso a RV 10/11/13/IV.
5. **RV di pareggio** e margine con la RV attesa.
6. IV di ciascuna gamba corta contro RV (dove è il premio).
7. Spread % e open interest delle quattro gambe.
8. Perdita massima in % del NAV.
9. Eventi in finestra (utili, dati macro, FOMC): un evento alza la RV attesa.

### 3. Come si calcola in pratica (codice)
La catena si raccoglie con `~/CODE/opzioni/collect_chain.py` (sola lettura, TWS con API attiva). La simulazione è questa:

```python
import numpy as np
from math import sqrt
S, T = 7739.49, 32/365
z = np.random.default_rng(3).standard_normal(1_000_000)
def valore_atteso(sig, credito, kp, wp, kc, wc):
    F = S*np.exp(-0.5*sig*sig*T + sig*sqrt(T)*z)
    perdita = (np.maximum(kp-F,0) - np.maximum(kp-wp-F,0)
             + np.maximum(F-kc,0) - np.maximum(F-kc-wc,0))
    return (credito - perdita).mean()*100      # dollari per contratto (SPX x100)
# esempio: valore_atteso(0.11, 8.42, 7350, 50, 8050, 50)
```

### 4. Limiti del metodo (da non dimenticare)
1. **Log-normale:** i mercati hanno code più grosse e i crolli arrivano a salti. Il modello sottostima i mesi peggiori.
2. **Drift zero:** ignora che l'indice sale in media (aiuta i lati put, danneggia i lati call).
3. **La RV attesa è un'opinione:** tutto il risultato dipende da quel numero. Per questo si guarda la **curva** e il **margine di sicurezza**, non un singolo valore.
4. **Tenuta a scadenza:** non si sono modellati take profit, stop e chiusura a 21 DTE.
5. **Prezzi a un istante:** IV, spread e credito cambiano durante la giornata.
6. **Il credito è quello che vedi, non quello che ottieni:** per questo uso il limite ragionevole, non il medio.
7. **Campione storico corto e un solo indice** per le statistiche di lungo periodo dello studio.

### 6. Lettura di un numero alla volta (per l'utente)
Da chiedere a Claude quando serve, un passo per volta, con conferma a ogni passo (richiesta del 21/09/2026): partire dal Passo 1 con la catena del giorno.

**Spiegazione interattiva fatta il 22/09/2026** (passo per passo, con esempi e conferma a ogni passo). Nella stessa sessione, un'osservazione dell'utente sui limiti della gestione attiva ha portato a estendere il metodo allo 0DTE: vedi 0DTE su indici - metodo e primo test.

---

## Aggiustamenti - competenze dai video

Sintesi con parole nostre delle competenze operative sugli aggiustamenti, ricavate dai video indicati dall'utente. **Niente trascrizioni**: solo il metodo, riorganizzato e confrontato con il nostro [Piano](PIANO.md). Le tecniche di base restano descritte nei moduli 31 Rolling, 32 Inversione e 33 Delta Hedging; qui c'e' il **quando** e il **quanto**, che li' mancava.

### Fonti

| Video | Durata | Cosa aggiunge |
|---|---|---|
| [Importance of Adjusting & Hedging Option Trades](https://youtu.be/-YqNM_881D8) | 7:13 | la gerarchia: ingresso prima di aggiustamento |
| [Knowing When to Adjust an Options Trade or NOT](https://youtu.be/3QH53ZxDw9E) | 28:42 | i quattro trigger, e quale scegliere |
| [The ULTIMATE Guide to Options Trade Adjustments](https://youtu.be/21lh5KK1lrk) | 41:02 | le quattro regole + 5 casi reali su rischio indefinito |
| [Complete Guide to Adjusting Credit Spreads, Iron Condors & Calendar Spreads](https://youtu.be/tbOPDiZfRPU) | 32:28 | **il piu' rilevante per noi**: rischio definito, 4 casi reali |
| [How to Correctly Adjust Strangles That Go Bad](https://youtu.be/u82-In4ZPyU) | 9:49 | perche' NON si muove il lato in perdita |
| [Short Straddle - How To Make Adjustments](https://youtu.be/n4gbdErXkCM) | 9:23 | inversione degli strike e suo pavimento |
| [Risk Free Butterfly Option Strategy Adjustment? YEP!](https://youtu.be/HV_AEyxxGoA) | 7:45 | lo stesso principio della DC Time Machine |
| [The Best Options Adjustment Strategies (TradeOptionsWithMe)](https://youtu.be/vamqjd1cEfU) | 13:36 | **non utilizzabile**, vedi nota sotto |

Sette video su otto sono di Option Alpha (Kirk Du Plessis), quindi questa e' in sostanza **una scuola sola**, non un consenso di mercato: va pesata come tale. L'ottavo, di TradeOptionsWithMe, e' **in tedesco** e sia i sottotitoli automatici tradotti che quelli originali sono risultati incomprensibili (frasi senza senso tipo "the sit and wait monastery position"): non ne e' stato estratto nulla, invece di inventare. Se ti interessa quel contenuto, serve guardarlo tu e raccontarmelo.

---

### 1. La gerarchia: aggiustare vale meno che entrare bene

La tesi piu' importante di tutto il materiale, ed e' una doccia fredda sull'entusiasmo per gli aggiustamenti:

> Entrare correttamente in un trade e' **cento volte piu' importante** che aggiustarlo correttamente. Non si aggiusta un trade che non andava aperto.

Il conto con cui lo dimostra, su uno spread a credito con 80% di probabilita':

|                   | Vincita (80%) | Perdita (20%) | Risultato su 10 trade                 |
| ----------------- | ------------- | ------------- | ------------------------------------- |
| **Prezzato bene** | +100          | −400          | zero sulla carta, positivo in pratica |
| **Prezzato male** | +75           | −425          | **−250 di partenza**                  |

Nel primo caso il risultato e' zero sulla carta, ma diventa positivo per tre motivi che si sommano: la volatilita' implicita sovrastima sistematicamente il movimento che si realizza (e' il variance risk premium su cui si basa tutto il nostro Piano), la chiusura anticipata al 50%, e **tagliare a meta' un solo perdente** basta a rendere profittevole l'intera serie.

Nel secondo caso parti con 250 di buco ogni dieci trade, e **tagliare a meta' un perdente non basta**: resti a −50. La capacita' di aggiustare non recupera un ingresso prezzato male.

**Conseguenza per noi, da prendere sul serio:** il filtro sull'edge del Piano Finale (RV di pareggio − RV attesa ≥ +2 punti, spread ≤ 10%, open interest ≥ 100) conta **piu'** del motore delle azioni migliorative. Il motore e' un moltiplicatore di un vantaggio che deve esistere prima. Il rischio concreto, adesso che la pagina propone aggiustamenti a ogni ricarica, e' di sentirsi autorizzati a entrare peggio.

---

### 2. Quando aggiustare: i quattro trigger

L'ingresso di riferimento e' a **15 delta per lato** (circa 70% di probabilita' di successo per uno strangle o un condor). Da li' i quattro trigger possibili:

| # | Trigger | Significato | Giudizio della fonte |
|---|---|---|---|
| 1 | **corta a 30 delta** | la probabilita' di perdere su quel lato e' **raddoppiata** (15% → 30%) | **preferito** |
| 2 | corta a 40 delta | piu' spazio al titolo, aggiustamento piu' tardi | valido, piu' tollerante |
| 3 | valore della posizione a 2x il credito | — | **sconsigliato**, vedi sotto |
| 4 | il prezzo sfonda la gamba **lunga** | si aspetta il piu' possibile | valido ma tardivo |

Kirk opera nella **fascia 30-40 delta**, sul delta della gamba corta. Due ragioni, entrambe buone:

- **Il delta e' direzionale.** Dice se il sottostante si sta davvero muovendo contro di te.
- **Il delta e' dipendente dal tempo.** Si muove da solo mentre la scadenza si avvicina, quindi lo stesso trigger diventa automaticamente piu' severo verso la fine del ciclo, senza dover cambiare soglia.

**Perche' il trigger "2x il credito" e' sconsigliato:** il valore della posizione sale anche solo per un aumento della volatilita' implicita, **con il sottostante fermo**. Scatterebbe un allarme senza che ci sia niente da aggiustare. In piu' non e' automatizzabile facilmente.

> **Nota per noi:** siamo arrivati alla stessa conclusione da un'altra strada. Lo stop a 2x il credito e' stato **abolito il 28/09/2026** per la regola "niente stop loss" ([Init](INIT.md) punto 20), non perche' fosse un cattivo indicatore. Due ragionamenti diversi, stessa conclusione: il 2x non va usato come innesco.

#### Le due trappole opposte

- **Aggiustare troppo.** L'analogia e' la medicina: ogni farmaco ha senso da solo, ma insieme si scontrano e il paziente peggiora. Allo stesso modo ogni aggiustamento puo' essere sensato sulla singola posizione e il risultato collettivo essere un disastro di portafoglio.
- **Paralisi da analisi.** Studiare ogni scenario e finire per non fare nulla.

La posizione dichiarata e' **sempre piu' lenti del necessario**, perche' nel lungo periodo l'edge della vendita di premio resta a favore anche senza aggiustare nulla.

#### Regole di buon senso sui tempi

- **Molto tempo alla scadenza:** lascia correre di piu'. Se il delta passa da 15 a 30 nel primo giorno, puo' essere un movimento di un giorno e non un trend.
- **Sotto i 15 giorni:** sii piu' proattivo, c'e' meno tempo perche' le probabilita' si riassestino.
- **Mai aggiustare troppo presto o troppo in fretta.** Puoi sempre fare un altro aggiustamento dopo.

---

### 3. Le quattro regole dell'aggiustamento

Valgono identiche su rischio definito e indefinito, e sono il cuore di tutto il materiale.

#### Regola 1: non muovere MAI il lato in perdita

La piu' controintuitiva e la piu' difesa. Se il mercato sale verso le tue call, **non tocchi le call**. Se scende verso le tue put, **non tocchi le put**.

Il perche', spiegato con i numeri: hai venduto la call a 1,00, ora vale 3,00. Chiudendola **realizzi la perdita di 2,00** e la riapri piu' lontano; se il titolo continua a salire la rivendi a 3 e la ricompri a 5, poi la vendi a 5 e la ricompri a 7. **Componi la perdita su se stessa**, scavando sempre piu' a fondo.

Quello che si fa invece: si muove il **lato non testato** verso il prezzo. Quel lato e' in guadagno (venduto a 1,00, ora vale 0,10: incassi il 90%), lo chiudi e lo riapri piu' vicino per un nuovo credito. Si ripete finche' si arriva a uno straddle.

#### Regola 2: sempre un credito netto

Se l'aggiustamento non si fa a credito, non si fa. Pagare per aggiustare aumenta il rischio della posizione invece di ridurlo.

> **Coincide con il nostro Piano** e con quello che `actions.py` gia' applica: nessun roll a debito viene proposto come azione.

#### Regola 3: mai aumentare la dimensione

Stesso numero di contratti, stessa larghezza degli strike. Se avevi 2 put e 2 call, chiudi 2 call e ne rivendi 2, non 3 o 5. Altrimenti stai aggiungendo rischio invece di ridurlo.

> Con la regola "niente stop loss" questa e' **ancora piu' vincolante per noi** che per la fonte: senza stop, la perdita massima e' la perdita davvero attesa nel caso peggiore, e il dimensionamento e' l'unico controllo che resta.

#### Regola 4: estendere la scadenza quando possibile

Rollare l'intera struttura al mese successivo, **mantenendo gli stessi strike**, se si incassa un credito. Non si cambia la forma: si compra tempo facendosi pagare.

#### Piu' due effetti che si cercano

- **Ricentrare** la struttura sopra il nuovo prezzo del sottostante, riconoscendo che il titolo si e' mosso.
- **Allargare i pareggi.** Il credito accumulato sposta il pareggio **oltre lo strike corto che non hai toccato**. E' il meccanismo per cui un trade andato al 100% contro di te puo' finire in pari.

---

### 4. Meccanica per struttura

#### Spread a credito testato → iron condor
Si vende il verticale **opposto** sulla stessa scadenza, con **stessa larghezza e stesso numero di contratti**. A scadenza solo un lato puo' essere dentro il prezzo, mai entrambi: quindi **non si aggiunge rischio**, e il nuovo credito si sottrae alla perdita massima.

> E' esattamente la **difesa laterale** che ho aggiunto al motore il 01/10/2026, con gli stessi vincoli (stessa larghezza, stesso numero di contratti). Il materiale la conferma e le da' un nome.

#### Iron condor testato
Si rolla il lato **non sfidato** verso il prezzo, piu' volte se serve. Il condor diventa piu' alto e piu' stretto.

#### Calendar testato
Si rolla **la corta del mese anteriore** nella direzione in cui il titolo si e' mosso, per un credito. Innesco suggerito: la corta anteriore ha perso il **50-75%** del suo valore. Il risultato non e' piu' un calendar ma un **diagonale** inclinato nella direzione del movimento.

#### Straddle
Non ha un trigger di delta (gli strike sono uguali): l'innesco e' **pareggio violato + molto tempo residuo**. Si rolla un lato verso il prezzo e si va **inverted** (strike della put sopra quello della call). Dettaglio tecnico importante: una volta inverted **non si puo' piu' ricomprare a zero** — il pavimento e' la larghezza dell'inversione.

#### Broken wing butterfly
Lo strike saltato lascia lo spazio per **ricomprare lo spread incorporato spendendo meno del credito incassato**: da li' la butterfly non puo' piu' perdere.

> **E' lo stesso principio della [DC Time Machine](METODI_OPERATIVI.md)** gia' nel Piano dal 24/09, e della trasformazione delle strutture a debito aggiunta il 30/09. Due scuole diverse arrivano alla stessa idea: **spendere meno del credito incassato per azzerare il caso peggiore**. E' la convalida indipendente piu' utile di tutto il materiale.

---

### 5. Il punto piu' importante per un libro a rischio definito

Un portafoglio come questo e' **quasi tutto a rischio definito**. Per quelle strutture il materiale e' esplicito:

> Con gli aggiustamenti a rischio definito l'obiettivo numero uno e' la **riduzione del rischio**, non trasformare i perdenti in vincenti. Sarai limitato nell'aggressivita' possibile.

E subito dopo:

> Se entri correttamente e non fai nulla, nel lungo periodo vinci comunque. Se riesci a tagliare costantemente i grandi perdenti **a meta' o meglio**, sei molto piu' avanti di tutti gli altri.

I tre casi reali documentati, tutti con il sottostante andato **al 100% contro**:

| Caso | Perdita senza aggiustare | Perdita dopo | Riduzione |
|---|---|---|---|
| Amazon, call credit spread → iron condor | −405 | −198 | **51%** |
| RUT, iron condor, put rollate 4 volte | −360 | −154 | **57%** |
| CMG, put calendar, corta rollata 2 volte | −470 | −141 | **70%** |

Nessuno dei tre e' diventato un vincente. **Tutti e tre sono rimasti perdenti, piu' piccoli.** E' il metro di giudizio corretto per noi: un aggiustamento riuscito non e' quello che ti fa guadagnare, e' quello che ti fa perdere la metà.

---

### 6. Dove questo materiale ci contraddice

Da leggere con attenzione: non e' tutto allineato al nostro Piano, e le differenze sono interessanti.

**a. Il nostro Piano non ha un trigger di delta.** Entriamo a 15-25 delta ma non c'e' scritto da nessuna parte a quale delta si guarda la posizione. Il motore usava la **distanza percentuale** dalla corta, che e' una approssimazione peggiore: il delta tiene dentro anche volatilita' e tempo residuo, e si inasprisce da solo vicino alla scadenza. **Modifica fatta il 02/10/2026** — vedi sezione 7.

**b. Il mio `_roll_out` propone una manovra che la fonte critica.** Fra le varianti c'e' "rolla in avanti **spostando gli strike di una larghezza**" nella direzione opposta al movimento. Su uno spread put testato significa spostare in basso la put venduta, cioe' **muovere il lato in perdita**: esattamente quello che la Regola 1 vieta, perche' realizza la perdita e la ricompone piu' in basso. Il roll **a strike fermi** sulla scadenza successiva invece e' proprio quello che la fonte fa (Regola 4), e resta valido. **Le due varianti sono ora distinte in pagina.**

**c. Il motore scegliva l'aggiustamento piu' aggressivo.** Il principio "piccoli aggiustamenti incrementali" e' ripetuto in tutti i video, con un esempio preciso: rollare subito lo spread fino al prezzo attuale crea un condor altissimo e strettissimo che **non ha piu' spazio per guadagnare**. Il mio `_alza_la_lunga` scegliva lo strike piu' alto fra quelli senza rischio, cioe' il piu' aggressivo. In un caso reale questo ha prodotto una proposta che perdeva in ogni scenario. **Corretto: ora propone il piu' piccolo aggiustamento che raggiunge l'obiettivo.**

**d. Nessuno dei nostri controlli guarda il portafoglio.** La raccomandazione e' esplicita e ripetuta: conta **piu' l'effetto sul portafoglio che sulla singola posizione**. Un aggiustamento che salva ESTX50 ma sbilancia il libro e' un aggiustamento sbagliato. `actions.py` valuta ogni gruppo in isolamento. **E' il gap piu' grosso che resta**, e non l'ho chiuso: richiederebbe un delta/vega di portafoglio pesato per beta confrontato con il tetto di 0,75. Da decidere se farlo.

**e. Gran parte del materiale sul rischio indefinito non ci riguarda.** Strangle, straddle, inversione degli strike: non abbiamo posizioni nude (le eccezioni hanno tutte l'ala). Lo tengo a verbale per completezza, non e' applicabile oggi.

**f. Una cautela sulla fonte.** Sette video su otto sono dello stesso autore, che vende formazione, e i casi di studio sono scelti da lui. I numeri delle riduzioni di perdita (51%, 57%, 70%) sono **esempi selezionati, non una statistica**. Il principio regge da solo sulla logica del payoff; le percentuali no.

---

### 7. Cosa e' stato messo nel motore

Le competenze sopra tradotte in codice, in `opzioni/actions.py`, visibili nel riquadro **Azioni migliorative** della dashboard:

1. **Trigger di delta sulla corta** accanto al cuscinetto: delta attuale, soglia 30 (raddoppio del rischio) e 40, con l'etichetta di quale e' stata superata. Sostituisce la distanza percentuale come innesco dichiarato.
2. **Verifica delle quattro regole** su ogni proposta: credito netto, dimensione invariata, lato in perdita non toccato, scadenza estesa. Una riga di esito per regola, cosi' si vede subito se la proposta rispetta il metodo o se e' una deroga.
3. **Preferenza per l'aggiustamento minimo** che raggiunge l'obiettivo, invece del piu' aggressivo.
4. **Distinzione fra le due varianti di roll**: a strike fermi (conforme alla Regola 4) e con strike spostati (contestato dalla Regola 1, segnalato come tale).
5. **Promemoria di portafoglio** in testa al riquadro, visto che il controllo automatico non c'e'.

Non e' stata toccata nessuna regola del Piano: questo materiale e' una fonte, non un'autorita'. Le regole restano quelle del [Piano](PIANO.md).

---

## DC Time Machine - trasformazione dei calendar

**Fonte:** video Theta Profits, "How Steve turns Double Calendar Spreads Into Risk-Free Iron Condors" (Steve Bernich, Navigation Trading, 07/06/2026, 48 min). Sintesi fatta dalla trascrizione, con parole nostre.
**Decisione dell'utente (24/09/2026):** applicare la trasformazione a tutti i calendar aperti quando possibile e valutare il double calendar come strategia. La regola è nel [Piano](PIANO.md).

### Il metodo del video
- **Ingresso:** double calendar su SPX (regolamento cash), put sotto e call sopra a **30-40 delta**. La corta scade la settimana dopo (6-15 giorni), la lunga **1-4 giorni** dopo; la preferita è la coppia venerdì/lunedì. Si entra quando il rapporto IV corta / IV lunga ha appena fatto un picco: il calendar guadagna quando quel rapporto rientra.
- **Trasformazione:** con un solo ordine si **vendono le lunghe** e si **comprano le ali** sulla scadenza corta, a distanza w (5 punti su SPX). Il risultato è un iron condor.
- **Regola:** credito della trasformazione ≥ **debito pagato + w**. A quel punto il caso peggiore a scadenza è ≥ 0.
- **Se non si trasforma:** a fine giornata si chiude oppure si tiene per la notte; stop mentale a −20% del debito.
- **Statistiche dichiarate** (solo 3 mesi, fornite dall'autore, che vende corsi): circa il 60% dei trade si trasforma; circa il 25% dei trasformati arriva al profitto massimo; feb-apr 2026 +66.000 $ su 106 trade.

### Cosa vuol dire davvero "senza rischio"
Trasformare equivale a **chiudere il calendar incassando l'utile e usarlo per aprire uno spread** la cui perdita massima è coperta da quell'utile. Il rischio vero sta **prima** della trasformazione, cioè nel 40% dei trade che non ci arriva. Dopo, tenere lo spread conviene solo se ha valore atteso positivo per conto suo: si scambia un utile certo (chiudere ora) con un intervallo "da poco a molto".

### La formula, senza conoscere i prezzi delle singole opzioni
credito della trasformazione = **valore del calendar** + **valore del verticale corto/ala sulla scadenza corta**

quindi si può trasformare quando  valore del calendar + valore del verticale ≥ debito + w.
Il guadagno minimo garantito è (credito − debito − w); il massimo è (credito − debito).
Esempio svolto: calendar XSP, debito 6,32, calendar a 9,00, verticale 750/745 a 2,40 → credito 11,40 ≥ 11,32 → caso peggiore +8 $, migliore +508 $, contro +268 $ chiudendo subito.

### Strumenti (sola lettura, nessun ordine)
- `~/CODE/opzioni/transform.py`: per ogni calendar e double calendar aperto calcola credito mid/eseguibile, soglia, cifra mancante, caso peggiore/migliore, "chiudere ora", e propone l'ordine esatto. Integrato nella **dashboard** (riquadro Azioni migliorative, ogni 10 minuti) tramite `actions.py` e nella **serata** (sezione e avvisi). **Dal 01/10/2026 nessuna notifica di sistema** e lo stato "pronta" richiede il credito ai **prezzi eseguibili**, non a metà spread.
- `~/CODE/opzioni/double_calendar.py`: valuta double calendar nuovi con prezzi reali (stile video, oppure `--long` per scadenze come le nostre).

### Primo test del double calendar a breve
| DC | Debito | Guadagno necessario per trasformare | Rapporto IV corta/lunga | 1 giorno, prezzo fermo | IV corta −10% | Tenuto a scadenza: EV / prob. utile | Liquidità |
|---|---|---|---|---|---|---|---|
| ESTX50 6250P/6325C, 30/09 → 02/10 | 212 € | 91 € (43%) | 0,94 / 0,93 | +5 € | +85 € | −57 € / 39% | buona |
| SPX 7635P/7730C, 30/09 → 01/10 | 951 $ | 185 $ (19%) | 0,97 | +25 $ | +762 $ | −331 $ / 31% | ottima |
| **SPX 7630P/7740C, ven 02/10 → lun 05/10** | 990 $ | 175 $ (18%) | **1,07** | **+133 $** | +1.088 $ | +484 $ / 55% | ottima |
| SPX 7620P/7750C, 06/10 → 07/10 | 714 $ | 170 $ (24%) | 0,99 | +10 $ | +1.143 $ | −271 $ / 26% | ottima |
| XSP 764P/773C, 30/09 → 01/10 | 112 $ | 38 $ (35%) | 0,98 | +3 $ | +77 $ | −49 $ / 25% | scarsa sulla lunga (OI 11-42) |

**Cosa si vede:**
1. Il motore della trasformazione è il **rapporto IV corta/lunga** che rientra, non il prezzo: un calo del 10% della IV corta vale da 4 a 6 volte il guadagno necessario. Un movimento di prezzo di 1 deviazione standard invece fa perdere. Coerente con quanto dice il video.
2. Tenuti a scadenza senza trasformare, quasi tutti hanno valore atteso negativo: la strategia regge **solo** se la trasformazione (o la chiusura in utile) avviene spesso.
3. L'unico positivo è la coppia **venerdì/lunedì** (la preferita nel video). Attenzione però: il modello conta il weekend come giorni pieni di volatilità, quindi sopravvaluta la gamba del lunedì. **Il +484 $ è probabilmente ottimistico**, serve un modello a giorni di borsa.
4. XSP è troppo illiquido sulla gamba lunga; il candidato è SPX (1 contratto circa 1.000 $, circa 0,7% del NAV).

**Prossimi passi:** rifare il test all'apertura USA; correggere il modello per il weekend; decidere un budget e una prova minima (1 contratto SPX) prima di qualunque trade.

---

## Valutazione della copertura e del rischio

> Obiettivo dichiarato: che tu possa, da solo, guardare il pannello Portfolio di IBKR e rispondere con sicurezza a "quanto sono coperto oggi" e "quanto/cosa dovrei comprare per coprirmi meglio" — senza bisogno di chiedermelo ogni volta. Questo documento non ripete la teoria (per quella vedi [Manuale](MANUALE.md) §1.4 per le greche e §3 per le protezioni) — qui c'è il **metodo applicato ai tuoi numeri reali**, passo per passo, così puoi rifarlo tu.
>
> **I numeri usati come esempio sono una fotografia reale del 17/09/2026 ore 17:09** — non sono la tua situazione di oggi. Il metodo è quello che conta: rifallo con i numeri live ogni volta che vuoi la risposta vera.

---

### 1. La domanda "quanto sono coperto" in realtà sono tre domande diverse

Prima di calcolare qualunque cosa, separa bene questi tre piani — è l'errore più comune, mescolarli:

1. **Coperto da cosa, esattamente?** Un aumento di volatilità (rischio vega) è diverso da un movimento di prezzo (rischio delta), che è diverso da un crollo violento dei due insieme (il vero scenario di crisi, Piano §5.3).
2. **Coperto quanto, in euro/percentuale NAV?** Non basta sapere "ho un hedge" — serve un numero: se succede X, perdo/guadagno Y€, cioè Z% del capitale.
3. **Coperto rispetto a cosa?** Il Piano ha limiti precisi (§5.2, §5.3) con cui confrontare il tuo numero — senza quel confronto, "quanto sono coperto" resta una domanda senza risposta operativa.

Questo documento ti fa calcolare il numero (punto 2) e confrontarlo col limite (punto 3), per ogni tipo di rischio (punto 1).

---

### 2bis. Scorciatoia scoperta il 18/09/2026: Risk Navigator lo calcola già per te

Il metodo manuale del §2 sotto (sommare Portfolio Vega per valuta dal pannello Portfolio) resta utile per un controllo al volo. Ma se hai **TWS Risk Navigator** aperto (§5), la tabella principale mostra già le colonne **Delta, Gamma, Vega, Theta** aggregate per sottostante e in totale — sia per il portafoglio reale ("Market Scenario") sia sotto uno shock ("Custom scenario"). Non serve sommare nulla a mano: è lo stesso numero, calcolato meglio e già pronto.

### 2. Il dato di partenza: leggere il Vega di portafoglio dal pannello Portfolio

Apri **Portfolio → Positions** su IBKR Desktop. Per ogni riga trovi (tra le altre) le colonne **Delta**, **Vega**, **Portfolio Delta**, **Portfolio Vega**. La distinzione conta:

- **Vega** (o Delta) da solo = la sensibilità **per singolo contratto**, il numero "puro" della greca.
- **Portfolio Vega** (o Portfolio Delta) = la sensibilità **già moltiplicata per quanti contratti hai e per il moltiplicatore del contratto** (100 per XSP, 10 per OESX). **È questo il numero da usare per i calcoli di portafoglio** — non il primo.

#### Esempio illustrativo

Si sommano le righe **per valuta**: se le quattro gambe ESTX50 valgono +35, −5, −10 e +40 il lato EUR vale +60 (EUR per punto di volatilità); se le due gambe XSP valgono +46 e +47 il lato USD vale +93. Non si sommano EUR e USD: sono mercati diversi.

---

### 3. Cosa significano questi numeri: la sorpresa che vale la pena notare

**Entrambi i lati sono Vega netto POSITIVO oggi.** Significa che — al 17/09 ore 17:09 — il tuo portafoglio nel complesso **guadagna se la volatilità sale** e **perde se scende ulteriormente**, su entrambi i mercati. Sembra controintuitivo per un piano che vende premio, ma la ragione è semplice: hai **un solo hedge grande + un calendar grande** contro **due core ancora piccoli** (le due gambe strette del bull put spread hanno vega minuscolo, −4,8 e −10,0, contro i +35 e +40 di hedge e calendar). Man mano che il bucket A cresce (più core, size maggiori), questo equilibrio si sposterà verso vega negativo — è previsto, non un errore.

**Perché questo risponde davvero a "quanto sono coperto":** oggi sei ampiamente coperto contro un'impennata di volatilità (ci guadagni, non solo non ci perdi) — il rischio reale, oggi, è l'opposto: **un'ulteriore discesa della volatilità ti costa**, esattamente quello che è successo il 17/09 quando VIX e V2TX sono crollati e le due put hedge hanno perso valore.

---

### 4. Tradurre il Vega in euro: la stima di scossa (shock test veloce)

Formula, dal Manuale §1.4:

```
Perdita/guadagno stimato ≈ Portfolio Vega totale (lato) × variazione di IV in punti
```

Il Piano usa **+10 punti VIX** come riferimento standard per il limite (§5.2: perdita ≤6% NAV). Applichiamolo ai due lati:

| Lato | Vega netto | Se IV +10 punti | Se IV −10 punti | % NAV (valore assoluto, sul movimento peggiore per te) |
|---|---|---|---|---|
| EUR (V2TX) | +59,826 | **+598 €** (guadagno) | **−598 €** (perdita) | 598 / 29.934 ≈ **2,0% NAV** |
| USD (VIX) | +93,020 | **+930 €** (guadagno) | **−930 €** (perdita) | 930 / 29.934 ≈ **3,1% NAV** |

**Come leggerlo contro il limite del Piano:** il limite (§5.2) è pensato per il caso "vega negativo, IV sale" (lo scenario che fa male a un venditore di opzioni) — oggi, con vega netto positivo su entrambi i lati, quel limite specifico **non è a rischio di essere sforato in caso di rialzo della IV** (anzi guadagni). Il rischio che *non* è coperto da quel limite, perché il Piano non lo tratta esplicitamente come simmetrico, è la **perdita da ulteriore compressione della IV** — che oggi vale circa 2-3% NAV nel caso peggiore (IV che scende ancora di 10 punti). Non è un rischio catastrofico, ma è reale ed è il costo dell'assicurazione di cui parla il Piano §7.1.

**Limite onesto di questo calcolo (già segnalato nel Manuale §1.4):** è una stima **lineare** — il vega stesso cambia mentre la IV si muove (effetto di secondo ordine, "vomma"), quindi per movimenti molto ampi (tipo lo scenario S3 di −20% indice e +40 punti VIX) questa stima diventa via via meno precisa. Per quello serve il passo successivo.

---

### 5. Il vero test: prezzo e volatilità insieme (stress test S1-S5)

Il Vega da solo non basta: in un crollo vero, prezzo e volatilità si muovono **insieme**, e il Delta (rischio di prezzo) si somma all'effetto Vega. Il Piano (§5.3, richiamato in Manuale §3.7) ha cinque scenari standard:

| Scenario | Indice | VIX | Perdita massima ammessa |
|---|---|---|---|
| S1 – Correzione | −5% | +8 punti | ≤ 6% NAV |
| S2 – Shock | −10% | +20 punti | ≤ 12% NAV |
| S3 – Crash | −20% | +40 punti | ≤ 20% NAV |
| S4 – Gap weekend | −7% apertura, nessuna uscita | +15 punti | ≤ 10% NAV |
| S5 – Rally violento | +7% | −6 punti | ≤ 5% NAV |

**Perché non li calcolo qui a mano con Delta+Vega lineari:** per uno spostamento di prezzo del 10-20% il Delta e il Vega di ogni posizione **cambiano lungo il percorso** (è la Gamma, Manuale §1.4) — una stima lineare fatta con i valori di oggi sottostimerebbe l'effetto reale, specialmente sulle gambe vendute del core. Il Piano stesso lo sa: per questo prescrive di farlo con uno strumento che ricalcola scenario per scenario, non a mano.

**Come farlo davvero tu, da solo, ogni venerdì (Piano §9, Routine Operativa §7):**
1. Apri **TWS → Risk Navigator** (non disponibile su IBKR Desktop, [Setup e Watchlist](SETUP_BROKER.md))
2. Menu **View → Custom Scenario**
3. Imposta contemporaneamente la variazione % del sottostante **e** la variazione di IV, per ciascuno dei 5 scenari sopra
4. Salva ciascuno come scenario nominato — così li richiami invece di reinserirli ogni venerdì
5. Leggi il P&L stimato per ciascuno, confrontalo con la colonna "Perdita massima ammessa"

**Questo è l'unico modo affidabile di rispondere con precisione a "quanto sono coperto in un vero crollo"** — il calcolo veloce del §4 sopra è un termometro quotidiano, il Risk Navigator del venerdì è la visita specialistica.

---

### 6. "Come mi copro meglio": la logica per scegliere QUANTO hedge comprare

Non è "compra una put e basta" — è dimensionare la protezione sul vega che vuoi effettivamente compensare.

**Il metodo:**

```
Contratti di hedge necessari ≈ Vega netto del core che vuoi compensare / Vega per contratto della put di hedge
```

**Esempio concreto (numeri di ieri):** immagina che il bucket A cresca fino ad avere un Vega netto di −150 (contro gli attuali −14,8 di oggi, sommando le due gambe core) — un core più maturo, con più posizioni. Se la put di hedge XSP ha un vega per contratto di circa 0,46 (come quella che hai), servirebbero circa 150 / 0,46 ≈ **326 "unità" di vega**, cioè bisognerebbe scalare la size dell'hedge (più contratti, o strike diversi con vega più alto) fino a coprire quella cifra — non un numero fisso di contratti scelto a caso.

**Due vincoli che si incrociano, non uno solo:**
1. **Vincolo di protezione:** quanto vega netto vuoi effettivamente compensare (di solito non tutto — il Piano non vuole mai vega a zero, §2: "il portafoglio non deve mai essere puramente short volatilità" ma nemmeno perfettamente neutro, altrimenti perdi l'edge del core)
2. **Vincolo di budget:** il costo totale dell'hedge non deve superare il **3-5% del NAV all'anno** (Piano §2) — se per coprire tutto il vega servirebbe un hedge più costoso di questo, il vincolo di budget vince, e si accetta una copertura parziale

**Quando ridurre l'hedge invece di aumentarlo — la regola di monetizzazione (Manuale §3.6):**
- Primo terzo venduto: quando l'hedge vale 3× il costo, o al trigger di Livello 2
- Secondo terzo: al Livello 3, o quando il VIX raddoppia dall'ingresso
- Ultimo terzo: tenuto come "biglietto della lotteria" fino a scadenza o al primo calo del VIX ≥15% dal picco

---

### 7. Checklist ripetibile — falla tu, ogni volta che vuoi la risposta vera

- [ ] Apri Portfolio → Positions, leggi **Portfolio Vega** (non Vega) per ogni riga
- [ ] Somma per **valuta separata** (EUR vs USD), mai insieme
- [ ] Calcola perdita/guadagno stimato = Vega netto × 10 (shock standard) per un primo termometro veloce
- [ ] Confronta col limite Piano §5.2 (≤6% NAV per +10 punti VIX) — ricorda che il limite è pensato per vega negativo, se sei vega positivo il rischio speculare è la **discesa** della IV, non la salita
- [ ] Il venerdì (o quando il portafoglio cambia molto), rifai lo stress test vero su TWS Risk Navigator per i 5 scenari — è l'unico numero affidabile per movimenti grandi
- [ ] Se vuoi ridimensionare l'hedge, parti dal vega netto del core che vuoi compensare, non da un importo scelto a sensazione — poi verifica che il costo resti dentro il budget 3-5% NAV/anno

---

### 8. Cosa questo metodo non cattura (onestà, non un dettaglio da ignorare)

- **Correlazione tra i due lati in una crisi globale reale:** oggi tratti EUR e USD come mondi separati (giustamente, per il calcolo quotidiano) — ma in una vera crisi sistemica, VIX e V2TX tendono a salire **insieme** ([Init](INIT.md), Piano §5.1 tratta gli strumenti correlati come blocco unico ai fini dei limiti). Questo documento non somma i due lati per una buona ragione tecnica, ma non dimenticare che in un vero crollo il danno/guadagno arriva su entrambi contemporaneamente.
- **Il ribasso lento senza impennata di IV** (Manuale §3.10): tutto questo documento misura la protezione contro un movimento **rapido**. Un ribasso lento e prolungato (come il 2022) non fa scattare né il Vega né il circuit breaker basato su VIX — solo il trigger di drawdown puro lo intercetta, e lo fa in ritardo. Non è un buco di questo metodo, è un limite intrinseco della protezione stessa.
- **Vomma e altri effetti di secondo ordine:** per movimenti piccoli (pochi punti di IV) la stima lineare del §4 è affidabile; per movimenti grandi, no — usa sempre il Risk Navigator per quelli.

---

### Collegamenti

- Teoria completa delle greche: [Manuale](MANUALE.md) §1.4
- Teoria completa delle protezioni, regimi, circuit breaker: [Manuale](MANUALE.md) §3
- Numeri e limiti operativi: [Piano](PIANO.md) §5, §7
- Dove eseguire lo stress test vero: [Setup e Watchlist](SETUP_BROKER.md)
- Cadenza settimanale per rifare questo controllo: [Piano §9 (routine)](PIANO.md) §7
