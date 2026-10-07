"""list_documents: the documents of a framework area, an agreement or a document type.

What:
    `list_documents` returns the files the tools may show whose scope matches
    the filters: for each its sha256, file title, type, agreement pages, framework
    areas, agreement numbers, version date and number of readable sections.
    The agreement's documents come first, then the procurement's, then the
    support for call-offs. `total` counts every match, also beyond `limit`.

Why:
    The search finds sections by their words, but some questions are about
    which documents there are ("vilka bilagor har Advanias avtal?"), and the
    agent needs a file's sha256 to read its outline when the search did not
    find it. The filters mean what they mean in the search (an agreement
    number gives the supplier's card and its procurement's shared files, not
    other suppliers' cards), so a list and a search with the same filters
    cover the same files. Without an index there is nothing to list, and an
    empty list would read as "the agreement has no documents", so the tool
    says the index is missing, as the search does. For the same reason an
    area or agreement that the register has but whose documents are not
    loaded (outside the pilot) is an error that tells the model to answer
    from the register, as in the search.

How:
    The filters are put in the register's spelling, with a number without a
    supplier's sequence as the procurement's filter
    (`visibility.document_filters`, which the search uses too; it raises a
    `NotFoundError` naming the loaded areas when the area, agreement or
    procurement matches no shown file), and become the search's
    own WHERE conditions on `document_scope`
    (`hybrid_search.scope_conditions`). A file is listed only when the
    visibility shows it: indexed (it has a scope) and not held back whole.
    A file held back whole is not counted either: one held back when the
    index was built has no scope that says which filters it would match.
    The files are sorted in Python by `DOCUMENT_GROUPS`, title and sha256;
    one more query counts each listed file's sections, leaving out those the
    quarantine holds, so the count is what `get_outline` shows. When
    `index_store.current_build` finds no index (between `process` and
    `index`), the tool raises an `UnavailableError` in Swedish first.
"""

from collections.abc import Sequence
from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.extracted import DOCUMENT_GROUPS, DocumentGroup, DocumentType
from avtalsagent.ingestion.index_store import current_build
from avtalsagent.mcp_server.arguments import AgreementNumber, DocumentTypeArg, FrameworkArea
from avtalsagent.mcp_server.errors import MissingArgumentError, UnavailableError
from avtalsagent.mcp_server.visibility import Visibility, document_filters, load_visibility
from avtalsagent.retrieval.hybrid_search import scope_conditions

# Larger than the search's `Limit`: an area can have a hundred files, and an entry is short.
DocumentLimit = Annotated[int, Field(ge=1, le=200, description="Högsta antal dokument (1–200).")]

# The agreement and its annexes first, then the procurement, then support for call-offs.
GROUP_ORDER = (DocumentGroup.AGREEMENT, DocumentGroup.PROCUREMENT, DocumentGroup.SUPPORT)


class DocumentEntry(BaseModel):
    """A file the tools may show, with where it belongs."""

    sha256: str
    file_title: str  # the link text used most often, e.g. "Allmänna villkor"
    document_type: str  # `DocumentType` value, e.g. "general_terms"
    page_titles: list[str]  # the agreement pages that link to the file
    framework_areas: list[str]
    agreement_numbers: list[str]  # a supplier card's; empty for a file all suppliers share
    version_date: date | None  # when the document was written or last changed, if it says
    section_count: int  # the sections get_outline shows: held-back ones are not counted


class DocumentList(BaseModel):
    documents: list[DocumentEntry]  # agreement, procurement, then support documents; by title
    total: int  # every file that matches, also those beyond the limit


def list_documents(
    session: Session,
    framework_area: FrameworkArea = None,
    agreement_number: AgreementNumber = None,
    document_type: DocumentTypeArg = None,
    limit: DocumentLimit = 50,
) -> DocumentList:
    """Lista dokumenten i ett ramavtalsområde, i ett avtal eller av en viss dokumenttyp.

    Ange minst ett av framework_area, agreement_number och document_type. Ett avtalsnummer
    ger leverantörens eget avtal och upphandlingens gemensamma dokument (allmänna villkor,
    krav), men inte andra leverantörers avtal; ett upphandlingsnummer utan löpnummer ger alla
    upphandlingens dokument. Använd för att se vilka dokument som finns och
    få deras sha256 till get_outline och read_section; för att hitta var något står, använd
    search_documents. total är antalet dokument som matchar, även de som inte ryms i limit,
    och dokument som hålls tillbaka i granskningen visas inte.
    """
    if framework_area is None and agreement_number is None and document_type is None:
        raise MissingArgumentError(
            "Ange minst ett av framework_area, agreement_number och document_type. "
            "Ramavtalsområden och avtalsnummer hittar du med search_register."
        )
    if current_build(session) is None:
        raise UnavailableError(
            "Sökindexet är inte byggt, så dokumenten kan inte listas just nu. Säg det till "
            "användaren i stället för att svara att avtalet saknar dokument."
        )
    visibility = load_visibility(session)
    filters = document_filters(session, visibility, framework_area, agreement_number, document_type)
    scope, metadata = models.DocumentScope, models.DocumentMetadata
    rows = session.execute(
        select(
            scope.sha256,
            metadata.title,
            metadata.document_type,
            scope.page_titles,
            scope.framework_areas,
            scope.agreement_numbers,
            metadata.version_date,
        )
        .join(metadata, metadata.sha256 == scope.sha256)
        .where(*scope_conditions(filters))
    ).all()
    shown = sorted(
        (row for row in rows if visibility.shows_file(row.sha256)),
        key=lambda row: (
            GROUP_ORDER.index(DOCUMENT_GROUPS[DocumentType(row.document_type)]),
            row.title,
            row.sha256,
        ),
    )
    listed = shown[:limit]
    counts = _section_counts(session, visibility, [row.sha256 for row in listed])
    return DocumentList(
        documents=[
            DocumentEntry(
                sha256=row.sha256,
                file_title=row.title,
                document_type=row.document_type,
                page_titles=list(row.page_titles),
                framework_areas=list(row.framework_areas),
                agreement_numbers=list(row.agreement_numbers),
                version_date=row.version_date,
                section_count=counts.get(row.sha256, 0),
            )
            for row in listed
        ],
        total=len(shown),
    )


def _section_counts(
    session: Session, visibility: Visibility, files: Sequence[str]
) -> dict[str, int]:
    """By file: the number of its sections that the quarantine does not hold back."""
    if not files:
        return {}
    section = models.DocumentSection
    wanted = set(files)
    held = [key for key in visibility.quarantine.sections if key[0] in wanted]
    query = (
        select(section.sha256, func.count())
        .where(section.sha256.in_(list(files)))
        .group_by(section.sha256)
    )
    if held:
        query = query.where(tuple_(section.sha256, section.position).not_in(held))
    return {sha256: count for sha256, count in session.execute(query)}
