# Demoskript

Fem frågor som visar vad agenten gör: den frågar när frågan är oklar, väljer registret i stället
för dokumenten, följer hänvisningar i flera steg, hittar en rättelse som ersätter klausulen och
säger "framgår inte" i stället för att gissa. Fråga 3 och 4 visar det som ett fast workflow inte
klarar, eftersom nästa steg beror på vad agenten just har läst. Fyra
av frågorna kommer ur testsamlingen ([`evals/datasets/gold_sv.jsonl`](../evals/datasets/gold_sv.jsonl)),
så de har ett facit och en mätning bakom sig. Den första är en öppnare variant av q04, gjord för
att agenten ska behöva fråga.

Alla fem kördes mot den riktiga databasen 2026-10-07, både i terminalen och i webbappen, och
reservfrågan i terminalen. Resultaten står i [steg 12](steg/12-demo.md). Körningen hittade ett fel:
i webbappen föll fråga 3 första gången, eftersom API:ts AG-UI-adapter stoppade körningen efter 25
steg i grafen, ungefär sex modellanrop. Webbappens första körningar av fråga 3–5 gjordes med
rättelsen provad lokalt. En andra körning av alla fem i webbappen gjordes med `main` efter PR #22
och webbappen från PR #23. Hämta `main` före demot. Beslutet bakom urvalet står i
[ADR 0022](adr/0022-demot.md).

## Ordningen

| # | Fråga | Visar | Status | Tid i webbappen | Tid i terminalen |
|---|---|---|---|---|---|
| 1 | Uppsägningstiden i IT-drift | Agenten frågar när frågan är oklar | Verifierat | 52–55 s med dialogen | 54 s med dialogen |
| 2 | q10 Nordlo Advance | Registret i stället för dokumenten | Verifierat | 12–14 s | 19 s |
| 3 | q14 Lördagsarbete | Flera steg genom hänvisningar | Verifierat | 39–62 s | 50 s |
| 4 | q21 Antal anbud | En rättelse ersätter klausulen | Verifierat | 32–45 s | 36 s |
| 5 | q27 Rangordnad etta | Agenten gissar inte | Inget svar eller Verifierat | 35–48 s | 31 s |
| R | q24 Lägsta takpris | Jämförelse mellan leverantörer | Verifierat | – | 42 s |
| F | Egna filer: jämför ett eget avtal | Agenten läser din fil och jämför den med ramavtalet | Verifierat mot ersättaren | – | 66 s för punkt 7 genom API:t, 4 min 19 s för hela avtalet |

Tiderna kommer från körningarna mot den riktiga databasen, två i webbappen (q14 tre) och en i
terminalen; terminalens tider räknar med att programmet startar. Räkna med upp till en minut per
fråga. En fråga kan ta längre tid om
kontrollen skickar tillbaka ett utkast, och svaret kan formuleras olika mellan körningar.
Fakta och källor ska vara desamma.

Ställ frågorna i en ny flik var för sig (uppdatera sidan mellan frågorna), så att agenten inte
läser in en tidigare fråga i nästa.

## Före demot

Dagen före, på datorn du demonstrerar på:

1. `git pull` och `docker compose up -d --build --wait --wait-timeout 300`.
2. Om koden för inläsningen har ändrats sedan förra inläsningen:
   `docker compose --profile ingest run --rm ingest` (med tolkningscachen i `data/` tog det
   knappt tio minuter 2026-10-07, se [steg 12](steg/12-demo.md); cachen förklaras i
   [steg 9](steg/09-api.md)).
3. Ställ alla fem frågorna, reservfrågan och frågan om egna filer en gång i webbappen och
   notera tiderna.
4. Spara skärmbilder av svaren (nivå E nedan) och gärna en skärminspelning av hela demot
   (nivå C).

En halvtimme före:

1. `docker compose up -d --wait --wait-timeout 300` och `docker compose ps`: alla fyra tjänster
   ska vara `healthy`.
2. Öppna http://localhost:3000 och ställ fråga 2 som uppvärmning. Den är snabb och visar att
   nyckeln, avtal-mcp och databasen fungerar.
