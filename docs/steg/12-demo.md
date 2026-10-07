# M12 – Demot, README:n och hela systemet mot den riktiga databasen

**Mål:** göra systemet färdigt att visa och granska: en README som svarar på hur uppgiften är löst,
hur systemet fungerar, vilka val som gjorts och hur det är kontrollerat, ett demoskript med fem
frågor som visar agentens styrkor, och en körning av hela systemet mot den riktiga databasen så att
siffrorna i README:n är systemets. Besluten och varför står i [ADR 0022](../adr/0022-demot.md).

**Klart när:** en ny utvecklare kan starta allt med `docker compose up` och köra demot. README:n
beskriver systemet som det är byggt. Demoskriptet har fem frågor, vad som visas för varje och vad
som görs om något går fel. Demofrågorna, sökningen och de 30 testfrågorna är körda mot den riktiga
databasen. Ingen kod ändras i M12; felet som körningen hittade är rättat i PR #22.

## Resultat

Körningen gjordes 2026-10-07 på `main` (`023ed01`, efter PR #20 och #21) i en Linux-container. Postgres 17
med pgvector körde i Docker. avtal-mcp, API:t och webbappens produktionsbygge körde direkt på
värden från samma kod (`uv` och `npm start`), eftersom miljön inte kunde bygga containrarna (se
Kända begränsningar). Modellerna var de riktiga: `gpt-6.1-sol` som agent och `gpt-6-astra` som
granskare, båda med resonemangsnivån `low`.

### Inläsningen

`ingestion run` med tolkningscachen tog knappt tio minuter (11:19–11:29): hämtningen från avropa.se
fem och en halv minut, tolkningen ur cachen fyra sekunder, avsnitten och kontrollen mot registret
en halv minut, och sökindexet (steg 6) 217 sekunder, mest embeddings för 13 803 texter.

| Mått | Resultat |
|---|---|
| Registret (Giltiga ramavtal 2026-10-07) | 4 507 rader, 0 underkända; 2 361 avtal, 797 leverantörer, 79 upphandlingar |
| Filer | 207 (3 924 PDF-sidor), alla tolkningar ur cachen, 0 misslyckade |
| Avsnitt och chunkar | 13 175 avsnitt, 13 979 chunkar |
| Hänvisningar upplösta | 74,6 % (7 519 av 10 080); bara med regler 73,5 % |
| Fynd | karantän 7, rapport 3, notering 11; 9 godkända i `accepted_findings.toml` |
| I karantän | 3 filer och 4 avsnitt |
| Täckning | 66 av 121 avtal täckta; 54 har bara upphandlingens version av huvuddokumentet, 0 bara dokument i karantän, 1 inte täckt |
| Sökindexet | 13 935 chunkar ur 204 filer, 44 hålls tillbaka; 13 803 unika texter fick embeddings |

Registret hade 802 leverantörer i listan från 2026-10-05 och 797 i listan från 2026-10-07. Täckningen
efter de nio godkända avvikelserna är mätt här för första gången (steg 4 visar siffrorna före).

### Testerna

`uv run pytest` på `023ed01`: 2 046 godkända på 162 sekunder, 1 854 enhetstester och 192
integrationstester mot Postgres i testcontainers. På `main` efter PR #22 är det 2 047 (1 855 och
192), alla godkända. Webbappen: 67 enhetstester godkända och produktionsbygget gick igenom.

### Sökningen på det riktiga indexet

`uv run python -m evals.run_retrieval_eval` mot sökindexet i Postgres, de 22 testfrågorna med källa
i avtalstexten. Det är den första mätningen mot indexet i databasen; steg 5:s siffror kommer från
mätningens eget index i minnet. Ett integrationstest visar att det indexet rangordnar som
SQL-sökningen.

