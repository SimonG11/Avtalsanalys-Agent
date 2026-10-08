# ADR 0024: Baslinjen: ett fast arbetsflöde med agentens modell, kontroll och domare, och motfrågor i testsamlingen

**Status:** Föreslaget.
Bygger på [ADR 0001](0001-workflow-for-inlasning-agent-for-fragor.md) (agent för frågor) och
[ADR 0019](0019-matning-av-svaren.md) (mätningen av svaren).

## Kontext

ADR 0001 valde en agent för frågorna och ett fast arbetsflöde för inläsningen, med motiveringen
att frågorna kräver steg som inte går att bestämma i förväg. Mätningen i M11 visar att agenten
svarar rätt på 27–28 av 30 testfrågor, men inte att en agent behövs för det. Rekryteringspanelens
granskning pekade ut just det som den största luckan: att agenten slår ett arbetsflöde är ett
påstående, inte ett mätt resultat.

- **En jämförelse är bara värd något mot en stark baslinje.** En sökning och ett svar ur de bästa
  utdragen vore lätt att slå, och skillnaden skulle bero på att baslinjen läser mindre, inte på
  att agenten väljer sina steg.
- **Allt annat måste vara lika.** Samma modell och resonemangsnivå, samma kontroll
  (`AnswerCheck`: citaten, registeruppgifterna, den senaste lydelsen och granskaren, med två nya
  försök), samma gräns för modellanrop, samma domare och samma frågor. Annars mäts något annat än
  skillnaden mellan att välja steg och att följa fasta steg.
