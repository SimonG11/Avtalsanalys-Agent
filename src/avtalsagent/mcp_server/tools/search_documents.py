"""search_documents: the sections that best match a question, by the hybrid search.

What:
    `search_documents` runs `retrieval.hybrid_search.search` with the
    configured embedding model and returns each hit with the fields of a
    citation (`results.SectionRef`), its scope (framework areas, agreement
    numbers), its heading path, its best chunk as a snippet, its scores and
    ranks, and the other sections in scope with the same text (copies).

Why:
    The agent's first step for most questions is to find where something is
    written. The search itself (ADR 0011) is M5's; this tool only checks the
    filters against the register, so a misspelt area or agreement is an
    error the model can correct instead of an empty result it would take as
    "the agreements say nothing", and keeps the result inside what the tools
    may show. An area or agreement that the register has but whose
    documents are not loaded (outside the pilot) is an error too, which
    tells the model to answer from the register. The copies let the agent
    cite the document of the agreement asked about when the same general
    terms are printed in several.

How:
    The filters are put in the register's spelling and a number without a
    supplier's sequence becomes the procurement's filter
    (`visibility.document_filters`, which `list_documents` uses too). There,
    before the question is embedded, an area, agreement or procurement that
    no shown file matches becomes a `NotFoundError` that names the loaded
    areas. The number of candidates per branch and the fusion's k come from
    the settings. Hits and copies are checked against the visibility,
    though the index already leaves out what the quarantine holds. Without
    an embedding model (no OPENAI_API_KEY), with an index that is missing or
    built with another model, or when the model cannot be reached, the tool
    raises an `UnavailableError` in Swedish.
"""

import logging

from pydantic import BaseModel
from sqlalchemy.orm import Session

from avtalsagent.config import get_settings
from avtalsagent.mcp_server.arguments import (
    AgreementNumber,
    DocumentTypeArg,
    FrameworkArea,
    Limit,
    Query,
)
from avtalsagent.mcp_server.errors import UnavailableError
from avtalsagent.mcp_server.results import SectionRef
from avtalsagent.mcp_server.visibility import document_filters, load_visibility
from avtalsagent.retrieval.embedder import Embedder, EmbeddingError
from avtalsagent.retrieval.hybrid_search import IndexNotReadyError, search

_log = logging.getLogger(__name__)


class SectionCopy(BaseModel):
    """Another section, in the search's scope, with the same text as the hit."""

    sha256: str
    file_title: str
    section_position: int
    section_number: str | None


class SearchHit(SectionRef):
    """A section that matches the question, shown by its best chunk."""

    framework_areas: list[str]
    agreement_numbers: list[str]  # a supplier card's; empty for a file all suppliers share
    path: list[str]  # the headings from the top down to this one
    snippet: str  # the best chunk's text: part of the section, not for quoting
    score: float  # reciprocal rank fusion; higher is better
    vector_rank: int | None  # 1-based place in each branch of the search; None if absent
    text_rank: int | None
    copies: list[SectionCopy]


class SearchResult(BaseModel):
    hits: list[SearchHit]  # best first; empty when nothing matched


def search_documents(
    session: Session,
    embedder: Embedder | None,
    query: Query,
    framework_area: FrameworkArea = None,
    agreement_number: AgreementNumber = None,
    document_type: DocumentTypeArg = None,
    limit: Limit = 8,
) -> SearchResult:
    """Sök i ramavtalens dokument efter de avsnitt som bäst svarar på frågan.

    Använd först, när du inte vet var något står. Varje träff har fil, avsnittsnummer,
    rubrik, sidor och ett utdrag (snippet); läs hela avsnittet med read_section innan du
    citerar. Begränsa med framework_area för ett ramavtalsområde, med agreement_number för
    en leverantörs avtal (med upphandlingens gemensamma dokument, som allmänna villkor) eller
    en hel upphandling (numret utan löpnummer), och med document_type för en dokumenttyp.
    copies listar andra dokument med exakt samma text, så att du kan citera det som hör till
    avtalet frågan gäller.
    """
    if embedder is None:
        raise UnavailableError(
            "Sökningen är inte tillgänglig: servern saknar nyckel till inbäddningsmodellen "
            "(OPENAI_API_KEY). Hitta avsnitt med list_documents och get_outline i stället."
        )
    visibility = load_visibility(session)
    filters = document_filters(session, visibility, framework_area, agreement_number, document_type)
    settings = get_settings()
    try:
        hits = search(
            session,
            embedder,
            query,
            filters,
            limit=limit,
            candidates=settings.search_candidates,
            rrf_k=settings.rrf_k,
        )
    except IndexNotReadyError as error:
        _log.warning("search_documents: %s", error)
        raise UnavailableError(
            "Sökindexet är inte byggt, eller byggt med en annan inbäddningsmodell, så "
            "dokumenten kan inte sökas just nu. Säg det till användaren i stället för att "
            "svara utan källor."
        ) from None
    except EmbeddingError as error:
        _log.warning("search_documents: %s", error)  # names the error's type, never the key
        raise UnavailableError(
            "Inbäddningsmodellen svarade inte, så frågan kunde inte sökas. Försök igen om en stund."
        ) from None
    return SearchResult(
        hits=[
            SearchHit(
                sha256=hit.sha256,
                file_title=hit.file_title,
                document_type=hit.document_type,
                page_titles=list(hit.page_titles),
                section_position=hit.section_position,
                section_number=hit.section_number,
                section_title=hit.section_title,
                page_start=hit.page_start,
                page_end=hit.page_end,
                framework_areas=list(hit.framework_areas),
                agreement_numbers=list(hit.agreement_numbers),
                path=list(hit.path),
                snippet=hit.snippet,
                score=hit.score,
                vector_rank=hit.vector_rank,
                text_rank=hit.text_rank,
                copies=[
                    SectionCopy(
                        sha256=copy.sha256,
                        file_title=copy.file_title,
                        section_position=copy.section_position,
                        section_number=copy.section_number,
                    )
                    for copy in hit.copies
                    if visibility.shows_section(copy.sha256, copy.section_position)
                ],
            )
            for hit in hits
            if visibility.shows_section(hit.sha256, hit.section_position)
        ]
    )