| System | R@1 | R@10 | MRR@10 | nDCG@10 | Alla@10 |
|---|---:|---:|---:|---:|---:|
| Vektor | 0,42 | 0,81 | 0,62 | 0,64 | 0,68 |
| BM25 | 0,36 | 0,69 | 0,62 | 0,57 | 0,55 |
| Hybrid | 0,49 | **0,84** | **0,78** | **0,72** | 0,77 |

Hybrid mot BM25: +0,145 i nDCG@10 (95 % intervall +0,039 till +0,257), större än slumpen. Hybrid
mot vektor: +0,079 (−0,024 till +0,187), kan vara slump. Siffrorna ligger nära steg 5:s rad för
indexet med de nio godkända avvikelserna: där var R@10 0,84, MRR@10 0,77 och nDCG@10 0,71, här
0,84, 0,78 och 0,72. Alla embeddings gjordes om i den här inläsningen, vilket kan förklara de små
skillnaderna.

### Svaren på de 30 testfrågorna

`MCP_TRANSPORT=streamable_http uv run python -m evals.run_answer_eval --label postgres`, samma
mätning som i steg 11 men mot avtal-mcp på den riktiga databasen. Steg 11:s siffror kom från
ersättaren för avtal-mcp.

| Mått | Riktiga databasen | Ersättaren, körning 1 och 2 (steg 11) |
|---|---|---|
| Rätt enligt domaren | **28 av 30** (93 %); delvis rätt 1 (q18), fel 1 (q06) | 27 och 28 av 30 |
| Status | Kontrollerat 29, Inget svar 1 (q27) | Kontrollerat 30; Kontrollerat 28 och Inget svar 2 |
| Frågor som avtalen inte besvarar | 4 av 4 fick ett rätt "framgår inte" | 4 av 4 |
| Citat som står ordagrant i sitt avsnitt | 71 av 71 | 74 av 74; 72 av 72 |
| Facits källor citerade | 27 av 36 (75 %, en undre gräns) | 28 av 36 |
| Facits avtal bland svarens registerrader | 17 av 17, inga utöver facit | 17 av 17 |
| Nya försök | 1 fråga (q08), 1 nytt försök | 3 och 2 frågor |
| Verktygsanrop per fråga | 6,7 i medel, högst 16 (q15) | 6,6 och 6,3, högst 14 |
| Tid per fråga | median 34 s, 90:e percentilen 48 s, längst 61 s (q15) | median 37 och 36 s |
| Kostnad, agent och granskare | 1,99–5,37 USD för alla 30 (0,07–0,18 per fråga) | 2,12–5,48 och 1,91–5,11 USD |
| Domarens kostnad | 0,43 USD | 0,42 och 0,43 USD |
| Hela körningen | 311 s, fyra frågor åt gången | 6 minuter |

Felen är desamma som med ersättaren: q06 citerar vägledningens tio arbetsdagar i stället för
delområdets 15, och q18 har rätt pris och villkor men utan "exklusive moms". q26, som var fel i
steg 11:s första körning, blev rätt.

### Demofrågorna

Varje fråga ställdes en gång i terminalen (`python -m avtalsagent.agent --json`, med svaren på
agentens frågor inmatade i förväg). Fråga 1–5 ställdes också i webbappen, fråga 3 en gång till
efter rättelsen nedan, och reservfrågan bara i terminalen. Tiden i terminalen räknar med att
programmet startar. Verktygsanropen är anrop till avtal-mcp, som i mätningen.

