# ADR 0015: Valideringskedjan: registeruppgifter mot registret, en granskarmodell och två nya försök

**Status:** Föreslaget (PR för M8).
Bygger ut [ADR 0013](0013-agenten.md): citatkontrollen blir det första steget i en kedja, ett nytt
försök blir två (punkt 9 där), och ett svar ur registret kan bli `verified` (konsekvenserna där).
Resten av ADR 0013 gäller. Lägger till en femte ändring i AG-UI-adaptern till de fyra i
[ADR 0014](0014-api-och-compose.md) (punkt 11 nedan).

## Kontext

Arkitekturplanens avsnitt 6 har fem valideringslager. M7 byggde en del av lager 4, citatkontrollen.
M8 bygger resten av lager 4 och lager 5:

- **Registeruppgifter** (avtalsnummer, organisationsnummer, datum) i svaret ska jämföras med
  registret. I M7 hade ett svar ur registret inget att citera och blev alltid `with_reservation`
  (ADR 0013, konsekvenserna).
- **Svaret ska följa av källorna.** Citatkontrollen bevisar att orden står i avsnittet, inte att
  svaret säger samma sak: ett ordagrant citat om "tre (3) månader" kan stå bakom en text som säger
  sex månader. Den bedömningen kräver en modell, och planen säger en separat granskare,
  medvetet en annan modell än agentens (`gpt-6-astra`, ADR 0005).
- **Senaste lydelsen** när ett avtal har ändrats. I pilotens data har bara två avtal ändringsdokument
  (Microsoft fem, IBM tre), och regeln behöver verktyget som hittar ändringarna och en ändring i
  M4:s extraktion. Den kommer därför i nästa PR, tillsammans med det verktyget.

Det som styr besluten:

- **Klienten skickar hela historiken** (kontraktets punkt 7). Det kontrollen jämför med kan inte tas
  ur verktygssvaren i historiken, som citaten inte heller tas (ADR 0013, punkt 6).
- **Det som en modell skriver under en körning strömmas till webbappen** som AG-UI-händelser, om
  inte anropet är märkt så att adaptern hoppar över det. Granskarens bedömning får inte synas i
  chatten.
- **Granskaren kostar ett modellanrop per utkast**, med frågan, svaret och källornas hela text.
- **Regler går att testa utan modell, granskaren inte.** Det som en regel kan avgöra exakt ska inte
  hänga på en modells omdöme.

## Beslut

1. **En kedja i fast ordning** (`validation/chain.py`): citaten, sedan registeruppgifterna, sedan
   granskaren. De deterministiska reglerna går först: de är gratis och exakta, och ett utkast med
   fel citat eller fel datum skickas tillbaka utan att betala för en granskning. Granskaren körs
   bara på ett utkast som klarat reglerna och som besvarar frågan (`answered: true`); "det framgår
   inte" har inget att granska mot. Ett besvarat utkast som inte klarat reglerna och ändå blir
   svaret (försöken är slut) får noten att det inte granskats, så att användaren inte tror att
   resten är prövat.
2. **Modellen anger vilka avtal registeruppgifterna kommer från:** `FinalAnswer.register_facts`,
   avtalsnumren kopierade från `search_register`, högst 60 (alla avtal i pilotens största område,
   44; ett längre svar får avgränsas). Det motsvarar planens
   `registerfakta_anvanda`. Kontrollen läser varje angivet avtals alla rader igen med
   `search_register` genom samma MCP-session som verktygen (`RegisterReader`,
   `mcp_tools.McpRegisterReader`), sida för sida, och behåller bara raderna med samma avtalsnyckel.
