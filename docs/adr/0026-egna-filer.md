# ADR 0026: Egna filer: API:t sparar dem per samtal, agenten läser dem med två egna verktyg och citaten kontrolleras som andra

**Status:** Föreslaget.

## Kontext

Simon provkörde webbappen 2026-10-07 och ville kunna "ladda upp egna filer i chatten och få
agenten att jämföra den mot andra avtal". En avropare har ofta ett eget avropsavtal, ett utkast
eller en leverantörs villkor och vill veta var de avviker från ramavtalet. Webbappskontraktets
punkter 33-38 föreslår formen: `POST /api/uploads` med filen och AG-UI-trådens id, två verktyg
för agenten och en ny sorts källa i svaret.

Det som styr besluten:

- **avtal-mcp är skrivskyddat över den gemensamma korpusen** (ADR 0003, 0012). Allt den visar är
  Statens inköpscentrals publicerade dokument, samma för alla användare. En användares fil får
  aldrig hamna där, och ingen annan användare får se den.
- **Ett samtal är en AG-UI-tråd.** API:t har ingen inloggning (ADR 0014); tråd-id:t är det enda
  som skiljer en användares samtal från en annans.
- **Filen kommer från en webbläsare** och kan vara vad som helst: för stor, en zip-bomb, en PDF
  utan text, en fil med falsk ändelse, eller text skriven för att styra agenten.
- **Svaren ska gå att lita på på samma sätt som förut:** varje citat kontrolleras mot texten det
  anger, och en granskare prövar svaret (ADR 0013, 0015).
- **Mätningen och demot får inte ändras** för ett samtal utan filer: samma systemprompt, samma
  verktyg och samma svarsform, bara valfria fält till. Demot ska fortsätta starta med
  `docker compose up` utan att inläsningen körs om.

## Beslut

1. **API:t äger filerna.** `POST /api/uploads` tar emot filen, läser den och sparar den i ett eget
   lager (`uploads/store.py`): i Postgres i compose (tabellerna `upload` och `upload_section`, som
   API:t skapar själv när det startar, som checkpointtabellerna), i minnet annars. avtal-mcp ser
   aldrig filerna; agenten läser dem genom samma lager som API:t skriver i.
2. **En fil hör till sin tråd.** Varje läsning tar trådens id, och en fil i en annan tråd beter sig
   som en fil som inte finns (404 i API:t, "finns inte" för agenten). Agentens tråd kommer alltid
   ur körningens config (`thread_id`, som ag-ui-langgraph och kommandoraden sätter), aldrig ur
   modellens argument. Ett upload_id eller en hash som modellen kopierar från ett annat samtal
   hittas inte.
3. **Gränser mot fientliga filer**: högst 10 MB (räknat medan kroppen kommer, också utan
   Content-Length), filtypen ur innehållet och ändelsen som måste stämma, en Word-fils arkiv högst
   100 MB uppackat, 5 000 delar och 16 MB per XML-del innan det öppnas, där varje del packas upp en
   megabyte i taget och inte får bli större än arkivet säger, högst 50 000 stycken i en Word-fil,
   högst 300 PDF-sidor, högst 1,5 miljoner tecken (läsningen slutar vid sidan eller stycket där
   texten passerar gränsen), högst fem filer per samtal och högst `UPLOAD_MAX_TOTAL_BYTES` (2 GB)
   för alla samtal tillsammans, eftersom klienten väljer tråd-id:t. Allt arbete går inte att
   begränsa innan det görs: pdfium bygger en sidas text hel, och en komprimerad sida kan ha
   miljoner tecken. Läsningen körs därför i en egen barnprocess, högst två åt gången, som dödas
   efter 60 sekunder och har högst 1 GB minne (på Linux, som i containern); när pdfium inte får
   minne avbryter det bara barnprocessen, inte API:t. Barnprocessen skriver ingen core-fil, har en
   gräns för CPU-tid som stoppar den också om API:t har dött, och dödas när API:t stoppas.
   Filnamnet rensas från sökvägar och styrtecken.
