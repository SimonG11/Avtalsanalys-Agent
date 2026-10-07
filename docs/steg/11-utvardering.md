# M11 – Mätning av agentens svar (förenklad)

**Mål:** mäta det en användare får för de 30 testfrågorna: om svaret är rätt, om det är
kontrollerat, vilka källor det citerar, hur lång tid det tar och vad det kostar, och visa det i en
rapport till presentationen. Förenklad jämfört med arkitekturplanens avsnitt 8: DeepEval i CI, fler
frågor och jämförelsen av agentmodeller kommer efter presentationen
(`plan-mot-presentationen.md`, punkt 4). Besluten och varför står i
[ADR 0019](../adr/0019-matning-av-svaren.md).

**Klart när:** ett kommando ställer alla testfrågor till agenten, med samma graf, kontroll och
granskare som API:t, och skriver en rapport med andelen rätta svar enligt en domare, status,
citat, facits källor, registeruppgifter, nya försök, tid och kostnad, per fråga och per kategori;
samma kommando går att köra mot den riktiga databasen med Docker Compose; allt utom modellanropen
har tester utan nätverk; och mätningen är körd på alla 30 frågor.

## Resultat

Alla 30 frågor kördes två gånger 2026-10-07 mot de riktiga modellerna (`gpt-6.1-sol` som agent
och `gpt-6-astra` som granskare, båda med resonemangsnivån `low`, och `gpt-6-astra` som domare med
`medium`), med den tillfälliga ersättaren för avtal-mcp över pilotens data (ingen Postgres i
utvecklingsmiljön), fyra frågor åt gången:

| Mått | Körning 1 | Körning 2 |
|---|---|---|
| Rätt enligt domaren | **27 av 30** (90 %); delvis rätt 1, fel 2 | **28 av 30** (93 %); delvis rätt 1, fel 1 |
| Status | Kontrollerat 30 | Kontrollerat 28, Inget svar 2 (q27 och q30) |
| Frågor som avtalen inte besvarar (q27–q30) | 4 av 4 fick ett rätt "framgår inte" | 4 av 4 |
| Citat som står ordagrant i sitt avsnitt | 74 av 74 | 72 av 72 |
| Facits källor citerade | 28 av 36 (78 %, en undre gräns, se nedan) | 28 av 36 |
| Facits avtal bland svarens registerrader | 17 av 17, inga avtal utöver facit | 17 av 17, inga utöver |
| Nya försök | 3 frågor (q01, q08 och q15), 4 nya försök | 2 frågor (q03 och q15), 2 nya försök |
| Verktygsanrop per fråga | 6,6 i medel, högst 14 | 6,3 i medel, högst 14 |
| Tid per fråga | median 37 s, 90:e percentilen 54 s, längst 101 s (q15) | median 36 s, 90:e percentilen 47 s, längst 104 s (q15) |
| Kostnad per fråga, agent och granskare | 0,07–0,18 USD (2,12–5,48 USD för alla 30) | 0,06–0,17 USD (1,91–5,11 USD för alla 30) |
| Domarens kostnad | 0,42 USD för alla 30 | 0,43 USD |
| Hela körningen | 6 minuter | 6 minuter |

Körning 1 per kategori (körning 2 hade samma bedömningar utom q26, som blev rätt):

| Kategori | Frågor | Rätt | Delvis | Fel | Tid (median) |
|---|---:|---:|---:|---:|---:|
| Enkel uppslagning | 9 | 8 | 0 | 1 | 37 s |
| Registerfråga | 4 | 4 | 0 | 0 | 20 s |
| Flerstegshänvisning | 7 | 6 | 1 | 0 | 43 s |
| Ändring ersätter klausul | 3 | 3 | 0 | 0 | 35 s |
| Jämförelse mellan leverantörer eller delområden | 3 | 2 | 0 | 1 | 36 s |
| Fråga utan svar i avtalen | 4 | 4 | 0 | 0 | 40 s |

De tre svar som inte var rätt i körning 1:

