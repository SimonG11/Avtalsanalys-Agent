# ADR 0004: PostgreSQL + pgvector som enda databas

**Status:** Godkänt av Simon 2026-10-05

## Kontext

Systemet behöver lagra registret från Excel, dokumentens avsnitt med embeddings och svensk
fulltext, hänvisningsgrafen mellan avsnitt och agentens checkpoints.

## Beslut

- **En PostgreSQL 17** med tillägget **pgvector** för allt detta.
- Sökning: vektor (pgvector) och fulltext (`swedish`), sammanslaget med Reciprocal Rank Fusion.
- Hänvisningsgrafen är en kanttabell, inte en grafdatabas.
- Lokalt körs databasen i Docker Compose med en låst version (`pgvector/pgvector:0.8.7-pg17-bookworm`).

## Konsekvenser

- En databas att drifta, säkerhetskopiera och förklara. Transaktioner gäller över register,
  avsnitt och hänvisningar samtidigt.
- pgvector räcker till tiotals miljoner vektorer. Vi räknar med storleksordningen hundratusen avsnitt.
- Postgres fulltext har svensk ordstamning men är inte riktig BM25. Uppgraderingsväg: ParadeDB, om
  mätningen i M5 visar att nyckelordssökningen är svag (licensen AGPL behöver kontrolleras först).

## Alternativ som valts bort

- **Separat vektordatabas** (Qdrant, Weaviate). Ytterligare en tjänst utan behov vid vår storlek.
- **Grafdatabas** (Neo4j) för hänvisningarna. Graferna är små, hundratals noder per ramavtal.
