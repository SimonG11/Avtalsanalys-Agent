"""The rules an answer must pass before the user sees it (M7, M8).

What:
    `citations`: the citation check. Every quote must be word for word in
    the section it cites, and the answer's [n] markers and its sources must
    match one to one. `register_facts`: every agreement number, org number
    and date in the answer must be in the register rows of the agreements
    it names, in a cited section, or in the user's own words. `review`: a
    second model judges whether the sources support each claim and whether
    something the question asks is missing. `chain`: the three in order,
    with the notes the user gets when an answer keeps failing.

Why:
    The validation is the step the agent cannot get past (ADR 0002, ADR
    0013): plain rules, written down and tested without a language model,
    so a reviewer can read what an answer must meet. The one judgement no
    rule can make, whether the answer says what its sources say, is the
    reviewer model's (ADR 0015), and code decides what its verdict means.
    The latest-wording rule comes with the tool that finds amendments.

How:
    Pure functions over the model's draft and what the caller has read;
    `chain.check_answer` does the reading through the readers it is given.
    Acting on the result (a new attempt, an answer with reservation) is the
    agent's middleware (`agent/middleware.py`).
"""
