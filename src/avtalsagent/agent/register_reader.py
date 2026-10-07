"""The register rows the answer check reads: what an agreement's rows hold, and the reader.

What:
    `RegisterEntry`, one agreement in one sub-area as avtal-mcp's
    `search_register` gives it, and `RegisterReader`, which reads an
    agreement's rows for the register-facts rule
    (`validation/register_facts.py`). `mcp_tools.McpRegisterReader` reads
    through avtal-mcp; the tests read from a list.

Why:
    The check proves the register facts of an answer against the register
    itself, read again after the agent has answered, not against the
    message history, which a client sends and could forge (as for sections,
    ADR 0013). The agent's process has no database: like every other read,
    this one goes through avtal-mcp (ADR 0003).

How:
    The fields are the same as `search_register`'s `RegisterRow`, so a
    result validates into this model as it is. A reader gives every row of
    one agreement (all its sub-areas), or None when the agreement is
    unknown or the register cannot be read.
"""

from datetime import date
from typing import Protocol

from pydantic import BaseModel


class RegisterEntry(BaseModel):
    """One agreement in one sub-area, as the register has it."""

    agreement_number: str  # e.g. "23.3-5890-2023-003"
    procurement_number: str
    supplier_name: str
    org_number: str  # NNNNNN-NNNN, or a foreign number as written
    former_names: list[str]
    framework_area: str
    sub_area: str  # the path, levels joined with " / "
    valid_from: date
    valid_to: date
    max_extension_to: date | None


class RegisterReader(Protocol):
    """Reads an agreement's rows for the register-facts rule."""

    async def read(self, agreement_number: str) -> list[RegisterEntry] | None:
        """Every row of the agreement, or None when it is unknown or cannot be read."""
        ...
