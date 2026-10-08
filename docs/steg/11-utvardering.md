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

# Med commit i rapporten (containern har varken git eller .git):
docker compose --profile eval run --rm -e AVTALSAGENT_COMMIT=$(git rev-parse --short HEAD) eval
```

Rapporterna hamnar i `evals/reports/`, som git ignorerar. Utan `--only` ställs alla 30 frågor,
vilket tar ungefär sex minuter och kostar några dollar.

I compose-tjänsten `eval` kan mätningen inte fråga git vilken commit den kör, så rapporten skriver
"okänd commit" om du inte anger den med `-e AVTALSAGENT_COMMIT=…` som ovan. Variabeln används bara
när git inte kan svara och ska vara en commits sha (4 till 40 hexadecimala tecken); annars lämnas
den bort med en varning i loggen. Rapporten skriver att commit kommer från variabeln: den prövas
inte mot koden i imagen, och om arbetskatalogen hade ändringar som inte var incheckade syns inte.

Med Langfuses nycklar i `.env` blir varje fråga också en spårning i Langfuse, med frågans id som
namn och mätningens körning som session (`eval-<tid>[-<etikett>]`), taggad `eval` och med
frågans kategori. En fråga i rapporten kan då öppnas steg för steg i Langfuse. Domarens anrop
spåras inte; det hör till mätningen, inte till agenten
([ADR 0021](../adr/0021-sparning-med-langfuse.md), [steg 07](07-agent.md#spårning-med-langfuse)).

| Flagga | Standard | Betydelse |
|---|---|---|
| `--gold` | `evals/datasets/gold_sv.jsonl` | testsamlingen |
| `--mode` | `agent` | `agent`, eller `workflow` för baslinjen (se nedan) |
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
fråga skulle få samma fel och felets text visar en del av nyckeln. `UsageCounter` räknar varje
modellanrop per modell (`ls_model_name`) med tokens ur `usage_metadata`; `cost` och `cost_range`
räknar dollar med arkitekturvalideringens priser.

Agentens väg läses ur samma meddelanden (`evals/answer_steps.py`): varje verktygsanrop i ordning
med sina argument, också `ask_user` och varje utkast, och om verktyget svarade med fel. Ett
`read_section` märks när avsnittet stod som mål under `references` i ett tidigare svar från
`read_section` eller `resolve_reference`, eller var en ändring som ett tidigare `find_amendments`
angav. Det märks också om ett tidigare svar från `search_documents` hade avsnittet bland sina
träffar eller deras kopior (`found_by_search`), så att en läsning som bara kan komma ur
hänvisningen går att skilja från en som agenten också hade fått ur en sökning. En position räknas
bara när den är ett heltal eller högst nio av siffrorna 0–9 som text; ett argument som "²" pekar
inte ut något avsnitt och kan inte stoppa läsningen, så en fråga som redan är betald går inte
förlorad. Varje utkast som kontrollen skickade tillbaka sparas med sina fel, och varje fel räknas
till regeln som skrev det (citat, registeruppgifter, senaste lydelsen eller granskaren) efter hur
regeln formulerar sina fel; testerna prövar varje regels egna fel, så en regel som formuleras om
syns där i stället för att räknas fel. Modellanropen är den räkning som `ModelCallLimitMiddleware`
för i trådens tillstånd. En fråga som aldrig nådde agenten (sessionen mot avtal-mcp kom inte igång)
har varken väg, skäl eller modellanrop sparade (`QuestionRun.path_saved`).

### 3. `evals/answer_scores.py` – poängen

`score` gör ett `QuestionResult` av frågan, körningen och bedömningen. `cited_places` tar varje
citats position ur utkastet, `sources_found` säger vilka av facits källor som har ett godkänt citat
på samma plats, och `register_score` jämför facits avtal med svarets registerrader.
`rule_judgement` bedömer ett svar som inte blev klart inom gränsen för modellanrop som fel.
`summarize` ger siffrorna för en grupp frågor, hela körningen eller en kategori, och
`summarize_paths` anropen per verktyg, modellanropen, läsningarna ur hänvisningar (alla, och de
som ingen tidigare sökning hade gett) och de nya försöken efter regel. Vägens tal räknas i de
frågor vars väg sparades; de andra räknas för sig som inte sparade.

### 4. `evals/answer_report.py` – rapporterna

Markdown på svenska: sammanfattningen, körningen, metoden, en tabell per kategori och per fråga,
agentens väg, tokens och kostnad per modell, domarens skäl och varje svar bredvid facit. JSON med
samma innehåll, varje verktygsanrop med alla argument och varje svar som webbappen får det, för
att jämföra körningar.

- **Sammanfattningen** har alltid raden Följdfrågor, också när agenten inte frågade något ("0 av
  30"), de nya försöken efter regel (ett utkast som flera regler underkände räknas under var och
  en, så talen kan bli fler än de nya försöken), agentens modellanrop per fråga (median och högst,
  mot gränsen `AGENT_MODEL_CALL_LIMIT`, som gäller per körning av grafen), hur många
  `read_section` som gick till ett mål ur ett tidigare svars hänvisningar och hur många av dem
  till ett mål som ingen tidigare sökning hade gett, och anropen till `resolve_reference`. Metoden
  definierar måtten, och vilka frågor de räknas i.
- **Körningen** anger commit (kort sha, och om arbetskatalogen hade ändringar som inte var
  incheckade) och sha256 av mallen `SYSTEM_PROMPT` (innan dagens datum fylls i), av granskarens
  `REVIEWER_PROMPT` och, med domare, av domarnas `JUDGE_PROMPT` och `ASK_JUDGE_PROMPT` tillsammans,
  så att rapporten säger vilken kod och vilka prompter den mätte. Commit gäller koden i processen
  som kör agenten och mätningen. Med `MCP_TRANSPORT=stdio` är avtal-mcp en barnprocess ur samma
  installation; mot `MCP_URL` kan avtal-mcp vara en annan version eller den tillfälliga ersättaren,
  och raden säger det. I compose-tjänsten `eval` finns varken git eller `.git`, så där kommer commit
  från `AVTALSAGENT_COMMIT` (se Kommandon), annars står den som okänd; prompternas hash står ändå.
- **Agentens väg** har anropen per verktyg (anrop och frågor), de nya försöken efter regel (utkast,
  frågor och fel) och en rad per fråga med anropen i ordning och argumentet som säger vad agenten
  letade efter, till exempel `search_documents("lördag", Bemanning) → read_section(9.9.2) → …`.
  Ett `read_section` till ett mål ur en hänvisning märks med ↪, en ändring ur `find_amendments`
  med Δ och ett verktygsfel med ✗. Ett utkast som kontrollen skickade tillbaka står som
  `FinalAnswer(underkänt: regel)`, och det som blev ett svar med reservation som
  `FinalAnswer(med reservation)`, så att det inte ser ut som ett som kontrollen godkände. Under
  raden står skälen till varje nytt försök.

En fråga där körningen aldrig nådde agenten, som när sessionen mot avtal-mcp inte kom igång, står
som "inte sparat" i stället för att räknas som noll: på sin rad under Agentens väg, och som "inte
sparat för n frågor" efter modellanropen, hänvisningarna och `resolve_reference`. Talen räknas i
de övriga frågorna. I JSON är frågans `model_calls`, `steps` och `rejections` då `null`, liksom
median, högsta värde och antal vid gränsen för modellanropen när ingen fråga sparade dem; utskriften
säger "not saved".

### 5. `evals/run_answer_eval.py` – körningen och kommandoraden

Gör modellerna, prövar avtal-mcp en gång, kör frågorna med en egen MCP-session var
(`--concurrency` åt gången), låter domaren bedöma varje svar när det är klart och skriver
rapporterna. Fel som stoppar körningen ger en rad och slutkod 1, som agentens kommandorad. Före
frågorna noterar `run_info` commit (`measured_commit`: ur git, annars ur `AVTALSAGENT_COMMIT`)
och prompternas sha256.

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
| `tests/unit/evals/test_answer_run.py` | 19 | En fråga genom agentens riktiga graf med en skriptad modell: svar, utkast, verktyg med argument, modellanrop och tokens; `ask_user` får det fasta svaret, eller frågans förtydligande, och alternativen sparas per motfråga; ett utkast som kontrollen och ett som granskaren skickar tillbaka, med skälen ur återkopplingen; ett `read_section` till ett mål ur ett tidigare svars hänvisningar; ett verktygsfel; ett fel med en hemlighet som döljs; en nyckel som OpenAI inte tar emot stoppar körningen; tidsgränsen; gränsen för modellanrop; ett felaktigt argument som inte gör att svaret går förlorat; tokens, kostnad och felgrupper |
| `tests/unit/evals/test_answer_steps.py` | 17 | Varje anrop i ordning med argument och utfall; mål ur hänvisningar (position eller nummer, bara ur svar före anropet, inte hela filer, också ur `resolve_reference` och ur JSON-texten) och ändringar ur `find_amendments`; om en tidigare sökning hade gett avsnittet (träff eller kopia, bara ur lyckade svar före anropet); positioner som inte är siffrorna 0–9 ("²", "①", "٣٧"); varje regels egna fel räknas till rätt regel; återkopplingens rader och de underkända utkasten |
| `tests/unit/evals/test_answer_scores.py` | 27 | Citatens positioner ur utkastet, källor på plats (position eller nummer), bara godkända citat, avtal med samma nyckel, regeln för svar utan utkast, sammanfattningen (där ett svar utan utkast inte räknas som "framgår inte"), agentens väg (verktyg, modellanrop, läsningar ur hänvisningar med och utan sökträff och nya försök efter regel, i de frågor vars väg sparades), kategorierna och percentilen; motfrågorna (rätt bara när den skiljer alternativen åt, onödig, ej bedömd, en körning som aldrig frågar, en körning som aldrig nådde grafen och därför varken bedöms eller räknas, och en som frågade innan den föll och räknas) och poängen per bedömning |
| `tests/unit/evals/test_answer_report.py` | 29 | Rapporternas namn, Markdown med svenska tal, skäl, fel och svar som citat, utan domare, en domare som inte svarade, svar utan utkast, samma modell som agent och granskare, raden Följdfrågor också vid noll, vägen per fråga med argument och märken, utkastet som blev ett svar med reservation, tabellerna per verktyg och regel, båda måtten för hänvisningar och `resolve_reference`, en fråga som aldrig nådde agenten (inte sparat, `null` i JSON) och en som nådde den utan anrop (noll), metodens definitioner, commit (ur git eller `AVTALSAGENT_COMMIT`, och vad den gäller) och prompternas hash (domarnas bara med domare), JSON, utskriften, och mappen som prövas före körningen och vid skrivning; avsnittet Motfrågor (fråga, inte fråga, onödig, en körning som aldrig nådde agenten och inte räknas, och metodtexten), dess JSON och utskrift, och en körning av baslinjen (namn, de fasta stegen, 0 av de frågor där den borde fråga) |
| `tests/unit/evals/test_judge.py` | 10 | Domarens klient, frågan med båda svaren, förtydligandet efter frågan, fallen och facits fall när frågan inte ställdes, att ingen text kan avsluta sitt element, det strikta schemat, ett lyckat och två misslyckade anrop |
| `tests/unit/evals/test_run_answer_eval.py` | 26 | Urvalet av frågor, inställningarna, commit och ändringar ur git (och utan git eller utanför ett repo, oberoende av var testets mapp ligger och av `GIT_DIR`), commit ur `AVTALSAGENT_COMMIT` när git inte kan svara, prompternas hash (domarnas bara med domare), bedömningen (ingen utan domare), avtal-mcp som inte svarar, en nyckel som OpenAI inte tar emot, frågor åt gången i facits ordning, kommandoradens utskrift och fel, och en mapp som inte går att skriva i stoppar före första frågan; `--mode workflow` (baslinjens graf, rapportens namn och hashen av baslinjens prompter), förtydligandet till domaren som svaret agenten fick, eller som facits fall när den borde ha frågat men inte gjorde det, motfrågornas domare bara där agenten skulle fråga och frågade, och båda domarnas tokens |

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
  funnits i en sökträff. Det strängare måttet räknar bara mål som ingen tidigare sökning hade gett,
  men ett sådant avsnitt kan agenten också ha hittat i en innehållsförteckning (`get_outline`).
- **Ett svar med reservation märks, inte varför.** Kontrollen sparar inte i grafens tillstånd om
  det sista utkastet underkändes när de nya försöken var slut eller om något i det inte gick att
  kontrollera, så vägen märker utkastet med svarets status; reservationerna säger vilket.
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

## Baslinjen och oklara frågor (tillagt efter M12)

Rekryteringspanelens granskning pekade ut den största luckan: att agenten slår ett fast
arbetsflöde var ett påstående, inte ett mått. Mätningen har därför fått en baslinje och stöd för
frågor där agenten ska fråga användaren. Besluten och varför står i
[ADR 0024](../adr/0024-baslinje-fast-workflow.md).

### Kommandon

```bash
# Baslinjen på de 30 testfrågorna, med samma modell, kontroll, granskare, gränser och domare:
uv run python -m evals.run_answer_eval --mode workflow
uv run python -m evals.run_answer_eval                 # agenten, som förut

