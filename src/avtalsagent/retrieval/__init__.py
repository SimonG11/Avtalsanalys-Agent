"""Hybrid search over the indexed chunks (M5).

What:
    `swedish_text` turns Swedish text into the stems of the word index,
    `bm25` weights those stems per chunk, `embedder` turns text into vectors
    (OpenAI), `fusion` merges the vector ranking and the BM25 ranking into one
    list of sections, and `hybrid_search` runs both branches in PostgreSQL.

Why:
    Neither branch is enough alone. A vector finds a clause that says the same
    thing in other words ("säga upp avtalet" for "uppsägning"); BM25 finds
    rare exact words that a vector blurs ("vite", "Säkerhetsskyddsavtal").
    Copies of one section (general terms repeated in several documents)
    would fill the results, so the fusion returns each text once.

How:
    Everything but `hybrid_search` is pure Python with no database, so step 6
    (`ingestion/step6_index.py`) and the offline measurement (`evals/`) call
    the same functions as the search: the measured numbers are those of the
    code that runs.
"""
