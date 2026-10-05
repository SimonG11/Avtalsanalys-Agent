# ADR 0008: Tolkning och uppdelning av dokumenten

**Status:** Förslag (M3), granskas av Simon i PR:en

## Kontext

Urvalet från M2 är 207 unika filer: 176 PDF med 3 924 sidor och 31 Word-filer. Agenten ska citera
avtalen ordagrant och med punktnummer ("punkt 6.21.9"), så texten måste vara exakt och varje
avsnitt måste ha rätt nummer.

Det som avgör valet:

- **Textlager.** 37 av 3 924 PDF-sidor (0,9 %) är skannade bilder utan text. De finns i fyra filer:
  IBM:s volymavtal (10 + 1 sidor) och, för Systemutveckling, Allmänna villkor (22 av 31 sidor) och
  Kravkatalog (4 av 11 sidor).
- **Numrering.** Avtalen exporteras ofta från TendSign och numreras som `6.21.9 Ramavtalsleverantörens
  uppsägningsrätt`. Numret börjar inte alltid på 1 (Allmänna villkor är kapitel 6). Mallarna i Word
  har numrerade klausuler där hela stycket är rubriken (`3.1 Personuppgiftsbiträdesavtalet utgör en
  bilaga ...`). Microsofts och IBM:s dokument saknar ofta numrerade rubriker, och prisbilagor är
  bara tabeller.
- **Falska rubriker.** Numrerade listor (`1. FN:s barnkonvention`), innehållsförteckningar
  (`6.6.1 Dokumentation 8`), belopp, datum och citerade kravnummer börjar också med siffror.
- **Miljön.** Doclings PDF-flöde kräver PyTorch. Version 2.133 importerar PyTorch även när
  layoutmodellen körs i ONNX Runtime (tabellmodellernas tillägg och valet mellan CPU och GPU), så
  det finns ingen väg utan PyTorch. Modellerna laddas ner från Hugging Face första gången.

## Beslut

1. **Docling läser filerna, bakom gränssnittet `DocumentParser`.** Vi använder Docling med
   PyTorch för processorn (CPU-paketen från PyTorchs eget paketindex, så inga GPU-bibliotek
   installeras). Layoutmodellen Heron hittar rubriker, listor, tabeller, sidhuvuden och sidfötter
   och ger texten i läsordning. Tabellmodellen TableFormer delar tabellerna i celler, så en
   prisrad blir `Konsult nivå 3 | 1 150 kr`. OCR är avstängd (beslut 2). All text kommer ur PDF:ens
   textlager, så citaten blir exakta. Får en tabell inga celler läses texten i tabellens ruta ur
   textlagret, en rad per tabellrad. Word läses av Doclings Word-läsare, som behåller
   rubriknivåer och rubriknummer utan modell. Simon valde Docling med PyTorch 2026-10-05.
2. **Steg 2 kontrollerar varje sida.** En sida med färre än 50 tecken där bilder täcker minst halva
   sidan räknas som skannad och markeras `needs_ocr`. Den rapporteras i varje körning och lagras i
   `parsed_file.pages_needing_ocr`. Ingen OCR-tjänst byggs nu (M3b), eftersom andelen är under 1 %.
   Det innebär att IBM:s volymavtal och större delen av Allmänna villkor för Systemutveckling
   saknar text tills OCR finns. Om det räcker är Simons beslut.
3. **Resultatet av tolkningen sparas** som JSON per fil (`data/parsed/<sha256>.json`) med parserns
   namn och version. En omkörning tolkar bara nya filer eller filer som en äldre parser läst.
4. **Avsnitten hittas som den bästa sammanhängande numreringen,** inte rad för rad.
   Alla block som börjar med ett nummer och en rubrikliknande titel är kandidater. Varje kandidat får
   poäng (högre om Docling kallar blocket rubrik, om titeln är kort och om numret finns i dokumentets
   innehållsförteckning). En dynamisk programmering väljer den kedja med högst poäng där varje nummer
   får följa det förra: första barnet (6.6 → 6.6.1) eller nästa nummer på samma eller högre nivå
   (6.6.8 → 6.6.9, 6.7, 7). Saknade nummer kostar poäng. En ny nivå börjar på 1, utom när numret
   står i innehållsförteckningen (en mall med strukna avsnitt går från 1.3 till 2.4). Numreringen
   får inte börja om på 1, eftersom en omstart i de här dokumenten nästan alltid är en numrerad
   lista. Innehållsförteckningens nummer läses innan förteckningen tas bort (beslut 5).
