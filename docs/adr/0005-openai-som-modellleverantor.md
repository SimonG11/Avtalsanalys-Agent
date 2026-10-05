# ADR 0005: OpenAI som modellleverantör

**Status:** Godkänt av Simon 2026-10-05

## Kontext

Systemet använder språkmodeller i tre roller med olika krav: agenten (många anrop per fråga),
granskaren (ett anrop per svar, ska vara oberoende av agenten) och extraktion vid inläsning
(stora volymer, enkla uppgifter).

## Beslut

| Roll | Modell | Inställning |
|---|---|---|
| Agent | `gpt-6.1-sol` | `AGENT_MODEL` |
| Granskare | `gpt-6-astra` | `REVIEWER_MODEL` |
| Extraktion och inskydd | `gpt-6-luna` | `EXTRACTION_MODEL` |

- Granskaren är medvetet en annan och starkare modell än agenten.
- Modellerna anropas via `langchain-openai`, så grafen är inte bunden till leverantören.
- Modellnamnen ligger i `src/avtalsagent/config.py` och kan bytas med en miljövariabel.
- API-nyckeln läses bara från miljövariabeln `OPENAI_API_KEY`. Den finns aldrig i koden, i git
  eller i loggar (`SecretStr` döljer den).

## Konsekvenser

- En leverantör och en nyckel för alla roller.
- Kostnaden per fråga mäts i utvärderingen (M11). Där prövas också `gpt-6-astra` som agent.
- Embeddings väljs separat, genom mätning på den svenska testsamlingen (M5).

## Alternativ som valts bort

- **Flera leverantörer** (t.ex. olika för agent och granskare). Mer oberoende granskning men fler
  nycklar och avtal. Kan prövas senare med en ändring i `agent/models.py`, eftersom grafen inte är bunden till leverantören.
