-- Runs once when the database is created (docker-entrypoint-initdb.d).
-- pgvector stores the embeddings for document search (ADR 0004).
CREATE EXTENSION IF NOT EXISTS vector;
