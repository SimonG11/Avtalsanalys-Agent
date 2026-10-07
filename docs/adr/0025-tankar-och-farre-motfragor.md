# ADR 0025: Agentens tankar som eget syfte och OpenAI:s sammanfattningar, och färre motfrågor

**Status:** Föreslaget.
Svarar på Simons synpunkter efter att han provat webbappen 2026-10-07: färre motfrågor, ställda på
ett trevligare sätt än en ruta ovanpå, och att se hur agenten tänker, "som om man kan se chain of
thought". Webbappens del står i `webbapp-kontrakt.md`, punkterna 29-32 och 39. Kompletterat samma
kväll med beslut 7, agentens eget syfte i varje steg, efter att sammanfattningarna mätts.

## Kontext

- **OpenAI visar inte resonemanget.** Agentmodellen (`gpt-6.1-sol`, ADR 0005) resonerar före varje
  steg, men Responses API ger aldrig själva resonemanget. Det ger en sammanfattning av det
  (`reasoning.summary`: `auto`, `concise` eller `detailed`) när man ber om den, annars en
  resonemangspost utan text. Sammanfattningen skrivs av OpenAI, inte av agenten.
- **Adaptern kan redan visa den.** ag-ui-langgraph (0.0.46) gör om sammanfattningen, som
  langchain-openai strömmar som innehållsblock av typen `reasoning`, till AG-UI:s `REASONING_*`
  -händelser, som CopilotKit kan visa. Metadatan `emit-messages: False`, som håller agentens text
  ur strömmen ([ADR 0013](0013-agenten.md)), gäller inte dem. Granskaren strömmas inte alls
  (`disable_streaming`, [ADR 0015](0015-valideringskedjan.md)).
- **En resonemangspost måste skickas tillbaka rätt.** Responses API vill ha tillbaka modellens
  poster i samma ordning som de kom, eller inte alls. langchain-openai skickar en post med dess id
  (`rs_…`), som OpenAI hittar eftersom svaren sparas (`store`, standard). Utan sparade svar skulle
  posten behöva sin krypterade text (`include: reasoning.encrypted_content`).
- **Regel 4 gav motfrågor där ett svar hade räckt.** Regeln sa att agenten skulle fråga med
  `ask_user` när frågan passar flera avtal eller delområden med olika svar. Granskningen
  2026-10-07 mätte att agenten frågade i demot men aldrig i de 30 testfrågorna, som är ställda
  precist. Demots fråga 1 ("Vad är uppsägningstiden i IT-driftavtalet?") är vald för att den
  frågar. Där passar svaret två delområden och två eller tre fall (kundens uppsägning utan skäl,
  vid avtalsbrott, leverantörens), och svaret på vart och ett är en mening. Simon tyckte att
  agenten frågade för mycket.
- **Sammanfattningarna räckte inte för att visa hur agenten tänker.** Mätningen nedan gav en
  sammanfattning i 1 av 47 modellanrop med `low` och 4 av 42 med `medium`, alla på engelska, och
  en läste 6.21 som "the Sixth Amendment". Webbappen visade alltså nästan aldrig en tanke, och
  när den gjorde det var den på fel språk och ibland fel.
- **Alternativen var valfria.** `ask_user` kunde komma utan `options`, så webbappen kunde inte
  alltid visa en knapp per svar i stället för en ruta med ett textfält.

## Beslut

1. **Agentmodellen ber om sammanfattningen i varje anrop.** `make_agent_model` sätter
   `reasoning={"effort": AGENT_REASONING_EFFORT, "summary": AGENT_REASONING_SUMMARY}`. Den nya
   inställningen `AGENT_REASONING_SUMMARY` är `auto` (standard, OpenAI väljer längden),
   `concise`, `detailed` eller `off`, som inte ber om någon. Granskaren ber inte om någon och
   strömmas inte, som förut.
