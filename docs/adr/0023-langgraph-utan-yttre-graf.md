# ADR 0023: LangGraph och `create_agent` också utan den yttre grafen

**Status:** Föreslaget.
Kompletterar [ADR 0002](0002-langgraph-och-create-agent.md). Den yttre grafen, ADR 0002:s lösning
på det första av dess tre krav, ersattes av middleware i en enda `create_agent`-graf i
[ADR 0013](0013-agenten.md). ADR 0002:s övriga skäl gäller; den här ADR:en lägger till det som
bygget visade.

## Kontext

ADR 0002 ställde tre krav på ramverket: det ska klara både deterministiska steg (inskydd,
validering, svar med reservation) och en fri agentloop, kunna pausa för en fråga till användaren
och spara tillståndet så att en körning överlever en omstart. För det första kravet valde ADR 0002
en yttre LangGraph-graf (inskydd → agent → svarsutkast → validering → svar) runt en agentnod från
`create_agent`. ADR 0013 ersatte den yttre grafen: stegen runt loopen är krokar i samma graf som
loopen, och "Resten av ADR 0002 gäller". Kraven står alltså kvar, och de två senare uppfylls med
checkpoints som ADR 0002 skrev. Det som saknas är vad bygget visade om ramverket, nu när den yttre
grafen är borta.

Arkitekturvalideringen i fas 3 ([validering.md](../validering.md), avsnitt 1) jämförde LangGraph
med sex andra ramverk. Agno fanns inte med.

## Beslut

LangGraph och LangChains `create_agent` är fortfarande ramverket. Punkt 1 uppfyller ADR 0002:s
krav på paus och omstart så som ADR 0002 skrev. Punkt 2–4 är det som bygget visade: hur det
första kravet uppfylls utan den yttre grafen (valideringen och svaret med reservation; inskyddet
byggdes inte, se [arkitekturplanen](../arkitektur.md)), och två integrationer.

1. **Checkpointern med interrupt och återupptagning bär `ask_user`.** Verktyget anropar LangGraphs
   `interrupt()`, som sparar körningen i checkpointern och avslutar den, och användarens svar
   kommer tillbaka med `Command(resume=…)` (`agent/ask_user.py`). API:t sparar checkpoints i
   Postgres, och en körning som väntar på användaren går att återuppta också efter en omstart
   (`tests/integration/test_agent_checkpointer.py`).
2. **`after_agent` med `jump_to` lägger kontrollen i samma graf som loopen.** `AnswerCheck`
   kontrollerar utkastet när loopen slutar och skickar ett underkänt utkast tillbaka till modellen
   med `jump_to: "model"` (`agent/middleware.py`). Agenten kan inte gå förbi kontrollen, de nya
   försöken räknas mot samma gräns för modellanrop, och eftersom allt är en graf ser webbappen
   agentens steg också medan körningen väntar på användaren. Det är provat: ADR 0013 byggde både en
   yttre graf och middleware och körde dem genom AG-UI, och bara med middleware innehöll
   `MESSAGES_SNAPSHOT` agentens steg vid en `ask_user`-interrupt.
3. **ag-ui-langgraph** gör grafens händelser till AG-UI:s, som webbappen läser
   ([ADR 0010](0010-webbapp-copilotkit-ag-ui.md)). API:t är bibliotekets `LangGraphAgent` med sex
   ändrade standardval (`api/agui.py`), inte ett eget protokoll.
4. **langchain-mcp-adapters** gör avtal-mcp:s verktyg till LangChain-verktyg (`load_mcp_tools`)
   och ett felsvar från servern till ett verktygssvar med status `error`, som modellen läser och kan
   rätta sitt anrop efter (`agent/mcp_tools.py`).

Punkt 3 och 4 sparar egen kod, men de skiljer inte i sig LangGraph från Agno, som enligt
granskningen också har stöd för AG-UI och MCP (Alternativ nedan). Om Agno klarar det som punkt 1
och 2 kräver är inte prövat.

Loopen, gränsen för modellanrop (`ModelCallLimitMiddleware`), verktygsfelen
(`ToolErrorMiddleware`), svaret som verktyget `FinalAnswer` (`ToolStrategy`) och dagens datum i
prompten (`dynamic_prompt`) kommer också från ramverket (`agent/graph.py`). Den egna koden är
kontrollen (`AnswerCheck` i `agent/middleware.py` och reglerna i `validation/`), `ask_user`,
`AnswerOpenToolCalls`, som ger modellen ett felsvar för ett verktygsanrop vars körning bröts
(`agent/open_tool_calls.py`), prompten, kopplingen till MCP och `AvtalAguiAgent`, som ändrar
ag-ui-langgraphs standardval (`api/agui.py`).

## Konsekvenser

- Valet vilar på ADR 0002:s krav på paus och omstart och på det bygget visade, inte på den yttre
  grafen. Byts ramverket ska de fyra delarna ovan byggas igen: pausen för `ask_user` med
  checkpoints som klarar en omstart, kontrollen efter loopen med nya försök, strömmen till
  webbappen och kopplingen till avtal-mcp.
- Av de middleware som ADR 0002 räknade upp används bara gränsen för modellanrop.
  [Steg 7](../steg/07-agent.md#middleware-som-inte-används-och-varför) förklarar varför
  sammanfattningen av lång kontext och `HumanInTheLoopMiddleware` inte används. Mot en gräns för
  verktygsanrop skrevs inget skäl, och steg 7 säger vad det betyder.
- Ramverkens standardval passar inte alltid. ag-ui-langgraph satte LangChains gräns på 25 steg i
  grafen i stället för grafens egen, så en fråga med fler än ungefär sex modellanrop föll i
  webbappen. Demokörningen hittade felet och PR #22 rättade det ([steg 12](../steg/12-demo.md)).
  Räknaren för modellanrop sparas inte i checkpointen, så den börjar om efter ett svar på
  `ask_user` (ADR 0013).
- Vi är fortsatt bundna till LangChains ekosystem, som ADR 0002 skrev.

## Alternativ som valts bort

- **Den yttre grafen från ADR 0002.** Byggd och provad i ADR 0013: en delgrafs meddelanden når
  föräldern först när delgrafen är klar, så webbappen tappar agentens steg medan den väntar på
  användaren.
- **En egen loop.** Samma skäl som i ADR 0002: mer egen kod som gör det `create_agent` redan gör.
- **Agno.** Vägdes inte in i fas 3 och är inte prövat i projektet. En granskning av projektet
  2026-10-07 påpekade att Agno också har människa i loopen, krokar och stöd för AG-UI och MCP; det
  är inte kontrollerat här. En jämförelse behöver pröva det ADR 0013 provade: att webbappen ser
  agentens steg medan en fråga till användaren väntar, och att kontrollen efter loopen kan skicka
  tillbaka ett utkast till modellen.