| Fråga | Terminalen | Webbappen |
|---|---|---|
| 1 Uppsägningstiden i IT-drift | Verifierat, 3 citat (6.21.7–6.21.9), 54 s med tre frågor till användaren; 7 verktygsanrop | Verifierat, 2 källor, 52 s med en fråga till användaren |
| 2 q10 Nordlo Advance | Verifierat, registerraden, 19 s; bara `search_register` | Verifierat, 12 s, ett steg |
| 3 q14 Lördagsarbete | Verifierat, 3 citat (9.9.2, 9.2, prisbilagan), 50 s; 9 verktygsanrop | Verifierat, 2 källor (9.9.2, 9.2), 39 s, 7 steg |
| 4 q21 Antal anbud | Verifierat, 2 citat (rättelsen 2024-02-20 och 3.2), 36 s; 5 verktygsanrop | Verifierat, 2 källor, 32 s, 7 steg |
| 5 q27 Rangordnad etta | Inget svar, "framgår inte", 1 citat (10.5.1), 31 s; 7 verktygsanrop | Inget svar, 35 s, 8 steg |
| R q24 Lägsta takpris | Verifierat, Iver Sverige AB 585 kr, 2 citat (2.7.3), 42 s; 6 verktygsanrop | – |

Alla citat stod ordagrant i sina avsnitt. Frågorna agenten ställde i fråga 1 skilde sig mellan
körningarna. I terminalen var svaren skrivna i förväg ("IT-drift Mindre", "Kontraktet",
"Kontraktet") och passade inte frågorna, så agenten frågade tre gånger: först om det gäller
kontraktet, leverantörens uppsägning eller ramavtalet, sedan vem som säger upp och varför. I
webbappen frågade den en gång, om frågan gäller det avropade kontraktet eller ramavtalet och vem
som säger upp.

### Felet som körningen hittade

I webbappen föll fråga 3 första gången med "Agenten kunde inte svara på frågan. Försök igen om en
stund." API:ts logg visade `GraphRecursionError: Recursion limit of 25 reached` efter sex
modellanrop. Orsaken: `ag-ui-langgraph` 0.0.46 bygger körningens konfiguration med
`ensure_config`, som sätter LangChains standardgräns på 25 steg i grafen. Den gränsen ersätter den
som `create_agent` sätter (9 999), och `make_agui_agent` i `api/agui.py` skickar ingen egen. Med
middleware tar ett modellanrop ungefär fyra steg, så en fråga med fler än ungefär sex modellanrop
föll i webbappen. Terminalen och mätningen anropar grafen direkt och påverkades inte, och CI:s
tester kör bara korta frågor genom adaptern, med en skriptad modell och högst två modellanrop, så
gränsen nåddes aldrig där. I mätningen ovan behövde 10 av 30 frågor sju eller fler modellanrop.

Rättelsen ger `AvtalAguiAgent` grafens egen gräns i konfigurationen; gränsen på 16 modellanrop
(`AGENT_MODEL_CALL_LIMIT`) begränsar fortfarande körningen. Med den provade lokalt gick fråga 3, 4
och 5 igenom i webbappen (tiderna ovan). M12 ändrar inte koden, så rättelsen gjordes i PR #22, med
ett test som kör en fråga med nio modellanrop genom `POST /agui`. Den är sammanslagen i `main`.

Andra iakttagelser från webbappen, som också lämnats vidare:

- Källpanelen markerar inte alltid ett citat som fortsätter på nästa sida. Citatet ur 6.21.8 i
  fråga 1 börjar på sidan 26, som panelen visar, och slutar på sidan 27. I PDF.js ligger radslutet
  efter "enligt" i en egen tom textbit, så panelen godtar inte början som en del av citatet och
  visar "Citatet hittades inte i sidans text." bredvid "Kontrollerat mot avtalstexten". PR #23
  (öppen) rättar det: panelen öppnar sidan 26, markerar början och säger att bara en del av
  citatet finns på sidan.
- CopilotKit visar en engelsk rad, "Thought for a few seconds", mellan stegen. PR #23 (öppen) byter
  den mot svensk text.
- Webbappen visar inte när kontrollen skickar tillbaka ett utkast; terminalen gör det.

## Kommandon

