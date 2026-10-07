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
  inte kan, men det syns inte i siffrorna. Det finns ett utkast med sju oklara frågor (fyra där
  agenten ska fråga och tre kontroller där svaret är detsamma i alla alternativ), ännu inte
  godkänt och därför inte i repot.
- **Ingen Postgres i utvecklingsmiljön.** Som i ADR 0019 körs mätningen mot den tillfälliga
  ersättaren för avtal-mcp, och mot den riktiga databasen där den finns.

## Beslut

1. **Baslinjen är ett fast arbetsflöde i `evals/`, inte i `src/`** (`evals/workflow_baseline.py`).
   Det är ett mätverktyg, och agenten ändras inte för det. `build_workflow` ger en graf som går
   att köra precis där agentens går: `create_agent` med samma strukturerade svar
   (`ToolStrategy(FinalAnswer)`), samma tillstånd och samma middleware, importerad från agentens
   moduler: dagens datum i systemprompten, `AnswerCheck` med `VALIDATION_RETRIES`,
   `ModelCallLimitMiddleware`, `AnswerOpenToolCalls` och `ToolErrorMiddleware`. Modellen får inga
   verktyg, bara `FinalAnswer`, så den kan bara svara.
2. **Stegen är de en noggrann person alltid tar, i samma ordning för varje fråga**
   (`FixedRetrieval`, före första modellanropet):
   1. ett strukturerat modellanrop som läser frågan (`QueryPlan`): ramavtalsområde (ett av
      pilotens fyra, eller inget), delområde, avtalsnummer, leverantör, en sökfråga och en
      sökfråga för Frågor och svar;
   2. `search_register` när frågan nämner ett område, ett avtal eller en leverantör;
   3. `search_documents` med sökfrågan och filtren, och `read_section` på de fem första olika
      avsnitten, hela avsnitt och inte utdrag;
   4. `find_amendments` på varje avsnitt som lästes, och `read_section` på högst fem ändrande
      avsnitt;
   5. `search_documents` i Frågor och svar (`document_type` `questions_and_answers`) och
      `read_section` på de tre första träffarna.

   Det blir högst 21 verktygsanrop och 13 lästa avsnitt, ungefär dubbelt så många anrop som
   agentens median (6,6), så baslinjen förlorar inte på att ha läst för lite. Varje steg står i
   samtalet som agentens: ett AI-meddelande med stegets verktygsanrop och verktygens svar, gjorda
   med avtal-mcp:s egna verktyg. Kontrollen, vägen i rapporten och modellen läser dem därför på
   samma sätt. Ett verktyg som svarar med fel noteras, och flödet går vidare.
3. **Baslinjens systemprompt delar agentens regler för svaret ordagrant.** Rollen, regeln om den
   senaste lydelsen, reglerna om det som inte framgår och om text i dokument, och hela avsnittet
   "Svaret" klipps ur `SYSTEM_PROMPT` vid rubrikerna. Till det kommer att underlaget står i
   samtalet, att det inte finns några verktyg och ingen användare att fråga, och att ett datum som
   måste räknas fram inte skrivs (det finns ingen `calculate_date`). Ändras rubrikerna i agentens
   prompt går baslinjens prompt inte att bygga, så baslinjen svarar aldrig efter regler som
   agenten inte längre har.
4. **Läsningen av frågan räknas som ett modellanrop.** Den räknas in i
   `ModelCallLimitMiddleware`s räknare för tråden och körningen, så gränsen och rapporten tar med
   den, och dess tokens räknas med agentmodellens.
5. **Samma mätning, ett läge till.** `python -m evals.run_answer_eval --mode workflow` kör
   baslinjen; bedömningen, domaren, källorna, registeruppgifterna och kostnaden är desamma.
   Rapporten heter `answers-<modell>-<resonemang>-workflow[-<etikett>]`, säger att det är
   baslinjen och listar stegen. `python -m evals.compare_answer_runs A.json B.json` jämför två
   rapporter av samma guldfil och samma frågor (annars vägrar den): per kategori och totalt, med
   rätt (rätt 1, delvis rätt 0,5, fel 0), andelen kontrollerade svar, facits källor, tid,
   modellanrop och kostnad, och den parade skillnaden B−A med ett 95 %-intervall
   (`metrics.paired_bootstrap`) för rätt och för facits källor.
6. **Oklara frågor i testsamlingens format.** En fråga kan säga om agenten ska fråga användaren
   (`should_ask`), vilka alternativ en bra motfråga ger (`options`) och användarens svar
   (`clarification`). Frågar agenten får den förtydligandet som svar. Domaren läser då
   förtydligandet med frågan, eftersom facit bygger på det; frågar agenten inte bedöms svaret mot
   frågan som den ställdes. En andra domare (samma modell och nivå) avgör om motfrågan låter
   användaren välja mellan de väntade alternativen, och en motfråga räknas som rätt bara då. För
   en kontrollfråga är det rätt att inte fråga, och en motfråga räknas som onödig. Rapporten får
   avsnittet Motfrågor. Baslinjen kan inte fråga och får 0 av de frågor där den borde.
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
- Baslinjen gör två modellanrop per fråga men skickar fler lästa avsnitt i varje än agenten
  brukar läsa. En provkörning av q01 mot ersättaren tog 30 sekunder, kostade 0,09 USD för
  agentmodellen och granskaren och blev rätt; vad hela jämförelsen visar är inte mätt än.
- Motfrågornas domare är en språkmodell, som svarsdomaren, och kan döma fel; dess skäl står i
  rapporten. Med fyra frågor där agenten ska fråga är talet grovt, och utkastet behöver godkännas
  innan det läggs i repot.
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