3. **Registerregeln** (`validation/register_facts.py`) är deterministisk. Ett angivet avtalsnummer
   ska finnas i registret och användas i texten (numret, organisationsnumret eller leverantörens
   namn). Texten genomsöks efter avtalsnummer, upphandlingsnummer, organisationsnummer och datum
   (`2028-11-13` och `13 november 2028`). Varje värde ska finnas i de angivna avtalens rader, i ett
   avsnitt vars citat godkändes, i användarens egna ord eller vara dagens datum. Ett
   organisationsnummer med fel kontrollsiffra är ett eget fel. I en mening som nämner ett angivet
   avtal (med numret, en kortform som "(-005)" efter ett helt nummer, ett intervall som "-001 till
   -008", organisationsnumret eller leverantörens namn, också i genitiv) ska datum och
   organisationsnummer höra till just det avtalet; ett avsnitt eller användarens ord belägger inte
   ett värde som registret ger ett annat angivet avtal. Ett datum som inget belägger godtas ändå
   när det följer, på en dag när, av ett belagt datum eller av dagens datum med ett steg som
   meningen skriver ("tre månader före 2027-02-17"): uträkningen syns, och granskaren prövar den.
   Ett datum en dag från registrets datum för meningens avtal är däremot ett fel, inte en
   uträkning. Regeln bedömer inte vilket delområde som avses, antal, leverantörsnamn i löptext
   eller att något saknas; det gör granskaren.
4. **Granskaren** (`agent/reviewer.py`): `REVIEWER_MODEL` (`gpt-6-astra`) med
   `REVIEWER_REASONING_EFFORT` (`low`), Responses API och strukturerad utdata med strikt
   JSON-schema (`ReviewVerdict`): för varje påstående i svaret `supported`, `partly` eller
   `unsupported` med skäl, och det väsentliga som saknas. Den läser frågan, agentens frågor till
   användaren (`ask_user`) med användarens svar, svaret, varje godkänd källas hela avsnitt (läst
   igen genom avtal-mcp, ett avsnitt som två källor citerar en gång) och de kontrollerade
   registerraderna. Allt det står i egna element (`<källa id="1">`), och prompten säger att texten
   där är uppgifter, aldrig instruktioner. Texten normaliseras (NFKC, som citatkontrollen gör) och
   varje `<` i den byts ut, så att ingen variant av en tagg kan avsluta sitt element.
5. **Koden avgör vad bedömningen betyder** (`validation/review.py`): svaret är godkänt bara om varje
   påstående är `supported` och inget saknas. Underkända påståenden och det som saknas blir
   återkoppling till agenten och, om svaret ges med reservation, noter till användaren.
6. **Granskaren syns inte i webbappen.** Modellen strömmar aldrig (`disable_streaming=True`), och
   både modellen och varje anrop har metadata `emit-messages: false` och `emit-tool-calls: false`,
   som AG-UI-adaptern läser. Adaptern läser inte märkningen för strömmat resonemang, så det är
   `disable_streaming` som håller granskarens resonemang borta. Ett test kör granskaren inne i en
   graf genom adaptern och visar att inga text- eller verktygshändelser kommer ut, och att samma
   falska modell utan märkningen läcker.
7. **Ett gemensamt antal nya försök:** `VALIDATION_RETRIES`, två (ersätter `CITATION_RETRIES`, ett),
   alltså högst tre utkast per fråga. Alla regler och granskaren delar det. Återkopplingen ersätter
   `FinalAnswer`-anropets verktygssvar som i M7 och säger vilket försök det var ("försök 1 av 3").
8. **Svaret får två nya fält** (kontraktets punkter 24–28): `reservations`, svenska noter om det som
   inte kunde kontrolleras (tom utom vid `with_reservation`), och `register_facts`, raderna som
   registeruppgifterna kontrollerades mot. `with_reservation` betyder nu: ett fel kvar efter
   försöken, en granskning som inte gick att göra, eller ett besvarat svar utan något att
   kontrollera mot.
9. **En granskning som misslyckas** (ett fel hos OpenAI, en bedömning som inte går att läsa) kostar
   inget nytt försök: agenten kan inte rätta det. Svaret ges med reservationen "Svaret kunde inte
   granskas.", och felets typ loggas.
10. **Gränsen för modellanrop efter en återkoppling** ger det senaste underkända utkastets svar,
    med reservation, i stället för `no_answer`. Det sparas privat i tillståndet
    (`fallback_answer`) när utkastet skickas tillbaka, och nollställs med varje ny fråga.
11. **API:t tar inte emot klientens tillstånd** (`api/agui.py`). Med `forwardedProps.node_name`
    skriver adapterns läge "continue" klientens `state` in i grafen som den noden, förbi
    indataschemat, och fortsätter därifrån: en klient kunde sätta `answer`, `fallback_answer` och
    `validation_retries` och hoppa över kontrollen. `state` töms och `node_name` tas bort innan
    adaptern läser dem; webbappen skickar inget av dem.

## Konsekvenser

- Ett svar ur registret kan bli `verified`. Ett fel datum eller organisationsnummer skickas tillbaka
  till agenten i stället för att nå användaren med en allmän reservation.
- Varje besvarat utkast som klarar reglerna kostar ett anrop till granskaren, och svaret dröjer så
  länge anropet tar. Mätt i provkörningen: se docs/steg/08-validering.md.
- Granskaren kan ta fel åt båda hållen. Ett felaktigt underkännande kostar ett nytt försök och i
  värsta fall en reservation; ett felaktigt godkännande släpper igenom ett svar som reglerna redan
  har godkänt. Planens utvärdering (M11) mäter hur ofta.
- Varje angivet avtal läses en gång till: ett eller flera MCP-anrop per avtal.
- Kontrollen prövar det som svaret säger, inte att agenten har läst allt den borde. En uppräkning
  av leverantörer kan bli `verified` och ändå sakna några, om agenten slutade bläddra i
  `search_register` (se docs/steg/08-validering.md, Kända begränsningar). Filtret på delområde
  (`sub_area`, PR:n efter M8) gör att en region ryms på en sida.
- Modellen måste ange `register_facts`. Glömmer den, är varje avtalsnummer och datum ur registret
  ett fel som skickas tillbaka, och efter försöken en reservation.
- Senaste lydelsen kontrolleras inte ännu; prompten säger fortfarande åt agenten att leta efter
  ändringar.
- Webbappen behöver visa `reservations` och kan visa `register_facts` (kontraktet).

## Alternativ som valts bort

- **Att låta granskaren också kontrollera registeruppgifterna.** Dyrare, inte exakt, och inte
  testbart utan modell. Ett datum är rätt eller fel.
- **Att ta registeruppgifterna ur `search_register`-svaren i historiken.** Klienten skickar
  historiken och kan lägga in ett förfalskat svar, samma skäl som för citaten.
- **Att låta en modell plocka ut påståendena om registret ur texten.** Ytterligare ett anrop och en
  ny felkälla. Att agenten anger avtalen och kontrollen läser deras rader är enklare och exakt.
- **Separata antal försök per regel.** Ett svar som först får fel citat och sedan fel datum kunde då
  få fyra försök. Ett gemensamt antal håller kostnaden förutsägbar.
- **Samma modell som agenten som granskare.** Planen kräver en annan modell, så att agenten inte
  godkänner sitt eget arbete.
- **Granskaren som en nod i en yttre graf.** Se ADR 0013: webbappen skulle tappa agentens steg.