2. **Strömmen är adapterns.** För varje modellanrop med en
   sammanfattning kommer `REASONING_START`, `REASONING_MESSAGE_START` (rollen `reasoning`),
   `REASONING_MESSAGE_CONTENT` med texten i delar, `REASONING_MESSAGE_END` och `REASONING_END`,
   före verktygsanropen från samma anrop. En sammanfattning i flera delar ger ett sådant block per
   del: den första delen har OpenAI:s id (`rs_…`) som `messageId`, de följande ett eget.
   `MESSAGES_SNAPSHOT` har varje resonemangspost som ett meddelande med rollen `reasoning` före
   modellens meddelande, med id `rs_…` och delarna åtskilda av en radbrytning, utan krypterad text.
   `STATE_SNAPSHOT` är fortfarande bara `{"answer": ...}`, svaret strömmas fortfarande aldrig som
   text, och ett fel ger samma fasta text.
3. **Sammanfattningen skickas tillbaka med sitt id.** Resonemangsposten ligger kvar i modellens
   meddelande i checkpointen, och langchain-openai skickar den, med sammanfattningen, före
   verktygsanropet i nästa anrop. Det gäller också efter `ask_user` och i en följdfråga.
   `AvtalAguiAgent` tar bort resonemangsmeddelandena ur historiken som webbappen skickar tillbaka:
   checkpointen har redan varje sammanfattning i modellens meddelanden. Efter en körning som
   misslyckades eller stoppades finns ingen `MESSAGES_SNAPSHOT`, och webbappen har modellens
   meddelande under det strömmade id:t, inte checkpointens. Adaptern skulle då lägga till det som
   ett nytt meddelande med sammanfattningen, och OpenAI skulle få samma resonemangspost två gånger
   och neka varje följande anrop i tråden. Inget ändras i `store` eller `include`.
4. **Texten visas som OpenAI skriver den.** Sammanfattningen är på engelska och börjar ofta med en
   rubrik i `**fetstil**`. Två rader i prompten, "Skriv dina resonemang på svenska." och "Skriv
   sammanfattningen av ditt resonemang på svenska, inte på engelska.", gav fortfarande engelska i
   provet nedan, så prompten får ingen sådan rad. Webbappen ger tankarna en svensk etikett.
5. **Regel 4 svarar för varje fall när det går kort.** Den lyder nu: "Passar frågan flera avtal,
   delområden eller fall med olika svar: svara för vart och ett när det går kort (några få fall,
   en eller två meningar var) och säg vad som avgör. Fråga med ask_user bara när fallen är för
   många eller svaren för långa för ett svar, eller när svaret beror på uppgifter om användarens
   eget fall, till exempel vilken leverantör, roll eller vilket kontraktsvärde. Ge då 2-5 korta
   alternativ i options som delar upp det som avgör svaret, till exempel de vanligaste fallen
   eller ett intervall." Beskrivningen av `options` i verktygets schema ger leverantörerna,
   rollerna eller beloppsgränserna som exempel. Resten av prompten är oförändrad.
6. **`ask_user` kräver 2-5 alternativ.** `options` är obligatoriskt i argumentens schema, med 2-5
   strängar som inte är tomma. Ett anrop utan dem nekas innan verktyget körs; modellen läser
   felet som anropets svar och kan fråga igen, och körningen fortsätter. Svaret är som förut en
   sträng, det valda alternativet eller egen text, och en avbruten fråga når modellen som
   "Användaren svarade inte på frågan". Det nekade anropet strömmas ändå som ett steg:
   `TOOL_CALL_START`, `TOOL_CALL_ARGS` och `TOOL_CALL_END` för `ask_user` (till exempel utan
   `options` eller med ett enda alternativ), men inget `TOOL_CALL_RESULT` och inget avbrott. I
   `MESSAGES_SNAPSHOT` har verktygsmeddelandet pydantics felet på engelska som innehåll och
   `error` satt. Webbappen visar en fråga bara för avbrottet och visar inte ett nekat steg som
   "Fick svar" (kontraktets punkt 32).