- **q06 (fel):** "senast tio arbetsdagar" i stället för 15. Agenten citerade Vägledningen för
  IT-konsulttjänster, avsnitt 3.6, som säger tio arbetsdagar, i stället för delområde 1:s
  huvuddokument (1.9.4.1), som säger 15. Det är den fälla som frågan byggdes med: det äldre
  avtalet för delområde 2 och 4 och vägledningen har nästan samma mening med tio dagar.
  Citatet är ordagrant och granskaren godkände svaret, eftersom källan stöder det; bara facit
  visar att det är fel källa.
- **q26 (fel):** Pulsen AB med 945 kr i stället för Redpill Linpro AB med 999 kr. Redpill Linpros
  cell är tom i prissammanställningen, och agenten skrev att priset saknas men letade inte i
  Redpill Linpros eget avtal, där 999 kr står. Också det är frågans avsiktliga svårighet. I
  körning 2 läste agenten avtalet och svarade rätt.
- **q18 (delvis rätt):** rätt pris, 1 098 kr per timme, och rätt villkor, men utan "exklusive
  moms". Domaren räknade momsen till kärnan; det är en sträng bedömning.

q06 och q18 bedömdes likadant i båda körningarna, så de beror inte på slumpen. q26 visar att
en enskild fråga kan bli rätt den ena gången och fel den andra.

Domarens bedömningar av de 27 rätta svaren prövades i stickprov (q11, q15, q16, q19, q24, q28,
q29) mot facit: alla stämmer. Varje bedömning står med sitt skäl i rapporten.

Siffrorna ovan gäller ersättaren för avtal-mcp. Den har samma data som pilotens databas och samma
verktygsscheman, men söker i minnet och inte med Postgres.

**Mot Postgres.** Samma mätning kördes 2026-10-07 mot den riktiga databasen, efter hela
inläsningen med Simons godkännanden, med samma modeller och fyra frågor åt gången:

| Mått | Mot Postgres |
|---|---|
| Rätt enligt domaren | **28 av 30** (93 %); delvis rätt 1 (q18), fel 1 (q06) |
| Status | Kontrollerat 29, Inget svar 1 (q27) |
| Frågor som avtalen inte besvarar (q27–q30) | 4 av 4 fick ett rätt "framgår inte" |
| Citat som står ordagrant i sitt avsnitt | 71 av 71 |
| Facits källor citerade | 27 av 36 (75 %); alla källor i 14 av 22 frågor |
| Facits avtal bland svarens registerrader | 17 av 17, inga avtal utöver facit |
| Nya försök | 1 fråga (q08), 1 nytt försök |
| Verktygsanrop per fråga | 6,7 i medel, högst 16 (q15) |
| Tid per fråga | median 34 s, 90:e percentilen 48 s, längst 61 s (q15) |
| Kostnad per fråga, agent och granskare | 0,066–0,18 USD (1,99–5,37 USD för alla 30) |
| Domarens kostnad | 0,43 USD för alla 30 |
| Hela körningen | 5 minuter |

Samma två svar som med ersättaren var inte rätt, av samma skäl: q06 (tio arbetsdagar ur
vägledningen i stället för 15 ur huvuddokumentet) och q18 (utan "exklusive moms"). q26 blev rätt,
som i körning 2. Med Postgres var svaren något snabbare och behövde färre nya försök, men en
körning räcker inte för att säga att skillnaden är verklig.

## Flödet

```
python -m evals.run_answer_eval
 ├─ testsamlingen (evals/datasets/gold_sv.jsonl), --only väljer frågor
 ├─ modellerna: agent, granskare, domare (utan OPENAI_API_KEY stannar det här)
 ├─ en session mot avtal-mcp öppnas och stängs (svarar den inte stannar det här)
 └─ för varje fråga, fyra åt gången (--concurrency):
     ├─ egen MCP-session och graf, som en körning i API:t
     ├─ agenten svarar (ask_user får ett fast svar), kontrollen och granskaren som vanligt
     ├─ tokens räknas per modellanrop och modell
     ├─ domaren jämför svaret med facit: rätt, delvis rätt eller fel, och varför
     └─ svarets citat, källor och registerrader jämförs med facits
 └─ rapporter: evals/reports/answers-<modell>-<resonemang>[-<etikett>].md och .json
```

