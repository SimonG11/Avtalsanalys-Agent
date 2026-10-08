# Demoskript

Fem frågor som visar vad agenten gör: den frågar med alternativ när fallen är för många,
väljer registret i stället för dokumenten, följer hänvisningar i flera steg, hittar en rättelse
som ersätter klausulen och säger "framgår inte" i stället för att gissa. Fråga 3 och 4 visar det
som ett fast workflow inte klarar, eftersom nästa steg beror på vad agenten just har läst. Fyra
av frågorna kommer ur testsamlingen ([`evals/datasets/gold_sv.jsonl`](../evals/datasets/gold_sv.jsonl)),
så de har ett facit och en mätning bakom sig. Den första är en öppnare variant av q04, gjord för
att agenten ska behöva fråga.

Alla fem kördes mot den riktiga databasen 2026-10-07, både i terminalen och i webbappen, och
reservfrågan i terminalen. Fråga 1 kördes då med den tidigare regel 4 i systemprompten.
Resultaten står i [steg 12](steg/12-demo.md). Körningen hittade ett fel:
i webbappen föll fråga 3 första gången, eftersom API:ts AG-UI-adapter stoppade körningen efter 25
steg i grafen, ungefär sex modellanrop. Webbappens första körningar av fråga 3–5 gjordes med
rättelsen provad lokalt. En andra körning av alla fem i webbappen gjordes med `main` efter PR #22
och webbappen från PR #23. Hämta `main` före demot. Beslutet bakom urvalet står i
[ADR 0022](adr/0022-demot.md).

## Ordningen

| # | Fråga | Visar | Status | Tid i webbappen | Tid i terminalen |
|---|---|---|---|---|---|
| 1 | Uppsägningstiden i IT-drift | Agenten frågar med alternativ när fallen är för många | Verifierat | 37–46 s genom API:t mot ersättaren, utan din tid att svara | – |
| 2 | q10 Nordlo Advance | Registret i stället för dokumenten | Verifierat | 12–14 s | 19 s |
| 3 | q14 Lördagsarbete | Flera steg genom hänvisningar | Verifierat | 39–62 s | 50 s |
| 4 | q21 Antal anbud | En rättelse ersätter klausulen | Verifierat | 32–45 s | 36 s |
| 5 | q27 Rangordnad etta | Agenten gissar inte | Inget svar eller Verifierat | 35–48 s | 31 s |
| R | q24 Lägsta takpris | Jämförelse mellan leverantörer | Verifierat | – | 42 s |

Tiderna för fråga 2–5 och reservfrågan kommer från körningarna mot den riktiga databasen, två i
webbappen (q14 tre) och en i terminalen; terminalens tider räknar med att programmet startar.
Fråga 1 besvaras annorlunda med den nya regel 4 i systemprompten
([ADR 0025](adr/0025-tankar-och-farre-motfragor.md)). Dess tid kommer från tre körningar genom
API:t (`POST /agui`) med `low` 2026-10-07 mot en tillfällig ersättare för avtal-mcp med pilotens
data, inte mot databasen, och räknar inte med tiden du tar på dig att svara. Den är inte körd om i
terminalen. Räkna med upp till en minut per fråga. En fråga kan ta längre tid om kontrollen
skickar tillbaka ett utkast, och svaret kan formuleras olika mellan körningar. Fakta och källor
ska vara desamma.

Ställ frågorna i en ny flik var för sig (uppdatera sidan mellan frågorna), så att agenten inte
läser in en tidigare fråga i nästa.

## Före demot

Dagen före, på datorn du demonstrerar på:

1. `git pull` och `docker compose up -d --build --wait --wait-timeout 300`.
2. Om koden för inläsningen har ändrats sedan förra inläsningen:
   `docker compose --profile ingest run --rm ingest` (med tolkningscachen i `data/` tog det
   knappt tio minuter 2026-10-07, se [steg 12](steg/12-demo.md); cachen förklaras i
   [steg 9](steg/09-api.md)).
3. Ställ alla fem frågorna och reservfrågan en gång i webbappen och notera tiderna.
4. Spara skärmbilder av svaren (nivå E nedan) och gärna en skärminspelning av hela demot
   (nivå C).

En halvtimme före:

1. `docker compose up -d --wait --wait-timeout 300` och `docker compose ps`: alla fyra tjänster
   ska vara `healthy`.
2. Öppna http://localhost:3000 och ställ fråga 2 som uppvärmning. Den är snabb och visar att
   nyckeln, avtal-mcp och databasen fungerar.
3. Ha en terminal öppen i repot för nivå B, och mappen med skärmbilder redo.
4. Stäng aviseringar och andra flikar. Zooma webbläsaren så att svaret syns på projektorn.

## Fråga 1: agenten frågar när fallen är för många

**Fråga:** `Vad är uppsägningstiden i IT-driftavtalet?`