3. Ha en terminal öppen i repot för nivå B, och mappen med skärmbilder redo.
4. Stäng aviseringar och andra flikar. Zooma webbläsaren så att svaret syns på projektorn.

## Fråga 1: agenten frågar när frågan är oklar

**Fråga:** `Vad är uppsägningstiden i IT-driftavtalet?`

**Vad som händer:** agenten söker i registret och i dokumenten, och ser att svaret beror på vem
som säger upp och vad som sägs upp. Den pausar och frågar dig (`ask_user`). Frågan varierar
mellan körningar. I webbappen kom den 2026-10-07 som "Menar du er organisations avropade
kontrakt med leverantören eller själva ramavtalet …? Gäller frågan er egen uppsägning eller
leverantörens?", i terminalen som fyra val om vem som säger upp och varför. Ibland frågar den
också om IT-drift Mindre eller Större.

**Välj:** "Vårt avropade kontrakt – vi vill säga upp det", eller valet "utan att ange skäl".
Frågar den om delområdet, välj Mindre. Efter svaret läser agenten 6.21.8 i Allmänna villkor och
letar efter ändringar. I terminalen läste den också 6.21.7 och 6.21.9; i webbappen läste den 6.21.8
för både IT-drift Mindre och IT-drift Större.

**Visa:**
- Dialogen. Körningen står still i grafen tills du svarar, och fortsätter sedan från samma
  checkpoint i Postgres.
- Stegen ovanför svaret: varje verktyg agenten valde, med argumenten. "Visa svaret från
  verktyget" visar vad den fick tillbaka.
- Statusen "Verifierat" och citatet i källkortet under svaret. Källpanelen med PDF-sidan visar du
  hellre i fråga 3 (se "Om det går fel" nedan).

**Säg:** "Ett workflow hade valt en tolkning och svarat på den. Agenten ser att frågan kan betyda
olika saker och frågar. Det är en av anledningarna till att frågorna är en agent och inläsningen
ett workflow."

**Rätt svar:** utan angivande av skäl får ni säga upp efter halva kontraktstiden, dock tidigast
efter tre år, om kontraktet inte säger annat (6.21.8). Vid uppsägning enligt 6.21.7, till exempel
vid leverantörens väsentliga avtalsbrott, sker uppsägningen skriftligen, med omedelbar verkan eller senast nio månader efter uppsägningen (6.21.7).
Leverantören har minst sex månaders uppsägningstid om ni inte rättar ett väsentligt avtalsbrott
inom 30 dagar (6.21.9). Vilka av punkterna svaret tar med beror på vad du svarade i dialogen.

**Om det går fel:**
- Agenten frågar inte utan svarar för alla fall direkt: det är också ett rimligt svar. Säg att
  den här gången valde agenten att täcka alla fall, och gå vidare.
