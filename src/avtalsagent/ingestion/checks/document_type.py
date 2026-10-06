"""Step-5 check: every file got its type from a rule that knows its link title.

What:
    `run` gives a REPORT finding for a file that no type rule of step 4
    matched (`DocumentType.UNKNOWN`), and a NOTE for a file typed only by the
    heading its link is listed under (the fallback rules F1-F3 of
    `extract/document_type.py`).

Why:
    The type decides what a file is checked against and whether it is part of
    the agreement or only support for call-offs. A file no rule types counts
    as support, so a binding document could be missed: a person must look at
    it, and a new kind of document on avropa.se needs a rule. A type from the heading
    alone is right in the pilot, but it rests on the site's category, which is
    wrong for 3 files there (M4 survey, doctypes.md §1), so it is worth
    knowing and no deviation: 19c85c74c3b2 link "Nuts 2 indelning" under
    "Avtal" is an annex by F1, as are 83b9c9db99bf and f3f138d74139
    "Finansiella villkor vid köp av hårdvara som tjänst".

How:
    Reads the type and the rule step 4 stored (`DocumentMetadata.type_rule`).
    One finding per file; its subject is the file's title (the link text used
    most often), the text no keyword rule knows, and its evidence the first
    link's title as written. The message names the link titles, the headings
    they are listed under and the type given. In the pilot: 3 NOTEs (the files
    above), 0 REPORTs.
"""

from collections.abc import Iterable

from avtalsagent.domain.extracted import DocumentType, Finding, Severity
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.extract.document_type import FALLBACK_RULES

CHECK = "document_type"

# The Swedish names of the types the fallback rules give (M4 survey, doctypes.md §2).
_TYPE_NAMES = {
    DocumentType.ANNEX: "bilaga",
    DocumentType.PROCUREMENT_DOCUMENT: "upphandlingsdokument",
    DocumentType.CALL_OFF_GUIDANCE: "avropsvägledning",
}


def run(context: CheckContext) -> list[Finding]:
    """A REPORT for each file without a type, a NOTE for each typed by its heading only."""
    findings: list[Finding] = []
    for file in context.files:
        metadata = file.metadata
        if metadata.document_type is DocumentType.UNKNOWN:
            findings.append(_finding(file, Severity.REPORT, _unknown_message(file)))
        elif metadata.type_rule in FALLBACK_RULES:
            findings.append(_finding(file, Severity.NOTE, _fallback_message(file)))
    return findings


def _finding(file: CheckedFile, severity: Severity, message: str) -> Finding:
    return Finding(
        check=CHECK,
        severity=severity,
        subject=file.metadata.title,
        message=message,
        sha256=file.sha256,
        evidence=file.links[0].title if file.links else None,
    )


def _unknown_message(file: CheckedFile) -> str:
    headings = _quoted(link.category for link in file.links if link.category)
    under = f", listad under {headings}," if headings else ","
    return (
        f"Ingen typregel känner igen {_titles(file)}{under} så dokumentets typ är okänd. "
        "Det räknas som stöddokument, inte som en del av avtalet, tills en regel ger det en typ."
    )


def _fallback_message(file: CheckedFile) -> str:
    metadata = file.metadata
    name = _TYPE_NAMES.get(metadata.document_type, metadata.document_type.value)
    headings = _quoted(link.category for link in file.links if link.category)
    return (
        f"Ingen typregel känner igen {_titles(file)}. Dokumentet fick typen {name} "
        f"från rubriken {headings} som {_links(file)} står under (regel {metadata.type_rule})."
    )


def _links(file: CheckedFile) -> str:
    return "länkarna" if len(file.links) > 1 else "länken"


def _titles(file: CheckedFile) -> str:
    titles = list(dict.fromkeys(link.title for link in file.links))
    noun = "länktitlarna" if len(titles) > 1 else "länktiteln"
    return f"{noun} {_quoted(titles)}"


def _quoted(values: Iterable[str]) -> str:
    """'"Avtal"', or '"Avtal" och "Upphandling"', each value once."""
    items = [f'"{value}"' for value in dict.fromkeys(values)]
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} och {items[-1]}"
