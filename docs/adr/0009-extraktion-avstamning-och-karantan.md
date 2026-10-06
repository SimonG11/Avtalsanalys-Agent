# ADR 0009: Extraktion, avstämning mot registret och karantän

**Status:** Föreslaget (PR #6). Bygger på förslaget som Simon godkände 2026-10-06
(`implementering/m4-forslag.md`). Ändringarna mot förslaget står i en egen tabell nedan. Ändrar ADR
0008 beslut 5 (beslut 10 nedan).

## Kontext

Steg 4 och 5 i inläsningen ska läsa ut vad varje dokument är och säger, stämma av det mot registret
och hålla tillbaka det som avviker. Klart när: inläsningsrapporten visar täckning och avvikelser,
avvikande dokument ligger i karantän och andelen upplösta hänvisningar är mätt.

Innan något byggdes kartlade vi alla 207 filer i urvalet (13 175 avsnitt) och mätte varje regel på
dem. Det som styr besluten:

- **Diarienumret står i sidhuvudet.** 3 003 av 3 257 diarienummer finns i sidhuvuden, som steg 3
  tar bort ur avsnitten. 34 filer har sitt nummer bara där. Microsofts volymavtal anger sin
  avtalsperiod bara i en sidfot.
- **Samma nummer skrivs på många sätt:** `23.3-2940-20:018` och `23.3.2940-20:018`,
  `23.3.2649-22-001` och `23.3-2649-2022-001`. Registret har 79 diarienummer och 2 361
  avtalsnummer i sin egen stavning.
- **Ett dokument kan citera en annan upphandling** utan att avvika: "ramavtal
  IT-konsulttjänster Resurskonsulter, region Södra (dnr 23.3-7067-17)".
- **Leverantörens part kan vara en annan juridisk person** än registret har: fyra
  leverantörsavtal är tecknade av ÅF Digital Solutions AB och Tieto Sweden AB, där registret har
  AFRY Sweden AB och Tieto AB med andra organisationsnummer.
- **De flesta hänvisningar pekar på en rubrik, inte ett nummer:** "enligt avsnitt Avtalsbrott och
  påföljder" (3 382 gånger) mot "punkt 6.21.9" (2 115). 978 av 1 221 hänvisningar till en bilaga
  med namn gäller anbudsblanketter som inte publiceras på avtalssidan.
- **Undertecknade avtal har ett certifikat** från e-signeringstjänsten sist i filen, med namn och
  id för dem som skrivit under.
- **Språkmodellen behövdes nästan inte.** Reglerna gav typ åt 207 av 207 filer. Ingen tid i ord
  eller något nummer krävde en modell. Det som återstod var rubriker som en hänvisning skriver
  annorlunda än dokumentet ("Försäljningsredovisning och administrativ avgift" mot "9.15
  Försäljningsredovisning och administrationsavgift") och hänvisningar med ett ämne i stället för
  en punkt ("villkor i Allmänna villkor gällande viten").

## Beslut

1. **En modul per sorts uppgift och en per kontroll.** Steg 4 är `ingestion/step4_extract.py`, som
   sätter ihop modulerna i `ingestion/extract/`: dokumenttyp och datum (`document_type.py`),
   diarie-, avtals- och organisationsnummer (`identifiers.py`), parter (`parties.py`), avtalsperiod
   och underskrifter (`dates.py`), hänvisningar (`reference_patterns.py`) och upplösningen av dem
   (`reference_resolver.py` med `resolve_in_document.py`, `resolve_on_page.py` och
   `resolve_questions.py`), och språkmodellens val av rubrik (`title_matcher.py`). Steg 5 är
   `ingestion/step5_validate.py` med en kontroll per fil i `ingestion/checks/`. `pipeline.py` kör
   steg 3–5 utan databas och `report.py` skriver rapporten. Varje regel har ett id ("P3", "R4")
   som sparas med det den hittat, så varje uppgift kan spåras till sin regel och sin text.
2. **Uppgifterna läses ur alla block,** också sidhuvuden och sidfötter. Hänvisningarna läses ur
   avsnitten, eftersom de går från ett avsnitt till ett annat.
3. **Nummer jämförs på en nyckel.** `procurement_key` och `agreement_key` i
   `domain/identifiers.py` ger samma nyckel för varje stavning (`23.3-2940-2020-018`). Ett
   avtalsnummer utan löpnummer får upphandlingens nyckel. Numret sparas som registret skriver det
   när registret har nyckeln.
4. **Dokumenttypen sätts med regler på länken:** länktexten, rubriken den står under,
   avtalsnumret och filnamnet (R01–R14, sedan reservreglerna F1–F3 på kategorin). Det ger 14 typer
   i tre grupper: avtalet med bilagor, upphandlingen och stöd för avrop. En typ från en reservregel
   noteras i rapporten, och en fil som ingen regel typar rapporteras.
5. **Språkmodellen väljer bara rubrik.** `gpt-6-luna` får frågan bara när reglerna hittat filen
   men inte avsnittet: en rubrik som inte stämmer exakt (regel R4-llm) eller ett ämne efter ett
   dokumentnamn (R2-llm). Den väljer bland filens rubriker, och ett svar som inte är en av dem
   används inte. Svaren sparas i `data/llm_cache/` med en nyckel av modell, promptversion, frasen
   och kandidaterna, så en omkörning inte kostar något. Utan nyckel hoppas steget över, och
   rapporten anger andelen med och utan modellen.
6. **Tre allvarlighetsgrader.**
   - **Karantän** när dokumentets egen identitet strider mot registret: dess eget diarienummer,
     dess eget avtalsnummer, ett organisationsnummer för en part eller i en tabell, eller den
     avtalsperiod det anger. Också när dokumentet saknar text, när ingen sida på avropa.se längre
     länkar till det, och för ett avsnitt på skannade sidor.
   - **Rapport** för det som inte gäller ett dokuments innehåll: ett avtal utan inläst
     huvuddokument, en avtalssida vars period skiljer sig från registret och en fil utan typ.
   - **Notering** för det som är värt att veta men ingen avvikelse: en hänvisning till en annan
     upphandling, ett leverantörsnamn skrivet på ett annat sätt med samma organisationsnummer, en
     fil med några skannade sidor och en typ från en reservregel.
7. **Karantänen är stängd som standard.** En tolkad fil som inte gått igenom steg 4 och 5 sedan
   steg 3 sparade den räknas som i karantän (`extraction_store.quarantine`). Körs bara steg 3,
   eller avbryts en körning, indexeras alltså inget okontrollerat.
8. **En person kan godkänna en avvikelse** i `accepted_findings.toml` i repots rot, med
   avvikelsens nyckel (som rapporten skriver ut), ett skäl, sitt namn och ett datum. Filen granskas
   som kod. Ett godkänt fynd står kvar i rapporten men håller inte tillbaka något. Filen är tom
   tills Simon beslutar.
9. **Andelen upplösta hänvisningar** är upplösta delat med alla hänvisningar utom lagar och
   standarder, "fråga N" i en frågelogg, listpunkter och dokumentets hänvisningar till sig själv.
   En hänvisning till en bilaga som inte publiceras räknas med, som ej upplöst, eftersom den visar
   vad agenten inte kan följa.
10. **Steg 3 tar bort e-signaturens certifikat** (ändrar ADR 0008 beslut 5): från den första sida
    där ett block som inte är sidhuvud eller sidfot innehåller "Adobe CDS" och ett block "juridiskt
    bindande", den sidan och alla efter. Det gäller 25 leverantörsavtal. Datumet för underskriften
    sparas, men bara "datum klockslag", aldrig namn eller id.
11. **`process` ersätter `chunk`,** och `run` kör alla steg i ordning. `process` sparar avsnitt,
    uppgifter, hänvisningar och fynd i en transaktion och skriver rapporten som Markdown och JSON
    i `data/reports/`.

## Ändringar mot förslaget

| Förslaget | Det som byggdes | Varför |
|---|---|---|
| Språkmodellen sätter dokumenttyp när länktexten inte räcker | Regler | Reglerna typar 207 av 207 filer, och de 9 filer där innehållet pekar på en annan typ är förklarade. En fil utan typ rapporteras för granskning |
| Språkmodellen läser giltighetstid skriven i ord | Regler | 2 931 tider i ord och siffror, aldrig oense. Tal i ord läses med en ordlista |
| Språkmodellen löser hänvisningar utan nummer (`se Allmänna villkor om vite`) | Regeln hittar dokumentet, modellen väljer rubrik bland dokumentets rubriker | Modellen kan då aldrig peka på något som inte finns. Den svarar också för rubriker som skrivs annorlunda |
| Svaren sparas per filhash, modell och promptversion | Per modell, promptversion, fras och kandidater | Samma fråga ur olika filer ger samma svar och kostar en gång |
| Metadata ur avsnitten | Uppgifter ur alla block, hänvisningar ur avsnitten | 34 filer har sitt diarienummer bara i sidhuvudet |
| Momsnummer (`SE556677889901`) | Läses, men finns inte i urvalet | Det enkla mönstret `SE\d+` gav 105 falska träffar på NUTS-koder. Det strikta ger 0 |
| `bilaga 3` löses till dokumentet Bilaga 3 på samma sida | Fem former: nummer, rubrik, bilaga med nummer, bilaga med namn, dokumentnamn | Rubriker och namn är de vanligaste formerna |
| Diarienumret i dokumentet finns i registret | Det ska vara upphandlingen på sidan som länkar till dokumentet | Ett nummer som finns i registret men gäller en annan upphandling är också fel |
| Leverantörer och organisationsnummer som nämns finns på rätt avtal | Alla organisationsnummer och partsklausulen | Ett leverantörsnamn utan organisationsnummer kontrolleras inte i M4 |
| Karantän för hela dokumentet vid avvikelse | Karantän när dokumentets egen identitet avviker, annars rapport eller notering | En citerad upphandling eller en sidas period är ingen avvikelse i dokumentet |
| Karantän för avsnitt på skannade sidor | Också hela filer utan avsnitt | 16 av de 37 skannade sidorna ligger i inget avsnitt, och två filer har inga avsnitt alls |
| Fem dokumenttyper (arkitekturplanen 3.2) | 14 typer i tre grupper | Tabellen nedan |
| `pipeline.py` kör alla steg | `pipeline.py` kör steg 3–5 utan databas; kommandot `run` kör alla | Samma kod körs i testerna, i mätningen och i drift |

Typerna mot arkitekturplanens fem: leverantörsavtal och huvuddokument är *huvuddokument*;
prisbilaga, kravkatalog och kravspecifikation är *bilaga*; allmänna villkor, bilaga och ändring
är som i planen; avropsstöd är *avropsvägledning*. Licensvillkor, upphandlingsdokument, frågor och
svar, kravredovisning och mall är nya.

## Konsekvenser

- {{siffror: karantän, täckning, andel}}
- En avvikelse i ett dokument syns med sin text och sin regel i rapporten, och en person kan
  godkänna den med ett skäl som granskas.
- Andelen upplösta hänvisningar beror på om språkmodellen körts. CI och en granskare utan nyckel
  får andelen utan modell; rapporten anger båda.
- Det som inte byggs i M4 (var och en under 50 hänvisningar i urvalet, tillsammans ungefär 1,2
  procentenheter): en rubrik följd av ett dokument ("avsnitt X i Allmänna villkor"), "kapitel
  <Dokument>" när filen saknar ett sådant kapitel, en unik rubrik som börjar med hänvisningens
  fras, och steget nummer + rubrik i frågeloggarna. Leverantörsnamn utan organisationsnummer
  kontrolleras inte. Vad ett tillägg ersätter ("ersätter avsnitt 7.19.1.3") markeras, men vilket
  dokument tillägget gäller läses inte ut ur dess titel.
- IBM:s volymavtal är en .doc-fil som steg 1 inte hämtar, så avtalet 6765/05 rapporteras utan
  huvuddokument.

## Alternativ som valts bort

- **En språkmodell som läser varje avsnitt** och ger metadata och hänvisningar. Den kostar per
  körning, ger inte samma svar varje gång och skulle ändå behöva kontrolleras mot texten. Reglerna
  täckte allt utom rubrikvalet.
- **Karantän för varje avvikelse.** Då skulle varje dokument som citerar en annan upphandling och
  varje mall med tomma fält hållas tillbaka.
- **Jämföra nummer som text.** Samma avtal skrivs på upp till fyra sätt i urvalet.
