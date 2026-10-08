"""Offline measurement of the search (M5) and of the agent's answers (M11).

What:
    `offline` holds the whole search index in memory and runs both branches
    and the fusion without a database; `metrics` scores a ranking against the
    sections a question needs; `gold` reads the test set
    (`datasets/gold_sv.jsonl`) and finds each source's section;
    `run_retrieval_eval` measures vector search, BM25 and the hybrid on the
    built index, or on one built in memory (`--offline`), and writes a report.
    `run_answer_eval` asks the agent every question through avtal-mcp
    (`answer_run`, with its path read by `answer_steps`), has `judge`
    compare each answer with the gold answer, scores sources, register rows,
    time and cost (`answer_scores`) and writes a report (`answer_report`,
    with numbers written by `report_text`). `--mode workflow` asks the
    baseline instead, a fixed workflow with the agent's model and check
    (`workflow_baseline`, ADR 0024); `ask_judge` judges the agent's
    questions to the user; and `compare_answer_runs` compares two answer
    reports question by question, with paired bootstrap intervals.

Why:
    Comparing chunk sizes, embedding models or analysers means rebuilding the
    index many times; in memory that takes seconds instead of a database load
    each time. The offline branches compute what the SQL computes, in the
    same order and precision, and an integration test checks that both return
    the same chunks, so a number measured here is the production search's.
    The search finding the right section does not make the answer right, so
    the answers are measured too, against the same test set (ADR 0019).

How:
    Plain Python and numpy over the values step 6 produces (`IndexPlan`), the
    cached embeddings and the files' scopes. The answer measurement builds the
    agent as the API does. Nothing here is imported by the application.
    Reports go to `evals/reports/`, which git ignores: they are rebuilt by
    running the measurement.
"""