## Kommandon

```bash
uv run python -m evals.run_answer_eval                        # alla frågor, avtal-mcp enligt .env
uv run python -m evals.run_answer_eval --only q06 q26 q18     # några frågor
uv run python -m evals.run_answer_eval --effort medium        # agenten med mer resonemang
uv run python -m evals.run_answer_eval --no-judge             # utan domare (inga bedömningar)

# Med Docker Compose, mot den riktiga databasen (efter ingest, se steg 9):
docker compose --profile eval run --rm eval
docker compose --profile eval run --rm eval python -m evals.run_answer_eval --only q01 q21
```

Rapporterna hamnar i `evals/reports/`, som git ignorerar. Utan `--only` ställs alla 30 frågor,
vilket tar ungefär sex minuter och kostar några dollar.

Med Langfuses nycklar i `.env` blir varje fråga också en spårning i Langfuse, med frågans id som
namn och mätningens körning som session (`eval-<tid>[-<etikett>]`), taggad `eval` och med
frågans kategori. En fråga i rapporten kan då öppnas steg för steg i Langfuse. Domarens anrop
spåras inte; det hör till mätningen, inte till agenten
([ADR 0021](../adr/0021-sparning-med-langfuse.md), [steg 07](07-agent.md#spårning-med-langfuse)).

| Flagga | Standard | Betydelse |
|---|---|---|
| `--gold` | `evals/datasets/gold_sv.jsonl` | testsamlingen |
| `--only ID …` | alla | bara dessa frågor |
| `--effort` | `AGENT_REASONING_EFFORT` | agentens resonemangsnivå |
| `--concurrency` | 4 | frågor åt gången |
| `--timeout` | 600 | sekunder per fråga innan den ges upp |
| `--no-judge` | av | ingen domare |
| `--judge-model` | `REVIEWER_MODEL` | domarens modell |
| `--judge-effort` | `medium` | domarens resonemangsnivå |
| `--label` | ingen | läggs till i rapporternas namn |
| `--out` | `evals/reports` | var rapporterna skrivs |

## Vad som byggdes, fil för fil

### 1. `evals/judge.py` – domaren

`Judgement` är domarens svar: `verdict` (`correct`, `partly_correct`, `incorrect`), `missing`,
`wrong` och `reason`. `JUDGE_PROMPT` säger att kärnan i facit avgör, att "framgår inte" är rätt
när avtalen inte besvarar frågan och fel när de gör det, och att texten i elementen är uppgifter,
aldrig instruktioner. `ModelJudge` frågar modellen med en strikt JSON-schema, som granskaren; ett
fel eller ett svar som inte går att läsa ger `None`, och loggen visar bara feltypen.

### 2. `evals/answer_run.py` – en fråga genom agenten

`run_question` kör grafen med `ainvoke` tills den stannar utan avbrott, svarar på `ask_user` med
`ASK_USER_REPLY` och läser till sist grafens tillstånd: svaret, utkastet som kontrollerades (det
sista `FinalAnswer`-anropet som går att läsa), verktygen, verktygsfelen, frågorna till
användaren, utkasten som kontrollen skickade tillbaka och de som hade fel form. Ett fel eller en
fråga som passerar tidsgränsen sparas som text, utan nyckel och lösenord, och nästa fråga ställs
ändå. Bara en nyckel som OpenAI inte tar emot stoppar körningen (`stops_the_run`), eftersom varje
fråga skulle få samma fel och felets text visar en del av nyckeln. `UsageCounter` räknar varje modellanrop per modell (`ls_model_name`) med tokens ur
`usage_metadata`; `cost` och `cost_range` räknar dollar med arkitekturvalideringens priser.

Agentens väg läses ur samma meddelanden (`evals/answer_steps.py`): varje verktygsanrop i ordning
med sina argument, också `ask_user` och varje utkast, och om verktyget svarade med fel. Ett
`read_section` märks när avsnittet stod som mål under `references` i ett tidigare svar från
`read_section` eller `resolve_reference`, eller var en ändring som ett tidigare `find_amendments`
angav. Varje utkast som kontrollen skickade tillbaka sparas med sina fel, och varje fel räknas
till regeln som skrev det (citat, registeruppgifter, senaste lydelsen eller granskaren) efter hur
regeln formulerar sina fel; testerna prövar varje regels egna fel, så en regel som formuleras om
syns där i stället för att räknas fel. Modellanropen är den räkning som
`ModelCallLimitMiddleware` för i trådens tillstånd.

### 3. `evals/answer_scores.py` – poängen

`score` gör ett `QuestionResult` av frågan, körningen och bedömningen. `cited_places` tar varje
citats position ur utkastet, `sources_found` säger vilka av facits källor som har ett godkänt citat
på samma plats, och `register_score` jämför facits avtal med svarets registerrader.
`rule_judgement` bedömer ett svar som inte blev klart inom gränsen för modellanrop som fel.
`summarize` ger siffrorna för en grupp frågor, hela körningen eller en kategori, och
`summarize_paths` anropen per verktyg, modellanropen, läsningarna ur hänvisningar och de nya
försöken efter regel.

### 4. `evals/answer_report.py` – rapporterna

Markdown på svenska: sammanfattningen, körningen, metoden, en tabell per kategori och per fråga,
agentens väg, tokens och kostnad per modell, domarens skäl och varje svar bredvid facit. JSON med
samma innehåll, varje verktygsanrop med alla argument och varje svar som webbappen får det, för
att jämföra körningar.

- **Sammanfattningen** har alltid raden Följdfrågor, också när agenten inte frågade något ("0 av
  30"), de nya försöken efter regel, agentens modellanrop per fråga (median och högst, mot gränsen
  `AGENT_MODEL_CALL_LIMIT`) och hur många `read_section` som gick till ett mål ur ett tidigare
  svars hänvisningar. Metoden definierar måtten.
- **Körningen** anger commit (kort sha, och om arbetskatalogen hade ändringar som inte var
  incheckade) och sha256 av `SYSTEM_PROMPT` (före datumet) och granskarens `REVIEWER_PROMPT`, så
  att rapporten säger vilken kod och vilka prompter den mätte. I compose-tjänsten `eval` finns
  varken git eller `.git`, så där står commit som okänd; prompternas hash står ändå.
- **Agentens väg** har anropen per verktyg (anrop och frågor), de nya försöken efter regel (utkast,
  frågor och fel) och en rad per fråga med anropen i ordning och argumentet som säger vad agenten
  letade efter, till exempel `search_documents("lördag", Bemanning) → read_section(9.9.2) → …`.
  Ett `read_section` till ett mål ur en hänvisning märks med ↪, en ändring ur `find_amendments`
  med Δ och ett verktygsfel med ✗. Under raden står skälen till varje nytt försök.

Det en körning inte sparade, som en fråga där sessionen mot avtal-mcp aldrig kom igång, står som
"inte sparat" och räknas inte som noll.

### 5. `evals/run_answer_eval.py` – körningen och kommandoraden

Gör modellerna, prövar avtal-mcp en gång, kör frågorna med en egen MCP-session var
(`--concurrency` åt gången), låter domaren bedöma varje svar när det är klart och skriver
rapporterna. Fel som stoppar körningen ger en rad och slutkod 1, som agentens kommandorad.

### 6. `docker-compose.yml` – tjänsten `eval`

Profilen `eval`, samma image som API:t, `MCP_TRANSPORT=streamable_http` mot `mcp` och
kontrollpunkter i minnet. `./evals` monteras, eftersom imagen bara har `src/`, så rapporterna
hamnar i `evals/reports/` på datorn. Containern skriver som uid 1000, som inläsningen i `./data`
([steg 9](09-api.md)); på Docker Desktop för Mac spelar det ingen roll, men på Linux måste
`./evals` vara skrivbar för den. Mätningen prövar att mappen går att skriva i innan första frågan
ställs, så en körning som har kostat pengar inte går förlorad på slutet.

## Tester

| Fil | Tester | Vad |
|---|---:|---|
| `tests/unit/evals/test_answer_run.py` | 17 | En fråga genom agentens riktiga graf med en skriptad modell: svar, utkast, verktyg med argument, modellanrop och tokens; `ask_user` får det fasta svaret; ett utkast som kontrollen och ett som granskaren skickar tillbaka, med skälen ur återkopplingen; ett `read_section` till ett mål ur ett tidigare svars hänvisningar; ett verktygsfel; ett fel med en hemlighet som döljs; en nyckel som OpenAI inte tar emot stoppar körningen; tidsgränsen; gränsen för modellanrop; tokens, kostnad och felgrupper |
| `tests/unit/evals/test_answer_steps.py` | 14 | Varje anrop i ordning med argument och utfall; mål ur hänvisningar (position eller nummer, bara ur svar före anropet, inte hela filer, också ur `resolve_reference` och ur JSON-texten) och ändringar ur `find_amendments`; varje regels egna fel räknas till rätt regel; återkopplingens rader och de underkända utkasten |
| `tests/unit/evals/test_answer_scores.py` | 20 | Citatens positioner ur utkastet, källor på plats (position eller nummer), bara godkända citat, avtal med samma nyckel, regeln för svar utan utkast, sammanfattningen (där ett svar utan utkast inte räknas som "framgår inte"), agentens väg (verktyg, modellanrop, läsningar och nya försök efter regel, det som inte sparades för sig), kategorierna och percentilen |
| `tests/unit/evals/test_answer_report.py` | 21 | Rapporternas namn, Markdown med svenska tal, skäl, fel och svar som citat, utan domare, en domare som inte svarade, svar utan utkast, samma modell som agent och granskare, raden Följdfrågor också vid noll, vägen per fråga med argument och märken, tabellerna per verktyg och regel, det som inte sparades, commit och prompternas hash, JSON, utskriften, och mappen som prövas före körningen och vid skrivning |
| `tests/unit/evals/test_judge.py` | 8 | Domarens klient, frågan med båda svaren, att ingen text kan avsluta sitt element, det strikta schemat, ett lyckat och två misslyckade anrop |
| `tests/unit/evals/test_run_answer_eval.py` | 19 | Urvalet av frågor, inställningarna, commit och ändringar ur git (och utan git), prompternas hash, bedömningen (ingen utan domare), avtal-mcp som inte svarar, en nyckel som OpenAI inte tar emot, frågor åt gången i facits ordning, kommandoradens utskrift och fel, och en mapp som inte går att skriva i stoppar före första frågan |

## Kända begränsningar

- **Domaren är en modell.** Den kan döma fel; stickproven stämde, men den är inte prövad mot en
  människa på alla frågor. Den är samma modell som granskaren, men ser facit och inte källorna.
- **En körning per fråga.** Agentens svar varierar mellan körningar, och med 30 frågor är en fråga
  drygt tre procentenheter.
- **Facits källor räknas på plats.** Samma text i en fil som facit inte anger räknas inte (q18 och
  q19 citerade samma mening i en annan fil), så 78 procent är en undre gräns.
- **Agentens väg är läst ur meddelandena.** Skälen till ett nytt försök räknas till regel efter
  hur regeln formulerar sina fel, och ett `read_section` till ett mål ur en hänvisning säger att
  agenten hade hänvisningen framför sig, inte varför den valde avsnittet: målet kan också ha
  funnits i en sökträff.
- **Kostnaden är ett intervall**, eftersom priset för cachad indata saknas i prislistan. Omkring 84
  procent av agentens indata var cachad.
- **Ersättaren och databasen.** De två första körningarna är mot ersättaren och den tredje mot
  Postgres. De ger samma bild, men varje körning är en enda körning per fråga.

## Så verifierar du M11 själv

```bash
uv run pytest tests/unit/evals
docker compose --profile ingest run --rm ingest        # om databasen inte redan är inläst
docker compose --profile eval run --rm eval            # alla 30 frågor, cirka sex minuter
```

Öppna sedan `evals/reports/answers-gpt-6.1-sol-low.md` och kontrollera:

- att sammanfattningen och tabellerna har 30 frågor och inga fel i körningen;
- några av domarens bedömningar mot svaren längst ned i rapporten, särskilt de som inte är rätt;
- att q06 och q26, frågornas fällor, finns med bland de svar du vill tala om på presentationen.