7. **Varje steg har agentens eget syfte, på svenska.** Modellen får avtal-mcp:s verktyg med ett
   argument till, `syfte`, först bland argumenten och obligatoriskt: "En kort mening om vad du
   vill ta reda på med anropet och varför. Användaren ser den som din tanke.", högst 200 tecken.
   Argumentet läggs till i grafen (`agent/purpose.py`, middleware `StatedPurpose` i
   `build_agent`), inte i avtal-mcp: servern och dess verktyg är desamma för alla klienter, och
   `McpTools.tools`, som baslinjen med fast arbetsflöde anropar direkt, är oförändrade. Modellen
   binds till kopior av verktygen med `syfte` i schemat; LangGraph kör originalen, och
   `syfte` tas bort ur anropet innan det går till avtal-mcp, som får exakt de argument det får
   i dag (dess verktyg tar inte emot okända argument). Verktygens namn, beskrivningar, svar och
   felhantering är oförändrade. OpenAI håller inte modellen till schemat (verktygen är inte
   strikta), så ett anrop utan `syfte`, med ett tomt eller med ett längre än 200 tecken nekas
   innan verktyget körs: modellen läser "Anropet nekades: syfte saknas. Ange i syfte en kort
   mening …" som anropets svar, med status error, och kan anropa igen; körningen fortsätter. Det
   nekade anropet strömmas som ett steg utan `TOOL_CALL_RESULT`, som ett nekat `ask_user`.
   `ask_user` och `FinalAnswer` får inget `syfte`: frågan visas som den är, och svaret är inget
   steg. Prompten får ingen rad om syftet; beskrivningen i schemat räckte i provet (15 av 15
   anrop). Webbappen läser `syfte` ur stegets argument (kontraktets punkt 39), och kommandoraden
   skriver det på en egen rad under anropet ("  Syfte: …"). OpenAI:s sammanfattningar (beslut
   1-4) finns kvar och visas när de kommer.

## Konsekvenser

- **Provet** (2026-10-07, `gpt-6.1-sol` genom `make_agent_model`, systemprompten och ett låtsat
  sökverktyg, strömmat, ungefär 45 anrop för uppskattningsvis under 0,25 USD): sammanfattningar
  kommer, men bara när modellen resonerar några tiotal tokens eller mer. Med resonemangsnivån `low`
  (standard) resonerade modellen 0-30 tokens per anrop, och 1 av ungefär 20 anrop hade en
  sammanfattning. Med `medium` hade 4 av 5 anrop som läste det låtsade verktygets sökträffar en,
  men inget av de första anropen, som gick direkt till en sökning; med avtal-mcp:s sökträffar höll
  det inte (nästa punkt). Den första texten kom 2-3 sekunder in i ett anrop på 5-7 sekunder, före
  anropets svar. Alla sammanfattningar var på engelska. Ett anrop med
  föregående anrops resonemangspost och sammanfattning i historiken (en följdfråga) gick igenom.
- **Demofrågorna** (2026-10-07) kördes genom `POST /agui` med agentmodellen och granskaren, mot
  den tillfälliga ersättaren för avtal-mcp med pilotens data och inte mot Postgres, en gång per
  fråga och tre gånger för fråga 1, med `low` och med `medium`: 16 körningar. Alla gav rätt fakta
  mot facit och statusen Verifierat, utom q27 med `medium`, som blev Inget svar med "framgår inte"
  (godtaget i demot). Tiderna räknar inte med tiden för att svara på en fråga. Körningarna med
  `low` och `medium` gick samtidigt, med varandra och med mätningen av de 30 testfrågorna mot
  samma ersättare, så en tid kan vara några sekunder för lång.

  | | `low` | `medium` |
  |---|---|---|
  | Tankar som visades | 1 i 47 anrop (1 av 8 körningar) | 4 i 42 anrop (3 av 8 körningar) |
  | Resonemangstokens, alla 8 körningar | 30 | 545 |
  | Fråga 1: frågade med `ask_user` | 3 av 3, med 4 alternativ | 2 av 3, med 4 och 3 alternativ |
  | Fråga 1: tid | 37, 42 och 46 s | 46, 69 och 70 s |
  | Övriga frågor: tid | 14-46 s | 14-42 s |
  | Kostnad, agent och granskare, 8 körningar | 0,51-1,55 USD | 0,61-1,62 USD |

  Tankarna per fråga: med `low` en (390 tecken) i fråga 1:s första körning och ingen annars. Med
  `medium` en (408 tecken) i fråga 1:s andra körning, två (816 tecken) i dess tredje och en (467
  tecken) i q14, och ingen i q10, q21, q27 eller q24. Kostnaden är ett intervall, från cachad
  indata gratis till cachad indata till fullt pris, eftersom `evals/answer_run.py` inte har något
  pris för cachad indata; alla 16 körningarna kostade 1,13-3,16 USD. Med `medium` tog fråga 1
  längre tid i två av tre körningar, eftersom agenten läste fler avsnitt (6.21.7 och 6.21.9) och
  skrev längre svar.
