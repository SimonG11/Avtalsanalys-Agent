"""The sections the citation check reads: what it needs of one, and who reads it.

What:
    `CitedSection`: a section as the check uses it, its place, its citation
    fields and its whole text. `SectionReader`: anything that reads one by
    (sha256, section_position) and gives None when it does not exist or is
    held back.

Why:
    The check must not trust the message history (the client sends it and
    could put a forged tool result in it), so it reads each cited section
    again itself. Behind a protocol, the check and its tests do not depend
    on MCP: the agent reads through avtal-mcp's `read_section`
    (`mcp_tools.McpSectionReader`), a test through a dict.

How:
    `CitedSection` has the fields of avtal-mcp's `read_section` result that
    the check needs, under the same names, so a reader can validate the
    tool's structured content into it; the other fields (references, the
    heading path) are ignored.
"""

from typing import Protocol

from pydantic import BaseModel


class CitedSection(BaseModel):
    """A section the answer cites: where it is and its whole text, exactly as stored."""

    sha256: str
    section_position: int
    section_number: str | None
    section_title: str
    file_title: str
    page_titles: list[str]  # the agreement pages that link to the file
    page_start: int | None  # 1-based PDF page; None for a Word file
    text: str


class SectionReader(Protocol):
    """Reads a section for the citation check."""

    async def read(self, sha256: str, section_position: int) -> CitedSection | None:
        """The section, or None when it does not exist or may not be shown."""
        ...
