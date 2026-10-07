# ADR 0023: LangGraph och `create_agent` också utan den yttre grafen

**Status:** Föreslaget.
Kompletterar [ADR 0002](0002-langgraph-och-create-agent.md), vars skäl för LangGraph var den yttre
grafen, och [ADR 0013](0013-agenten.md), som ersatte den grafen med middleware i en enda
`create_agent`-graf.

## Kontext

ADR 0002 valde LangGraph för att bygga en yttre graf (inskydd → agent → svarsutkast → validering
→ svar) runt en agentnod från `create_agent`. Skälet var att frågebesvarandet blandar
deterministiska steg med en fri agentloop. ADR 0013 tog bort den yttre grafen: stegen runt loopen
är krokar i samma graf som loopen. Det skrivna skälet för LangGraph gäller alltså inte längre i den
form ADR 0002 gav det, och frågan är vad som bär valet nu.

Arkitekturvalideringen i fas 3 ([validering.md](../validering.md), avsnitt 1) jämförde LangGraph
med sex andra ramverk. Agno fanns inte med.

## Beslut

LangGraph och LangChains `create_agent` är fortfarande ramverket. Fyra saker som koden använder bär
valet:

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

Loopen, gränsen för modellanrop (`ModelCallLimitMiddleware`), verktygsfelen
(`ToolErrorMiddleware`), svaret som verktyget `FinalAnswer` (`ToolStrategy`) och dagens datum i
prompten (`dynamic_prompt`) kommer också från ramverket (`agent/graph.py`). Den egna koden är
kontrollen, `ask_user`, prompten och kopplingen till MCP.

## Konsekvenser

- Valet vilar på det koden använder, inte på den yttre grafen. Byts ramverket ska de fyra delarna
  ovan byggas igen: pausen för `ask_user` med checkpoints som klarar en omstart, kontrollen efter
  loopen med nya försök, strömmen till webbappen och kopplingen till avtal-mcp.
- Av de middleware som ADR 0002 räknade upp används bara gränsen för modellanrop. Varför gränsen
  för verktygsanrop, sammanfattningen av lång kontext och `HumanInTheLoopMiddleware` inte används
  står i [steg 7](../steg/07-agent.md#middleware-som-inte-används-och-varför).
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
