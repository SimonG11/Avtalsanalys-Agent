# ADR 0003: MCP-server som agentens verktygslager

**Status:** Godkänt av Simon 2026-10-05

## Kontext

Agenten behöver åtta läsande verktyg mot registret och dokumenten (sök i registret, hybridsökning,
läs avsnitt, visa innehåll, följ hänvisning, lista dokument, hitta ändringar, räkna datum).
Verktygen kan ligga i agentens egen kod eller i ett eget lager.

## Beslut

- Alla dataverktyg ligger i en egen **MCP-server, `avtal-mcp`** (`src/avtalsagent/mcp_server/`),
  byggd med det officiella MCP Python SDK:t. Ett verktyg per fil.
- Agenten är **MCP-klient** och laddar verktygen via `langchain-mcp-adapters`. Agenten har inga
  dataverktyg i sin egen kod.
- Servern har **bara läsbehörighet** i databasen. Argumenten valideras med Pydantic, och agenten
  kan aldrig skriva fri SQL.
- Transport: Streamable HTTP mellan containrar, stdio i tester och lokal utveckling.
- **Undantag:** `ask_user` ligger kvar i grafen, eftersom den pausar körningen och inte hämtar data.

## Konsekvenser

- En tydlig säkerhetsgräns: agenten kan bara göra det servern tillåter.
- Varje verktyg kan testas utan LLM.
- Samma verktyg kan användas av andra klienter, till exempel Claude Desktop eller MCP Inspector.
- Kostnad: ett extra nätverkssteg per verktygsanrop (millisekunder) och en tjänst till att drifta.

## Alternativ som valts bort

- **Verktyg som Python-funktioner i agenten.** Enklare i början, men gränsen mellan agent och data
  blir otydlig och verktygen kan inte återanvändas.
