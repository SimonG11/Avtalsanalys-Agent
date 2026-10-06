# ADR 0008: Tolkning och uppdelning av dokumenten

**Status:** Godkänt av Simon 2026-10-06 (PR #4). Rättelserna från självkontrollen samma dag
(beslut 2, 5, 6 och 7) granskas i PR #5.

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
   textlager. Får en tabell inga celler läses texten i tabellens ruta ur textlagret, en rad per
   tabellrad. Word läses av Doclings Word-läsare, som behåller rubriknivåer och rubriknummer utan
   modell. Simon valde Docling med PyTorch 2026-10-05.
   Stickprov mot textlagret visade att Docling tappar eller ändrar text på tre sätt, som rättas
   i parsern: ett bindestreck i slutet av en rad behålls (Docling gjorde `2025-08-19` till
   `202508-19` och `1-4` till `14`), text i rutor som layoutmodellen kallar bilder läses
   (TendSigns frågerutor) och tabellers fotnoter behålls.
2. **Steg 2 kontrollerar varje sida.** En sida med färre än 50 tecken där bilder täcker minst halva
   sidan räknas som skannad och markeras `needs_ocr`. Den rapporteras i varje körning och lagras i
   `parsed_file.pages_needing_ocr`. Ingen OCR-tjänst byggs nu (M3b), eftersom andelen är under 1 %.
   Det innebär att IBM:s volymavtal och större delen av Allmänna villkor för Systemutveckling
   saknar text tills OCR finns. Simon beslutade 2026-10-06 att vänta med OCR. Sidorna förblir
   markerade, och OCR kan läggas till senare som en parser bakom `DocumentParser`.
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
   Nummer som dokumentet skriver som bilder räknas fram ur innehållsförteckningens ordning, men
   bara när de passar utan lucka mellan de numrerade grannarna. Saknar den bästa kedjan nummer
   som innehållsförteckningen listar väljs den igen med så många listade nummer som möjligt,
   eftersom nummer som citeras från ett annat dokument (`4.6.1`–`4.6.6` efter `5 Tekniska krav`)
   annars kan slå ut ett riktigt avsnitt. En Word-fil får numren ur sin innehållsförteckning när
   den listar exakt samma rubriker: Word räknar fram numren den visar och gör förteckningen av
   dem, medan Docling räknar själv och i två mallar hamnar ett för lågt.
5. **Innehållsförteckningen tas bort** innan avsnitten byggs. Den upprepar rubrikerna och skulle
   annars hittas av sökningar. Sidnummer och rader som återkommer överst eller nederst på minst
   30 % av sidorna tas också bort; samma mening mitt på många sidor är avtalstext. Ett sidhuvud
   eller en sidfot enligt layoutmodellen tas bort bara om den står på mer än en sida eller
   innehåller ett sidnummer. Modellen kallar ibland första raden på en sida för sidhuvud fast den
   är avtalstext, och den texten får inte försvinna. Tre av layoutmodellens misstag rättas innan
   rubrikerna söks. En tabell utan kolumner som innehåller numrerade rader delas till text. En
   numrerad rubrik som modellen kört ihop med stycket före (`… försenas eller innehållas. 1.7
   Gällande lagar …`) får ett eget block, men bara efter en mening som slutar med ett ord på minst
   fyra bokstäver, så att `kl. 17.00` och `p. 5.15.3` står kvar. Ett nummer som står ensamt med
   titeln sist i nästa stycke (`4.` och `… för Valda program. Hårdvarukomponenter`) får tillbaka
   sin titel, och stycket hamnar i avsnittet före.
6. **Frågor och svar delas per fråga** (`12 Publik fråga` eller `Publik fråga 12`, `Privat fråga`,
   `Publikt informationsmeddelande`), aldrig vid nummer. Frågorna citerar upphandlingens rubriker
   (`5.6.3.1 Kvalitetsledningssystem`), som annars skulle bli avsnitt.
   **Dokument utan numrering** delas vid Doclings rubriker utan nummer. Docling ger inga
   rubriknivåer i en PDF, så rubriker som innehållsförteckningen listar får nivå 1 och de andra
   nivå 2. Ett dokument utan rubriker blir ett enda avsnitt.
   **Delar utan nummer efter de numrerade avsnitten** blir egna avsnitt på högsta nivån. I Word
   är det rubriker på högsta nivån (t.ex. "Instruktion till Personuppgiftsbiträdesavtalet" efter
   "16 Tvistelösning"). I en PDF är det den första rubriken utan nummer som börjar en sida, om
   dokumentet fortsätter minst en sida till (IBM:s "Del 2 - Landsspecifika villkor", Microsofts
   "Registreringsinformation"). Upprepar rubriken dokumentets titel ("IBM Användningsvillkor")
   och följs av en rubrik till på samma sida, får delen den andras namn. En rubrik på sista sidan
   är oftare en underrubrik eller en signatursida och stannar i sitt avsnitt.
   **En Word-fil vars nummer bara är listpunkter** delas vid sina Word-rubriker. I Word har
   avsnitten rubrikformat, så en sådan lista är en uppräkning (kontraktets handlingar i
   rangordning under rubriken "Kontraktets omfattning").
7. **Föräldra–barn.** Avsnittet är det agenten läser och citerar. Ett avsnitt längre än 1 500 tecken
   delas i bitar mellan stycken (och mellan meningar i mycket långa stycken). Bitarna är det som
   söks, och varje bit pekar på sitt avsnitt. Ett avsnitt som bara är en kort rubrik får ingen
   bit när dess text finns i underavsnitten. Ett numrerat avsnitt utan underavsnitt får en bit,
   eftersom rubriken då kan vara hela klausulen (`1.1 För närvarande har inga ändringar …`).
   Undantaget är avsnittet för den borttagna innehållsförteckningen (`6.1
   Innehållsförteckning`): det finns kvar för kontrollen mot förteckningen men ska inte sökas fram.
8. **Kontextrubriken byggs utan språkmodell:** `ramavtalsområde (diarienummer) › dokument ›
   rubrikstig`. Området kommer från registret via sidans diarienummer. Dokumentnamnet är länktexten,
   och ett leverantörsavtal får avtalsnummer och leverantör, t.ex. `Ramavtal 23.3-2940-20:010
   (Leverantör AB)`.
9. **Avsnitten lagras per fil** (`parsed_file`, `document_section`, `section_chunk`), nycklade på
   filens hash. Steg 3 ersätter alla avsnitt i en transaktion, så tabellerna alltid motsvarar en hel
   körning.

## Konsekvenser

- Samma fil ger alltid samma avsnitt och samma kontextrubriker, så resultatet kan granskas och
  testas. Kommandot `outline` skriver ut innehållsförteckningen för en fil, och `chunk`
  rapporterar varje fil där dokumentets egen innehållsförteckning listar ett nummer som inte
  blev ett avsnitt. Alla 104 filer med egen förteckning (78 PDF och 26 Word) klarar kontrollen.
  Kommandot `verify` jämför avsnitten i varje PDF med dess textlager: rader som inget avsnitt
  har, frågor i fråge- och svarsloggar och numrerade rader mellan två avsnitt som inte själva
  blev avsnitt. Så kontrolleras också PDF:erna utan egen förteckning, men bara på förlorad text
  och missade numrerade rubriker under en befintlig förälder. En del som hamnat i fel avsnitt
  behåller alla sina rader och syns inte där, och Word-filerna har inget textlager.
- PyTorch för CPU tar ungefär 1 GB. Modellerna laddas ner från Hugging Face första gången. CI
  sparar dem i en cache och kräver att PDF-testerna körs. Miljön måste nå `download.pytorch.org`
  (och `download-r2.pytorch.org`, där paketen ligger) och Hugging Face filservrar (`*.hf.co`).
- Text i skannade sidor och i bilder saknas tills OCR finns. Läsbar text efter skannade sidor
  hamnar i avsnittet före dem (också i texten före första rubriken), eftersom rubrikerna på de
  skannade sidorna saknas. Det gäller en fil (Allmänna villkor för Systemutveckling). M4 sätter sådana
  avsnitt i karantän.
- Doclings läsordning och tabellmodell gör ibland fel (marginaletiketter, försättsblad, en
  rubrik i en mall, tabeller med sammanslagna celler). Felen och var de finns står i
  `docs/steg/03-tolkning.md`. Uppgifter ur tabeller bör kontrolleras mot källan innan agenten
  använder dem.
- Docling ersätter typografiska citattecken och tankstreck med raka tecken (`”` blir `"`).
  Citatkontrollen (M8) behöver därför jämföra texten normaliserad på båda sidor.
- Tillägg som märker sina punkter med bokstäver (A, B, C) delas inte per punkt, eftersom bara
  siffernummer är kandidater.
- I tabellceller kan en listmarkör hamna efter sin punkt (`Tillhandahålls över internet; a.`).
  Texten finns kvar, men markören står på fel plats.
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
  stämmer med dokumentets egen innehållsförteckning i alla 104 filer som har en.
- **En språkmodell som hittar strukturen.** Kostar pengar per dokument, ger inte samma svar varje
  gång och numren skulle ändå behöva kontrolleras mot texten.
- **Bitar med fast längd över hela dokumentet.** Planen kräver uppdelning per numrerat avsnitt,
  eftersom avtal citeras per punkt.