# Jämför två rapporter av samma testsamling (A och B; B−A med 95 %-intervall):
uv run python -m evals.compare_answer_runs \
    evals/reports/answers-gpt-6.1-sol-low.json \
    evals/reports/answers-gpt-6.1-sol-low-workflow.json

# Oklara frågor (en fil i samma format med should_ask, options och clarification):
uv run python -m evals.run_answer_eval --gold <oklara-frågor>.jsonl
uv run python -m evals.run_answer_eval --gold <oklara-frågor>.jsonl --mode workflow
```

Baslinjens rapporter heter `answers-<modell>-<resonemang>-workflow[-<etikett>]`, och jämförelsen
skrivs till `compare-<A>-vs-<B>.md` i `--out` (standard `evals/reports/`). Jämförelsen vägrar två
rapporter av olika testsamlingar (guldfilens sha256), med olika frågor (till exempel olika
`--only`), med olika domare (modell, resonemang eller prompt) eller mot olika avtal-mcp, och listar
de övriga inställningar där körningarna skiljer sig åt.

### Vad som mäts

- **Baslinjen** (`evals/workflow_baseline.py`) tar samma steg i samma ordning för varje fråga:
  ett modellanrop som läser frågan (område, delområde, avtal, leverantör och två sökfrågor),
  `search_register` när frågan nämner ett område, ett delområde, ett avtal eller en leverantör,
  `search_documents` och `read_section` på de fem första olika avsnitten, `find_amendments` på
  dem och `read_section` på högst fem ändringar, och `search_documents` i Frågor och svar med
  `read_section` på de tre första träffarna. Några fasta regler gör det agenten gör när den läser
  ett fel eller `total`: registret görs om utan delområdet och sedan utan avtalet när det vägrar,
  och läses sida för sida upp till 100 rader; ger registret ett enda avtal för leverantören eller
  en enda upphandling för delområdet begränsas sökningen till det, som agentens regel 1 gör; en
  sökning som vägras görs om utan avtalsnumret och sedan utan området; och en sökfråga kortas
  till 500 tecken. Sedan skriver modellen svaret utan verktyg, och agentens kontroll, granskare
  och nya försök tar vid. Stegen står i rapportens väg per fråga som agentens anrop gör, varje
  steg sparas för sig (en körning som avbryts behåller stegen före), och läsningen av frågan
  räknas som ett modellanrop.
- **Jämförelsen** (`evals/compare_answer_runs.py`) ger för båda körningarna, totalt och per
  kategori: rätt (rätt 1, delvis rätt 0,5, fel 0; en fråga utan svar, ett fel i körningen,
  räknas som fel, och ett svar som domaren inte bedömde räknas inte), andelen kontrollerade
  svar, facits källor, tid och modellanrop (median) och kostnad, och den parade skillnaden B−A
  med antalet parade frågor, för rätt och för facits källor, med ett 95 %-intervall när minst tio
  frågor ingår (inte i de flesta kategorierna, som har tre till nio frågor); vidare
  bedömningarna sida vid sida per fråga, och motfrågorna när testsamlingen har sådana.
- **Oklara frågor:** en fråga i testsamlingen kan ange `should_ask` (om agenten ska fråga),
  `options` (alternativen en bra motfråga ger, minst två när den ska fråga) och `clarification`
  (användarens svar). Frågar agenten får den förtydligandet i stället för det fasta svaret, och
  domaren läser förtydligandet med frågan. Frågar den inte (baslinjen kan aldrig) läser domaren
  vilka fall frågan passar och vilket facit bygger på, och ett svar för det fallet, som säger att
  det gäller det fallet, har kärnan; att inte fråga räknas då bara under Motfrågor. En andra domare
  (`evals/ask_judge.py`, samma modell och nivå) avgör om motfrågan låter användaren välja mellan de
  väntade alternativen. Rapportens avsnitt Motfrågor visar per fråga om agenten skulle fråga, om den
  frågade, motfrågan med alternativen, om den skiljer alternativen åt och varför, och bedömningen av
  svaret, med raden "frågade när den borde i n av m (k med en motfråga som skiljer alternativen åt);
  frågade i onödan i n av m". En fråga där körningen aldrig nådde agenten, och där den inte
  frågade, räknas inte i något av talen. Baslinjen kan inte fråga och får 0 av m.

En provkörning 2026-10-07 mot ersättaren för avtal-mcp: baslinjen på q01 blev rätt och
kontrollerad på 30 sekunder med två modellanrop; agenten på den oklara frågan a01 frågade om
uppdraget är högst eller över 1 000 timmar (domaren: skiljer alternativen åt), fick
förtydligandet och svarade rätt; baslinjen på a01 svarade fel. Utkastet med de oklara frågorna
(v1) är inte godkänt och ligger inte i repot.

**Oklara frågor v2 (2026-10-07).** Den omskrivna testsamlingen ligger i
`evals/datasets/ambiguous_sv.jsonl`, med tre grupper: två frågor där agenten ska fråga (a08 och
a09), tre där den ska svara för varje fall (a01, a02 och a04) och tre kontroller där svaret är
detsamma överallt (a05, a06 och a07). Frågan a03 är struken, eftersom den ligger på gränsen mellan
få fall och för många. Frågorna är skrivna för den nya regel 4, så mätningen av den kräver både
den här grenen och PR #34 (regel 4 och `ask_user` med 2–5 alternativ); den här grenen ensam mäter
den gamla regeln. Frågedomarens prompt och rapportens metodtext följer den nya regeln. Simon
godkände frågorna som facit 2026-10-08 (resultatet står nedan). Kör
`uv run python -m evals.run_answer_eval --gold evals/datasets/ambiguous_sv.jsonl`, med
`--mode workflow` för baslinjen, och gärna med `--label oklara`, eftersom rapportens namn inte
säger vilken testsamling den mätte.

### Resultat: agenten mot baslinjen

Jämförelsen kördes 2026-10-07 på de 30 testfrågorna, för agenten och baslinjen med
resonemangsnivån `low` och `medium`, mot ersättaren för avtal-mcp (ingen Postgres). Agenten är
den från grenen `claude/tankar-fragor-2gc1kx` (ADR 0025 på den grenen): den nya regel 4 i
prompten, `ask_user` med 2–5 alternativ och sammanfattningar av resonemanget
(`AGENT_REASONING_SUMMARY`). I körningen var den grenen sammanslagen med den här (commit 7aa6519,
utan oincheckade ändringar). Alla fyra körningarna hade samma agentmodell (`gpt-6.1-sol`),
granskare (`gpt-6-astra`, `low`), domare (`gpt-6-astra`, `medium`, samma prompter) och gränser,
fyra frågor åt gången. Alla 120 frågor blev klara: inga fel, ingen tidsgräns, inget svar med
reservation, och alla bedömdes. B−A är baslinjen minus agenten i procentenheter (p.e.), parat på
frågan, med 95 %-intervall.

`low`:

| Mått | Agenten | Baslinjen | B−A |
|---|---|---|---|
| Rätt (poäng) | 28 av 30 (93 %) | 24 av 30 (80 %) | −13 p.e. (−32 till +3) |
| Kontrollerade svar | 30 av 30 | 28 av 30 (inget svar: q17, q29) | – |
| Citat ordagrant i sitt avsnitt | 75 av 75 | 71 av 71 | – |
| Facits källor citerade | 27 av 36 (75 %) | 15 av 36 (42 %) | −32 p.e. (−55 till −8) |
| Tid per fråga (median) | 38 s | 29 s | – |
| Modellanrop per fråga | median 6, högst 9 | median 2, högst 3 | – |
| Kostnad, agent och granskare | 2,24–5,63 USD | 2,70–3,21 USD | – |

`medium`:

| Mått | Agenten | Baslinjen | B−A |
|---|---|---|---|
| Rätt (poäng) | 29 av 30 (97 %) | 24,5 av 30 (82 %) | −15 p.e. (−28 till −3) |
| Kontrollerade svar | 30 av 30 | 28 av 30 (inget svar: q17, q29) | – |
| Citat ordagrant i sitt avsnitt | 77 av 77 | 69 av 69 | – |
| Facits källor citerade | 31 av 36 (86 %) | 15 av 36 (42 %) | −38 p.e. (−55 till −21) |
| Tid per fråga (median) | 42 s | 30 s | – |
| Modellanrop per fråga | median 5, högst 8 | median 2, högst 3 | – |
| Kostnad, agent och granskare | 2,28–5,59 USD | 2,74–3,41 USD | – |

Rätt är över 30 frågor och facits källor över de 22 frågor som har källor i dokumenten. Domaren
kostade 0,44–0,48 USD per körning, och alla fyra körningarna med domaren 11,82–19,69 USD.

Rätt per kategori, B−A (varje kategori har 3–9 frågor, för få för ett intervall):

- `low`: enkel uppslagning +6 (agenten 7,5, baslinjen 8 av 9), registerfråga +0,
  flerstegshänvisning −36 (6,5 mot 4 av 7), ändring ersätter klausul +0, jämförelse −67 (3 mot 1
  av 3), fråga utan svar +0. Bedömningarna skiljer sig i 9 av 30 frågor.
- `medium`: enkel uppslagning +6 (8,5 mot 9 av 9), registerfråga +0, flerstegshänvisning −50 (6,5
  mot 3 av 7), ändring ersätter klausul +0, jämförelse −33 (3 mot 2 av 3), fråga utan svar −12 (4
  mot 3,5 av 4). Bedömningarna skiljer sig i 7 av 30 frågor.
- Agenten `low` (A) mot `medium` (B): 28 mot 29 rätt, +3 p.e. (+0 till +10); bara q06 skiljer.

**Vad siffrorna säger.**

- Agenten svarade rätt på 4–4,5 frågor fler än baslinjen. På `medium` utesluter intervallet 0, på
  `low` inte (−32 till +3), så där kan skillnaden i rätt svar vara slump. Agenten citerar fler av
  facits källor (27 och 31 av 36 mot 15), men en del av skillnaden kommer av att källor räknas på
  plats (se Kända begränsningar); räknas samma klausul i en annan fil utesluter intervallet 0 bara
  på `medium`.
- Skillnaden kommer från flerstegsfrågorna och jämförelserna. På enkla uppslagningar är baslinjen
  lika bra eller något bättre (8–9 av 9), eftersom den alltid läser fem avsnitt och skriver
  fullständigare svar.
- Agenten är långsammare (median 38–42 s mot 29–30 s) och gör fler modellanrop (5–6 mot 2).
  Kostnaden är ungefär densamma; vilken som är billigast beror på priset för cachad indata, som
  saknas i prislistan (81–84 % av agentens indata var cachad, 12–19 % av baslinjens).
- `medium` ändrar nästan inget för agenten. Skillnaden är en fråga, q06, som var fel i alla tre
  körningarna på `low` i M11 och i denna men rätt på `medium`; det kan bero på nivån, men en
  körning på `medium` räcker inte för att säga det. Modellen resonerade knappt: 924
  resonemangstokens i 159 anrop (omkring sex per anrop), mot 53 i 168 på `low`.

**Stickprov.** Var agenten vinner (q19 och q25 prövade mot facit):

- **q19:** agenten tog fram innehållsförteckningen (`get_outline`) för Pulsens prisbilaga, läste
  avsnittet med leverantörernas namn och prisavsnittet bredvid, och fann 1 071,70 kr. Baslinjen
  läste bilagans regler men inte de två avsnitten och svarade att det inte går att avgöra.
- **q25:** agenten slog upp delområdena i registret och begränsade sökningen med det (på
  `medium` till ett avtalsnummer ur vartdera delområdet, 23.3-1688-2024-001 och -010; på `low`
  till upphandlingen 23.3-1688-2024) och läste 1.6 i båda huvuddokumenten: 600 miljoner och 1,4
  miljarder kr. Baslinjens registeranrop vägrades för ett sammanslaget delområde; den sökte i
  hela området och läste den äldre Ansökningsinbjudan 1.5.3 (1,2 och 1,6 miljarder kr), frågans
  fälla. Granskaren släppte igenom svaret, eftersom citatet stöder texten.
- **q17:** baslinjens läsning av frågan lade "Verksamhetens IT-behov" i Programvaror och
  tjänster, så allt den läste var i fel område, och den gav inget svar. Agenten fann rätt avtal
  i registret.
- **q15:** agenten sökte en gång till för att placera Visby (på `low` "Småland öarna Gotlands
  län", på `medium` "Gotland Visby Småland rangordning"); baslinjen kan inte ta det andra steget.
- **q01 och q26 (`low`):** baslinjens sökning i dokumenten gav inte avsnittet med svaret (9.25.4
  respektive vägledningens 2.5 med Redpill Linpros pris), och båda blev rätt på `medium`. Det är
  variation mellan körningar, inte en skillnad mellan agent och arbetsflöde.

Var agenten förlorar:

- **q06 (`low`, prövad mot facit):** agenten läste bara den första träffen, vägledningens 3.6
  (tio arbetsdagar). Baslinjen läser alltid fem avsnitt, såg också 1.9.4.1 (15 dagar, facit) och
  pekade ut motsägelsen. På `medium` läste agenten 1.9.4.1 och svarade rätt.
- **q08 (båda nivåerna, prövad mot facit):** agenten läste rätt klausul men utelämnade att den
  bara gäller programvara och/eller publik molntjänst, och blev delvis rätt. Baslinjens längre
  svar återger hela klausulen.
- **q18 (`low`):** utan "exklusive moms", som i M11. Det är en sträng bedömning av en detalj.

Vägarna stämmer med det de ska göra. Baslinjen tog sina fasta steg i varje fråga: 13–21
verktygsanrop i samma ordning, `find_amendments` exakt 150 gånger (30 × 5), två modellanrop per
fråga (tre med ett nytt försök), utan avvikelser. Agenten gjorde 1–20 verktygsanrop: den sökte
igen, begränsade sökningen med registrets avtalsnummer och använde `get_outline` och
`list_documents`. Den följde däremot aldrig en hänvisning (0 `read_section` till ett mål ur en
hänvisning, 0 `resolve_reference`), så dess försprång kommer från att söka igen och välja vad den
läser, inte från hänvisningarna. Agenten frågade användaren i 0 av 30, och testsamlingen har inga
frågor med `should_ask`, så den nya regel 4 och motfrågorna mäts inte här.

Rapporterna (fyra körningar och tre jämförelser) ligger utanför repot, eftersom
`evals/reports/` ignoreras av git:

```
/mnt/project-files/case-tokentek/implementering/matning-2026-10-07/
```

### Resultat: de oklara frågorna (2026-10-08)

Simon godkände de åtta oklara frågorna (v2) som facit 2026-10-08. Samma dag kördes de en gång för
agenten och en gång för baslinjen, med resonemangsnivån `low` och mot ersättaren för avtal-mcp
(ingen Postgres). Den nya regel 4 finns bara i PR #34, så körningen gick på en lokal gren där
PR #34:s gren (`claude/tankar-fragor-2gc1kx`) var sammanslagen med den här (commit 175b2ce, utan
oincheckade ändringar, inte pushad). Modellerna och gränserna var desamma som i jämförelsen
ovan, och svarsdomarens prompt likaså. Frågedomarens prompt följer nu den nya regel 4 och har
raden om fler än fem alternativ (domarnas sha256 `6c128d582c28` mot `c9ee05406838`). Ingen fråga
kördes om i körningen. Agentens a04 föll efter 16 s på "avtal-mcp: ReadError", utan svar och utan
sparade steg. Ersättaren loggade inget fel och svarade på de andra frågorna, så orsaken är okänd.
a04 räknas som fel i poängen. Den kördes om för sig efteråt (se Omkörningen av a04 nedan), och
omkörningen ingår inte i talen här.

| Mått | Agenten | Baslinjen | B−A |
|---|---|---|---|
| Rätt (poäng) | 6 av 8 | 5 av 8 | −12 p.e. (för få frågor för ett intervall) |
| Rätt, delvis rätt, fel | 5, 2, 0 och a04 fel i körningen | 3, 4, 1 | – |
| Kontrollerade svar | 7 av 8 | 8 av 8 | – |
| Facits källor citerade | 9 av 17 | 9 av 17 | +4 p.e. (andel per fråga) |
| Tid per fråga (median, längst) | 45 s, 150 s | 39 s, 117 s | – |
| Modellanrop per fråga (median) | 7 | 2 | – |
| Kostnad, agent och granskare | 0,85–2,12 USD | 1,16–1,52 USD | – |
| Kostnad, domarna | 0,16 USD | 0,19 USD | – |

De två körningarna kostade tillsammans 2,36–3,99 USD med domarna. Agentens körning tog 171 s och
baslinjens 183 s, fyra frågor åt gången.

Per grupp (poäng som ovan):

| Grupp | Agentens motfrågor | Agentens svar | Baslinjens svar |
|---|---|---|---|
| A, ska fråga (a08, a09) | i 2 av 2, skiljer fallen åt i 1 (a08) | 2 av 2 | 2 av 2 |
| B, svara för varje fall (a01, a02, a04) | i onödan i 1 av 2 (a02) | 2 av 3 | 1 av 3 |
| C, kontroller (a05, a06, a07) | i onödan i 1 av 3 (a07) | 2 av 3 | 2 av 3 |

Baslinjen kan inte fråga: 0 av 2 i grupp A och ingen onödig motfråga. Svaren räknas för sig. I
grupp A fick båda Rätt på båda frågorna: agenten efter förtydligandet, och baslinjen eftersom
domaren läser vilket fall facit bygger på när ingen frågar (domarens regel 7). Baslinjens a09 är
hela tabellen med sju takpriser, alltså det långa svar som grupp A ska undvika. Baslinjens enda
fel är a01, där den svarade att underlaget inte räcker. Utan a04 blir poängen 6 av 7 för agenten
mot 4,5 av 7 för baslinjen.

Agentens motfrågor:

- **a08** (demots fråga 1): "Vilken uppsägning gäller din fråga?" med fyra alternativ, i
  korthet: vi som avropare säger upp utan skäl, vi säger upp för leverantörens avtalsbrott,
  leverantören säger upp vårt kontrakt, och uppsägning av själva ramavtalet. De motsvarar facits
  fyra fall ett mot ett, och domaren fann att motfrågan skiljer dem åt. Likheten är väntad:
  facits alternativ är nästan ordagrant agentens fyra val i demokörningarna 2026-10-07
  (`demo-fragorna.md` i `matning-2026-10-07/`, se `docs/demo.md`), inte tagna ur prompten.
  Svaret efter förtydligandet var rätt.
- **a09:** "Vilken ramavtalsleverantör anlitar ni på IT-drift Större?" med fyra alternativ, i
  korthet: Advania eller Videnca, Iver eller Orange, Fujitsu eller Shibuya, och Nordlo. Facit har
  sju leverantörer, och `ask_user` tar högst fem alternativ. Domaren fann att motfrågan inte
  skiljer fallen åt, eftersom den slår ihop leverantörer som ska kunna särskiljas. Domaren nämner
  inte priserna, men varje par har två olika takpriser (1 116 och 733, 585 och 744,
  1 321 och 1 324 kr). Paren behövdes inte: ett av fem alternativ var oanvänt, och agenten
  frågade direkt efter registret, innan den hade läst pristabellen. Svaret efter förtydligandet
  (Iver, 585 kr per timme exklusive moms) var rätt.
- **a02** (grupp B, onödig): agenten läste båda taken (50 och 25 procent) och frågade sedan om
  delområdet, och dessutom om kontraktsvärde, skada och särskilda villkor. Svaret för alla fem
  delområden kom först när mätningen svarade "Svara för alla". a01 är därför den enda frågan i
  grupp B där agenten svarade för alla fall utan att fråga. a04 föll, och i omkörningen svarade
  agenten bara för ett delområde.
- **a07** (kontroll, onödig): agentens första utkast svarade utan att fråga, men granskaren
  skickade tillbaka det, eftersom regeln bara var citerad för Programvarulösningar. Först då
  frågade agenten om delområdet. Rätt drag enligt facit var att se att avsnittet Skadestånd är
  ordagrant lika i alla fyra delområdenas allmänna villkor (2.29, 7.30, 6.30 och 7.29) och svara
  utan att fråga. Efter motfrågan citerade agenten i stället bara vägledningens 2.8, utan att ange
  undantagen från taket, och fick 0 av 2 av facits källor.

**Granskningen för hand.** Två granskningar prövade alla bedömningar: svaren mot facit och
avsnittens lagrade text, och motfrågorna mot regel 4. Ingen av dem är oenig med domarna, och alla
59 citat står ordagrant i sitt avsnitt. Två bedömningar ligger nära gränsen. Baslinjens a07 är
delvis rätt men nästan rätt, och fylligare än agentens a07, som fick samma bedömning. Agentens a01
är rätt, fast "nej" för uppdrag på högst 1 000 timmar bara är underförstått. Granskningen fann tre
luckor i räkningen, texten och prompten. De är rättade efter körningen, så rapporterna i mappen
har kvar de gamla talen och den gamla texten:

- Rapporten räknade a04 bland frågorna där agenten inte skulle fråga ("frågade i onödan i 2 av
  6"), fast körningen aldrig nådde agenten. Nu räknar `summarize_asks` i
  `evals/answer_scores.py` inte en fråga där körningen aldrig nådde grafen och agenten inte
  frågade, varken bland frågorna där den skulle fråga eller bland dem där den inte skulle.
  Rapportens rad i Motfrågor säger då "räknas inte". Jämförelsen räknar nu motfrågorna ur
  frågornas rader, så en ny jämförelse av de här rapporterna ger de nya talen. Agenten frågade i
  onödan i 2 av 5, och baslinjen i 0 av 6, eftersom baslinjens a04 nådde grafen. Frågade när
  den borde är oförändrat: 2 av 2 (1 skiljer) mot 0 av 2. En fråga i grupp A vars körning
  faller räknas alltså inte heller som "frågade inte".
- Rapportens metodtext (punkten Motfrågor i `evals/answer_report.py`) sade att ett svar utan
  motfråga bedöms "mot frågan som den ställdes". Nu säger den att domaren läser förtydligandet
  med frågan när agenten frågade. När agenten inte frågade i en fråga där den skulle, får domaren
  i stället veta vilka fall frågan passar och vilket fall facit bygger på (regel 7), och ett svar
  för det fallet som säger att det gäller det fallet har kärnan. Det är därför baslinjen fick Rätt
  på a08 och a09. Bara övriga svar bedöms mot frågan som den ställdes.
- Frågedomarens regel 1 sade inte om undantaget för fler än fem alternativ behåller förbudet i
  regel 1 och 2 mot att slå ihop alternativ med olika svar. Nu slutar regeln med "Även då får
  inget av agentens alternativ slå ihop väntade alternativ som har olika svar." a09 bedömdes före
  den meningen, och domarens "skiljer inte" stämmer med den. Domarnas sha256 är nu
  `942ed5d2db0b`, så `compare_answer_runs` vägrar jämföra en ny körning med de här rapporterna.

**Omkörningen av a04.** Agentens a04 kördes om en gång för sig efter transportfelet, på samma
commit (175b2ce) och med samma modeller, gränser och prompter, 2026-10-08 kl. 03:11–03:12 UTC.
Den föll inte. Agenten frågade inte, vilket är rätt i grupp B, men svarade bara för
Programvarulösningar: leverantören får säga upp kontraktet helt eller delvis, med 60
arbetsdagars uppsägningstid. Det stämmer med facit men är ett av fyra delområden. Svaret säger
inte att Licenser och licenstjänster har samma regel, och inte att Informationsförsörjning och
Systemutveckling saknar den. Domaren gav Delvis rätt, svaret var kontrollerat, och agenten
citerade 1 av facits 3 källor. Granskaren skickade tillbaka ett första utkast som sade ja för
hela området, med en källa bara ur Programvarulösningar. Agenten smalnade då av svaret i stället
för att läsa de andra delområdena. Dess första sökning, gjord om efteråt mot ersättaren, ger
träffar också i dem. Baslinjens a04 var också Delvis rätt och täckte bara Licenser och
licenstjänster. Omkörningen tog 56 s och 7 modellanrop och kostade 0,13–0,31 USD för agent och
granskare. Läggs den in i agentens körning i stället för den som föll, blandas två körningar. Då
blir det 6,5 av 8 mot 5 av 8 (B−A −19 p.e.), 2,5 av 3 i grupp B, 10 av 17 av facits källor (B−A
0 p.e. per fråga) och onödiga motfrågor i 2 av 6.

**Vad åtta frågor kan visa.** Varje arm kördes en gång, och grupp A har två frågor. En fråga är
12,5 procentenheter, och jämförelsen ger inget intervall under tio frågor. B−A på −12 p.e. är
alltså ingen säker skillnad. a04 drar åt andra hållet: agentens körning föll och räknas som fel,
och utan a04 blir B−A −21 p.e. (6 av 7 mot 4,5 av 7), men inte heller det är en säker skillnad
på sju frågor. Frågorna visar ett mönster: agenten frågar när regel 4 säger att den ska, men ger
inte alltid alternativ som skiljer fallen åt (a09), och den frågar också där den borde svara för
alla fall (a02) eller där svaret är detsamma (a07). De visar inte hur ofta det händer. I
omkörningen av a04 frågade agenten inte, men svarade bara för ett delområde, som baslinjen. En ny
körning kan ge andra motfrågor, och domarens bedömning av a09 kan skifta. Grupp A ligger nära
guldfrågorna (a08 är q04 efter förtydligandet och a09 ligger nära q24), så den mäter frågandet
mer än svaren. Körningen gick mot ersättaren och bör göras om mot den riktiga databasen.

Rapporterna (två körningar, omkörningen av a04, en jämförelse och `LASMIG.md`) ligger utanför
repot, eftersom `evals/reports/` ignoreras av git:

```
/mnt/project-files/case-tokentek/implementering/matning-2026-10-08/
```

`LASMIG.md` skrevs före granskningen för hand. Dess rad om a09 (paren "för att få plats med högst
fem alternativ"), agentens 2 av 6 onödiga motfrågor och att a04 inte kördes om rättas av texten
ovan.

### Tester

| Fil | Tester | Vad |
|---|---:|---|
| `tests/unit/evals/test_workflow_baseline.py` | 23 | De fasta stegen i ordning med sina argument, sparade som steg (en ändring märkt som ändring); läsningen av frågan räknas som modellanrop, mot gränsen, och dess tokens når körningens räknare; modellen erbjuds bara `FinalAnswer`; kontrollen skickar tillbaka ett dåligt utkast; ett verktygsfel (också ett verktyg som saknas) stoppar inte flödet; en plan som inte går att läsa ger en sökning på frågan utan filter; registrets enda upphandling eller avtal begränsar sökningen (också när avtalsnumret är tomt, och två upphandlingar gör det inte); registret läst sida för sida upp till taket; ett vägrat delområde och avtalsnummer tas bort och stegen går vidare; sökfrågor kortas och en för kort blir frågan; en krasch i ett senare steg behåller stegen före och läsningens modellanrop; prompten delar agentens regler ordagrant och går inte att bygga om agentens prompt ändras (också en ny regel 7); planens områden täcker testsamlingens |
| `tests/unit/evals/test_ask_judge.py` | 7 | Motfrågans domare: prompten följer regel 4 och högst fem alternativ (utan att slå ihop alternativ med olika svar), frågan, de väntade alternativen och agentens motfrågor med alternativ, att ingen text kan avsluta sitt element, det strikta schemat, ett lyckat och två misslyckade anrop |
| `tests/unit/evals/test_compare_answer_runs.py` | 10 | Två rapporter sida vid sida (domaren, samma inställningar, för få frågor för ett intervall), den parade skillnaden över frågor som bedömts i båda, en fråga utan svar som räknas som fel (och ett intervall över tolv frågor) men inte i två rapporter utan domare, olika testsamlingar, frågor, domare (också domarnas prompter) och avtal-mcp vägras, inställningar som skiljer sig listas, kommandoraden, motfrågornas tal (också ej bedömda, räknade ur frågornas rader utan en körning som aldrig nådde agenten, och baslinjens rad bara när en körning är baslinjen) och en rapport från före läget |
| `tests/unit/evals/test_gold.py` | 14 nya (64) | Fälten för motfrågor: utan dem som förut, giltiga, och tio sätt att ange dem fel; `ambiguous_sv.jsonl` med två frågor där agenten ska fråga och sex där den inte ska |

Ändrade filer har fått tester i tabellen ovan (talen där gäller nu).

### Kända begränsningar

- **Baslinjen är vår konstruktion.** Ett annat fast flöde kunde göra bättre eller sämre ifrån
  sig; gränserna (fem, fem och tre avsnitt) är valda, inte uppmätta. Baslinjen kan inte söka
  igen, följa hänvisningar, bläddra i registret, räkna datum eller fråga användaren, och det är
  just det jämförelsen ska visa värdet av.
- **Ersättaren.** Jämförelsen är körd mot ersättaren för avtal-mcp; den bör köras om mot den
  riktiga databasen innan den visas.
- **Motfrågornas domare är en modell**, och med två frågor där agenten ska fråga är talet grovt.
- **En körning per fråga och läge.** Intervallet tar hänsyn till antalet frågor, inte till att
  svaren varierar mellan körningar. Enskilda frågor skiftade mellan `low` och `medium`: q01, q18,
  q26 och q29 för baslinjen, och q06 för agenten (fel i alla fyra körningarna på `low`, tre i M11
  och denna).
- **Två körningar åt gången.** Körningarna gick två och två (åtta frågor åt gången mot samma
  ersättare), så tiderna går att jämföra inom ett par men sämre mellan nivåerna.
- **Facits källor räknas på plats**, och det drabbar mest baslinjen: den citerar ofta samma
  klausul ur Upphandlingsdokumentet eller Anbudsinbjudan i stället för facits Allmänna villkor
  eller Avropsrutin (q02, q03, q14 och q16 på `low`; q03, q07, q14, q15 och q16 på `medium`),
  agenten bara i q17 på `low`. Räknas de blir B−A för källorna −22 p.e. (−45 till +1) på `low`
  och −24 p.e. (−40 till −11) på `medium`, i stället för −32 och −38.
- **Baslinjens rapport använder agentens ord** på några ställen ("där agenten svarade att det
  inte framgår", "Modellanrop: agentens"). Raden om "framgår inte" räknar bara status Inget svar,
  inte kontrollerade svar som säger att det avgörande inte går att fastställa, och raden om mål
  ur en hänvisning betyder inget för ett fast flöde.