- **Få tankar syns, också med `medium`.** Webbappen visar en rad "Tänker" bara för anrop med en
  sammanfattning, och modellen resonerar nästan inte. `medium` gav fyra tankar mot en, men gör
  inte tankarna till något demot kan lova, och kostar ungefär 5-20 % mer och upp till en halv
  minut mer i fråga 1. Standarden förblir `low`; `AGENT_REASONING_EFFORT=medium` är Simons val.
  Ska tankarna synas i de flesta steg behövs troligen `high` eller
  `AGENT_REASONING_SUMMARY=detailed`; ingen av dem är mätt. Därför beslut 7: agentens eget syfte
  kommer med varje steg.
- **Sammanfattningens text kontrolleras inte.** Alla tankar var på engelska med en rubrik i
  fetstil, och de kan vara fel eller pratiga: en läste 6.21 som "the Sixth Amendment", en annan
  slutade med "Let's sort this out together!". Svaret kontrolleras som förut; tankarna gör det
  inte.
- **Syftet är agentens motivering, inte dess resonemang.** Modellen skriver `syfte` som ett
  argument bland de andra, i samma utdata som anropet. Det säger vad agenten säger att den vill
  ta reda på och varför, inte hur den kom fram till anropet, och OpenAI:s dolda resonemang är
  lika dolt som förut. Ingenting kontrollerar syftet: ett vagt eller fel syfte visas som det är.
  Svaret kontrolleras som förut, och det är svaret som ska gå att lita på. Dokumentationen och
  webbappen ska inte kalla syftet modellens resonemang.
- **Provet av syftet** (2026-10-07 kväll, `gpt-6.1-sol` med `low` genom `make_agent_model` och
  `build_agent`, mot ersättaren för avtal-mcp, en körning per fråga, 0,34-0,82 USD med körningarna
  utan syfte): demots fråga 1, q14 och q10 gav 15 anrop till avtal-mcp, alla med ett rimligt
  svenskt syfte, alla med `syfte` först bland argumenten och inget nekat. Exempel: "Hitta regler
  om uppsägningstid i IT-driftavtalen." (search_documents), "Läsa villkoret för er uppsägning
  utan skäl." (read_section efter svaret på `ask_user`), "Kontrollera ändringar av
  uppsägningsvillkoret för IT-drift Mindre." (find_amendments), "Läsa definitionen som avgör om
  lördag ligger utanför Arbetsdag." (q14) och "Hämta Nordlo Advances avtalsnummer,
  organisationsnummer och tidigare namn för IT-drift Mindre." (q10). Alla tre svaren blev
  Verifierat, och fråga 1 frågade med `ask_user` som förut. Syftena var 40-94 tecken, i
  genomsnitt 62.

  | | Med syfte | Utan syfte |
  |---|---|---|
  | Fråga 1: modellanrop, tid | 6 anrop, 45 s | 6 anrop, 39 s |
  | q14: modellanrop, tid | 6 anrop, 44 s | 5 anrop, 39 s |
  | q10: modellanrop, tid | 2 anrop, 16 s | (inte körd) |
  | Utdatatokens per modellanrop (fråga 1, q14) | 187, 194 | 191, 202 |

  En körning per fråga skiljer sig mer än syftet gör: fråga 1 tog 37-46 s i de tre körningarna
  med `low` ovan, och körningen utan syfte gjorde 9 verktygsanrop mot 7 med.
