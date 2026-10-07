# ADR 0021: Spårning med Langfuse: en spårning per fråga, samtalet som session, avstängd utan nycklar

**Status:** Föreslaget (PR efter #21).
Genomför spårningen i arkitekturplanens avsnitt 8 och [arkitekturvalideringens](../validering.md)
punkt 9, det som stod kvar av M7:s klart-när ("varje körning syns i Langfuse").

## Kontext

- **En körning syns bara medan den pågår.** Kommandoraden skriver ut verktygsanropen, men inte vad
  varje modellanrop kostade i tokens eller hur lång tid det tog. API:t och webbappen visar inget i
  efterhand. Mätningen ([ADR 0019](0019-matning-av-svaren.md)) räknar tokens per modell men sparar
  inte stegen. För att visa och förklara hur agenten arbetade med en fråga (vilka verktyg, i
  vilken ordning, vad kontrollen underkände, vad granskaren sa) behöver körningen sparas.
- **Valideringen valde Langfuse:** MIT-licens, bygger på OpenTelemetry och kan köras själv.
  Lokalt används Langfuse Cloud i EU-regionen, så att ingen ClickHouse behöver köras bredvid appen;
  avtalen är offentliga.
- **Agenten körs på tre ställen:** kommandoraden, API:t (`POST /agui`) och mätningen. Granskaren
  anropas inne i svarskontrollen, inte av agenten.
- **Repot är publikt och demot ska gå utan konto.** Testerna och CI har inget Langfuse.

## Beslut

1. **Langfuses Python-SDK (version 4) med dess callback-hanterare för LangChain.** Hanteraren
   läggs i körningens config. LangChain skickar en körnings callbacks vidare till varje anrop
   inuti den, så hela körningen kommer med utan ändringar i grafen: agentens modellanrop (modell,
   tokens, tid), varje verktygsanrop till avtal-mcp med argument och svar, middleware-stegen och
   granskarens anrop i svarskontrollen.
2. **Avstängd utan nycklar.** `observability/tracing.py` startar Langfuses klient bara när både
   `LANGFUSE_PUBLIC_KEY` och `LANGFUSE_SECRET_KEY` finns; annars är spårningen avstängd och
   körningens config får inget tillägg, så körningen är densamma. Nycklarna läses in i
   inställningarna (`.env`) och ges till klienten därifrån. Den hemliga nyckeln är en `SecretStr`
   och tas bort ur felmeddelanden som OpenAI-nyckeln.
3. **En spårning per fråga, samtalet som session.** Spårningen heter "fråga" och har samtalets
   tråd-id som session: AG-UI:s `threadId` i API:t, ett nytt id per körning av kommandoraden.
   Taggen säger varifrån frågan kom (`api`, `cli`). I mätningen heter varje frågas spårning som
   frågans id (`q14`), sessionen är mätningens körning (`eval-<tid>[-<etikett>]`) och taggarna är
   `eval` och frågans kategori, så att en fråga i rapporten går att slå upp i Langfuse.
4. **En klient per process, stängd sist.** API:t öppnar spårningen i sin livscykel, kommandoraden
   och mätningen när de startar. Den stängs sist och skickar då det som är kvar. Varje körning får
   en egen hanterare.
5. **Langfuse Cloud i EU som standard** (`https://cloud.langfuse.com`). `LANGFUSE_BASE_URL` pekar
   om till en egen Langfuse eller USA-regionen.

## Konsekvenser

- Med nycklar syns varje fråga i Langfuse som ett träd: modellanrop med tokens och tid,
  verktygsanrop med argument och svar, kontrollen och granskaren, och nya försök när kontrollen
  underkänner. Sessionen samlar ett samtals frågor. Det går att visa i presentationen hur agenten
  valde sina verktyg för en fråga.
- Utan nycklar ändras ingenting. Testerna kontrollerar med OpenTelemetrys exportör i minnet att en
  körning blir en spårning med namn, session och taggar, med modellanropen (modellnamn och tokens)
  och verktygsanropen, utan nätverk.
- **Det som skickas till Langfuse** är frågorna, avtalstexten som verktygen returnerar och svaren.
  Avtalen är offentliga, men användarnas frågor lämnar datorn. I en produktion med interna frågor
  behövs en egen Langfuse eller maskning (SDK:ts `mask`).
- **Går Langfuse inte att nå** försöker SDK:t några gånger och kastar sedan spårningen, med en
  varning i loggen. Körningen påverkas inte, men avslutet väntar några sekunder (cirka 3 s mot en
  stängd port här).
- **I API:t blir svaret på agentens fråga (`ask_user`) en ny spårning** i samma session, eftersom
  webbappen skickar svaret som en ny körning med en ny hanterare. På kommandoraden och i mätningen
  fortsätter svaret frågans spårning, eftersom samma hanterare används.
- **Kostnaden i Langfuse** räknas bara för modeller som finns i Langfuses prislista. Saknas
  modellen visas tokens men ingen kostnad; priset kan läggas in i projektets inställningar.
  Mätningen räknar kostnaden själv ([ADR 0019](0019-matning-av-svaren.md)).
- Langfuse håller en klient per publik nyckel i processen. Därför öppnas spårningen en gång per
  process.
- Ett nytt beroende, `langfuse`, med OpenTelemetrys SDK och OTLP-exportör.

## Alternativ som valts bort

- **LangSmith.** Stängd källkod; att köra den själv kräver Enterprise (valideringen, punkt 9).
- **Langfuse i Docker Compose.** Kräver ClickHouse, Redis och objektlagring bredvid Postgres, för
  mycket för ett demo på en bärbar dator. Valideringen valde Cloud EU lokalt.
- **Egen OpenTelemetry-instrumentering mot en vanlig OTLP-mottagare** (Jaeger, Phoenix).
  Langfuses hanterare känner LangChains körningar: modellanrop som generationer med tokens, och
  sessioner. Med en vanlig mottagare hade den kopplingen fått byggas här.
- **Langfuses `@observe` i koden.** Skulle ändra grafens och verktygens kod; hanteraren ser samma
  saker utan ändringar.
- **En hanterare för alla körningar.** Då hade svaret på `ask_user` i API:t fortsatt frågans
  spårning. Men hanteraren håller varje körnings tillstånd i delade tabeller och LangChain anropar
  den från trådar; en hanterare per körning håller körningarna isär.