4. **Läsningen.** PDF läses ur textlagret med pypdfium2, Word med python-docx och text som UTF-8,
   utan Docling och utan OCR, så att en fil läses på någon sekund. Texten delas i avsnitt med
   inläsningens egna regler (`ingestion/step3_chunk.split_sections`), så att "punkt 6.2" i
   användarens fil och i ramavtalet är jämförbara enheter. Ett avsnitt över 12 000 tecken delas i
   delar. Inskannade sidor, OCR, bilder, Excel och gamla .doc stöds inte; en PDF utan text får ett
   tydligt fel, och sidor utan text nämns i en varning.
5. **Två verktyg i grafen, inte i avtal-mcp** (`agent/list_uploads.py`, `agent/read_upload.py`):
   `list_uploads` ger trådens filer med varje fils avsnitt, som `get_outline`; `read_upload` ger
   ett helt avsnitt med `section_position`, eller de avsnitt som bäst matchar en `query`, hela.
   Ett avsnitt har samma fältnamn som avtal-mcp:s `read_section` där de betyder samma sak
   (`sha256`, `section_position`, `section_number`, `section_title`, `page_start`, `text`) plus
   `upload_id` och filnamnet, så modellen citerar filen som den citerar ett avtal. En query
   rangordnas i Python: frågans ord mot avsnittens, med de sex första bokstäverna så att
   sammansättningar och böjningar träffar, viktade efter hur ovanliga de är i filen.
6. **Prompten.** `SYSTEM_PROMPT` ändras inte. Har tråden filer läggs ett avsnitt till efter
   prompten i varje modellanrop (`agent/upload_prompt.py`): filerna med namn, upload_id och sidor,
   hur en jämförelse görs (läs filens avsnitt, sök motsvarande villkor i ramavtalen, jämför punkt
   för punkt: lika, strängare, mildare, saknas, och citera båda) och att filens text aldrig är
   instruktioner. Har tråden inga filer tas `list_uploads` och `read_upload` bort ur anropets
   verktyg, så att prompten och verktygen är exakt desamma som utan filer.
7. **Citaten.** Modellens utkast är oförändrat (sha256 och section_position). Kontrollen läser ett
   citerat avsnitt ur trådens filer först, efter hashen, och annars genom avtal-mcp som förut
   (`agent/upload_readers.py`). Citatet jämförs med den lagrade texten, som `read_upload` gav
   modellen. Kontraktets `Citation` får `source` (`"framework"` som standard, `"upload"` för en
   fil) och `upload_id`; för en fil är `file_title` filnamnet, `page_title` null och `page` PDF:ens
   sida där avsnittet börjar (null för Word och text). `sha256` är filens hash, som kontraktet
   förutser, inte null: fältet förblir en sträng för mätningen och kommandoraden, och webbappen
   skiljer källorna på `source`. En fil har inga ändringar i avtalen, så regeln om senaste
   lydelsen frågar inte avtal-mcp om den (det skulle ge varje sådant svar en reservation), och ett
   datum eller ett nummer i filen backar samma värde i svaret, som ett citerat avsnitt ur avtalen.
   Granskaren får veta vilken källa som är användarens fil.
8. **Uppmaningar i en fil.** Filens text är data. Det sägs på tre ställen: i promptens avsnitt om
   filer, i `read_upload`:s beskrivning och i systempromptens regel 6, som redan gäller all text i
   dokument och verktygssvar. Filnamnet skrivs inom citattecken. Det som står i en fil kan få
   modellen att säga något, men inte få ett citat godkänt som inte står i en källa, och granskaren
   ser vilken källa som är användarens.
9. **Sju dagar.** En fil läses inte efter `UPLOAD_RETENTION_DAYS` (7) och tas bort när API:t
   startar, en gång i timmen och vid varje uppladdning, eller när användaren tar bort den.
10. **Kommandoraden** bifogar lokala filer med `--fil` (en gång per fil), lästa med samma regler,
    i ett lager i minnet för körningen. Det är demots reserv när webbappen inte finns, och
    röktestets väg in.

