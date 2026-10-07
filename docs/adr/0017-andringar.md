# ADR 0017: `find_amendments` och regeln om senaste lydelsen: ändringar ur steg 4:s hänvisningar

**Status:** Föreslaget (PR för `find_amendments`, efter `calculate_date`).
Lägger till ett åttonde verktyg i avtal-mcp ([ADR 0012](0012-avtal-mcp.md)), en regel i
valideringskedjan ([ADR 0015](0015-valideringskedjan.md)) och bygger ut hur steg 4 läser
ändringar ([ADR 0009](0009-extraktion-avstamning-och-karantan.md)). Resten av de besluten gäller.

## Kontext

Arkitekturplanen har verktyget `hitta_andringar` ("Ändringsdokument som påverkar ett visst
avsnitt") och en deterministisk kontroll: "Senaste giltiga version måste vara använd när ändringar
finns". M8 byggde kedjan utan den, eftersom verktyget saknades och steg 4 läste ändringarna för
dåligt (ADR 0015, Konsekvenser).

- **Ändringarna står på två ställen.** Ändringsdokument finns bara i Programvaror och tjänster
  (Microsofts bilagor 4, 5, 6, 7 och 10, och IBM:s tilläggsavtal). Frågor och svar finns i alla
  fyra pilotområden: 13 loggar med 1 391 publika svar, där Kammarkollegiet ibland ändrar ett
  avsnitt ("Rättelse. Texten som gäller är följande för punkt 3.2: …").
- **Steg 4 märkte redan ändringar**, i kolumnen `replaces` på hänvisningen, men inget läste den,
  och regeln missade många: den sökte bara ett ändringsverb i hänvisningens egen mening, så
  "Gällande avsnitt 4.2.4, bokstaven L: Kammarkollegiet ersätter härmed …" och "A. Punkt 2a. i
  Registreringen ersätts …" blev ingen ändring, och "Kammarkollegiet ändrar inte avtalsvillkoret
  i 7.19.11" blev en.
- **Ett ändringsdokument pekar på bilagor som dess titel nämner** ("Bilaga 4 Tillägg och
  förtydliganden till bilaga 4.1"), men steg 4 sökte "Punkten "Övrigt"" i alla filer på sidan
  och fick fyra kandidater, och "Punkt 2a" i tilläggets egen fil.
- **Testfrågorna q21–q23** gäller var sitt fall: ett svar i Frågor och svar som rättar ett
  avsnitt, ett Microsoft-tillägg som ändrar en punkt, och ett tillägg som ändrar ett
  beställningskrav i en av flera registreringar.

## Beslut

1. **Ingen ny tabell.** En ändring är en hänvisning med `replaces` från ett ändringsdokument
   eller ett svar i Frågor och svar, och `find_amendments` läser dem baklänges när det anropas:
   `document_reference` → `reference_target` → avsnittet eller filen som frågas om.
2. **Steg 4 läser fler ändringar och färre felaktiga.** Fler ändringsord ("texten som gäller",
   "gör följande tillägg", "strykas och ersättas"); en nekad ändring ("ändrar inte", "utan
   ändringar") och ett villkor ("om pris ändras") är ingen ändring; en punkt före en liten bokstav
   avslutar ingen mening ("Punkt 2a. i Registreringen ersätts"); och i ett svar räknas också nästa
   mening, fram till nästa hänvisning ("Gällande avsnitt 4.2.4, bokstaven L: Kammarkollegiet
   ersätter …"). Mätt på pilotens 2 977 hänvisningar i ändringsdokumenten och frågeloggarna: 12
   nya ändringar och 6 som inte var några (39 i stället för 33), alla lästa
   ([steg 4](../steg/04-extraktion.md), "Ändringar").
3. **Ett ändringsdokument söker först i bilagorna som dess titel nämner** (regel R1a för ett
   nummer, R4a för en rubrik), bara på samma ramavtalssida: "Punkten "Övrigt"" i Bilaga 4 är
   4.1 §10, och "Punkt 2a" i Bilaga 5 är §2 i 5.1, 5.2, 5.3 eller 5.4 (tvetydig). Sju
   hänvisningar får ett annat utfall, alla i Microsofts tillägg, och andelen som reglerna löser
   går från 73,48 till 73,51 procent.
4. **Verktyget** tar `sha256` och ett avsnitt (`section_number` eller `section_position`), eller
   bara filen. Svaret har `target` (det som frågades om), `amendments` och `held_back`. Varje
   ändring har `amending` (avsnittet som ändrar, med citatfälten), `amended` (avsnittet eller
   hela filen som ändras), `raw`, `status` (`resolved`, eller `ambiguous` när ändringen kan gälla
   en annan fil), `dated` (svarets datum i loggen, eller ändringsdokumentets) och `excerpt`.
   Nyaste först. Ett ändrande avsnitt som verktygen inte får visa räknas i `held_back`.
5. **Regeln om senaste lydelsen** (`validation/latest_wording.py`) kör efter citaten och
   registeruppgifterna, före granskaren. Ett citerat avsnitt med en ändring som är `resolved` och
   gäller just det avsnittet godkänns bara om svaret också citerar ändringen. Annars är det ett
   problem som säger vilken ändring det gäller, och efter försöken en reservation. Kan ändringarna
   inte läsas, blir det en reservation. Regeln läser ändringarna genom avtal-mcp, aldrig ur
   historiken.
6. **Prompten** säger åt agenten att köra `find_amendments` på varje avsnitt den citerar, bygga
   på den senaste lydelsen och citera både avsnittet och ändringen.

## Konsekvenser

- Ett svar som bygger på en ändrad punkt underkänns och får veta vilken ändring som gäller, utan
  modell. Granskaren behöver inte hitta ändringen själv.
- Ett anrop till per citerat avsnitt, för agenten och för kontrollen.
- Regeln kräver att en ändring citeras, inte just den senaste, när flera ändrar samma avsnitt.
- Tvetydiga ändringar och ändringar av en hel fil kontrolleras inte; verktyget visar dem och
  agenten avgör. Ett tillägg som ändrar "Registreringen" kan gälla tre filer.
- Steg 4 hittar bara det som står med ändringsord. En ändring som inte säger vad den ändrar, eller
  som ändrar en fil som aldrig publicerats, syns inte.
- Ett svar i Frågor och svar som ändrar ett upphandlingsdokument följer inte med till kopiorna av
  texten i de undertecknade avtalen.
- `process` och `index` måste köras igen (`run`) för att `replaces` och målen ska skrivas om.

## Alternativ som valts bort

- **En egen tabell för ändringar**, fylld i steg 4. Tydligare i inläsningsrapporten, men kräver
  en migrering, en domänmodell och nya integrationstester för samma uppgift som hänvisningarna
  redan bär.
- **Varje hänvisning från Frågor och svar.** 644 avsnitt har minst en; de flesta svar förklarar
  bara texten.
- **En språkmodell som avgör om ett svar ändrar avtalet.** 1 391 svar, en kostnad vid varje
  inläsning och ett utfall som inte går att upprepa exakt. Reglerna är mätta på samma svar.
- **Att regeln också kräver tvetydiga ändringar.** Agenten skulle då tvingas citera en ändring som
  kanske gäller en annan fil.
- **Att `read_section` visar ändringarna.** Ett verktyg per uppgift, som planen säger; ett
  avsnitts ändringar kan dessutom vara många.
