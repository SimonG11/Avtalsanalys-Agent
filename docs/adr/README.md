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
| [0008](0008-tolkning-och-uppdelning.md) | Tolkning med Docling och uppdelning i numrerade avsnitt | Godkänt (PR #4), rättelser i PR #5 |
| [0009](0009-extraktion-avstamning-och-karantan.md) | Extraktion, avstämning mot registret och karantän | Godkänt (PR #6) |
| [0010](0010-webbapp-copilotkit-ag-ui.md) | Webbappen med Next.js och CopilotKit över AG-UI | Godkänt (PR #7) |
| [0011](0011-hybridsokning.md) | Hybridsökning med exakt vektorsökning, BM25 och rangfusion | Godkänt (PR #8) |
| [0012](0012-avtal-mcp.md) | avtal-mcp med MCP SDK:t 1.x, vanliga funktioner som verktyg och samma regel som indexet | Godkänt (PR #9) |
| [0013](0013-agenten.md) | Agenten som en `create_agent`-graf med middleware och en citatkontroll som läser källorna själv | Godkänt (PR #10) |
| [0014](0014-api-och-compose.md) | API:t som ett tunt FastAPI-lager över agenten, en MCP-session per körning och hela systemet i Docker Compose | Godkänt (PR #11) |
| [0015](0015-valideringskedjan.md) | Valideringskedjan: registeruppgifter mot registret, en granskarmodell och två nya försök | Föreslaget (PR för M8) |
| [0016](0016-datumrakning.md) | `calculate_date`: datumräkning med svenska helgdagar, och registerregeln räknar om steget | Föreslaget (PR efter M8) |

Mall: *Status*, *Kontext* (problemet), *Beslut*, *Konsekvenser* (vad vi vinner och vad det kostar),
*Alternativ som valts bort*.
