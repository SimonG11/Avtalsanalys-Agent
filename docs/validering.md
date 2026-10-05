# Fas 3 – Validering av arkitekturen

*Status: godkänd av Simon 2026-10-05. Ändringarna är införda i [arkitektur.md](arkitektur.md). Underlag: källor publicerade fram till oktober 2026, länkade i varje avsnitt.*

Syftet är att visa att varje teknikval i [arkitektur.md](arkitektur.md) är **aktuellt, etablerat och motiverat** jämfört med alternativen. Varje beslut har ett av tre utfall:

- ✅ **Bekräftat**: valet står sig.
- 🔄 **Ändrat**: ett bättre alternativ finns och planen är uppdaterad.
- ➕ **Tillagt**: en ny etablerad metod som planen saknade.

---

## Sammanfattning

| # | Område | Utfall | Beslut |
|---|---|---|---|
| 1 | Agentramverk | ✅ + 🔄 | LangGraph 1.2 som yttre graf. **Agentnoden byggs med LangChain v1 `create_agent` + middleware** i stället för en egen loop. |
| 2 | Språkmodeller | 🔄 | **OpenAI** (Simons val): `gpt-6.1-sol` som agent, `gpt-6-astra` som granskare, `gpt-6-luna` för extraktion. |
| 3 | PDF-tolkning | ✅ | Docling för textlager. OCR-modell väljs genom mätning: Unlimited-OCR, PaddleOCR-VL-1.5, GLM-OCR. |
| 4 | Embeddings | 🔄 | Qwen3-Embedding som utgångsläge. Väljs slutgiltigt på den svenska testsamlingen. |
| 5 | Omrankning | 🔄 | Qwen3-Reranker som utgångsläge, Cohere Rerank 4 som jämförelse. |
| 6 | Kontextuella avsnitt | ➕ | Varje avsnitt får en kontextrubrik (ramavtal, dokumenttyp, rubrikstig) innan det indexeras. |
| 7 | Navigering i dokument | ➕ | Nytt verktyg `visa_innehall` så att agenten kan bläddra i strukturen som en jurist och inte bara söka. |
| 8 | Lagring | ✅ | PostgreSQL + pgvector. ParadeDB (riktig BM25) som uppgraderingsväg om mätningen visar behov. |
| 9 | Spårning | ✅ | Langfuse (MIT, självhostbart, OpenTelemetry). |
| 10 | Utvärdering | 🔄 | DeepEval (pytest, körs i CI, mäter verktygsanrop och agentens väg) + egna deterministiska mått. |
| 11 | Gränssnittsprotokoll | ➕ | **AG-UI** mellan agent och webbapp i stället för egen SSE. Webbapp i Next.js med CopilotKit. |
| 12 | Verktygslager | ➕ | **Alla dataverktyg ligger i en MCP-server** som agenten använder som MCP-klient (Simons beslut 2026-10-05). |
| 13 | Mönster | ✅ | Agentloop + extern validering är etablerat (jfr corrective RAG och self-RAG). GraphRAG behövs inte eftersom hänvisningsgrafen redan är explicit. |

---

## 1. Agentramverk

**Frågan:** Är LangGraph fortfarande rätt val i oktober 2026, och i så fall hur ska det användas?

