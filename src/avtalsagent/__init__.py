"""avtalsagent: answers questions about public framework agreements with verified citations.

What:
    The Python package for the whole system. Each layer is its own subpackage:
    register, ingestion, retrieval, mcp_server, agent, validation and api.

Why:
    One folder per layer keeps each part small enough to review and explain
    on its own (see docs/steg/00-grund.md).

How:
    The layers are added one milestone at a time (M0-M12 in the implementation
    plan). M0 contains only the settings in `config.py`.
"""
