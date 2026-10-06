# Arkitekturbeslut (ADR)

Varje designbeslut har en egen fil: vad som beslutades, varför och vad det kostar.
Ett beslut ändras aldrig i efterhand. Ändras beslutet skrivs en ny ADR som ersätter den gamla.

| Nr | Beslut | Status |
|---|---|---|
| [0001](0001-workflow-for-inlasning-agent-for-fragor.md) | Workflow för inläsning, agent för frågor | Godkänt |
| [0002](0002-langgraph-och-create-agent.md) | LangGraph som yttre graf, `create_agent` som agentnod | Godkänt |
| [0003](0003-mcp-som-verktygslager.md) | MCP-server som agentens verktygslager | Godkänt |
| [0004](0004-postgres-som-enda-databas.md) | PostgreSQL + pgvector som enda databas | Godkänt |
| [0005](0005-openai-som-modellleverantor.md) | OpenAI som modellleverantör | Godkänt |
| [0006](0006-registrets-datamodell.md) | Registrets datamodell och inläsning | Godkänt (PR #2) |
| [0007](0007-hamtning-av-dokument.md) | Hämtning av dokumenten från avropa.se | Godkänt (PR #3) |
| [0008](0008-tolkning-och-uppdelning.md) | Tolkning med Docling och uppdelning i numrerade avsnitt | Förslag (M3) |

Mall: *Status*, *Kontext* (problemet), *Beslut*, *Konsekvenser* (vad vi vinner och vad det kostar),
*Alternativ som valts bort*.
