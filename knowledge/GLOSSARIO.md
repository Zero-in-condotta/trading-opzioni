# Glossario

> Un solo documento per i concetti spiegati durante le lezioni. Per le greche e le strutture, uno per uno, vedi anche Strategie.

## Indice

- [[#Beta Weighting]]
- [[#Beta di portafoglio]]
- [[#Delta]]
- [[#Open Interest]]
- [[#Ticker e Simboli]]
- [[#VIX VSTOXX e IV Rank]]

## Beta Weighting

### Il problema che risolve

Il delta e il vega "grezzi" di una posizione sono calcolati rispetto al **proprio sottostante**. Con posizioni su sottostanti diversi (es. XSP a ~755, ESTX50 a ~6.266, valute diverse USD/EUR, volatilità diverse), sommare i delta/vega grezzi non ha significato economico: un "1 delta" su XSP e un "1 delta" su ESTX50 rappresentano esposizioni molto diverse.

### La soluzione

**Beta-weighting** traduce ogni posizione nell'equivalente "quante unità dell'indice di riferimento servirebbero per avere la stessa esposizione", usando il **beta storico** del sottostante della posizione rispetto all'indice di riferimento scelto (quanto si muove in media il sottostante per ogni 1% di movimento dell'indice).

Una volta riscalate, le greche di posizioni su strumenti diversi si possono **sommare in un unico numero coerente** — è l'unico modo per calcolare un delta o un vega di portafoglio quando si opera su più mercati insieme.

### Perché il Piano lo richiede (§5.2)

I limiti di rischio sulle greche ("Delta: perdita ≤1% NAV per movimento −3% dell'indice", "Vega: perdita ≤6% NAV per +10 punti VIX") presuppongono un delta/vega di portafoglio **espresso in un'unica unità di misura**. Con posizioni miste USD (XSP) ed EUR (ESTX50) come le nostre, senza beta-weighting quei limiti non si possono nemmeno calcolare correttamente.

### Come si ottiene su IBKR

- Le colonne "Portfolio Delta/Vega" della vista Positions/Portfolio **non sono beta-weighted** — sono solo delta/vega di posizione × size.
- Lo strumento corretto è il **Risk Navigator**, disponibile solo su **TWS classico**, non su IBKR Desktop (verificato 16/09/2026 — vedi [Piano §9 (routine)](PIANO.md) §7, nota sulla piattaforma del venerdì).
- Nel Risk Navigator si imposta un simbolo di riferimento per il beta-weighting. Con posizioni miste USD/EUR conviene fare **due letture separate**: una con **SPX** come riferimento (pesa la parte USD/XSP), una con **ESTX50** come riferimento (pesa la parte EUR) — mescolare le due valute in un solo calcolo non è corretto.

### Collegamento col Piano

- [Piano](PIANO.md) §5.2: i limiti sulle greche sono definiti esplicitamente "beta-weighted su SPX o ESTX50".
- [Piano](PIANO.md) §5.1: "XSP ed Euro Stoxx 50 trattati come un unico blocco" negli stress test — stesso principio di fondo (correlazione tra i due mercati in crisi), applicato lì al rischio aggregato invece che alle greche giorno per giorno.

---

## Beta di portafoglio

### In una frase

Il **beta** dice **di quanto si muove il tuo portafoglio quando l'indice si muove dell'1%**. È la "manopola" del rischio di mercato.

### Come si legge

| Beta | Se l'indice sale del 10% | Se l'indice scende del 10% |
|---|---|---|
| 0 | il portafoglio non si muove | non si muove |
| 0,5 | +5% | −5% |
| **1,0** | **+10%** (come l'indice) | **−10%** |
| 1,3 | +13% | −13% |
| −0,5 | −5% | +5% |

Con 100.000 € di capitale: **beta 1,0 = 100.000 € di esposizione all'indice** (come avere 100.000 € in un ETF sull'indice); **beta 1,3 = 130.000 €** di esposizione, cioè una leva di 1,3 volte con lo stesso capitale.

### Legame con quello che conosci già

- È il [delta](GLOSSARIO.md) di tutto il portafoglio, espresso in "quanti euro di indice ho addosso per ogni euro di capitale". Un ETF sull'indice, per esempio, ha delta 1 e beta 1.
- Per sommare posizioni su sottostanti diversi (SPX, ESTX50, singoli titoli) le si traduce in un'unica misura: [Beta Weighting](GLOSSARIO.md). Nel Risk Navigator di TWS il "delta beta-pesato" diviso per il capitale è proprio il beta del portafoglio.

### Perché il Piano v2 lo usa

- **È il motore della crescita:** se l'indice sale, il portafoglio sale in proporzione al beta.
- **È anche il rischio:** un beta di 1,3 ha una perdita del 13% quando l'indice perde il 10%, prima delle protezioni.
- Il piano usa il beta come **regola di riduzione del rischio**: lo si tiene a 1,0-1,3 quando il mercato è calmo e lo si abbassa (a 0,6-0,9 con mercato agitato, 0,2-0,4 in crisi) con regole scritte. Le put di protezione servono a limitare la perdita mentre il beta è ancora alto.

Vedi Piano v2 - Crescita, sezioni 4, 5 e 7.

---

## Delta

Il delta va da 0 a 1 per le call e da −1 a 0 per le put — **non** da 0 a max 1 in senso assoluto per entrambe: per le put è sempre negativo. Nel gergo dei trader "delta 15" è una convenzione per dire 0,15 (il valore moltiplicato per 100); per una put si intende −0,15, ma il segno spesso si omette.

### Cosa misura

Indica quanto si muove il prezzo dell'opzione rispetto al sottostante. Esempio: con XSP a 600 e una put a delta −0,15, se XSP scende di 1 punto la put guadagna circa 0,15; se XSP sale di 1 punto la put perde circa 0,15.

Il delta **non è costante**: cambia mentre il prezzo si muove. Se l'indice scende verso lo strike, il delta della put passa da −0,15 a −0,30, poi −0,50, fino a −1 quando l'opzione è molto in the money. Questa accelerazione del delta è la **gamma**.

### Perché si usa per scegliere lo strike (nel [Piano](PIANO.md))

Il delta è anche un'approssimazione della probabilità che l'opzione scada in the money. Una put a delta 0,15 ha circa il 15% di probabilità di chiudere sotto lo strike, quindi uno spread venduto lì ha circa l'85% di probabilità di scadere senza perdite. Per questo il piano indica "delta short strike 10–20" (§6 del Piano): è un modo per calibrare la distanza dello strike in base alla volatilità del momento, invece che con una percentuale fissa sul sottostante.

**Perché proprio 10-20, e non più basso o più alto (domanda del 17/09/2026):** è un compromesso tra due estremi.
- Delta troppo basso (es. 5): probabilità di successo altissima (~95%), ma il premio incassato è minimo rispetto al rischio preso (1-2% NAV per posizione) — rapporto rischio/rendimento povero.
- Delta troppo alto (es. 30-40): premio più ricco, ma la probabilità di essere toccati sale abbastanza da minacciare il KPI del Piano §10 — win rate bucket A sotto il 65% su 50+ trade è soglia d'allarme.

10-20 è la zona dove il variance risk premium (Piano §1) si monetizza bene senza spingere troppo la probabilità di perdita: premio sufficiente, win rate atteso comunque alto (75-85%).

### Delta per contratto

Ogni contratto XSP vale 100 volte il prezzo, quindi una put a delta −0,15 equivale a un'esposizione di −15 "quote" di XSP. Vendendola, l'esposizione netta diventa +15: si guadagna come se si avessero 15 quote se l'indice sale. Da qui l'abitudine di dire semplicemente "delta 15" per un'intera posizione.

---

## Open Interest

> Concetto introdotto il 17/09/2026, durante la scelta degli strike per il primo core OESX.

Numero di contratti di una specifica opzione (stesso strike, stessa scadenza) **ancora aperti** sul mercato in questo momento — quanti long e quanti short di quel preciso contratto esistono, non ancora chiusi, esercitati o scaduti.

### Non è il volume

Il **volume** conta quanti contratti sono stati scambiati *oggi*. L'**OI** conta quanti sono *strutturalmente in circolazione* in questo momento, accumulati nel tempo. Volume alto non implica OI alto (puoi avere scalping intraday che apre e chiude tutto in giornata) e viceversa.

### Perché il Piano lo richiede (≥500, [Piano](PIANO.md) §3)

Complementare al controllo sullo spread bid-ask, non ridondante:
- **Spread bid-ask** → quanto costa entrare/uscire *adesso*, in questo istante
- **OI** → quanta profondità strutturale c'è dietro quel prezzo, cioè se troverai controparti anche in futuro (es. per chiudere in fretta a un trigger di circuit breaker, [Piano](PIANO.md) §7.2, o per rollare a 21 DTE)

Uno spread stretto oggi non garantisce che il mercato regga quando serve muovere size vera: un OI basso è il segnale che quella apparente liquidità è fragile.

---

## Ticker e Simboli

> Concetto introdotto il 17/09/2026, su domanda dell'utente su cosa fosse OESX. Riferimento rapido per gli strumenti usati nel [Piano](PIANO.md) (§3).

| Ticker | Cos'è | Sottostante | Valuta / moltiplicatore | Note |
|---|---|---|---|---|
| **OESX** | Opzioni standard Eurex su indice | ESTX50 (SX5E) | EUR, €10 per punto | Strumento del core (bucket A) e dell'hedge (bucket C) lato EUR |
| **ESTX50** | L'indice Euro Stoxx 50 | — | — | Non è tradabile direttamente, solo tramite derivati (OESX, future) |
| **XSP** | Opzioni su mini-S&P500 (1/10 di SPX) | S&P500 | USD, $100 per punto | Usato al posto di SPX per size più granulari su capitale ridotto (Primo Trade §3.1) |
| **SPX** | Opzioni standard su S&P500 | S&P500 | USD, $100 per punto | Size piena, non usato nella fase attuale (capitale 30k) |
| **V2TX** | Indice di volatilità implicita di ESTX50, il "VIX europeo" | ESTX50 | — | Il future VSTOXX (FVS) **non è negoziabile su IBKR** (verificato 16/09/2026) — l'hedge EUR usa put dirette su OESX invece di un derivato sul V2TX |
| **VIX** | Indice di volatilità implicita di S&P500 | S&P500 | — | |
| **VIX3M** | Come VIX ma a 3 mesi | S&P500 | — | Usato per la struttura a termine VIX/VIX3M ([Piano](PIANO.md) §4) |

**Perché indici cash-settled e non singoli titoli, in generale:** nessuna assegnazione in azioni, nessun rischio di singolo evento societario, liquidità elevata — motivazione completa in [Piano](PIANO.md) §3.

---

## VIX VSTOXX e IV Rank

Questo file unisce due concetti che nel Piano vengono sempre letti insieme ma che misurano cose diverse — è un errore comune confonderli:

- **VIX / VSTOXX / VIX3M**: indici che misurano la volatilità implicita **del mercato nel suo complesso** (S&P 500 per il VIX, Euro Stoxx 50 per VSTOXX), in un dato momento. Un numero assoluto, uguale per tutti.
- **IV Rank / IV Percentile**: non misurano la volatilità in sé, misurano se la volatilità implicita **di un singolo sottostante specifico** (es. XSP, ESTX50, un'azione) è oggi alta o bassa **rispetto al proprio passato**. Un numero relativo, diverso per ogni strumento.

Il Piano (§4) li incrocia sempre e tre insieme — livello VIX, rapporto VIX/VIX3M, IV Rank del sottostante specifico — proprio perché rispondono a domande diverse e possono dire cose diverse nello stesso momento (è successo il 16/09/2026: vedi §11 sotto).

### 1. Cosa misurano davvero (il concetto comune)

Non misurano quanto si è mosso il mercato **finora** (quella è volatilità *realizzata*, un dato storico) — misurano quanto il mercato **si aspetta** che si muoverà **in futuro**, letto dai prezzi delle opzioni stesse. È il *valore estrinseco* di cui parla il [Manuale §1.3](MANUALE.md): più il mercato teme movimento, più le opzioni costano, più l'indice/il rank salgono.

### 2. VIX — il termometro USA

Calcolato dal CBOE a partire dai prezzi di **molte opzioni OTM** (put e call, su un'ampia gamma di strike) sulle opzioni **S&P 500** (SPX), con una formula "model-free" (non passa da Black-Scholes) che sintetizza la varianza attesa nei prossimi 30 giorni. Il risultato è una **percentuale annualizzata**: un VIX a 20 indica un'oscillazione annualizzata attesa del 20% circa (≈ ±1,15% al giorno, per la matematica della radice del tempo vista a proposito del theta in [Manuale §1.4](MANUALE.md)).

Includendo anche le put molto OTM ("di coda"), il VIX cattura direttamente quanto il mercato sta pagando per proteggersi da un crollo, non solo un movimento medio atteso. È il "termometro della paura" del mercato USA.

### 3. VSTOXX (ticker V2TX) — l'equivalente europeo

Stesso concetto del VIX, calcolato da STOXX/Eurex a partire dalle opzioni su **Euro Stoxx 50**. Una precisazione sul nome, perché genera confusione: **"VSTOXX" è il nome comune dell'indice**, quello che trovi scritto nella maggior parte degli articoli e materiali divulgativi; **"V2TX" è il ticker esatto** con cui lo stesso indice compare su Bloomberg, TWS e altri data provider di mercato. Sono la stessa identica cosa — è la stessa relazione che c'è tra "VIX" (nome) e il suo ticker CBOE, che coincidono; per VSTOXX invece nome comune e ticker sono parole diverse. Se lo cerchi su TWS e non lo trovi come "VSTOXX", prova "V2TX".

Il [Piano](PIANO.md) (§4) legge VIX e VSTOXX sempre insieme: VIX per la parte USD (XSP/SPX), VSTOXX per la parte EUR (Euro Stoxx 50).

### 4. Perché salgono quando il mercato scende

Relazione storicamente forte e inversa: quando l'indice scende, la domanda di protezione (put) sale, il loro prezzo sale, e la formula lo riflette — spesso *prima* che il prezzo dell'indice si sia mosso molto. È il meccanismo osservato nella simulazione di agosto 2024: il VIX è quintuplicato mentre l'S&P perdeva "solo" l'8,5%.

### 5. Livelli tipici (VIX, indicativi anche per VSTOXX)

| VIX | Significato |
|---|---|
| < 14 | Compressione, mercato quasi troppo tranquillo |
| 14-22 | Normale — media storica di lungo periodo intorno a 19-20 |
| 22-30 | Elevato — correzioni serie |
| 30-40 | Crisi conclamata |
| > 40, fino a 60-80+ | Crash severi — marzo 2020 picco ≈82, 2008 picco ≈81, agosto 2024 punta intraday a 65 |

### 6. Non sono direttamente negoziabili

Sono **indici calcolati**, non titoli. Per il VIX si prende esposizione con i **futures VIX** e le relative opzioni negli USA. Per il VSTOXX **non è possibile fare lo stesso tramite IBKR**: il future FVS ipotizzato inizialmente non risulta disponibile su questo broker (verificato il 16/09/2026) — l'hedge sulla parte EUR (bucket C del Piano) si fa quindi con put OTM dirette su Euro Stoxx 50 invece che con uno strumento di volatilità puro come per il VIX.

### 7. VIX3M e la struttura a termine

Stesso concetto del VIX, ma misura la volatilità attesa nei prossimi **~93 giorni** invece di 30 — stesso indice, orizzonte più lungo. Il rapporto **VIX/VIX3M** è l'indicatore di "struttura a termine" usato per classificare il regime (Piano §4):

- **Sotto 0,9** → **contango sano**: il mercato prezza più paura a breve che a lungo termine, condizione normale — è come deve essere quando non c'è stress imminente.
- **Sopra 1,0** → **backwardation**: segnale di stress acuto, il mercato teme di più il brevissimo termine del lungo — succede nelle crisi, quando la paura è concentrata sull'immediato.

### 8. IV Rank

```
IV Rank = (IV di oggi − IV minima del periodo) / (IV massima del periodo − IV minima del periodo) × 100
```

La posizione della IV di oggi **dentro il range** (minimo-massimo) toccato nel periodo scelto (tipicamente 52 settimane, ma anche 13 o 26 — vedi §10). Un IV Rank di 4 significa: la IV di oggi è quasi al minimo assoluto del periodo.

**Non è un indicatore "lagging"** nel senso tecnico (una media mobile che si ritarda) — il numeratore è la IV **di oggi**, in tempo reale. Il limite è un altro: **è sensibile a un singolo evento estremo nel periodo**. Se mesi fa c'è stato uno spike di volatilità molto forte, quel massimo resta nel denominatore per l'intera finestra, anche se ormai irrilevante — una IV oggi moderata può sembrare "bassissima" solo perché il range storico è gonfiato da un evento vecchio, non perché il mercato sia davvero a riposo.

### 9. IV Percentile

```
IV Percentile = % di giorni nel periodo in cui la IV ha chiuso sotto il livello di oggi
```

Conta i giorni, non guarda solo il minimo/massimo. Un singolo giorno di spike pesa come **un giorno solo** nella distribuzione, non deforma l'intero range come nell'IV Rank — più robusto a un evento estremo isolato.

### 10. Come usare IV Rank e IV Percentile insieme (verifica pratica)

1. **Confronta la stessa metrica su finestre diverse** (13/26/52 settimane, disponibili come colonne in TWS/Desktop — vedi [Setup e Watchlist](SETUP_BROKER.md)). Se l'IV Rank resta basso su tutte e tre, il segnale è solido. Se il 13 settimane è molto più alto del 52, il 52 settimane è probabilmente "sporcato" da un evento vecchio — fidati di più della finestra corta.
2. **Confronta IV Rank e IV Percentile sullo stesso periodo.** Se divergono molto, c'è stato un evento anomalo nel periodo che vale la pena capire prima di fidarsi ciecamente del numero.

### 11. Come si usano TUTTI insieme, in pratica — i "tre numeri" del Piano

Il Piano (§4) non guarda mai un solo indicatore: **livello VIX/VSTOXX**, **rapporto VIX/VIX3M**, **IV Rank del sottostante specifico**. Perché servono tutti e tre: il primo descrive il mercato nel suo complesso, il secondo descrive se lo stress è concentrato sul breve termine, il terzo descrive quanto è "cara" o "economica" la volatilità di **quello specifico strumento**, che può raccontare una storia diversa dal mercato generale nello stesso momento.

È successo davvero il 16/09/2026 (vedi Primo Trade): VIX intorno a 17 (regime "Normale"), VIX/VIX3M ≈0,88 (contango sano) — il quadro generale sembrava tranquillo. Ma l'**IV Rank di XSP era 4** (quasi al minimo dell'anno): il mercato generale era normale, ma la volatilità di quel singolo sottostante era compressa, rendendo poco conveniente vendere premio proprio lì quel giorno. Nessuno dei tre numeri da solo avrebbe dato l'immagine corretta.

**Un'ulteriore complicazione emersa lo stesso giorno**: le finestre di IV Rank possono divergere molto tra loro. Confrontando 13/26/52 settimane su XSP ed ESTX50, il 13 settimane risultava molto più alto del 52 (XSP: 3 vs 36 — ESTX50: 19 vs 75). Causa verificata: un picco di VIX a 60 in aprile 2026 (crisi legata all'escalation dei dazi commerciali dell'amministrazione Trump, poi rientrata) — dentro le finestre a 26 e 52 settimane (da cui i Rank bassi lì), fuori dalla finestra a 13 settimane (da cui il numero "pulito"). **Deciso il 16/09/2026: il Piano ora usa 13 settimane come riferimento primario** (Piano §4, §6), controllando comunque anche 26 e 52 settimane — se tutte e tre concordano il segnale è solido, se divergono vale la pena capire perché prima di decidere, non ignorare la discrepanza.

### 12. Collegamento col Piano

- Piano §4: livello di VIX/VSTOXX, rapporto VIX/VIX3M e IV Rank (finestra 13 settimane primaria) sono i tre indicatori per classificare il regime di volatilità.
- Piano §6: soglia "IV Rank ≥ 30 preferito" per aprire nuovo core (bucket A) — sotto quella soglia, il Piano suggerisce di spostarsi sul bucket B (calendar, debit spread) invece di forzare la vendita di premio (Manuale §4.2).
- Piano §7.2: soglie di VIX sono trigger espliciti del circuit breaker.
- [Manuale §1.4](MANUALE.md) (vega): il VIX misura esattamente ciò a cui il vega delle posizioni è sensibile.
- TWS calcola nativamente IV Rank e IV Percentile (su finestre 13/26/52 settimane) come colonne di watchlist/portfolio/scanner — non serve costruirsi lo storico a mano (vedi Automazione).