- Panelen säger "Citatet hittades inte i sidans text": så blev det för källa 1 i webbappen
  2026-10-07, före PR #23. Citatet ur 6.21.8 börjar på sidan 26 och slutar på sidan 27, och
  panelen godtog inte början på sidan 26. Med PR #23 öppnar panelen sidan 26, markerar början och
  säger "Bara en del av citatet finns på den här sidan. Resten finns på sidan före eller efter."
  Citatet är i båda fallen kontrollerat ordagrant mot avsnittets text i databasen ("Kontrollerat
  mot avtalstexten").

## Fråga 2: registret i stället för dokumenten

**Fråga:** `Vilket avtalsnummer och organisationsnummer har Nordlo Advance på IT-drift Mindre, och har bolaget hetat något annat tidigare?`

**Vad som händer:** agenten anropar bara `search_register` med delområdet IT-drift Mindre. Ingen
dokumentsökning.

**Visa:**
- Att det bara blev ett steg: agenten valde källan själv.
- Rutan "Ur registret: 1 avtal" under svaret: raden som kontrollen jämförde avtalsnumret och
  organisationsnumret med. Det tidigare namnet bedömer granskaren.

**Säg:** "Avtalsnummer och leverantörer finns i Excel-listan, som är facit för inläsningen och
samtidigt ett verktyg för agenten. Kontrollen läser om raden ur registret och jämför avtals- och
organisationsnummer och datum i svaret med den. Står det ett sådant nummer i svaret som varken
finns i registret eller i en kontrollerad källa går svaret tillbaka till agenten."

**Rätt svar:** avtalsnummer 23.3-5890-2023-002, organisationsnummer 556486-1689, tidigare EPM Data.

**Om det går fel:** frågan är den snabbaste och mest stabila. Svarar den inte alls är det
tjänsten, inte agenten: se felsökningen nedan.

## Fråga 3: flera steg genom hänvisningar

**Fråga:** `Vår inhyrda IT-tekniker, avropad genom rangordning, behöver jobba en lördag. Vad får bemanningsföretaget ta betalt för de timmarna?`

**Vad som händer:** i terminalen tog agenten nio verktygsanrop: den hittade punkt 9.9.2 om
särskild ersättning, letade efter ändringar, läste prisbilagan för rangordnade IT-tjänster, som
hänvisar arbete utanför Arbetsdag till avsnittet Särskild ersättning, och läste definitionen av
Arbetsdag i 9.2. I webbappen tog den sju steg och läste inte prisbilagan, och i en andra körning
nio steg med prisbilagan. Ordningen och stegen kan variera.

**Visa:**
- Stegen. Ingen enskild sökning ger svaret: det bygger på 9.9.2 och definitionen i 9.2, som står
  på olika ställen, och facit också på prisbilagans hänvisning. Sökningen ensam hittar inte alla
  källorna (steg 5, "Där sökningen inte räcker").
- "Kontrollerar svaret …" när utkastet är klart: citaten, registeruppgifterna och ändringarna
  kontrolleras, och sedan granskar `gpt-6-astra` att källorna stöder svaret.
- Källorna under svaret: 9.9.2 och 9.2, ibland också prisbilagan. Klicka på källa 1: panelen
  visar PDF-sidan med citatet ur 9.9.2 markerat.

**Säg:** "Det här är frågan som visar varför det är en agent. Svaret står i en punkt om särskild
ersättning, som prisbilagan hänvisar till, och i en definition på ett annat ställe. Ingen enskild
sökning ger båda. Agenten letar upp delarna själv, i flera steg."

**Rätt svar:** särskild ersättning efter överenskommelse: konsultens kompensation enligt
kollektivavtalet gånger 2,0, där faktorn redan innehåller arbetsgivaravgift, OH och påslag
(9.9.2). Lördag ligger utanför Arbetsdag, som är helgfri måndag till fredag 08.00–17.00 (9.2).

**Om det går fel:**
- "Med reservation": läs upp reservationen. Den säger vad som inte kunde kontrolleras, och det är
  så systemet ska bete sig när något inte går att belägga.
- "Agenten kunde inte svara på frågan. Försök igen om en stund.": se felsökningen och ställ
  frågan i terminalen (nivå B).

## Fråga 4: en rättelse ersätter klausulen

**Fråga:** `Hur många anbud skulle enligt utvärderingsmodellen antas i upphandlingen av IT-drift Mindre?`

**Vad som händer:** agenten hittar punkt 3.2 i upphandlingsdokumentet, som säger sju (7) anbud,
anropar `find_amendments` och hittar Kammarkollegiets rättelse i Frågor och svar den 2024-02-20,
som säger åtta (8). Svaret citerar båda.

**Visa:**
- Steget "Letade efter ändringar" och att svaret har två källor: den ursprungliga punkten och
  rättelsen.

**Säg:** "Punkt 3.2 säger sju. En sökning hittar den, och en vanlig RAG-lösning svarar sju.
Inläsningen läser ut vilka svar i frågor-och-svar-loggarna som ersätter en punkt, och kontrollen
underkänner ett svar som citerar en ändrad punkt utan att citera ändringen."

**Rätt svar:** åtta anbud, enligt rättelsen 2024-02-20 av punkt 3.2.

**Om det går fel:** svarar agenten sju och statusen är "Med reservation" med texten "kan bygga
på en lydelse som har ändrats senare", har kontrollen fångat felet efter två nya försök. Visa
reservationen: den pekar ut rättelsen.

## Fråga 5: agenten gissar inte

**Fråga:** `Vilket bemanningsföretag är rangordnat som nummer ett för kontorstjänster i Stockholm?`

**Vad som händer:** rangordningen står inte i något av de inlästa dokumenten. Agenten söker i
registret och dokumenten, läser avropsrutinen (10.5.1) och svarar att det inte framgår.

**Visa:** att svaret säger "framgår inte" och visar var rangordningen regleras, i stället för att
nämna ett företag.

**Säg:** "Fyra av de 30 testfrågorna är gjorda för att inte ha svar i avtalen. Rätt svar är
'framgår inte', och alla fyra fick det i mätningen mot den riktiga databasen."

**Status:** mot den riktiga databasen blev det "Inget svar" (agenten markerar att frågan inte är
besvarad) i terminalen och i den första körningen i webbappen, och "Verifierat" med samma
innehåll i den andra. Båda är
rätt, och det beror på om agenten räknar "framgår inte" som ett svar. Säg det om frågan kommer.

**Om det går fel:** nämner agenten ett företag är det fel. Visa källan och säg att det är just den
sortens fel testfrågorna finns för att mäta.

## Reservfråga: jämförelse mellan leverantörer

**Fråga:** `Vilken leverantör på IT-drift Större har lägst takpris för en cybersäkerhetsspecialist på kompetensnivå 4?`

**Rätt svar:** Iver Sverige AB, 585 kronor per timme exklusive moms, enligt prislistan i
Vägledning IT-drift (2.7.3). Ett takpris: leverantören får erbjuda lägre pris i avropssvaret.

Använd den om en av de fem fallerar eller om det finns tid över.

## Egna filer: jämför ett eget avtal

Agenten läser en fil som du laddar upp och jämför den med ramavtalet
([ADR 0026](adr/0026-egna-filer.md)). Avtalet är påhittat
([`examples/uppladdning/`](../examples/uppladdning/README.md)): ett avrop från
IT-konsulttjänster där fem klausuler avviker från ramavtalets allmänna villkor och fyra stämmer.

**Före:** bygg PDF:en med
`uv run python examples/uppladdning/render_pdf.py /tmp/exempelavtal-it-konsult.pdf`, eller ladda
upp Markdown-filen som den är. Öppna en ny flik, bifoga filen med gemet i chattfältet och ställ
frågan. Gemet kommer med webbappens ändring för uppladdning. Finns det inte, kör steget i
terminalen (nedan).

**Fråga:** `Jämför punkt 7 om skadestånd i mitt avtal med ramavtalet för IT-konsulttjänster. Vad avviker?`

**Vad som händer:** agenten läser filen med `read_upload` ("Läser din fil") och ser att avtalet är
ett avrop från ramavtal 23.3-1688-2024. Den söker i ramavtalets allmänna villkor, läser 2.19.8
med `read_section` och kör `find_amendments` på det. Källorna ur filen märks "Din fil".

**Visa:**
- Stegen: agenten hittade själv vilket ramavtal filen hör till, ur filens egen text.
- Att källorna kommer från två håll: "Din fil" med sidan och ramavtalets avsnitt med citatet.

**Säg:** "Filen syns bara i samtalet där den laddades upp och tas bort efter sju dagar. Det agenten
redan har läst ur den ligger kvar i samtalets historik. Filen tolkas i en egen process med gränser
för storlek, sidor och tid. Filverktygen ligger i API:t, så avtal-mcp är oförändrat och kan
fortfarande bara läsa ramavtalen. Kontrollen gäller citaten ur filen på samma sätt: varje citat ska
stå ordagrant i avsnittet det anger."

**Rätt svar:** ansvarstaket skiljer sig: 10 procent av kontraktets värde under hela tiden mot 50
procent av medelvärdet per kontraktsår i 2.19.8, och det får avropet ändra. Men avtalet låter
begränsningen gälla också vid grov oaktsamhet, och det undantaget får inte ändras.

**Hela avtalet, om det finns tid:**
`Jämför mitt avtal med ramavtalets allmänna villkor. Vad avviker?`

Rätt svar är fem avvikelser: resor till stationeringsorten ersätts (2.9.1 säger nej), leverantören
får begära prisjustering (2.9.2 säger nej), en faktureringsavgift på 45 kronor (2.11.1 säger nej),
ett lägre vite (2.19.1.3, som avropet får ändra) och ansvarsbegränsningen vid grov oaktsamhet
(2.19.8). [`examples/uppladdning/README.md`](../examples/uppladdning/README.md) har alla med
avsnitten.

**Status:** körd 2026-10-07 mot en ersättare för avtal-mcp, inte mot databasen och inte i
webbappen. Punkt 7 tog 66 s genom API:t och blev Verifierat. Hela avtalet tog 4 min 19 s i
terminalen, eftersom kontrollen underkände två utkast, och blev Verifierat med alla fem
avvikelserna. Kör båda en gång dagen före.

**I terminalen (nivå B):**

```bash
docker compose cp examples/uppladdning/exempelavtal-it-konsult.md api:/tmp/
docker compose exec api python -m avtalsagent.agent --fil /tmp/exempelavtal-it-konsult.md \
  "Jämför punkt 7 om skadestånd i mitt avtal med ramavtalet för IT-konsulttjänster. Vad avviker?"
```

**Om det går fel:** saknas gemet eller avvisas filen, kör steget i terminalen. Hittar agenten inte
ramavtalet, säg i frågan att det är IT-konsulttjänster 1. Verksamhetens IT-behov.

## Frågor att undvika live

- **Listor över många leverantörer** ("alla leverantörer i Bemanningstjänster"): listor inom ett
  delområde eller en region fungerar sedan filtret på delområde (PR #15), en lista över ett helt
  område är inte mätt, och svaren är långa och kontrollen tar tid.
- **Andra ramavtalsområden än de fyra i piloten**: IT-drift, Bemanningstjänster,
  IT-konsulttjänster Resurskonsulter och Programvaror och tjänster. Annat finns i registret men
  inte som dokument.
- **Inskannade dokument**, till exempel IBM:s volymavtal: de saknar textlager och hålls i
  karantän tills det finns OCR.
- **Frågor om dagens datum** ("hur länge till gäller avtalet?"): svaret beror på dagen, och då
  stämmer det inte med det du har övat på.

## Om något går fel: nivåerna

| Nivå | Vad | När |
|---|---|---|
| A | Webbappen, live | Normalfallet |
| B | Samma agent i terminalen: `docker compose exec api python -m avtalsagent.agent "…"` | En fråga felar i webbappen men tjänsterna lever |
| C | Skärminspelningen från dagen före | Tjänsterna startar inte, eller nätet är nere |
| D | Inläsningens resultat: rapporten i `data/reports/` och mätningarna i `evals/reports/` | För att visa workflow-sidan och kontrollerna utan nät |
| E | Skärmbilderna | Allt annat |

Terminalen (nivå B) visar samma verktygsanrop som webbappen och dessutom "→ Svaret lämnas för
kontroll." och, om kontrollen underkänner ett utkast, "✗ Kontrollen underkände svaret, som går
tillbaka till agenten:" med skälen. Webbappen visar agentens nya steg efter ett underkänt utkast,
men inte att utkastet underkändes eller varför. Terminalen kallar statusen Verifierat för
"Kontrollerat".

## Felsökning

| Det du ser | Gör så här |
|---|---|
| `docker compose up` slutar med att en tjänst inte blev frisk | `docker compose logs api --tail 50`. Oftast saknas `OPENAI_API_KEY` i `.env`. |
| "Agenten kunde inte svara på frågan. Försök igen om en stund." | `docker compose logs api --tail 50` visar felet. Ställ frågan i terminalen (nivå B). |
| Svaret dröjer mer än två minuter | Vänta till tre. Kontrollen kan ha skickat tillbaka ett utkast. Annars nivå B. |
| Steget "Sökte i dokumenten" har ett fel om sökindexet under "Visa svaret från verktyget" | Indexet saknas eller byggdes med en annan modell: kör inläsningen igen (steg 9). |
| Webbappen visar inget alls | `docker compose ps`; starta om med `docker compose up -d --wait`. |