```bash
# Hela systemet, som i README:n
docker compose --profile ingest run --rm ingest
docker compose up -d --wait --wait-timeout 300

# Mätningarna mot den riktiga databasen
uv run python -m evals.run_retrieval_eval
docker compose --profile eval run --rm eval

# En demofråga i terminalen, mot tjänsterna i compose
docker compose exec api python -m avtalsagent.agent "Vad är uppsägningstiden i IT-driftavtalet?"
```

## Vad som byggdes, fil för fil

### 1. `README.md` – ingången

Omskriven för den som granskar: hur systemet startas, uppgiften och varför den passar en agent,
med exempel ur demot; arkitekturen som den är byggd, i ett diagram och en fråga från början till slut;
var agenten bestämmer, vad systemprompten styr och var koden bestämmer; kontrollerna i sex lager med siffrorna ovan;
arbetssättet med Simons beslut; de kända begränsningarna; och en karta över dokumentationen. Utvecklarkommandona
finns kvar, och avsnittet om CI beskriver nu alla fem jobb.

### 2. `docs/demo.md` – demoskriptet

De fem frågorna och reservfrågan i ordning, med tider från körningen. För varje fråga: vad som
händer, vad som visas, vad som sägs, rätt svar och vad som görs om det går fel. Före frågorna står
förberedelsen dagen före och en halvtimme före, och efter dem frågor att undvika live, fem nivåer
från webbappen till skärmbilder och en felsökningstabell.

### 3. `docs/adr/0022-demot.md` – beslutet

Varför demofrågorna kommer ur testsamlingen, varför de körs mot den riktiga databasen innan de
visas, vilka frågor som undviks och hur README:n är uppbyggd.

### 4. `docs/projektide.md` och `docs/implementationsplan.md` – faserna 1 och 4

Faserna 2 och 3 fanns redan i repot (`docs/arkitektur.md` och `docs/validering.md`). Faserna 1 och
4 läggs bredvid, så som de godkändes 2026-10-05, så att hela vägen från idé till plan går att läsa
i repot. Implementationsplanens avsnitt om utvecklingsmiljöns förutsättningar är utelämnat, och
länkarna pekar på repots filer.

## Kända begränsningar

- **Containrarna byggdes inte i den här körningen.** Miljön når nätet genom en proxy vars
  certifikat bygget inte har, så avtal-mcp, API:t och webbappen körde från samma kod direkt på
  värden. CI bygger och startar hela compose-stacken på amd64 och arm64 vid varje push, men ställer
  aldrig en fråga. Den sista kontrollen är att köra demot på datorn det visas på (förberedelsen i
  `docs/demo.md`).
- **En körning per fråga.** Svaren och statusen kan skilja sig mellan körningar, särskilt frågan
  agenten ställer i fråga 1 och statusen för "framgår inte" i fråga 5.
- **Webbappens tider för fråga 3–5** kommer från körningen med rättelsen provad lokalt, samma
  ändring som PR #22 men före dess test.
- **Omrankning och spårning i Langfuse** ingår inte i M12; de görs i egna pull requests.
- **Skärmbilderna och rapporterna ligger utanför repot.** `evals/reports/` och `data/` checkas
  aldrig in, och skärmbilderna och svarsrapporten innehåller citat ur avtalstexten.

## Så verifierar du M12 själv

```bash
docker compose --profile ingest run --rm ingest
docker compose up -d --wait --wait-timeout 300
docker compose --profile eval run --rm eval
```

Öppna sedan http://localhost:3000 och gå igenom [`docs/demo.md`](../demo.md) fråga för fråga.
Kontrollera:

- att varje fråga får den status och det svar som står i demoskriptet, och att tiderna ligger nära
  tabellen ovan;
- att fråga 3 går igenom i webbappen (då finns rättelsen av gränsen för antal steg);
- att sammanfattningen i `evals/reports/answers-gpt-6.1-sol-low.md` ligger nära tabellen ovan, och
  att q06 och q18 är de som inte är rätt;
- att README:ns siffror stämmer med rapporterna.
