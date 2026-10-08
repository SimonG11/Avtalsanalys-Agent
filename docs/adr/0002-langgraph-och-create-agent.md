# ADR 0002: LangGraph som yttre graf, `create_agent` som agentnod

**Status:** Godkänt av Simon 2026-10-05 (arkitekturvalideringen, fas 3). Punkten om den yttre
grafen ersätts av [ADR 0013](0013-agenten.md): stegen runt agentloopen är middleware i en enda
`create_agent`-graf, eftersom webbappen annars inte ser agentens steg medan den väntar på
användaren. Skälen för LangGraph och `create_agent` också utan den yttre grafen föreslås i
[ADR 0023](0023-langgraph-utan-yttre-graf.md).

## Kontext

Frågebesvarandet blandar deterministiska steg (inskydd, validering, svar med reservation) med en
fri agentloop. Ramverket måste klara båda, kunna pausa för en fråga till användaren och spara
tillstånd så att en körning överlever omstart.

## Beslut

- **LangGraph 1.2** bygger den yttre grafen: inskydd → agent → svarsutkast → validering → svar.
  Hela grafen ligger i en fil (`agent/graph.py`) och kan läsas uppifrån och ner.
- **Agentnoden byggs med LangChain v1 `create_agent` och middleware** (gränser för modell- och
  verktygsanrop, human-in-the-loop, sammanfattning av lång kontext) i stället för en egen loop.
- Checkpoints sparas i Postgres (ADR 0004).

## Konsekvenser

- Mindre egen kod att granska, eftersom loopen och middleware kommer från ramverket.
- Checkpoints ger paus och återupptagning (`ask_user`), omstart och uppspelning vid felsökning.
- Vi binder oss till LangChains ekosystem. De exakta middleware-namnen verifieras mot
  dokumentationen när M7 påbörjas.

## Alternativ som valts bort

| Alternativ | Varför inte |
|---|---|
| Deep Agents | Maximal frihet med filsystem och långa uppgifter. Vi behöver valideringssteg som agenten inte kan gå förbi. |
| Pydantic AI | Bra typning, men svagare på orkestrering i flera steg och delagenter. |
| OpenAI Agents SDK | Bygger på överlämningar i en kedja, vilket passar sämre för en valideringsloop. |
| Egen loop i LangGraph | Mer egen kod som gör samma sak som `create_agent`. |

Fullständig jämförelse: [docs/validering.md](../validering.md), avsnitt 1.
