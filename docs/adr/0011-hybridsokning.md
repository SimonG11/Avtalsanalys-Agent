# ADR 0011: Hybridsökning med exakt vektorsökning, BM25 och rangfusion

**Status:** Föreslaget (PR #8). Ersätter sökdelen av [ADR 0004](0004-postgres-som-enda-databas.md)
(Postgres fulltext med `swedish` och vägen till ParadeDB). Allt annat i ADR 0004 gäller.

## Kontext

Steg 6 i inläsningen ska göra avsnitten sökbara, och agentens verktyg `sok_dokument` ska hitta de
avsnitt som svarar på en fråga. Klart när: sökningen fungerar på urvalet och är mätt på en svensk
testsamling, så att valet av modell och metod vilar på siffror (plan M5, validering punkt 4 och 5).

Det som styr besluten:

- **Frågorna och avtalen använder olika ord.** "Hur länge måste en konsult stanna?" ska hitta
  "Avrop får inte avslutas i förtid utan…". Det kräver embeddings.
- **Vissa ord är sällsynta och exakta:** "uppsägningstid", "Visby", ett avtalsnummer, ett
  produktnamn. Embeddings suddar ut dem, ordsökning hittar dem.
- **Postgres fulltext är inte BM25.** `ts_rank` har ingen IDF, så "ska" och "enligt" väger lika
  mycket som "uppsägningstid". Med `plainto_tsquery` (alla ord måste finnas) gav 13 av 30 naturliga
  frågor ingen träff alls.
- **Samma text står i många dokument.** Allmänna villkor trycks om i upphandlingsdokument och
  avropsstöd. Av de 10 773 avsnitt som indexeras har 6 365 minst en kopia, i 1 333 grupper. I 464
  av grupperna har kopiorna olika nummer, som "Prismodeller", som är 6.17 i ett villkor, 2.16 i
  ett annat och 7.16 i ett upphandlingsdokument. Utan gruppering fyller kopiorna träfflistan.
- **Urvalet är litet:** 13 178 bitar (chunks) som karantänen släpper igenom, från 207 filer. Det
  fullständiga registret blir några gånger större.
- **Mätningen ska kunna upprepas utan databas**, och den ska mäta exakt det som körs i produktion.
- **Demon körs på en MacBook** (M1/M2) om några dagar. En lokal embeddingmodell tar timmar på en
  processor, en API-modell under en minut för urvalet.

## Beslut

1. **Det som indexeras är varje bit som karantänen släpper igenom** (steg 5), med kontextrubriken
   först: "ramavtal › dokument › rubrikstig", en tom rad och bitens text (contextual retrieval, fas
   3). Samma text bäddas in och analyseras för BM25. En bit i karantän får ingen rad i indexet.
2. **Embeddings med OpenAI `text-embedding-3-large`**, anropad med `openai`-klienten direkt, med
   `dimensions=1536` (beslut 9). Frågor och dokument bäddas in med samma anrop. Vektorerna
   normaliseras till längd 1. Embeddings sparas i `embedding_cache` med nyckeln (modell och
   dimension, textens sha256), som överlever en ombyggnad av indexet. Steg 6 bäddar bara in texter
   som saknas och sparar cachen efter varje omgång, så en avbruten körning behåller det den gjort.
3. **Vektorgrenen är en exakt sökning**, utan HNSW- eller IVFFlat-index: `ORDER BY embedding <#>
   :fråga`, pgvectors negativa inre produkt, som för vektorer med längd 1 är minus cosinuslikheten.
   13 000 vektorer är millisekunder att gå igenom, och en exakt sökning ger samma rangordning som
   mätningen.
4. **Ordgrenen är BM25, beräknad i Python och lagrad som pgvector `sparsevec`.**
   - `retrieval/swedish_text.py` delar texten i ord och behåller diarie-, avtals- och
     organisationsnummer, datum och punktnummer hela (`23.3-2649-2022-003`, `6.21.9`). Stoppord är
     PostgreSQL 17:s svenska lista plus "ska" och "skall". Övriga ord stammas med Snowballs svenska
     stemmer (`snowballstemmer`, version låst under 4).
   - `retrieval/bm25.py` ger varje bit en gles vektor med BM25-vikter (k1 = 1,2, b = 0,75, IDF som
     Lucene: ln(1 + (N − df + 0,5) / (df + 0,5))). Varje stam har ett id i `search_term`.
   - En fråga blir en gles vektor med 1 för varje stam den har. Då är `term_weights <#> :fråga`
     minus bitens BM25-poäng, och Postgres rangordnar med samma operator som i vektorgrenen.
   - Analysens namn och version (`ANALYSER`) sparas med indexet, så en annan stemmer kräver en
     ombyggnad.
5. **Grenarna slås ihop med Reciprocal Rank Fusion** (k = 60, lika vikt) i Python. Varje gren ger
   sina 100 bästa bitar. Före fusionen blir varje grens bitar en rangordning av avsnittsgrupper, i
   den ordning gruppens bästa bit kom. Poängen är summan av 1 / (k + rang) över grenarna. Lika
   poäng avgörs av bitens nyckel, så samma fråga ger alltid samma ordning.
6. **Ett avsnitt med samma text i flera dokument är en träff.** Gruppen är sha256 av avsnittets
   text utan sitt inledande nummer och med blanktecken ihopslagna, så "6.17 Prismodeller" och
   "2.16 Prismodeller" med samma text hamnar i samma grupp. Rubrikens titel är kvar i texten, så två
   avsnitt som bara säger "Se bilaga Priser" under olika rubriker hålls isär. Träffen visas med
   den bästa bitens avsnitt, och de andra kopiorna inom filtret listas, så agenten kan citera rätt
   avtals dokument.
7. **Filter före rangordning.** Varje fil har ett omfång (`document_scope`): ramavtalsområden,
   diarienummer, avtalsnummer och avtalssidornas titlar, från avtalssidorna som länkar till filen
   och från registret, och dokumenttypen från steg 4. Numren sparas som registret skriver dem. Ett
   filter på ramavtalsområde, diarienummer eller dokumenttyp behåller filerna med det i sitt
   omfång. Ett filter på avtalsnummer behåller
   avtalets egna filer (leverantörskortet) och upphandlingens filer som inte hör till någon
   leverantör (allmänna villkor, krav), men inte andra leverantörers kort.
8. **Sökningen stänger vid fel.** Bitarnas rader i indexet och filernas omfång tas bort med sin
   bit eller fil (`ON DELETE CASCADE`), och kommandot `process` tömmer resten av indexet i samma
   transaktion som det sparar nya avsnitt. `process` och `index` tar samma lås i databasen innan
   de skriver, så de väntar på varandra, och `index` sparar inget om avsnitten, karantänen eller
   avtalssidorna har ändrats medan det bäddade in. Indexet har en rad i `index_build` med embeddingmodell och analys. Sökningen ger
   `IndexNotReadyError` om indexet saknas eller byggdes med en annan modell, dimension eller
   analys än sökningens, i stället för att jämföra vektorer som inte hör ihop.
9. **Valen av modell, dimension och metod görs med mätning** på testsamlingen
   (`evals/datasets/gold_sv.jsonl`, 30 svenska frågor, 22 av dem sökfrågor). Mätningen bygger
   samma index i minnet (`evals/offline.py`) med samma funktioner som steg 6 och sökningen. Ett
   integrationstest i CI kontrollerar att SQL-sökningen och mätningens sökning ger exakt samma
   bitar i samma ordning. Mätningen körs med `python -m evals.run_retrieval_eval`.
10. **Ingen omrankning i MVP:n.** En omrankare (cross-encoder) på processorn tar 25–80 sekunder per
    fråga här. Den kan läggas till efter fusionen och mätas på samma testsamling.

## Mätningen

22 sökfrågor med filter, 13 178 bitar, embeddings med API:ets egna dimensioner. Träff@k är
andelen av frågornas källor som finns bland de k första avsnittsgrupperna; "alla@10" är andelen
frågor där alla källor finns bland de tio första. Hela tabellen och metoden står i
[M5](../steg/05-sokning.md).

| Sökning | träff@1 | träff@5 | träff@10 | träff@20 | MRR@10 | nDCG@10 | alla@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| BM25 | 0,30 | 0,62 | 0,73 | 0,85 | 0,57 | 0,56 | 0,59 |
| Vektor, 1 536 dim | 0,42 | 0,81 | 0,81 | 0,89 | 0,65 | 0,65 | 0,68 |
| **Hybrid, 1 536 dim** | **0,52** | **0,74** | **0,86** | **0,93** | **0,81** | **0,73** | **0,77** |
| Hybrid, 1 024 dim | 0,43 | 0,76 | 0,88 | 0,93 | 0,73 | 0,70 | 0,82 |
| Hybrid, 3 072 dim | 0,49 | 0,76 | 0,83 | 0,93 | 0,78 | 0,71 | 0,73 |

- **Hybrid slår båda grenarna var för sig.** Mot BM25 är skillnaden säker (nDCG@10 +0,18, 95 %
  intervall +0,07 till +0,29 med parad bootstrap). Mot vektorsökningen är den +0,08 (−0,02 till
  +0,18): trolig men inte säker med 22 frågor.
- **1 536 dimensioner.** Med 1 536 kommer det första rätta avsnittet högre upp än med 1 024
  (MRR@10 0,81 mot 0,73, intervall för skillnaden +0,01 till +0,16), vilket agenten har mest nytta
  av eftersom den läser de första träffarna. 1 024 hittar en källa till bland de tio första. 3 072
  är inte bättre och tar dubbelt så mycket plats. 1 536 ryms också under pgvectors gräns på 2 000
  dimensioner för HNSW.
- **k i fusionen spelar liten roll:** 30, 60 och 100 ger samma resultat och k = 10 en fråga till
  bland de tio första. Vi behåller standardvärdet 60.
- **Filtren betyder mycket:** utan dem sjunker hybridens träff@10 från 0,86 till 0,82 och nDCG@10
  från 0,73 till 0,67.

## Konsekvenser

- En databas och inga nya tjänster. BM25 och embeddings ligger i samma tabell och rangordnas med
  samma operator, och tabellen töms med avsnitten.
- Sökningen och mätningen är samma kod, så ett mätresultat gäller produktionen.
- BM25 i Python kräver att hela indexet byggs om när texten ändras, eftersom IDF och
  medellängden beror på alla bitar. För urvalet tar det några sekunder plus embeddings för nya
  texter. Vid tiotusentals dokument kan vikterna i stället räknas ut stegvis.
- Exakt sökning blir långsam vid miljontals bitar. Då läggs ett HNSW-index på vektorkolumnen
  (pgvector tar högst 2 000 dimensioner, så 1 536 passar).
- OpenAI ser bitarnas text när de bäddas in. Agenten skickar redan frågor och avsnitt till
  OpenAI, så ingen ny part tillkommer.
- Testsamlingen är liten. Skillnader under ungefär 0,1 är inte säkra, så den behöver växa med
  fler frågor och fler ramavtal.

## Alternativ som valts bort

- **Postgres fulltext (`to_tsvector('swedish')`, `ts_rank`).** Ingen IDF och inga träffar när ett
  ord saknas, se kontexten. Vår analys utgår från Postgres svenska stopplista (plus "ska" och
  "skall") och en nyare version av Snowballs svenska stemmer, och räknar BM25.
- **ParadeDB (`pg_search`) för BM25.** Ett extra tillägg med AGPL-licens i databasen. Med vikterna
  i `sparsevec` behövs det inte vid vår storlek.
- **HNSW eller IVFFlat nu.** Ungefärlig sökning skulle missa träffar som filtren redan begränsat,
  och mätningen skulle inte längre ge exakt samma ordning som produktionen.
- **En lokal embeddingmodell (BGE-M3, Qwen3-Embedding).** Gratis per anrop men drygt två timmar
  för att bädda in urvalet på en processor. BGE-M3 mättes på samma frågor: bättre än OpenAI som
  ensam vektorsökning (MRR@10 0,72 mot 0,65), men sämre i hybriden som körs (0,75 mot 0,81), se
  [M5](../steg/05-sokning.md). Den kan bytas in bakom `Embedder` om data inte får lämna egna
  servrar.
- **Viktad sammanslagning av poängen.** Vektorernas och BM25:s poäng har olika skalor, och
  vikterna skulle behöva ställas in på samma 22 frågor som de mäts på.
- **LangChains `PGVector`-store.** Den har ingen BM25, inga egna filter på avtal och ingen
  gruppering av kopior, och den skulle dölja SQL:en som TokenTek granskar.
