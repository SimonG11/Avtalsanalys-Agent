"""The rules an answer must pass before the user sees it (M7, M8).

What:
    `citations`: the citation check. Every quote must be word for word in
    the section it cites, and the answer's [n] markers and its sources must
    match one to one.

Why:
    The validation is the step the agent cannot get past (ADR 0002, ADR
    0013): plain rules, written down and tested without a language model,
    so a reviewer can read what an answer must meet. M8 adds the other
    rules (register facts, the latest wording, the reviewer model) here.

How:
    Pure functions over the model's draft and the sections the caller has
    read. Fetching the sections and acting on the result (a new attempt, an
    answer with reservation) is the agent's middleware
    (`agent/middleware.py`).
"""