- **Vad syftet kostar:** ungefär 15-25 utdatatokens per verktygsanrop (62 tecken svensk text i
  genomsnitt; uppskattat, ingen tokeniserare fanns att tillgå), alltså ungefär 0,0002 USD per
  anrop och 0,002 USD för en fråga med 7-8 anrop till `gpt-6.1-sol`s pris för utdata. Syftet
  skickas sedan med som indata i varje följande anrop, mest som cachad indata. Skillnaden syntes
  inte i mätningen: utdatatokens per anrop varierade mer mellan körningarna än syftet väger.
- **Mätningen** ([ADR 0019](0019-matning-av-svaren.md)) kör `build_agent` och får alltså också
  syften; ett nekat anrop räknas som ett verktygsfel. Rapporten visar inga argument.
- **Kostnaden** för sammanfattningarna är liten: sammanfattningen syntes inte i utdatatokens i
  provet (utdata var resonemang plus svar). Den skickas tillbaka som indata i följande anrop,
  ungefär 100 ord per sammanfattning; det är inte mätt. Mätningen
  ([ADR 0019](0019-matning-av-svaren.md)) använder `make_agent_model` och ber alltså också om
  sammanfattningar.
- **Demots fråga 1 frågar fortfarande oftast.** Ett prov av bara steget efter den första
  sökningen, med låtsade sökträffar, frågade med den nya prompten 1 av 3 gånger. Hela körningarna
  ovan frågade i 3 av 3 med `low` och i 2 av 3 med `medium`, efter den första sökningen eller
  sökningarna och innan agenten läst något avsnitt. Med `low` var frågan nästan ordagrant
  densamma varje gång ("Menar du uppsägning av ert avropade kontrakt eller av själva ramavtalet,
  och vem ska säga upp det?"), och det första alternativet, att säga upp det egna kontraktet utan
  särskilt skäl, är demots val. En tanke säger varför: "too many options, possibly five variants".
  Modellen räknar ramavtalet som ett eget fall, i två delområden, och då är fallen för många
  enligt regel 4. Den körning med `medium` som inte frågade svarade för alla tre fallen (6.21.7,
  6.21.8 och 6.21.9), rätt och med sex kontrollerade citat, men slutade svaret med en fråga i
  texten ("Menar du ert avropade kontrakt eller själva ramavtalet …?") i stället för `ask_user`,
  så webbappen visar inga knappar för den. Ska fråga 1 besvaras direkt behöver prompten eller
  frågan ändras, till exempel så att agenten utgår från det avropade kontraktet när frågan inte
  nämner ramavtalet; det är inte beslutat här. `docs/demo.md` och README:n beskriver nu fråga 1
  som en fråga där agenten frågar med alternativ. De 30 testfrågorna kördes om samma dag med den
  nya regeln mot ersättaren, och agenten frågade i 0 av 30 med både `low` och `medium`.
- **Ett återupptaget `ask_user` strömmas två gånger.** När frågan är besvarad skickar den
  återupptagna körningen `TOOL_CALL_START` och `TOOL_CALL_ARGS` för samma `ask_user`-anrop igen,
  med samma `toolCallId`, före `TOOL_CALL_RESULT`. Webbappen behöver slå ihop stegen på id för att
  inte visa frågan två gånger.
- **En fråga som väntar när ändringen tas i drift** (ett `ask_user`-anrop utan `options` i en
  checkpoint) nekas när användaren svarar: LangGraph kör verktygsanropet igen mot det nya
  schemat, användarens svar når inte modellen, och modellen läser att `options` saknas och frågar
  igen med alternativ. Körningen fortsätter. Det gäller API:ts trådar i Postgres och kommandoraden
  med `CHECKPOINTER=postgres`. Ska väntande samtal klara driftsättningen behöver frågorna besvaras
  före den; inget i schemat kan skilja ett återupptaget anrop från ett nytt.
- **Mätningen av motfrågor** (ADR 0024, föreslagen i en annan tråd) räknar en fråga med
  `should_ask` som missad när agenten inte frågar. Med regel 4 svarar agenten för vart och ett
  när fallen är få och svaren korta, så utkastets fyra frågor där agenten ska fråga behöver
  prövas mot regel 4 innan det godkänns.
- **Webbappen** visar frågan som ett meddelande i chatten med en knapp per alternativ och ett
  fält för eget svar (kontraktets punkt 32), och ett nekat `ask_user`-steg inte som "Fick svar".
  Kommandoraden numrerar alternativen som förut.
- **Tester för syftet:** `tests/unit/agent/test_purpose.py` visar schemat modellen ser (`syfte`
  först och obligatoriskt, beskrivningen, högst 200 tecken, inget för `ask_user` och
  `FinalAnswer`), att `syfte` tas bort innan anropet når avtal-mcp (en MCP-session som spelar in
  argumenten, och ersättarservern, som nekar ett okänt argument), att ett anrop utan syfte, med
  ett tomt, ett som inte är text eller ett för långt nekas och att modellen rättar det och
  körningen fortsätter, och att `McpTools.tools` är oförändrade efter `build_agent`.
  `tests/unit/api/test_api_purpose.py` visar att `syfte` strömmas först i `TOOL_CALL_ARGS`, i
  delar, och finns kvar i `MESSAGES_SNAPSHOT`, och `tests/unit/agent/test_agent_cli.py`
  kommandoradens rad.
- **Tester:** `tests/unit/api/test_api_reasoning.py` kör appen genom `POST /agui` med modellen
  som `make_agent_model` bygger den, mot en låtsad OpenAI som strömmar en resonemangspost med
  sammanfattning och sedan ett anrop, som OpenAI gör. Testerna visar ordningen (tanke, steg,
  tanke, steg), att inget blir text och att ögonblicksbilden är svaret, vad `MESSAGES_SNAPSHOT`
  innehåller, och att varje post skickas tillbaka en gång, i ordning, efter `ask_user` och i en
  följdfråga, och att en sammanfattning som webbappen behöll efter en misslyckad körning inte
  skickas två gånger. Andra tester visar att `ask_user` utan 2-5 alternativ nekas och att
  modellen kan fråga igen, regel 4 och inställningen.

## Alternativ som valts bort

- **Själva resonemanget.** OpenAI ger det inte. Den krypterade texten
  (`reasoning.encrypted_content`) går bara att skicka tillbaka, inte att läsa.
- **Att låta agenten skriva sina tankar som text före varje steg.** Det kräver att agentens text
  strömmas, vilket `emit-messages: False` stänger av för att svaret inte ska synas före
  kontrollen, och en regel i prompten. Syftet som argument (beslut 7) ger en mening per steg
  utan det, och schemat håller den kort.
- **`syfte` i avtal-mcp.** Servern skulle ta emot ett argument den inte använder, alla klienter
  skulle behöva skriva det, och baslinjen med fast arbetsflöde skulle behöva ändras.
- **Att korta ett för långt syfte i stället för att neka det.** Modellen höll sig under 100
  tecken i provet; ett nekat anrop kostar ett modellanrop, men ett avkortat syfte kan sluta mitt
  i en mening.
- **Att översätta sammanfattningen till svenska** med ett eget modellanrop: ett anrop till per
  steg, och tankarna kommer senare än steget.
- **`detailed` som standard.** Längre texter i varje steg; `auto` låter OpenAI välja.
- **Att behålla regel 4 och bara visa frågan snyggare.** Simon bad också om färre frågor, och ett
  kort svar för varje fall ger användaren svaret direkt.
- **Ett alternativ, eller fler än fem.** Ett alternativ är inget val, och fler än fem knappar är
  svåra att läsa. Användaren kan alltid svara med egen text.