- **Agentens motfrågor mäts inte.** ADR 0019 ger `ask_user` ett fast svar ("svara utifrån frågan
  som den är ställd"), eftersom testfrågorna ska gå att besvara som de är ställda. Att fråga
  användaren när frågan passar flera delområden med olika svar är en av sakerna ett arbetsflöde
  inte kan, men det syns inte i siffrorna. De oklara frågorna i
  `evals/datasets/ambiguous_sv.jsonl` är åtta, skrivna för den nya regel 4 (PR #38): två där
  agenten ska fråga, tre där den ska svara för varje fall och tre kontroller där svaret är
  detsamma i alla alternativ ([steg 11](../steg/11-utvardering.md)).
- **Ingen Postgres i utvecklingsmiljön.** Som i ADR 0019 körs mätningen mot den tillfälliga
  ersättaren för avtal-mcp, och mot den riktiga databasen där den finns.

## Beslut

1. **Baslinjen är ett fast arbetsflöde i `evals/`, inte i `src/`** (`evals/workflow_baseline.py`).
   Det är ett mätverktyg, och agenten ändras inte för det. `build_workflow` ger en graf som går
   att köra precis där agentens går: `create_agent` med samma strukturerade svar
   (`ToolStrategy(FinalAnswer)`), samma tillstånd och samma middleware, importerad från agentens
   moduler: `AnswerCheck` med `VALIDATION_RETRIES`, `ModelCallLimitMiddleware`,
   `AnswerOpenToolCalls` och `ToolErrorMiddleware`, och dagens datum i baslinjens egen
   systemprompt (punkt 3). Modellen får inga verktyg, bara `FinalAnswer`, så den kan bara svara.
2. **Stegen är de en noggrann person alltid tar, i samma ordning för varje fråga**, före första
   modellanropet och var och ett i en egen middleware (`fixed_steps`), så att LangGraph sparar
   tillståndet efter varje steg och en körning som avbryts behåller stegen före:
   1. ett strukturerat modellanrop som läser frågan (`QueryPlan`): ramavtalsområde (ett av
      pilotens fyra, eller inget), delområde, avtalsnummer, leverantör, en sökfråga och en
      sökfråga för Frågor och svar. En sökfråga kortas till verktygets 500 tecken, och en som är
      tom eller kortare än två tecken blir frågan (den för Frågor och svar blir sökfrågan);
   2. `search_register` när frågan nämner ett område, ett delområde, ett avtal eller en
      leverantör. Vägras anropet görs det om utan delområdet och sedan utan avtalet; har svaret
      fler rader än en sida (`total`) hämtas resten med `offset`, högst 100 rader. Anger frågan
      inget avtalsnummer och gav registret alla sina rader begränsar de sökningen, som agentens
      regel 1 gör: till radernas enda avtal när frågan nämner en leverantör, annars till deras
      enda upphandling när den nämner ett delområde;
   3. `search_documents` med sökfrågan, området och frågans avtalsnummer eller registrets. Vägras
      sökningen görs den om utan numret och sedan utan området. `read_section` på de fem första
      olika avsnitten, hela avsnitt och inte utdrag;
   4. `find_amendments` på varje avsnitt som lästes, och `read_section` på högst fem ändrande
      avsnitt;
   5. `search_documents` i Frågor och svar (`document_type` `questions_and_answers`) med filtren
      från sökningen som svarade, och `read_section` på de tre första träffarna.

   Det blir 21 verktygsanrop och 13 lästa avsnitt när inget anrop vägras och registret ryms på
   en sida, och högst 29 anrop med omtagningarna och registrets sidor: ungefär tre gånger så
   många anrop som agentens medel (6,6 per fråga, högst 14), så baslinjen förlorar inte på att
   ha läst för lite. Omtagningarna och begränsningen är fasta regler, inte val: det agenten gör
   när den läser ett fel eller `total`, gjort på samma sätt för varje fråga, så att baslinjen
   inte förlorar på något annat än att inte välja sina steg. Varje steg står i samtalet som
   agentens: ett AI-meddelande med stegets verktygsanrop och verktygens svar, gjorda med
   avtal-mcp:s egna verktyg. Kontrollen, vägen i rapporten och modellen läser dem därför på
   samma sätt. Ett verktyg som svarar med fel noteras, och flödet går vidare.
3. **Baslinjens systemprompt delar agentens regler för svaret ordagrant.** Rollen, regeln om den
   senaste lydelsen, reglerna om det som inte framgår och om text i dokument, och hela avsnittet
   "Svaret" klipps ur `SYSTEM_PROMPT` vid rubrikerna. Till det kommer att underlaget står i
   samtalet, att det inte finns några verktyg och ingen användare att fråga, att den svarar för
   vart och ett av fallen och säger vad som avgör när frågan passar flera avtal, delområden eller
   fall med olika svar (det agenten gör när den inte frågar, regel 4), och att ett datum som
   måste räknas fram inte skrivs (det finns ingen `calculate_date`). Ändras rubrikerna,
   reglernas nummer eller meningen om den senaste lydelsen i agentens prompt går baslinjens
   prompt inte att bygga, så baslinjen svarar aldrig efter regler som agenten inte längre har.
   Rapportens hash gäller baslinjens prompt, frågans läsning (`PLAN_PROMPT`) och dess schema.
4. **Läsningen av frågan räknas som ett modellanrop.** Den räknas in i
   `ModelCallLimitMiddleware`s räknare för tråden och körningen, så gränsen och rapporten tar med
   den, och dess tokens räknas med agentmodellens.
5. **Samma mätning, ett läge till.** `python -m evals.run_answer_eval --mode workflow` kör
   baslinjen; bedömningen, domaren, källorna, registeruppgifterna och kostnaden är desamma.
   Rapporten heter `answers-<modell>-<resonemang>-workflow[-<etikett>]`, säger att det är
   baslinjen och listar stegen. `python -m evals.compare_answer_runs A.json B.json` jämför två
   rapporter av samma guldfil och samma frågor, bedömda av samma domare (modell, resonemang och
   prompt) och mätta mot samma avtal-mcp (annars vägrar den), och listar de inställningar där
   körningarna skiljer sig åt (modell, resonemang, granskare, gränser). Den ger per kategori och
   totalt rätt (rätt 1, delvis rätt 0,5, fel 0; en fråga utan svar, ett fel i körningen, räknas som
   fel, och ett svar som domaren inte bedömde räknas inte), andelen kontrollerade svar, facits
   källor, tid, modellanrop och kostnad, och den parade skillnaden B−A med antalet parade frågor för
   rätt och för facits källor. Ett 95 %-intervall (`metrics.paired_bootstrap`) ges bara över minst
   tio frågor: på tre frågor med samma tecken har percentilintervallet ingen bredd, fast ett
   teckentest ger p = 0,25.
6. **Oklara frågor i testsamlingens format.** En fråga kan säga om agenten ska fråga användaren
   (`should_ask`), vilka alternativ en bra motfråga ger (`options`) och användarens svar
   (`clarification`). Frågar agenten får den förtydligandet som svar. Domaren läser då
   förtydligandet med frågan, eftersom facit bygger på det. Frågar den inte, som baslinjen
   aldrig kan, läser domaren vilka fall frågan passar och vilket fall facit bygger på, och ett
   svar som ger facits svar för det fallet och säger att det gäller det fallet har kärnan
   (domarens regel 7). Annars räknades oförmågan att fråga två gånger: under Motfrågor och som
   fel svar mot ett facit som bygger på ett förtydligande svaret aldrig fick. En andra domare (samma
   modell och nivå) avgör om motfrågan låter användaren välja mellan de väntade alternativen, och en
   motfråga räknas som rätt bara då. För en kontrollfråga är det rätt att inte fråga, och en
   motfråga räknas som onödig. Detsamma gäller en fråga med få fall och korta svar, där agenten
   enligt den nya regel 4 (PR #38) ska svara för vart och ett och facit täcker alla fall. En
   fråga där inget av körningen sparades räknas inte bland motfrågorna, eftersom det inte går
   att se om agenten frågade. Rapporten får avsnittet Motfrågor. Baslinjen kan inte fråga och får
   0 av de frågor där den borde.
7. **Jämförelsen körs mot ersättaren för avtal-mcp** tills den riktiga databasen finns där
   mätningen körs. Rapporten säger vilken avtal-mcp den mätte, som förut.

## Konsekvenser

- Påståendet i ADR 0001 går att pröva med två kommandon och en jämförelse, på samma frågor och
  med samma domare. En liten skillnad på de 30 frågorna är också ett resultat: då ligger agentens
  värde i det baslinjen inte kan, att följa hänvisningar, söka igen, räkna datum och fråga
  användaren, och de oklara frågorna mäter det sista.
- Baslinjen är vår egen konstruktion. Ett annat arbetsflöde kunde göra bättre eller sämre ifrån
  sig, så jämförelsen gäller just detta: ett starkt, fast arbetsflöde med samma byggstenar.
  Gränserna (fem, fem och tre avsnitt) är valda, inte uppmätta.
- Baslinjen gör minst två modellanrop per fråga (fler när kontrollen skickar tillbaka utkastet) men
  skickar fler lästa avsnitt i svarsanropet än agenten brukar läsa. En provkörning av q01 mot
  ersättaren tog 30 sekunder, kostade 0,09 USD för agentmodellen och granskaren och blev rätt.
- **Resultat (2026-10-07, mot ersättaren, med agenten från grenen `claude/tankar-fragor-2gc1kx`,
  ADR 0025 där).** Agenten svarade rätt på 28 av 30 (`low`) och 29 av 30 (`medium`), baslinjen
  på 24 och 24,5. B−A är −13 p.e. (95 %-intervall −32 till +3) på `low` och −15 p.e. (−28 till
  −3) på `medium`, så bara på `medium` är skillnaden utöver slumpen. Facits källor: 27 och 31 av
  36 för agenten mot 15 av 36 för baslinjen (−32 och −38 p.e.; räknas samma klausul i en annan
  fil på samma avtalssida blir det −22 och −24 p.e., och på `low` innehåller intervallet då 0).
  Alla citat stod ordagrant i båda. Agenten vinner på flerstegsfrågor och jämförelser, genom att
  söka igen, begränsa med registret och läsa innehållsförteckningar, men följde aldrig en
  hänvisning; på enkla uppslagningar är baslinjen lika bra eller något bättre (8 och 9 av 9 mot
  agentens 7,5 och 8,5). Baslinjen var snabbare (median 29–30 s mot 38–42 s) och kostade ungefär
  lika mycket. Agenten frågade aldrig, och testsamlingen har inga oklara frågor, så motfrågorna
  är inte mätta. Siffrorna och stickproven står i
  [steg 11](../steg/11-utvardering.md#resultat-agenten-mot-baslinjen), och rapporterna, som inte
  ligger i repot (`evals/reports/` ignoreras), i
  `/mnt/project-files/case-tokentek/implementering/matning-2026-10-07/`. Jämförelsen bör köras
  om mot den riktiga databasen innan den visas.
- **Resultat, oklara frågor (2026-10-08, mot ersättaren, med PR #38).** De åtta oklara frågorna
  kördes på `low`, en gång per arm. Agenten frågade i båda frågorna där den skulle men skilde
  fallen åt bara i a08, frågade i onödan i a02 och a07 (2 av 5 med den rättade räkningen, där
  a04 inte räknas; rapporterna säger 2 av 6) och fick 6 av 8 mot baslinjens 5 av 8, där agentens
  a04 föll på ett transportfel. En omkörning av a04 för sig, på samma commit, gav Delvis rätt
  utan motfråga: agenten svarade bara för ett av fyra delområden, som baslinjen gjorde för ett
  annat. Den ingår inte i talen. Granskningen för hand höll med domarna, men åtta frågor visar
  ett mönster och ingen säker skillnad
  ([steg 11](../steg/11-utvardering.md#resultat-de-oklara-frågorna-2026-10-08)). De tre luckor
  som granskningen fann är rättade efter körningen: en körning där inget sparades räknades bland
  motfrågorna (och en session som föll under körningen kastade det som körningen hade sparat),
  metodtexten sade att ett svar utan motfråga alltid bedöms mot frågan som den ställdes, och
  frågedomarens regel 1 sade inte att undantaget för fler än fem alternativ behåller förbudet mot
  att slå ihop alternativ med olika svar. Rapporterna ligger i
  `/mnt/project-files/case-tokentek/implementering/matning-2026-10-08/`. Också den här körningen
  bör göras om mot den riktiga databasen.
- Motfrågornas domare är en språkmodell, som svarsdomaren, och kan döma fel; dess skäl står i
  rapporten. Med två frågor där agenten ska fråga är talet grovt.
- Varje fråga körs en gång per läge, som i ADR 0019, så en enskild fråga kan skifta mellan
  körningar. Bootstrapintervallet tar hänsyn till antalet frågor, inte till att agenten svarar
  olika från gång till gång.

## Alternativ som valts bort

- **Ingen baslinje.** Då står påståendet kvar omätt, vilket var granskningens invändning.
- **En svag baslinje:** en sökning och ett svar ur utdragen. Lätt att slå, och skillnaden skulle
  bero på hur mycket som lästes, inte på agentens val.
- **Baslinjen i `src/` som en egen LangGraph-graf** utan `create_agent`. Kontrollen,
  gränsen och de nya försöken skulle behöva byggas om eller kopieras, och då är det inte längre
  samma kontroll.
- **Att erbjuda modellen verktygen men ta bort dem i varje anrop** (`wrap_model_call`). Det
  behövdes inte: OpenAI tar emot en historik med anrop till verktyg som anropet inte erbjuder
  (provat med agentmodellen), så `tools=[]` räcker.
- **Agenten med färre verktyg som baslinje.** Den väljer fortfarande sina steg, så det mäter inte
  skillnaden mot ett fast flöde.
- **En människa som bedömer motfrågorna.** Bättre, men inte för varje körning; domarens skäl gör
  att en människa kan pröva bedömningarna i efterhand.
