"""Step-5 check: a document is still listed on avropa.se.

What:
    `run` gives a QUARANTINE finding for a file whose every link is on an
    agreement page that avropa.se no longer lists.

Why:
    A framework agreement that ends, or is replaced, leaves avropa.se's list
    of agreement pages, and its documents with it. The catalog keeps such a
    page and marks when it stopped being listed
    (`CatalogLink.page_missing_since`, set by step 1 when the page is not
    among those listed in a fetch), so the documents read from it stay in the
    database. A document no listed page links to may no longer be in force,
    and must not answer questions until a person has looked at it
    (ADR 0009 decision 6). A file that one listed page still links to stays
    in: in the pilot, the templates shared by several procurements are linked
    from up to 15 pages ("Utkast till Säkerhetsskyddsavtal"). In the pilot
    every page is listed, so no file is held back.

How:
    A file is held back when it has links and all of them have
    `page_missing_since` set. One finding per file; the message names each
    page and the date it was first missed.
"""

from avtalsagent.domain.extracted import Finding, Severity
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile

CHECK = "still_published"

SUBJECT = "inte publicerad"  # the subject of every finding: the file is no longer listed


def run(context: CheckContext) -> list[Finding]:
    """One finding per file that no page listed on avropa.se links to."""
    return [
        _finding(file)
        for file in context.files
        if file.links and all(link.page_missing_since is not None for link in file.links)
    ]


def _finding(file: CheckedFile) -> Finding:
    pages: dict[str, str] = {}
    for link in file.links:
        if link.page_missing_since is not None:
            pages.setdefault(
                link.page_url,
                f'"{link.page_title}" (saknas sedan {link.page_missing_since.date().isoformat()})',
            )
    listed = list(pages.values())
    named = listed[0] if len(listed) == 1 else f"{', '.join(listed[:-1])} och {listed[-1]}"
    which = "sidan" if len(listed) == 1 else "sidorna"
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=SUBJECT,
        message=(
            f"Ingen sida som avropa.se listar länkar längre till dokumentet: {which} {named} "
            "finns inte längre i listan över ramavtal."
        ),
        sha256=file.sha256,
    )
