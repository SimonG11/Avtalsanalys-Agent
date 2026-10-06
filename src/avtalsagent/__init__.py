"""avtalsagent: answers questions about public framework agreements with verified citations.

What:
    The Python package for the whole system. The settings are in `config.py`,
    and each layer is its own subpackage: domain (shared data types and
    identifier rules), db (tables and migrations), register, ingestion,
    retrieval, mcp_server, agent, validation and api.

Why:
    One folder per layer keeps each part small enough to review and explain
    on its own (see docs/steg/00-grund.md).

How:
    The layers are added one milestone at a time (M0-M12 in the implementation
    plan). A subpackage is created in the milestone that fills it with code,
    so a layer in the list above exists only once its milestone is reached.
"""
