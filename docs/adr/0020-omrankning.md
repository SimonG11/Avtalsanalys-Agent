# ADR 0020: Omrankning: mätt med två modeller, inte inbyggd

**Status:** Godkänt (PR #25).
Avgör omrankningen i [arkitekturvalideringens](../validering.md) punkt 5 (Qwen3-Reranker som
utgångsläge) och i planen mot presentationen ("om mätningen visar att den ökar recall").
[ADR 0011](0011-hybridsokning.md) punkt 10 lämnade den utanför MVP:n och till en mätning.

## Kontext

- **Sökningen hittar de flesta källorna, men inte alla bland de tio första.** Hybriden har 84 % av
  testfrågornas källor bland de tio första avsnitten och 97 % bland de femtio första (träff@10
  0,84 och träff@50 0,97, mätt mot Postgres 2026-10-07, [steg 5](../steg/05-sokning.md)). En
  omrankare läser frågan och varje avsnitt tillsammans (en cross-encoder) och ordnar om de första
  avsnitten. Den kan lyfta en källa från plats 11–50 till de tio första.
- **Omrankningen körs i varje sökning.** Agenten söker flera gånger per fråga (0–5 sökningar i
  demots sex frågor, oftast två eller tre), så omrankarens tid läggs på varje sökning.
- **Ingen GPU.** avtal-mcp körs i Docker Compose på en bärbar dator (demot på en Mac) och i
  utvecklingsmiljön på fyra processorkärnor. En omrankare körs då med PyTorch på processorn.
- **Testsamlingen är liten:** 22 sökfrågor med facit ([ADR 0011](0011-hybridsokning.md)).

## Mätningen

Samma 22 sökfrågor och filter som i ADR 0011, med sökningens inställning i drift (1 536
dimensioner, 100 kandidater per gren, RRF med k = 60) och indexet i minnet med de nio godkända
filerna (det ger samma siffror som Postgres, på en hundradel när). För varje fråga fick
omrankaren poängsätta de 50 första avsnittsgrupperna, på gruppens bästa bit så som den indexeras
(kontextrubriken och texten, högst 1 024 token), och de första 10, 20, 30 eller 50 ordnades om
efter poängen. Två omrankare mättes: Qwen3-Reranker-0.6B, valideringens utgångsläge, och
bge-reranker-v2-m3, en flerspråkig cross-encoder i samma storlek.

| Sökning | träff@1 | träff@5 | träff@10 | MRR@10 | nDCG@10 | alla@10 |
|---|---:|---:|---:|---:|---:|---:|
| Hybriden, utan omrankning | 0,49 | 0,76 | 0,84 | 0,77 | 0,71 | 0,77 |
| Qwen3-Reranker-0.6B, de 20 första | 0,42 | 0,76 | 0,87 | 0,70 | 0,70 | 0,77 |
| Qwen3-Reranker-0.6B, de 30 första | 0,42 | 0,76 | 0,87 | 0,69 | 0,69 | 0,77 |
| bge-reranker-v2-m3, de 20 första | 0,48 | 0,77 | 0,89 | 0,70 | 0,71 | 0,82 |
| bge-reranker-v2-m3, de 30 första | 0,51 | 0,80 | 0,91 | 0,74 | 0,74 | 0,82 |

Att ordna om bara de tio första ändrar inte träff@10 men sänkte MRR med båda modellerna; att ordna
om alla 50 gav inget utöver de 30 första.

- **Qwen3-Reranker-0.6B** flyttade ned rätt avsnitt från första platsen (träff@1 0,49 till 0,42,
  MRR 0,77 till 0,69). Den tog 2,5 minuter (median av tio frågor, 138–194 s) för 50 avsnitt per
  fråga, omkring 3 sekunder per avsnitt.
- **bge-reranker-v2-m3 på de 30 första** hittade flest källor: träff@10 +0,07 och alla@10 +0,05.
  Fyra frågor fick fler källor bland de tio första och en fick färre (q26). Med bootstrap över
  frågorna är skillnaden i träff@10 +0,068, med ett 95-procentsintervall från −0,023 till +0,167,
  och i MRR −0,02 (−0,18 till +0,13). Den tog 48 sekunder (median, 38–179 s) för 50 avsnitt,
  knappt en sekund per avsnitt, alltså omkring 30 sekunder för de 30 första i varje sökning.

Mätningen kördes med PyTorch på fyra processorkärnor i utvecklingsmiljön. Tiden på Macen är inte
mätt. Mätskriptet och poängen per fråga sparades utanför repot, eftersom skriptet läser
mellanresultat från inläsningen (M4) som inte finns i repot.

## Beslut

1. **Ingen omrankning nu.** Sökningen förblir den i ADR 0011. `retrieval/` får ingen
   `reranker.py`.
2. **bge-reranker-v2-m3 på de 30 första är kandidaten** när frågan tas upp igen, inte
   Qwen3-Reranker-0.6B.

Skälen:

- **Vinsten är osäker.** bge-reranker-v2-m3 hittar fler källor i genomsnitt, men intervallet
  rymmer noll, fem frågor av 22 ändrades, och MRR och nDCG är oförändrade inom bruset.
  Valideringens utgångsläge, Qwen3-Reranker-0.6B, gjorde rangordningen sämre i toppen.
- **Kostnaden är säker.** Omkring 30 sekunder per sökning på processorn, i två eller tre sökningar
  per fråga, skulle göra ett svar (median 34 s mot Postgres) ungefär tre gånger så långsamt.
- **Agenten kompenserar redan.** Den söker igen med andra ord och filter, läser avsnitt och följer
  hänvisningar och ändringar. I mätningen av svaren (M11) var 28 av 30 svar rätt och alla 71 citat
  ordagranna, med sökningen som den är.

## Konsekvenser

- Svarstiden förblir den som M11 mätte, och avtal-mcp behöver ingen modell som tar omkring 2 GB
  minne.
- De källor som hybriden lägger på plats 11–50 hittar agenten bara genom att söka igen. Det syns
  som fler sökningar i spårningen, inte som fel svar i mätningen.
- Frågan tas upp igen när testsamlingen har vuxit (fler frågor och ramavtalsområden efter
  presentationen) eller när en GPU eller en omrankare som tjänst finns. Då mäts
  bge-reranker-v2-m3 på de 30 första först, med samma skript.

## Alternativ som valts bort

- **Qwen3-Reranker-0.6B** (valideringens utgångsläge). Sämre i toppen och tre gånger så långsam som
  bge-reranker-v2-m3.
- **bge-reranker-v2-m3 inbyggd.** En osäker vinst mot en säker kostnad i varje sökning.
- **Qwen3-Reranker-4B.** Inte mätt: sju gånger fler parametrar än 0.6B, som redan tog 2,5 minuter
  per fråga på processorn.
- **Cohere Rerank 4** (valideringens jämförelse, en tjänst). Inte mätt: kräver ett konto och en
  nyckel, och frågorna skickas till ännu en leverantör. Den tar bort tiden på processorn och är
  det naturliga nästa försöket om en omrankare ska in.
- **Att ordna om bara de tio första.** Ändrar inte vilka källor agenten ser bland de tio och sänkte
  MRR med båda modellerna.
