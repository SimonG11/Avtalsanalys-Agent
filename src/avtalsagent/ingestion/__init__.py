"""Ingestion workflow: fetch, parse, chunk, extract, validate and index documents.

What:
    One step per file (step1_fetch.py, ...), run in a fixed order.

Why:
    Ingestion is a fixed workflow, not an agent (ADR 0001): the same input must
    always give the same result, and each step can be tested on its own.

How:
    Each step reads what the previous one stored and writes its own result.
    M2 adds the first step, fetching documents from avropa.se.
"""