**Läget:**
- LangGraph är på version **1.2.12** (21 sep 2026). 1.0 kom i oktober 2025 med hållbart tillstånd (checkpoints), och 1.2 (maj 2026) lade till timeout per nod, felhanterare, kontrollerad nedstängning och typad strömning. [PyPI](https://pypi.org/project/langgraph/), [genomgång av 1.0–1.2](https://www.jbinternational.co.uk/article/view/4680)
- LangChain beskriver själva tre nivåer (augusti 2026): **Deep Agents** ("maximal frihet", färdigt harness med filsystem och delagenter), **LangChain `create_agent`** (byggblock med middleware) och **LangGraph** för när "agenten inte passar en standardloop" eller när man **blandar deterministiska och agentiska steg**. [LangChain](https://www.langchain.com/blog/deep-agents-vs-langchain-vs-langgraph)

**Beslut:** ✅ LangGraph som yttre graf, eftersom vi just blandar deterministiska steg (inskydd, validering, reservationssvar) med en fri agent. 🔄 Agentnoden byggs med `create_agent` och dess middleware (begränsning av modell- och verktygsanrop, human-in-the-loop, sammanfattning av lång kontext) i stället för en handskriven loop. Det blir mindre egen kod att granska och följer ramverkets rekommenderade väg.

**Alternativ som valts bort:**

| Alternativ | Varför inte här |
|---|---|
| Deep Agents | Byggt för maximal frihet med filsystem och långa uppgifter. Vi behöver hårda valideringssteg som agenten inte kan gå förbi. |
| Pydantic AI | Mycket bra typning, men svagare på orkestrering av flera steg och delagenter. [Jämförelse](https://www.morphllm.com/ai-agent-framework) |
| OpenAI Agents SDK | Bygger på överlämningar mellan agenter i en kedja, vilket passar sämre för en valideringsloop. |
| Claude Agent SDK | Claude Codes harness med bash- och filverktyg. Vi vill ha en sluten uppsättning läsande verktyg. |
| Microsoft Agent Framework 1.0 | Stark på human-in-the-loop, men tyngdpunkten ligger i .NET/Azure. |
| Google ADK | Starkt beroende av Google Cloud. |

---

## 2. Språkmodeller

**Beslut (Simon, 2026-10-05):** OpenAI används som modellleverantör. Modellerna kommer från OpenAI:s egen modellsida (oktober 2026). [OpenAI-modeller](https://developers.openai.com/api/docs/models)

| Roll | Modell | Pris in/ut per 1M tokens | Motivering |
|---|---|---|---|
| Agent | `gpt-6.1-sol` | $2 / $10 | "Nära Astras prestanda till lägre kostnad". Agenten gör många anrop per fråga, så priset spelar roll. 1,05M tokens kontext. |
| Granskare | `gpt-6-astra` | $10 / $50 | Starkaste modellen och **en annan modell än agenten**, vilket ger en mer oberoende granskning. Ett anrop per svar. |
| Extraktion och inskydd | `gpt-6-luna` | $0,1 / $0,5 | Stora volymer vid inläsning, enkla uppgifter. |

**Att ta hänsyn till i implementeringen:**
- Strukturerad output med JSON-schema för svaret och strikta funktionsscheman för verktygen.
- Promptcache: cachad indata kostar en bråkdel. Systemprompt och verktygslista hålls därför stabila först i anropet.
- Modellnamnen ligger i konfigurationen och anropas via `langchain-openai`, så att LangGraph-grafen inte är bunden till en leverantör.
- `gpt-6-astra` som agent prövas i utvärderingen. Blir kvaliteten tydligt bättre kan den motivera sitt högre pris.

## 3. PDF-tolkning

**Läget:** OmniDocBench-topplistan (maj 2026) leds av GLM-OCR (94,62) och PaddleOCR-VL-1.5 (94,50). MinerU 2.5 ligger på 90,67. Unlimited-OCR (juni 2026) är för ny för att finnas med. Docling finns inte med eftersom det är en pipeline och inte en enskild modell. [OmniDocBench-topplista](https://www.codesota.com/ocr/benchmark/omnidocbench)

**Beslut:** ✅ Docling läser textlagret (exakt text, ingen GPU). För skannade sidor jämförs **Unlimited-OCR, PaddleOCR-VL-1.5 och GLM-OCR** på ett urval avropa.se-PDF:er, med Excel-avstämningen och teckenfel mot textlagret som mått. Valet görs på våra egna siffror, inte på en generell topplista.

---

## 4. Embeddings (svenska)

**Läget:** Scandinavian Embedding Benchmark har flyttats in i MTEB som en skandinavisk topplista. [SEB](https://github.com/KennethEnevoldsen/Scandinavian-Embedding-Benchmark/blob/main/docs/index.md) Bland de flerspråkiga modellerna 2026 hör Qwen3-Embedding (Apache 2.0), BGE-M3, Gemini Embedding, Jina v4 och Cohere Embed v4 till de starka. [Milvus-jämförelse](https://milvus.io/blog/choose-embedding-model-rag-2026.md)

**Beslut:** 🔄 Qwen3-Embedding (0,6B, körs utan GPU) som utgångsläge, med BGE-M3 och OpenAI `text-embedding-3-large` (samma API-nyckel, men en modell från 2024) som jämförelser. Slutvalet görs på recall@k i den svenska testsamlingen. Jag hittade inga publicerade svenska siffror för just juridisk text, så egen mätning är nödvändig.

## 5. Omrankning

**Läget:** Etablerade flerspråkiga omrankare 2026 är Cohere Rerank 4 (API), Qwen3-Reranker (Apache 2.0, över 100 språk), BGE-reranker-v2-m3 och mxbai-rerank-v2. [Jämförelse](https://futureagi.com/blog/best-rerankers-for-rag-2026/)

**Beslut:** 🔄 Qwen3-Reranker som utgångsläge (öppen licens, självhostad), med Cohere Rerank 4 som jämförelse.

## 6. Kontextuella avsnitt ➕

Ett avsnitt som "14.2 Leverantören får säga upp…" förlorar sin mening när det lyfts ur sitt dokument. Etablerad metod (*contextual retrieval*): lägg till en kort kontextrubrik innan avsnittet embeddas och indexeras, till exempel *"IT-drift 2023, Större (23.3-10639-2023) › Allmänna villkor › 14 Uppsägning"*. Vi får rubriken gratis ur strukturen och behöver därför ingen LLM för det.

## 7. Navigering i dokument ➕

**Läget:** En jämförelse från 2026 visar att agenter som läser hela filer slår vektorsökning i korrekthet på små dokumentmängder, medan vektorsökning vinner i hastighet och skalar bättre. [LlamaIndex](https://www.llamaindex.ai/blog/did-filesystem-tools-kill-vector-search)

**Beslut:** ➕ Vi kombinerar båda. Nytt verktyg **`visa_innehall`** visar ett dokuments innehållsförteckning (numrerade avsnitt och rubriker). Agenten kan då välja mellan att söka brett och att navigera som en jurist ("öppna Allmänna villkor, kapitel Uppsägning"). Ett ramavtalspaket är litet nog för det, och vilket sätt som används bestämmer agenten själv.

## 8. Lagring

**Läget:** pgvector räcker upp till ungefär tiotals miljoner vektorer. Vi räknar med storleksordningen hundratusen avsnitt. Postgres inbyggda fulltextsökning har svensk ordstamning men är inte riktig BM25. ParadeDB lägger till BM25 i Postgres. [Jämförelse](https://www.web3aiblog.com/blog/postgres-vector-search-compared-pgvector-pgvectorscale-paradedb-lantern-2026), [Qdrants motargument](https://qdrant.tech/blog/pgvector-tradeoffs/)

**Beslut:** ✅ Postgres + pgvector + svensk fulltext, sammanslaget med Reciprocal Rank Fusion. ParadeDB blir uppgraderingsväg om utvärderingen visar att nyckelordsdelen är svag. Licensen (AGPL) behöver kontrolleras först.

## 9. Spårning

**Läget:** Langfuse är MIT-licensierat och självhostbart och stöder OpenTelemetry. Det köptes av ClickHouse i januari 2026 med löfte om oförändrad licens. LangSmith är stängt, och självhosting kräver Enterprise. [Jämförelse](https://www.truefoundry.com/blog/langfuse-vs-langsmith)

**Beslut:** ✅ Langfuse. Lokalt används Langfuse Cloud (EU) för att slippa drifta ClickHouse. Det går bra eftersom avtalen är offentliga.

## 10. Utvärdering

**Läget:** DeepEval är bäst för CI (pytest) och mäter agentens väg och verktygsanrop. Ragas är specialiserat på RAG-mått. [Jämförelse](https://www.cognee.ai/llm-agent-evaluation-tools)

**Beslut:** 🔄 DeepEval i CI. Kärnmåtten (recall@k, källprecision, korrekt "vet ej", antal verktygsanrop, kostnad) räknas med egen enkel Python-kod så att varje mått kan förklaras rad för rad.

## 11. AG-UI ➕

**Läget:** AG-UI är ett öppet protokoll för agent ↔ användare. Det strömmar verktygsanrop, tillståndsändringar och human-in-the-loop-pauser och har förstapartsstöd i LangGraph. Det kompletterar MCP (agent ↔ verktyg) och A2A (agent ↔ agent). [AG-UI](https://docs.ag-ui.com/introduction)

**Beslut:** ➕ AG-UI ersätter den egna SSE-lösningen. Webbappen byggs i Next.js med CopilotKit, så att agentens steg, källor och frågor till användaren visas utan specialkod.

## 12. MCP som verktygslager ➕

**Läget:** MCP är standardprotokollet för agent ↔ verktyg och data, med stöd i alla stora ramverk. LangChain/LangGraph ansluter till MCP-servrar via `langchain-mcp-adapters`. [AG-UI om protokollagren](https://docs.ag-ui.com/introduction)

**Beslut (Simon, 2026-10-05):** ➕ Alla dataverktyg ligger i en egen MCP-server (`avtal-mcp`) och agenten är MCP-klient. Det ger en tydlig säkerhetsgräns (endast läsbehörighet), verktyg som kan testas utan LLM och återanvändning för andra klienter. Kostnaden är ett extra nätverkssteg per anrop, vilket är försumbart jämfört med LLM-anropen. `fraga_anvandaren` stannar i grafen eftersom den styr flödet och inte hämtar data.

## 13. Agentmönster

- **Agentloop + extern validering med återkoppling** motsvarar det etablerade *corrective RAG*/*self-RAG*-mönstret, men med deterministiska kontroller i stället för att modellen bedömer sig själv. ✅
- **Parallella delagenter** via LangGraph `Send` för jämförelser över många avtal. ✅
- **GraphRAG** väljs bort. Det bygger en kunskapsgraf med LLM, men vår graf finns redan explicit i dokumenten (hänvisningar och ändringar) och extraheras billigare och mer exakt med regex och struktur.
- **Inläsningen är fortfarande ett workflow.** Ingen källa motiverar en agent där. ✅

---

## Risker som återstår

| Risk | Hantering |
|---|---|
| Svenska embeddings presterar sämre på juridisk text | Mäts tidigt. Fulltextsökningen och navigeringsverktyget kompenserar. |
| OCR-modeller kräver GPU | Isolerat i en egen tjänst som bara används för skannade sidor. |
| Kostnad per fråga | Sol som agent, promptcache och gränser för antal anrop. Kostnaden mäts per fråga i utvärderingen. |
| Avropa.se ändrar URL-struktur | Hämtningen är egen modul med tester. Filhash gör omkörning billig. |
