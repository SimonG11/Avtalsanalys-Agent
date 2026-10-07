"""The agent's answer: the model's draft, the checked answer and the graph's state.

What:
    `FinalAnswer` and `DraftCitation`: what the model hands in, as the
    arguments of the structured-output tool `FinalAnswer`. `Answer` and
    `Citation`: the checked answer, the shared state `answer` of the web
    app's contract (webbapp-kontrakt.md). `AvtalState`: the graph's state.

Why:
    The model fills in only what it can know: its text, whether the
    agreements answer the question, and for each source the section's
    sha256 and position, a quote and, when the section belongs to several
    agreement pages, the one the question is about. Everything else in a
    citation (file, page, section number and title) is copied by the check
    from the section it reads itself, and a page title the section does not
    have is not taken, so the model cannot invent a source's name or page. The
    model reads these classes as a tool schema, so their docstrings and
    field descriptions are in Swedish; the contract's classes are not shown
    to the model.

How:
    Pydantic models. `create_agent` turns `FinalAnswer` into a tool (its
    docstring is the tool's description) and puts the parsed call in
    `structured_response`. `AvtalState` adds `answer`, which a client may
    read but not send (`OmitFromInput`, so a stale or forged answer from
    the client is dropped), and the private retry count. The checkpointer
    must allow these classes in its serializer (`checkpointer.py`), or they
    come back as dicts.
"""

from typing import Annotated, Literal, NotRequired

from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import OmitFromInput, PrivateStateAttr
from pydantic import BaseModel, Field

# A quote is the sentence that supports a claim; a longer one is refused as malformed, so a
# draft cannot make the check compare megabytes.
MAX_QUOTE_LENGTH = 1000


class DraftCitation(BaseModel):
    """En källa i svaret: ett ordagrant citat ur ett avsnitt som du har läst med read_section."""

    id: int = Field(
        ge=1,
        description=(
            "Källans nummer, som i texten skrivs [id]. Numrera källorna 1, 2, 3 … utan luckor."
        ),
    )
    sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Dokumentets sha256, kopierat från read_section.",
    )
    section_position: int = Field(
        ge=0,
        description="Avsnittets section_position, kopierat från read_section.",
    )
    quote: str = Field(
        max_length=MAX_QUOTE_LENGTH,
        description=(
            "Ett ordagrant citat ur avsnittets text från read_section: en sammanhängande "
            "del på minst 15 tecken, utan utelämningar och utan egna ord."
        ),
    )
    page_title: str | None = Field(
        default=None,
        description=(
            "När avsnittet hör till flera ramavtalssidor (page_titles i read_section): den "
            "sida som frågan gäller, kopierad därifrån. Annars utelämnas den."
        ),
    )


class FinalAnswer(BaseModel):
    """Lämna ditt slutliga svar på användarens fråga.

    Svaret kontrolleras innan användaren ser det: varje citat jämförs med texten i
    avsnittet det anger.
    """

    answered: bool = Field(
        description=(
            "true när du besvarar frågan helt eller delvis ur avtalen eller registret; false "
            "bara när inget av det som frågas framgår av dem."
        ),
    )
    text: str = Field(
        description=(
            "Svaret på svenska, kort och konkret, som vanlig text utan Markdown. Sätt [n] "
            "direkt efter varje påstående ur ett dokument. När answered är false: skriv vad "
            "som inte framgår, och var frågan regleras i stället om avtalen säger det."
        ),
    )
    citations: list[DraftCitation] = Field(
        default_factory=list,
        description="Källorna, en för varje [n] i texten.",
    )


class Citation(BaseModel):
    """A checked source of the answer, as the web app's contract has it."""

    id: int
    sha256: str
    file_title: str
    # The agreement page that links to the file: the draft's choice among them, else the first.
    page_title: str | None
    section_number: str | None
    section_title: str
    page: int | None  # the section's first PDF page; None for a Word file
    quote: str
    verified: bool  # the quote is word for word in the section's text


# verified: every source passed the check. with_reservation: a source failed after the
# retries, or the answer has no source to check. no_answer: the agreements do not answer.
AnswerStatus = Literal["verified", "with_reservation", "no_answer"]


class Answer(BaseModel):
    """The answer the user sees: the contract's shared state `answer`."""

    text: str  # plain text; [n] refers to the citation with id n
    status: AnswerStatus
    citations: list[Citation]


class AvtalState(AgentState[FinalAnswer]):
    """The graph's state: the agent's messages and draft, and the checked answer.

    `structured_response` (the draft) stays in the output schema: the
    agent's base state declares it there, and `create_agent` takes the union
    of every middleware's state.
    """

    # Read by the web app (STATE_SNAPSHOT), never taken from a client's input.
    answer: NotRequired[Annotated[Answer | None, OmitFromInput]]
    # How many new attempts the citation check has asked for in this question.
    citation_retries: NotRequired[Annotated[int, PrivateStateAttr]]
