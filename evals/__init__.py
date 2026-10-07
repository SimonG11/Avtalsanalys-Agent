"""Offline measurement of the search (M5).

What:
    `offline` holds the whole search index in memory and runs both branches
    and the fusion without a database; `metrics` scores a ranking against the
    sections a question needs; `gold` reads the test set
    (`datasets/gold_sv.jsonl`) and finds each source's section;
    `run_retrieval_eval` measures vector search, BM25 and the hybrid on the
    built index, or on one built in memory (`--offline`), and writes a report.

Why:
    Comparing chunk sizes, embedding models or analysers means rebuilding the
    index many times; in memory that takes seconds instead of a database load
    each time. The offline branches compute what the SQL computes, in the
    same order and precision, and an integration test checks that both return
    the same chunks, so a number measured here is the production search's.

How:
    Plain Python and numpy over the values step 6 produces (`IndexPlan`), the
    cached embeddings and the files' scopes. Nothing here is imported by the
    application. Reports go to `evals/reports/`, which git ignores: they are
    rebuilt by running the measurement.
"""
