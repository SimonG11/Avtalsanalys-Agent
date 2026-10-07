"""The amendments the answer check reads: what it needs of one, and who reads them.

What:
    `AmendmentInfo`: a section that changes a cited section or its whole
    file, as the latest-wording rule (`validation/latest_wording.py`) uses
    it: where the change is written, what it changes, how sure the
    reference is and its date. `AmendmentReader`: anything that gives the
    amendments of one section by (sha256, section_position), or None when
    they cannot be read.

Why:
    An answer built on a wording that an amendment or a questions log has
    replaced is wrong even when every quote is exact: punkt 3.2 of the
    tender documents for IT-drift Mindre (e394ae5dfd2c §3.2) says seven
    tenders, and Kammarkollegiet's answer (7a765d649e25 §9) corrects it to
    eight. The check reads the amendments again itself, as it reads the
    sections, not from the message history, which a client sends and could
    forge (ADR 0013). Behind a protocol, the rule and its tests do not
    depend on MCP: the agent reads through avtal-mcp's `find_amendments`
    (`mcp_tools.McpAmendmentReader`), a test through a dict.

How:
    The fields are those of one entry of `find_amendments`' result, flattened:
    the amending section's place and citation fields (`amending` there), the
    position of the section it changes (`amended.section_position`, None for
    the whole file), the reference's status and the entry's date. The other
    fields (the reference as written, the excerpt) are for the model, which
    calls the tool itself.
"""

from datetime import date
from typing import Protocol

from pydantic import BaseModel


class AmendmentInfo(BaseModel):
    """A section that changes a section, or its whole file."""

    sha256: str  # the amending section's file: an amendment or a questions log
    section_position: int
    file_title: str
    section_number: str | None
    section_title: str
    amended_position: int | None  # the section it changes; None for the whole file
    status: str  # "resolved", or "ambiguous" (the reference has several candidates)
    dated: date | None  # a log's answer: its TendSign stamp; an amendment: its file's date


class AmendmentReader(Protocol):
    """Reads the amendments of a section for the latest-wording rule."""

    async def read(self, sha256: str, section_position: int) -> list[AmendmentInfo] | None:
        """The amendments of the section and of its file, or None when they cannot be read."""
        ...