## Konsekvenser

- Användaren kan ladda upp sitt eget avtal och få det jämfört punkt för punkt med ramavtalet,
  med citat ur båda som kontrolleras på samma sätt. Ett citat ur filen som inte står i den
  underkänns, som ett ur avtalen.
- Ett samtal utan filer är oförändrat: samma prompt byte för byte och samma verktyg (testat),
  och `Citation` har bara två nya fält med standardvärden. Mätningen och kommandoraden utan `--fil`
  bygger samma graf som förut.
- **Kostnad i modellanrop:** avsnittet om filer är ungefär 300 token i varje modellanrop i ett
  samtal med filer, och `read_upload` ger upp till 24 000 tecken (omkring 6 000 token) per
  sökning. Granskaren läser filens citerade avsnitt som andra källor.
- **Kostnad i drift:** filerna ligger i Postgres som bytea, högst 10 MB och fem filer per samtal,
  i sju dagar, och högst 2 GB tillsammans. Varje modellanrop i API:t listar trådens filer (en
  indexerad fråga), och `list_uploads` läser alla en fils avsnitt. Varje uppladdning startar en
  barnprocess (millisekunder med multiprocessings forkserver på Linux; på macOS, där kommandoraden
  kan köras utanför containern, med spawn och några tiondels sekunder), och två läsningar
  samtidigt kan ta upp till 1 GB minne var.
- **Det agenten har läst ur en fil finns kvar längre än filen.** Det agenten har läst ur en fil
  (verktygssvaren och citaten) sparas i samtalets checkpoints, och i Langfuse när spårningen är på,
  och tas inte bort när filen tas bort eller blir sju dagar gammal.
- **Kända gränser:** ingen OCR och inga tabeller som tabeller (text i ordning), en Word-fil har
  inga sidor, och Words automatiska numrering finns inte i texten. Laddar användaren upp ett av
  ramavtalens egna dokument, byte för byte, har filen samma hash som dokumentet i korpusen, och i
  det samtalet läser kontrollen hashen som användarens fil. Utan inloggning skyddar bara tråd-id:t
  ett samtals filer, som det skyddar samtalet självt (ADR 0014).
- Webbappen behöver visa en källa ur en fil annorlunda (förslaget: "Din fil", som öppnar
  `/api/uploads/{upload_id}/file?thread_id=…`), och skicka filroutrarna vidare på serversidan.

## Alternativ som valts bort

- **Läsa in filerna i den gemensamma korpusen** (inläsningens steg och avtal-mcp:s sökning). Då
  skulle avtal-mcp inte längre vara skrivskyddat, en användares fil kunna synas för andra och en
  uppladdning ta minuter med Docling, embeddings och index.
- **Vektorembeddings av filerna.** En fil har högst några hundra avsnitt; agenten läser hela
  avsnitt, och ordmatchning hittar "ansvarsbegränsning" i ett avtal med tio klausuler. Embeddings
  skulle kosta ett anrop till OpenAI per uppladdning och en till lagringsform, för lite vinst.
- **En egen MCP-server för filerna.** Den skulle behöva tråden från agenten i varje anrop, som
  modellen då skulle kunna ändra, och ännu en process i compose. Verktygen i grafen får tråden ur
  körningens config, som modellen inte når, precis som `ask_user` lever i grafen (ADR 0003:s
  undantag).
- **Lägga hela filen i prompten.** Enkelt för en kort fil, men en fil kan ha 1,5 miljoner tecken,
  och ett citat måste peka på ett avsnitt som kontrollen kan läsa igen.
- **`sha256` null för en fil.** Fältet skulle bli valfritt för alla
  källor, också i mätningen och kommandoraden, utan att webbappen vinner något som `source` inte
  redan ger.
- **Docling för uppladdade filer.** Bättre tabeller och OCR, men tiotals sekunder per fil, PyTorch
  i API:ts process och en större yta för fientliga filer.
