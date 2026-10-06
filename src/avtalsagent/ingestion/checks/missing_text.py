"""Step-5 check: what is indexed has its text, and every linked file was read.

What:
    `run` holds back what has text missing: a section whose pages include one
    without a text layer (QUARANTINE for that section), a file that gave no
    section at all (QUARANTINE), and a file a page links to that was never
    parsed (QUARANTINE). A file with pages without a text layer that still
    has sections gets a NOTE, whether or not a section lost text.

Why:
    A scanned page has no text layer, and step 2 runs no OCR (M4 survey,
    doctypes.md §8). A section that runs over such a page has lost that
    page's text, and an answer from it could miss a term printed there:
    e04bad6a0ced (Allmänna villkor, Systemutveckling) has 22 of 31 pages
    without text, and its §7.16 runs over p14-22 with the text of p14 and p22
    only: "7.16 Prismodeller Kund och Ramavtalsleverantör kan avtala om olika
    prismodeller och modeller för prisindexering ...". Such a section is held
    back; the file's other sections are indexed (M4 design, decision 3). A
    file whose every page is scanned gives no section and so nothing to
    index: 21dd4fde89d5 and 5c9b05f2cc79, IBM's "Tilläggsavtal nr 7" (10
    pages) and "nr 5" (1 page), both linked as "Volymavtal". Holding it back
    puts it in the report's quarantine list, so the gap is seen. Scanned pages
    outside every section lose no section text, but the file lacks what is on
    them: 124261dc2ad4 (Kravkatalog, Systemutveckling) p1-3 and p11. That is
    worth knowing, a NOTE; a file held back for having no section needs none.
    A file a page links to that was never parsed (its
    parse failed) has not been read or checked at all.

How:
    The pages without a text layer are step 2's (`CheckedFile.ocr_pages`). A
    section's pages are `page_start..page_end`; a Word file has no pages and
    no text layer to lack. A section finding names the section by its number,
    or its title when it has none, and adds its pages when another section of
    the file has the same name, so the finding's key survives a new split and
    is unique. In the pilot: the 3 sections of e04bad6a0ced that contain such
    pages ("Text före första rubriken" p2-14, 7.16 p14-22, 7.25 p23-31; 7.24
    p22-23 has text on both pages), 2 files without sections (21dd4fde89d5,
    5c9b05f2cc79), 2 NOTEs (e04bad6a0ced and 124261dc2ad4), and no link to an
    unparsed file.
"""

from collections import Counter, defaultdict
from collections.abc import Sequence

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import Finding, Severity
from avtalsagent.domain.parsed import Section
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile

CHECK = "missing_text"

# The subjects of the findings about a whole file; a section's is its number or title.
NO_SECTION = "inga avsnitt"
NOT_PARSED = "inte tolkad"

# Step 2 reads the text layer and runs no OCR, so a scanned page gives no text.
_NO_OCR = "ingen OCR körs"


def run(context: CheckContext) -> list[Finding]:
    """The findings of every file, then those of the links to files that were not parsed."""
    findings: list[Finding] = []
    for file in context.files:
        ocr_pages = set(file.ocr_pages)
        subjects = _section_subjects(file.sections)
        # A section that lost a page's text (e04bad6a0ced §7.16, p15-21 scanned).
        for section in file.sections:
            if pages := _pages_in(section, ocr_pages):
                findings.append(_section_finding(file, section, subjects[section.position], pages))
        # Nothing to index at all (21dd4fde89d5, every page scanned).
        if not file.sections:
            findings.append(_no_section_finding(file))
        # Scanned pages, in a section or not (124261dc2ad4 p1-3 and p11, in none).
        elif file.ocr_pages:
            findings.append(_scanned_pages_note(file))
    # A linked file step 2 never gave a parse: no file in `context.files` has its hash.
    parsed = {file.sha256 for file in context.files}
    unparsed: defaultdict[str, list[CatalogLink]] = defaultdict(list)
    for link in context.links:
        if link.sha256 not in parsed:
            unparsed[link.sha256].append(link)
    findings.extend(_not_parsed_finding(sha256, links) for sha256, links in unparsed.items())
    return findings


