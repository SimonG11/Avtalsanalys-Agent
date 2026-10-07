"""The tools' argument types, with the Swedish descriptions the model reads.

What:
    One `Annotated` type per kind of argument: `Query`, `Sha256`, `Limit` and
    `Offset`, which a tool requires or gives a default, and the optional
    `FrameworkArea`, `AgreementNumber`, `DocumentTypeArg`, `SectionNumber`,
    `SectionPosition`, `Reference`, `Supplier`, `SubAreaArg`, `OrgNumber` and
    `ValidOn`.

Why:
    The MCP SDK builds each tool's JSON schema from its signature, and the
    model sees only that schema and the tool's description. A type defined
    once gives every tool the same name, limits and explanation for the same
    thing, as the web app's contract asks (webbapp-kontrakt.md point 5). The
    limits are checked by the server before the tool runs, so a call with a
    malformed hash or a limit of 500 never reaches the database. So is the
    NUL character in a text: Postgres cannot store it, and the query would
    fail as if the database were down.

How:
    Pydantic `Field` constraints and descriptions. The names are the
    contract's where its point 5 has one: `query`, `framework_area`,
    `agreement_number`, `sha256`, `section_number`, `reference` and `limit`.
    `document_type`, `section_position`, `supplier`, `org_number`, `valid_on`,
    `offset` and `sub_area` are new names, to be sent to the web-app thread.
    Descriptions are in Swedish because the questions and the documents are.
    An optional argument's type includes None (`framework_area:
    FrameworkArea = None`): Pydantic then puts the description on the
    argument itself, where every client shows it, and the limits on its
    non-null alternative. Written as `FrameworkArea | None`, the description
    would sit inside `anyOf`, where a client may not look. The document
    types are listed in the schema as plain strings, without the enum's
    English docstring. A query is stripped first, so blanks are no query.
    Every text type refuses NUL with an `AfterValidator`, which leaves the
    schema as it is (`Sha256`'s pattern refuses it already).
"""

from datetime import date
from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints, WithJsonSchema

from avtalsagent.domain.extracted import DocumentType


def _refuse_nul(text: str | None) -> str | None:
    """The text unchanged; a ValueError if it holds NUL, which Postgres cannot store."""
    if text is not None and "\x00" in text:
        raise ValueError("Texten får inte innehålla tecknet NUL (\\x00).")
    return text


_NO_NUL = AfterValidator(_refuse_nul)

Query = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=500),
    Field(description="Vad som söks, på svenska och gärna med avtalens egna ord."),
    _NO_NUL,
]
Sha256 = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Dokumentets sha256 (64 tecken), från en sökträff eller list_documents.",
    ),
]
Limit = Annotated[int, Field(ge=1, le=20, description="Högsta antal träffar (1–20).")]
Offset = Annotated[
    int,
    Field(
        ge=0,
        description=(
            "Hoppa över så många rader (0 = från början); använd med total för att bläddra."
        ),
    ),
]

# --- optional: None means "not given" ---

FrameworkArea = Annotated[
    str | None,
    Field(
        min_length=2,
        max_length=200,
        description="Ramavtalsområdet som registret skriver det, t.ex. 'IT-drift'.",
    ),
    _NO_NUL,
]
AgreementNumber = Annotated[
    str | None,
    Field(
        min_length=5,
        max_length=40,
        description=(
            "Ett avtalsnummer ur registret, t.ex. '23.3-5890-2023-003', eller ett "
            "upphandlingsnummer utan leverantörens löpnummer, t.ex. '23.3-5890-2023', för hela "
            "upphandlingen."
        ),
    ),
    _NO_NUL,
]
_DocumentType = Annotated[
    DocumentType,
    WithJsonSchema({"type": "string", "enum": [kind.value for kind in DocumentType]}),
]
DocumentTypeArg = Annotated[
    _DocumentType | None,
    Field(
        description=(
            "Dokumenttyp: supplier_agreement (leverantörens undertecknade ramavtal), "
            "main_document (ramavtalets huvuddokument), general_terms (allmänna villkor), "
            "annex (övrig bilaga), price_annex (prisbilaga), requirements_catalogue (kravkatalog), "
            "requirements_specification (kravspecifikation), amendment (tillägg eller ändring), "
            "licence_terms (licensvillkor), procurement_document (upphandlingsdokument), "
            "questions_and_answers (frågor och svar under upphandlingen), call_off_guidance "
            "(vägledning för avrop), requirements_report (kravredovisning), template (mall), "
            "unknown (okänd typ)."
        )
    ),
]
SectionNumber = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=32,
        description="Avsnittets nummer som dokumentet skriver det, t.ex. '6.21.9'.",
    ),
    _NO_NUL,
]
SectionPosition = Annotated[
    int | None,
    Field(
        ge=0,
        description=(
            "Avsnittets plats i dokumentet (section_position i en sökträff eller i "
            "get_outline), för ett avsnitt utan nummer."
        ),
    ),
]
Reference = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=300,
        description=(
            "Hänvisningen som den står i texten, t.ex. 'punkt 6.21.9' eller 'bilaga Priser'."
        ),
    ),
    _NO_NUL,
]
Supplier = Annotated[
    str | None,
    Field(
        min_length=2,
        max_length=200,
        description="Leverantörens namn eller en del av det.",
    ),
    _NO_NUL,
]
SubAreaArg = Annotated[
    str | None,
    Field(
        min_length=2,
        max_length=200,
        description=(
            "Delområde eller region, eller en del av namnet, t.ex. 'Övre Norrland' eller "
            "'IT-drift Större'. Skilj flera delar med ' / '; alla delar måste finnas i "
            "delområdet, t.ex. 'IT-tjänster / Övre Norrland'."
        ),
    ),
    _NO_NUL,
]
OrgNumber = Annotated[
    str | None,
    Field(
        min_length=6,
        max_length=20,
        description="Leverantörens organisationsnummer, t.ex. 'NNNNNN-NNNN'.",
    ),
    _NO_NUL,
]
ValidOn = Annotated[
    date | None,
    Field(description="Bara avtal som gäller detta datum (ÅÅÅÅ-MM-DD)."),
]