5. **Innehållsförteckningen tas bort** innan avsnitten byggs. Den upprepar rubrikerna och skulle
   annars hittas av sökningar. Sidnummer och rader som återkommer på minst 30 % av sidorna tas
   också bort. Ett sidhuvud eller en sidfot enligt layoutmodellen tas bort bara om den står på
   mer än en sida eller innehåller ett sidnummer. Modellen kallar ibland första raden på en sida
   för sidhuvud fast den är avtalstext, och den texten får inte försvinna.
6. **Frågor och svar delas per fråga** (`12 Publik fråga` eller `Publik fråga 12`, `Privat fråga`,
   `Publikt informationsmeddelande`), aldrig vid nummer. Frågorna citerar upphandlingens rubriker
   (`5.6.3.1 Kvalitetsledningssystem`), som annars skulle bli avsnitt.
   **Dokument utan numrering** delas vid Doclings rubriker utan nummer. Ett dokument utan rubriker
   blir ett enda avsnitt. I Word läggs omärkta delar på högsta nivån till efter de numrerade
   avsnitten (t.ex. "Instruktion till Personuppgiftsbiträdesavtalet" efter "16 Tvistelösning").
7. **Föräldra–barn.** Avsnittet är det agenten läser och citerar. Ett avsnitt längre än 1 500 tecken
   delas i bitar mellan stycken (och mellan meningar i mycket långa stycken). Bitarna är det som
   söks, och varje bit pekar på sitt avsnitt.
8. **Kontextrubriken byggs utan språkmodell:** `ramavtalsområde (diarienummer) › dokument ›
   rubrikstig`. Området kommer från registret via sidans diarienummer. Dokumentnamnet är länktexten,
   och ett leverantörsavtal får avtalsnummer och leverantör, t.ex. `Ramavtal 23.3-2940-20:010
   (Leverantör AB)`.
9. **Avsnitten lagras per fil** (`parsed_file`, `document_section`, `section_chunk`), nycklade på
   filens hash. Steg 3 ersätter alla avsnitt i en transaktion, så tabellerna alltid motsvarar en hel
   körning.

## Konsekvenser

- Samma fil ger alltid samma avsnitt och samma kontextrubriker, så resultatet kan granskas och
  testas. Kommandot `outline` skriver ut innehållsförteckningen för en fil.
- PyTorch för CPU tar ungefär 1 GB. Modellerna laddas ner från Hugging Face första gången. CI
  sparar dem i en cache och kräver att PDF-testerna körs. Miljön måste nå `download.pytorch.org`
  (och `download-r2.pytorch.org`, där paketen ligger) och Hugging Face filservrar (`*.hf.co`).
- Text i skannade sidor och i bilder saknas tills OCR finns.
- Numrerade rubriker med nummer som citeras från ett annat dokument (t.ex. kravnummer i en
  redovisningsmall) blir avsnitt om de följer ordningen och annars en del av texten.
- Bitarnas storlek (1 500 tecken) är en startpunkt. Den mäts på den svenska testsamlingen i M5.

## Alternativ som valts bort

- **Docling utan PyTorch (`docling-slim` med layoutmodellen i ONNX Runtime).** Prövades först,
  eftersom det sparar ungefär 1 GB. Det fungerar inte i Docling 2.133: PDF-flödet importerar
  PyTorch ändå (se Kontext).
- **Bara textlagret med pypdfium2.** Snabbt och utan modell, men utan läsordning, sidhuvuden,
  tabellceller och rubriker utan nummer. Simon valde Docling. pypdfium2 används bara för
  kontrollen av textlagret och för tabeller utan celler.
- **Rubriker med reguljära uttryck rad för rad.** Testat på textlagret: numrerade listor,
  innehållsförteckningar och omstarter gav falska avsnitt och tappade riktiga. Kedjan i beslut 4
  stämmer med dokumentens egen innehållsförteckning i 76 av 80 PDF:er som har en.
- **En språkmodell som hittar strukturen.** Kostar pengar per dokument, ger inte samma svar varje
  gång och numren skulle ändå behöva kontrolleras mot texten.
- **Bitar med fast längd över hela dokumentet.** Planen kräver uppdelning per numrerat avsnitt,
  eftersom avtal citeras per punkt.
