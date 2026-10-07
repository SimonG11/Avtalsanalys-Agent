# ADR 0025: Agentens tankar som OpenAI:s sammanfattningar, och färre motfrågor med 2-5 alternativ

**Status:** Föreslaget.
Svarar på Simons synpunkter efter att han provat webbappen 2026-10-07: färre motfrågor, ställda på
ett trevligare sätt än en ruta ovanpå, och att se hur agenten tänker, "som om man kan se chain of
thought". Webbappens del står i `webbapp-kontrakt.md`, punkterna 29-32.

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

## Konsekvenser

- **Provet** (2026-10-07, `gpt-6.1-sol` genom `make_agent_model`, systemprompten och ett låtsat
  sökverktyg, strömmat, ungefär 45 anrop för uppskattningsvis under 0,25 USD): sammanfattningar
  kommer, men bara när modellen resonerar några tiotal tokens eller mer. Med resonemangsnivån `low`
  (standard) resonerade modellen 0-30 tokens per anrop, och 1 av ungefär 20 anrop hade en
  sammanfattning. Med `medium` hade 4 av 5 anrop som läste sökträffarna en, men inget av de första
  anropen, som gick direkt till en sökning. Den första texten kom 2-3 sekunder in i ett anrop på
  5-7 sekunder, före anropets svar. Alla sammanfattningar var på engelska. Ett anrop med
  föregående anrops resonemangspost och sammanfattning i historiken (en följdfråga) gick igenom.
- **I demot syns få tankar med `low`.** Webbappen visar en rad "Tänker" bara för anrop med en
  sammanfattning, och med `low` är de få. `AGENT_REASONING_EFFORT=medium` ger fler, men kostar fler
  resonemangstokens och mer tid per steg. Det är Simons val; standarden ändras inte här.
- **Kostnaden** är liten: sammanfattningen syntes inte i utdatatokens i provet (utdata var
  resonemang plus svar). Den skickas tillbaka som indata i följande anrop, ungefär 100 ord per
  sammanfattning; det är inte mätt. Mätningen ([ADR 0019](0019-matning-av-svaren.md)) använder
  `make_agent_model` och ber alltså också om sammanfattningar.
- **Demots fråga 1 besvaras oftast direkt.** I ett prov av steget efter den första sökningen
  (sökträffar för kundens och leverantörens uppsägning i båda delområdena) frågade den gamla
  prompten 3 av 3 gånger och den nya 1 av 3; de andra två sökte vidare för att svara. Hela
  körningen och de 30 testfrågorna är inte körda om. `docs/demo.md` (en annan tråds fil) behöver
  uppdateras: fråga 1 visar då att agenten svarar för varje fall och säger vad som avgör, och en
  fråga som visar `ask_user` behöver bero på användarens eget fall, till exempel vilken
  leverantör hen har. README:n beskriver den gamla regeln (stycket om systemprompten: "fråga med
  `ask_user` när svaret skiljer sig mellan avtal eller delområden") och fråga 1 som exemplet på en
  motfråga (punkten "En oklar fråga"); båda behöver uppdateras av tråden som äger README:n.
  `docs/demo.md` och `README.md` lämnas därför till den tråden.
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
- **Att låta agenten skriva sina tankar som text före varje steg.** Det kostar utdatatokens i
  varje anrop, ändrar prompten och kräver att agentens text strömmas, vilket `emit-messages:
  False` stänger av för att svaret inte ska synas före kontrollen.
- **Att översätta sammanfattningen till svenska** med ett eget modellanrop: ett anrop till per
  steg, och tankarna kommer senare än steget.
- **`detailed` som standard.** Längre texter i varje steg; `auto` låter OpenAI välja.
- **Att behålla regel 4 och bara visa frågan snyggare.** Simon bad också om färre frågor, och ett
  kort svar för varje fall ger användaren svaret direkt.
- **Ett alternativ, eller fler än fem.** Ett alternativ är inget val, och fler än fem knappar är
  svåra att läsa. Användaren kan alltid svara med egen text.