**Vad som händer:** agenten söker i registret och i dokumenten och ser att svaret beror på vem
som säger upp och vad som sägs upp: ert avropade kontrakt eller själva ramavtalet, i två
delområden. Regel 4 i systemprompten säger att den ska svara för varje fall när fallen är få och
svaren korta, och annars fråga med 2–5 alternativ. Här räknar den fallen som för många och frågar
(`ask_user`) efter de första sökningarna, 8–13 sekunder in och innan den har läst något avsnitt.
Webbappen visar frågan i chatten med en knapp per alternativ och ett fält för eget svar. Mot
ersättaren 2026-10-07 frågade den i alla tre körningarna med `low`, som demot kör med, och varje
gång nästan ordagrant likadant: "Menar du uppsägning av ert avropade kontrakt eller av själva
ramavtalet, och vem ska säga upp det?" Alternativen var fyra: "Vi vill säga upp vårt kontrakt
utan särskilt skäl", samma uppsägning på grund av leverantörens avtalsbrott, "Leverantören vill
säga upp vårt kontrakt" och "Uppsägning av själva ramavtalet".

**Välj:** "Vi vill säga upp vårt kontrakt utan särskilt skäl", det första alternativet. Är
alternativen formulerade annorlunda, välj det om er egen uppsägning av det avropade kontraktet
utan skäl. Frågar den om delområdet, välj Mindre. Efter svaret läser agenten 6.21.8 i Allmänna
villkor för både IT-drift Mindre och IT-drift Större, letar efter ändringar och söker i Frågor
och svar.

**Visa:**
- Agentens fråga i chatten. Körningen står still i grafen tills du svarar, och fortsätter sedan
  från samma checkpoint i Postgres.
- Stegen ovanför svaret: varje verktyg agenten valde, med argumenten. När svaret har kommit är
  de ihopfällda till raden "Arbetade i … s · N steg"; klicka på den. "Visa svaret från
  verktyget" visar vad den fick tillbaka.
- Agentens tankar, om de kommer: en egen rad i tidslinjen med OpenAI:s sammanfattning av
  modellens resonemang, under sammanfattningens rubrik i fetstil. Rubriken och texten är på
  engelska. "Tänker …" överst i tidslinjen säger bara att modellen arbetar, inte att en tanke
  kommer. Med `low` kom en tanke i 1 av 8 demokörningar mot ersättaren (1 av 47 modellanrop), i
  fråga 1, så lova den inte.
- Statusen "Verifierat" och citatet i källkortet under svaret. Källpanelen med PDF-sidan visar du
  hellre i fråga 3 (se "Om det går fel" nedan).

**Säg:** "Ett fast workflow kan inte fråga: det väljer en tolkning eller räknar upp alla fall.
Agenten ser att svaret beror på vem som säger upp och vad som sägs upp. När fallen är få svarar
den för vart och ett och säger vad som avgör. Här är de för många, så den frågar och ger några
alternativ att välja mellan. Det är en av anledningarna till att frågorna är en agent och
inläsningen ett workflow."

**Rätt svar:** 6.21.8 anger ingen fast uppsägningstid. Utan angivande av skäl får ni säga upp
efter halva kontraktstiden, dock tidigast efter tre år, om kontraktet inte säger annat (6.21.8).
Vid uppsägning enligt 6.21.7, till exempel
vid leverantörens väsentliga avtalsbrott, sker uppsägningen skriftligen, med omedelbar verkan eller senast nio månader efter uppsägningen (6.21.7).
Leverantören har minst sex månaders uppsägningstid om ni inte rättar ett väsentligt avtalsbrott
inom 30 dagar (6.21.9). Vilka av punkterna svaret tar med beror på vad du svarade på agentens fråga.

**Om det går fel:**
- Agenten frågar inte utan svarar för alla fall direkt: det är vad regel 4 säger när fallen är
  få, och ett rimligt svar. Med `medium` gjorde den så i 1 av 3 körningar mot ersättaren och tog
  med 6.21.7, 6.21.8 och 6.21.9, alla rätt; med `low` hände det inte. Visa att svaret säger vad
  som avgör, säg att agenten den här gången räknade fallen som få nog, och gå vidare.
- Svaret slutar med en fråga i texten ("Menar du ert avropade kontrakt eller själva ramavtalet
  …?") och inga knappar: agenten skrev frågan i svaret i stället för att fråga med `ask_user`. Så
  blev det i körningen ovan. Svaret är ändå färdigt och kontrollerat; gå vidare.
- Frågan har tre alternativ i stället för fyra, eller andra ord: välj det som gäller er egen
  uppsägning av det avropade kontraktet.
- En tanke läser fel eller låter pratig: sammanfattningen skrivs av OpenAI, inte av agenten, och
  kontrolleras inte. En gång läste den 6.21 som "the Sixth Amendment". Säg det och peka på svaret,
  som är kontrollerat.
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
