# ADR 0019: Mätningen av agentens svar: samma körning som API:t, en domare mot facit och kostnaden som intervall

**Status:** Godkänt (PR #21, den förenklade M11).
Förenklar utvärderingen i arkitekturplanens avsnitt 8 inför presentationen
(`plan-mot-presentationen.md`, punkt 4). DeepEval i CI och fler frågor kommer efter
presentationen.

## Kontext

Mätningen i M5 ([ADR 0011](0011-hybridsokning.md)) visar om sökningen hittar rätt avsnitt. Inför
presentationen behövs det en användare får: om svaret är rätt, om det är kontrollerat, vad det
kostar och hur lång tid det tar, för de 30 testfrågorna.

- **Kontrollen visar inte att svaret är rätt.** Citatkontrollen, registerregeln, regeln om senaste
  lydelsen och granskaren ([ADR 0015](0015-valideringskedjan.md)) visar att svaret stöds av sina
  källor. Ett svar kan stödjas av fel klausul, och bara facit säger vilken som är rätt.
- **Facit är fritext.** "Fram till tio (10) arbetsdagar före överenskommen start, eftersom
  uppdraget är längre än tio arbetsdagar" och "senast tio arbetsdagar innan uppdraget börjar" är
  samma svar. Ord för ord går det inte att jämföra.
- **Ingen Postgres i utvecklingsmiljön.** Agenten har provkörts mot en tillfällig ersättare för
  avtal-mcp över pilotens data. På Simons dator finns den riktiga databasen. Mätningen ska gå att
  köra på båda.
- **Prislistan saknar cachad indata.** Arkitekturvalideringen anger pris för indata och utdata per
  modell, men inte för cachad indata. En fråga skickar systemprompten och verktygen i varje
  modellanrop, och i provkörningarna var omkring 80 procent av agentens indata cachad.
- **Agenten kan fråga användaren** (`ask_user`), och en mätning har ingen användare.

## Beslut

1. **Mätningen kör agenten som API:t gör.** Samma graf, kontroll och granskare
   (`build_agent`), en egen MCP-session och graf per fråga ([ADR 0014](0014-api-och-compose.md)),
   och kontrollpunkter i minnet. Den når data bara genom avtal-mcp (`MCP_TRANSPORT`, `MCP_URL`),
   så samma kommando mäter ersättaren eller den riktiga servern. I Docker Compose är det tjänsten
   `eval` (profilen `eval`), som når `mcp` som API:t gör och skriver rapporterna i
   `evals/reports/` på värddatorn.
2. **En domarmodell jämför svaret med facit** (`evals/judge.py`): rätt, delvis rätt eller fel,
   med vad som saknas, vad som motsäger facit och en mening om varför. Kärnan i facit avgör: det
   frågan efterfrågar, inte bakgrunden. För en fråga som avtalen inte besvarar är "framgår inte"
   rätt. Domaren är granskarmodellen (`REVIEWER_MODEL`, `gpt-6-astra`) med resonemangsnivån
   `medium`, med en strikt JSON-schema som svar, som granskaren. Ett svar som inte blev klart inom
   gränsen för modellanrop är fel utan domare. Varje bedömning står med sitt skäl i rapporten,
   så att en person kan pröva den.
3. **Källor och register räknas på plats, inte på text.** En källa i facit räknas som citerad när
   svaret har ett citat som kontrollen godkände i samma fil och på samma plats (avsnittets
   position ur utkastet, eller dess nummer). Facits avtal jämförs med svarets registerrader med
   registrets avtalsnyckel. Statusen är den användaren ser.
4. **Agentens följdfrågor får ett fast svar:** "Jag har inget att tillägga: svara utifrån frågan
   som den är ställd." Testfrågorna ska kunna besvaras som de är ställda, och rapporten räknar
   frågorna där agenten frågade.
5. **Kostnaden är ett intervall.** Tokens räknas per modellanrop och modell. Kostnaden anges från
   cachad indata utan kostnad till cachad indata till fullt pris, med arkitekturvalideringens
   priser; det verkliga priset ligger mellan. Domarens anrop räknas för sig.
6. **Rapporterna innehåller frågorna, svaren och citaten**, till skillnad från sökningens, eftersom
   en bedömning inte går att pröva utan dem. De skrivs till `evals/reports/`, som git ignorerar,
   så de stannar på datorn där de körs. `docs/steg/11-utvardering.md` har bara siffrorna och
   egna sammanfattningar.

## Konsekvenser

- Presentationen får siffror för hela kedjan: andelen rätta svar, andelen kontrollerade svar,
  tid och kostnad per fråga, och vad som gick fel i de svar som inte var rätt.
- Simon kan köra samma mätning mot den riktiga databasen med ett kommando
  (`docker compose --profile eval run --rm eval`), och jämföra med körningen mot ersättaren.
- Domaren är en språkmodell och kan döma fel. Skälen och svaren står bredvid facit i rapporten,
  men bedömningen är inte prövad mot en människas på mer än stickprov.
- Domaren och granskaren är samma modell. Granskaren ser källorna men inte facit, domaren facit
  men inte källorna, så de prövar olika saker, men de kan dela en modells blinda fläckar.
- En källa i en fil som facit inte anger räknas inte, också när avsnittet har samma text. Talet
  för citerade källor är därför en undre gräns.
- Varje fråga körs en gång. Agentens svar varierar mellan körningar, så en enskild fråga kan bli
  rätt den ena gången och fel den andra; 30 frågor ger ett grovt mått.
- En körning av alla 30 frågor tar några minuter med fyra frågor åt gången och kostar några
  dollar, domaren inräknad.

## Alternativ som valts bort

- **DeepEval eller RAGAS nu.** Planen har DeepEval i CI. Det ger färdiga mått (till exempel
  faithfulness och answer relevancy), men med engelska promptar som behöver anpassas och ett nytt
  beroende, och det mäter mest det som kontrollen och granskaren redan prövar. Svarskorrekthet mot
  facit behövs ändå. Det skjuts till efter presentationen, när testsamlingen är större.
- **Att jämföra med facit utan modell** (ord, tal eller nyckelord ur facit). Fungerar för tal och
  avtalsnummer, men inte för villkor och "framgår inte", som är hälften av frågorna.
- **Agentmodellen som domare.** En modell bedömer sina egna formuleringar mildare, och
  granskarmodellen är den starkaste.
- **Att jämföra källor på text** (avsnittets hash, som sökningens mätning gör). Det kräver
  avsnittens text ur databasen, och ersättaren har ingen; på plats räcker för de källor facit
  anger.
- **Att mäta genom API:t** (`POST /agui`). Samma graf, men händelserna måste tolkas tillbaka till
  ett svar, och tokens per modell syns inte. API:t provas för sig ([M9](../steg/09-api.md)).