def _pages_in(section: Section, ocr_pages: set[int]) -> list[int]:
    """The pages without a text layer in the section's page range."""
    if section.page_start is None:
        return []
    end = section.page_end if section.page_end is not None else section.page_start
    return sorted(page for page in ocr_pages if section.page_start <= page <= end)


def _section_subjects(sections: Sequence[Section]) -> dict[int, str]:
    """Each section's subject by position: its number or title, with its pages if not unique."""
    names = Counter(_name(section) for section in sections)
    return {
        section.position: _name(section)
        if names[_name(section)] == 1
        else f"{_name(section)} (s. {_span(section)})"
        for section in sections
    }


def _name(section: Section) -> str:
    return section.number or section.title


def _span(section: Section) -> str:
    start, end = section.page_start, section.page_end
    return f"{start}" if end is None or end == start else f"{start}-{end}"


def _section_finding(
    file: CheckedFile, section: Section, subject: str, pages: list[int]
) -> Finding:
    name = f"Avsnitt {section.number}" if section.number else f'Avsnittet "{section.title}"'
    which = "sidan" if len(pages) == 1 else "sidorna"
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=subject,
        message=(
            f"{name}, s. {_span(section)}, omfattar {which} {_page_list(pages)} som saknar "
            f"textlager ({_NO_OCR}), så avsnittets text är ofullständig."
        ),
        sha256=file.sha256,
        section=section.position,
    )


def _no_section_finding(file: CheckedFile) -> Finding:
    if file.page_count == 0:
        reason = "Dokumentet gav ingen text att dela i avsnitt"
    elif len(file.ocr_pages) == file.page_count == 1:
        reason = f"Dokumentet gav inget avsnitt: dess enda sida saknar textlager ({_NO_OCR})"
    elif len(file.ocr_pages) == file.page_count:
        reason = (
            f"Dokumentet gav inget avsnitt: ingen av dess {file.page_count} sidor har "
            f"textlager ({_NO_OCR})"
        )
    else:
        reason = f"Dokumentet gav inget avsnitt ur sina {file.page_count} sidor"
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=NO_SECTION,
        message=f"{reason}, så det har inget att läsa in.",
        sha256=file.sha256,
    )


def _scanned_pages_note(file: CheckedFile) -> Finding:
    """The note for a file with sections and some pages without a text layer."""
    pages = _page_list(file.ocr_pages)
    lacking = f"{len(file.ocr_pages)} av dokumentets {file.page_count} sidor saknar textlager"
    on_them = "den" if len(file.ocr_pages) == 1 else "dem"
    return Finding(
        check=CHECK,
        severity=Severity.NOTE,
        subject=f"s. {pages}",
        message=(
            f"{lacking} ({_NO_OCR}): s. {pages}. Det som står på {on_them} finns inte i "
            "dokumentets avsnitt."
        ),
        sha256=file.sha256,
    )


def _not_parsed_finding(sha256: str, links: list[CatalogLink]) -> Finding:
    link = links[0]
    others = f" Den länkas från {len(links)} ställen." if len(links) > 1 else ""
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=NOT_PARSED,
        message=(
            f'Filen som länken "{link.title}" på sidan "{link.page_title}" pekar på har inte '
            f"tolkats (steg 2), så den är varken läst eller kontrollerad.{others}"
        ),
        sha256=sha256,
        evidence=link.title,
    )


def _page_list(pages: Sequence[int]) -> str:
    """Page numbers with runs joined, for a message: "1, 6-13, 15-21"."""
    runs: list[list[int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][-1] + 1:
            runs[-1].append(page)
        else:
            runs.append([page])
    return ", ".join(f"{run[0]}" if len(run) == 1 else f"{run[0]}-{run[-1]}" for run in runs)
