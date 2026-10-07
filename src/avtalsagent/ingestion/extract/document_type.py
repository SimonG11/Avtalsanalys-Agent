"""Step 4: what kind of document a file is, and when it was written.

What:
    `classify` gives a file its `DocumentType` (main document, general terms,
    template, ...) and the rule that gave it, from the links to the file on the
    agreement pages. `annex_number` reads "4.1" from "Bilaga 4.1 Microsoft
    Business and Services Agreement". `first_chapter` gives the chapter a file
    printed from a larger procurement document starts at ("9 Ramavtalets
    Huvuddokument"). `tendsign_cover` reads the cover word of a printout from
    TendSign, the procurement system. `version_date` says when the document
    was written or last changed, and from which source. `published_on` gives
    the date a procurement document was first published, for matching
    questions in a questions log to it.

Why:
    The type decides what step 5 checks a file against (a supplier's signed
    agreement against its agreement number, a main document against the
    agreement period) and whether it is part of the agreement or only support
    for call-offs. The link says what a file is better than its content: a
    chapter printed from TendSign opens with the same header ("Upphandlingsdokument
    2024-09-24 ...") whether it is the main document, the general terms or a
    requirements catalogue. The rules typed all 207 files of the pilot, so no
    language model is used (ADR 0009). The date is kept apart from avropa.se's
    "Senast uppdaterad", which is when the file was uploaded: in the pilot
    TendSign's "Publicerad" date is 69 to 660 days earlier, and a file uploaded
    at two URLs has two such dates.

How:
    Each link is typed by the first rule in `_TYPE_RULES` that matches its
    title, file name, agreement number or category heading. A file linked
    from several pages gets the type of the earliest rule that matches any of
    its links; the links never disagree in the pilot. Dates are read from the
    raw blocks, page headers included, since step 3 removes those from the
    sections. `version_date` tries its sources in a fixed order (see
    `version_date`); `published_on` reads TendSign's "Publicerad" date or else the
    first date near the start.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote, urlsplit

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import DocumentType
from avtalsagent.domain.parsed import Block, BlockKind, ParsedDocument, Section
from avtalsagent.ingestion.step3_chunk import OutlineKind

NO_RULE = "none"  # the type rule of a file no rule types (DocumentType.UNKNOWN)

# --- Document type -------------------------------------------------------------------------


@dataclass(frozen=True)
class _TypeRule:
    """A rule that types a link when all of its parts that are set match."""

    id: str
    document_type: DocumentType
    title: re.Pattern[str] | None = None  # searched in the link text
    file_name: re.Pattern[str] | None = None  # ...or in the file name (either one is enough)
    file_name_not: re.Pattern[str] | None = None  # the file name must not contain this
    category: str | None = None  # the heading the link is listed under, exactly
    supplier_card: bool = False  # the link is in a supplier's card (has an agreement number)

    def matches(self, link: CatalogLink) -> bool:
        title = " ".join(link.title.split())
        name = _file_name(link.url)
        if self.supplier_card and not link.agreement_number:
            return False
        if self.category is not None and (link.category or "").strip() != self.category:
            return False
        if self.file_name_not is not None and self.file_name_not.search(name):
            return False
        if self.title is None and self.file_name is None:
            return True
        return bool(
            (self.title is not None and self.title.search(title))
            or (self.file_name is not None and self.file_name.search(name))
        )


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# First match wins, so the order matters where two rules match one link. Each such
# place is named at the earlier rule; all of them occur in the pilot. Files typed per
# rule in the pilot (207 files): R01 25, R02 13, R03 19, R04 8, R05 12, R06 21, R07 11,
# R08 8, R09 18, R10 31, R11b 1, R11 18, R12 10, R13 5, R14 4, F1 3, F2 0, F3 0.
_TYPE_RULES: tuple[_TypeRule, ...] = (
    # A link in a supplier's card is the supplier's signed agreement; the card gives its
    # agreement number. 14aa1cc8ee3d link: "Ramavtal" in the card of 23.3-2940-20:010.
    _TypeRule("R01", DocumentType.SUPPLIER_AGREEMENT, supplier_card=True),
    # A TendSign questions log. 3a316e27aadf link: "Frågor och svar - Upphandlingsdokument".
    _TypeRule(
        "R02", DocumentType.QUESTIONS_AND_ANSWERS, title=_rx(r"fr[åa]gor\s*(?:och|&|-)\s*svar")
    ),
    # The tender documents and the qualification chapter. After R02, whose titles name
    # the document they answer ("Frågor och svar - Ansökningsinbjudan").
    # 54211e718d8e link: "Ansökningsinbjudan"; a0924d85e62c link: "Kvalificering av
    # sökande", listed under "Avtal" although it is a chapter of the tender documents.
    _TypeRule(
        "R03",
        DocumentType.PROCUREMENT_DOCUMENT,
        title=_rx(
            r"upphandlingsdokument|ansökningsinbjudan|anbudsinbjudan|kvalificering av sökande"
        ),
    ),
    # Written amendments. Before R05 and R14: IBM's amendments are linked as "Volymavtal"
    # and only the file name says what they are (21dd4fde89d5 link: "Volymavtal", file
    # "tillaggsavtal-nr-7-till-volymavtal-for-programvaror-2005-…pdf"), and Microsoft's
    # start with "Bilaga" (d54ed0900be5 link: "Bilaga 4 Tillägg och förtydliganden till
    # bilaga 4.1").
    _TypeRule(
        "R04",
        DocumentType.AMENDMENT,
        title=_rx(r"tilläggsavtal|tillägg och förtydligande|ändring"),
        file_name=_rx(r"tillaggsavtal"),
    ),
    # The main document of an agreement area. 34d71a7e4da0 link: "Ramavtalets
    # huvuddokument"; 171a3cacf5fd link: "Volymavtalets huvudavtal 1.0". "IBM Volymavtal"
    # is the title of IBM's main document, a .doc file step 1 does not fetch today, and
    # of its annex "…-050413-bilaga-1.doc", which the file name tells apart.
    _TypeRule(
        "R05",
        DocumentType.MAIN_DOCUMENT,
        title=_rx(
            r"^(?:ramavtalets huvuddokument|volymavtalets huvudavtal|ibm volymavtal|volymavtal)\b"
        ),
        file_name_not=_rx(r"bilaga"),
    ),
    # Microsoft's and IBM's own terms. Before R07 ("IBM Användningsvillkor-Allmänna
    # villkor", 255e496fa266), R08 ("Bilaga 5.1 Enterprise-avtal": "enter-pris-e") and
    # R14 ("Bilaga 9 Produktvillkor-Product terms", 0ba5ca07759e). The forms "Bilaga 6.2
    # Select Plus-registreringsformulär" and "Bilaga 8 Enrollment for Education
    # Solutions" are typed here too: they are part of Microsoft's terms.
    _TypeRule(
        "R06",
        DocumentType.LICENCE_TERMS,
        title=_rx(
            r"business and services agreement|enterprise|select plus|campus|enrollment"
            r"|produktvillkor|product terms|dataskyddstill|data processing|passport advantage"
            r"|\bIPLA\b|academic|molntj|användningsvillkor|arbetsorder|registrering|attachment"
        ),
    ),
    # Kammarkollegiet's general terms. 0a5491b1398e link: "Allmänna villkor".
    _TypeRule("R07", DocumentType.GENERAL_TERMS, title=_rx(r"^allmänna villkor\b")),
    # Price annexes. 4d5c1654801e link: "Prisbilaga"; 33570086cce8 link: "Timpriser -
    # sammanställning".
    _TypeRule("R08", DocumentType.PRICE_ANNEX, title=_rx(r"pris")),
    # Kammarkollegiet's account of its sustainability and security requirements.
    # 5cfcfd3384a1 link: "Redovisning av hållbarhetskrav".
    _TypeRule("R09", DocumentType.REQUIREMENTS_REPORT, title=_rx(r"^redovisning av")),
    # Templates for the call-off and the contract. 93bd3b2bfeae link: "Utkast till
    # Säkerhetsskyddsavtal (Nivå 1)"; 145e34c51489 link: "Kontraktsmall". "Avropsblankett"
    # is the order form, an .xlsx file step 1 does not fetch today.
    _TypeRule(
        "R10",
        DocumentType.TEMPLATE,
        title=_rx(r"^utkast till|avropsmall|kontraktsmall|avropsblankett"),
    ),
    # The call-off routine is listed under "Stöddokument och länkar" but is a binding
    # annex: the main document lists it ("Bilaga Avropsrutin", 34d71a7e4da0 §8.4). Before
    # R11 and R12. 6145b5eefb35 link: "Avropsrutin och Kravkatalog".
    _TypeRule("R11b", DocumentType.ANNEX, title=_rx(r"^avropsrutin")),
    # Guidance for call-offs. 4f886a784c5d link: "Vägledning - IT-drift"; 0c2dd21d474a
    # link: "Byte av konsult".
    _TypeRule(
        "R11",
        DocumentType.CALL_OFF_GUIDANCE,
        title=_rx(
            r"vägledning|snabbguide|checklista|tips|byte av konsult|avbokningsmatris"
            r"|uppföljning|exempelroller"
        ),
    ),
    # The catalogue of call-off requirements. 1b2527015c7f link: "Kravkatalog".
    _TypeRule("R12", DocumentType.REQUIREMENTS_CATALOGUE, title=_rx(r"kravkatalog")),
    # The requirements of the procurement. 58c50a829044 link: "Kravspecifikation";
    # da5391afde0d link: "Krav på kompetenser".
    _TypeRule(
        "R13",
        DocumentType.REQUIREMENTS_SPECIFICATION,
        title=_rx(r"kravspecifikation|krav på kompetenser"),
    ),
    # Any other numbered annex. 3f646362f5b7 link: "Bilaga 1 Kontaktuppgifter".
    _TypeRule("R14", DocumentType.ANNEX, title=_rx(r"^bilaga\b")),
    # Fallbacks on the heading the link is listed under, for a title no rule knows; step 5
    # notes the file for a person to look at. 19c85c74c3b2 link: "Nuts 2 indelning"
    # under "Avtal". "Upphandling" and "Stöddokument och länkar" type no file in the
    # pilot: every title there is known.
    _TypeRule("F1", DocumentType.ANNEX, category="Avtal"),
    _TypeRule("F2", DocumentType.PROCUREMENT_DOCUMENT, category="Upphandling"),
    _TypeRule("F3", DocumentType.CALL_OFF_GUIDANCE, category="Stöddokument och länkar"),
)
FALLBACK_RULES = frozenset({"F1", "F2", "F3"})  # a type from these is reported as a note


def classify(links: Sequence[CatalogLink]) -> tuple[DocumentType, str]:
    """The type of the file the links point to, and the id of the rule that gave it.

    Each link is typed on its own. If the links of one file got different types,
    the earliest rule in `_TYPE_RULES` would win; in the pilot they never differ.
    A file no rule types is UNKNOWN with the rule `NO_RULE`.
    """
    best: int | None = None
    for link in links:
        for index, rule in enumerate(_TYPE_RULES):
            if rule.matches(link):
                best = index if best is None else min(best, index)
                break
    if best is None:
        return DocumentType.UNKNOWN, NO_RULE
    return _TYPE_RULES[best].document_type, _TYPE_RULES[best].id


def _file_name(url: str) -> str:
    """The last part of the URL's path, decoded and in lower case."""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1]).lower()


# --- Annex number and chapter --------------------------------------------------------------

# Microsoft's annexes number themselves in the link text: 005469cd3990 link: "Bilaga 4.1
# Microsoft Business and Services Agreement".
_ANNEX_IN_TITLE = re.compile(r"^Bilaga (\d+(?:\.\d+)?)", re.IGNORECASE)
# IBM's only in the file name: b6a94d1c1a90 link: "PA Government Attachment", file
# "volymavtal-ibm-bilaga-5.2--pagovernmentattachment_sweden-z125-6501-01-110916.pdf".
_ANNEX_IN_FILE_NAME = re.compile(r"bilaga-(\d+(?:\.\d+)?)")


def annex_number(links: Sequence[CatalogLink]) -> str | None:
    """The annex number the link text or, failing that, the file name gives, e.g. "4.1"."""
    for link in links:
        if match := _ANNEX_IN_TITLE.match(" ".join(link.title.split())):
            return match[1]
    for link in links:
        if match := _ANNEX_IN_FILE_NAME.search(_stem(_file_name(link.url))):
            return match[1]
    return None


def first_chapter(sections: Sequence[Section], outline: OutlineKind) -> int | None:
    """The chapter a document starts at, when it is not 1.

    A chapter printed from a larger TendSign document keeps that document's numbers:
    14aa1cc8ee3d §9 "Ramavtalets Huvuddokument", 0a5491b1398e §6 "Allmänna villkor". The
    chapter is the first number of the first numbered section, so a file whose first
    pages have no text still gets one (e04bad6a0ced starts at §7.16). Only a numbered
    outline counts: a questions log numbers its questions, not chapters.
    """
    if outline is not OutlineKind.NUMBERED:
        return None
    for section in sections:
        if section.number:
            chapter = section.number.split(".")[0]
            if not chapter.isdigit():
                return None
            return int(chapter) if int(chapter) != 1 else None
    return None


# --- Dates ---------------------------------------------------------------------------------

_DATE = r"((?:19|20)\d{2}-\d{2}-\d{2})(?!\d)"
_ANY_DATE = re.compile(r"(?<!\d)" + _DATE)
_LONE_DATE = re.compile(r"^" + _DATE + r"$")

# The cover of a printout from TendSign: the cover word, then the print date alone.
# 76dfb5d1ae1f block 0-1: "Ramavtal", "2024-09-25"; once in one block, f479352f0a55
# block 0: "Ramavtal 2024-09-27". The word tells a signed agreement ("Ramavtal") from the
# version in the procurement ("Upphandlingsdokument") and an invitation ("Inbjudan").
_COVER_WORDS = ("Upphandlingsdokument", "Ramavtal", "Inbjudan")
_COVER_WORD = re.compile(r"^(" + "|".join(_COVER_WORDS) + r")$")
_COVER_LINE = re.compile(r"^(" + "|".join(_COVER_WORDS) + r") " + _DATE + r"$")
_COVER_BLOCKS = 6  # the cover is the first thing on page 1; the pilot has it in blocks 0-1

# Kammarkollegiet's template versions, in the page header of a Word template.
# 3117fd65796c page header: "Kammarkollegiets version 4.0 (2021-09-30)".
_TEMPLATE_VERSION = re.compile(r"Kammarkollegiets version \d+(?:\.\d+)* \(" + _DATE + r"\)")
# The date of a call-off template. 232f65cf161a page header: "Malldatum: 2024-10-11";
# 143bbcc43f2d page header: "Mall för Kontrakt – Programvaror och tjänster | 2026-03-31".
_TEMPLATE_DATE = re.compile(r"Malldatum:\s*" + _DATE + r"|^Mall för [^|]+\|\s*" + _DATE)
# TendSign's page header: when the document was published, and which version it is.
# 124cb5f66cc3 block 36: "23.3-1688-2024 Publicerad 2024-09-05 13:46 Sista anbudsdag:
# 2024-10-14 23:59"; b2bf8baefe48 block 37: "Version 3: publicerad 2024-11-19 11:47".
_PUBLISHED = re.compile(r"(?:Version (\d+): )?[Pp]ublicerad " + _DATE + r" \d{2}:\d{2}")
# The date in Kammarkollegiet's letterhead, a page header: alone, after "Datum" or
# before the case number. 1d58dc2e8387 page 1: "2025-06-05"; 43e45ab02475 page 1:
# "Datum 2026-04-08"; 4f886a784c5d page 2: "Datum Sid 2 (27) 2026-08-28 Dnr
# 23.5-9343-2024 …"; 171a3cacf5fd page 2: "2024-05-01 Dnr 23.5-3718-2024".
_LETTERHEAD = re.compile(
    r"^(?:Datum\s+(?:Sid\s+\d+\s*\(\d+\)\s+)?)?" + _DATE + r"(?:\s+(?:Dnr|Sid)\b|$)"
)
# A date in the file name: "…-annonserat-2024-09-24.pdf", "…_20260331.docx" or, as
# yymmdd, "2_anbudsinbjudan-241119.pdf". Not inside a longer number or a version
# ("version-1.02").
_FILE_NAME_DATES = (
    (re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)"), ""),
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), ""),
    (re.compile(r"(?<![\d.])(\d{2})(\d{2})(\d{2})(?![\d.])"), "20"),
)

_PUBLISHED_WITHIN_BLOCKS = 40  # the start of a document: its cover and first page


def tendsign_cover(document: ParsedDocument) -> str | None:
    """The cover word of a TendSign printout: "Upphandlingsdokument", "Ramavtal" or "Inbjudan"."""
    cover = _cover(document.blocks)
    return cover[0] if cover else None


def _cover(blocks: Sequence[Block]) -> tuple[str, date] | None:
    first = [" ".join(block.text.split()) for block in blocks[:_COVER_BLOCKS]]
    for index, text in enumerate(first):
        if match := _COVER_LINE.match(text):
            day = _iso(match[2])
            if day:
                return match[1], day
        if (word := _COVER_WORD.match(text)) and index + 1 < len(first):
            lone = _LONE_DATE.match(first[index + 1])
            day = _iso(lone[1]) if lone else None
            if day:
                return word[1], day
    return None


def version_date(
    document: ParsedDocument, links: Sequence[CatalogLink], signed_on: Sequence[date]
) -> tuple[date | None, str | None]:
    """When the document was written or last changed, and the source of that date.

    The sources, in order: a template's version ("template_version") or date
    ("template_date"); TendSign's latest "Publicerad" date ("tendsign_published") or
    else its cover date ("tendsign_cover"); the date in the letterhead
    ("letterhead"); the last electronic signature ("signed"); the latest date in a
    file name of its links ("file_name"). (None, None) when there is none.
    `signed_on` are the dates of the document's SIGNED_ON facts.
    """
    texts = [" ".join(block.text.split()) for block in document.blocks]
    for rule, pattern in (
        ("template_version", _TEMPLATE_VERSION),
        ("template_date", _TEMPLATE_DATE),
    ):
        for text in texts:
            if (match := pattern.search(text)) and (day := _iso(_first_group(match))):
                return day, rule
    published = [day for text in texts for _, day in _published(text)]
    if published:
        return max(published), "tendsign_published"
    if cover := _cover(document.blocks):
        return cover[1], "tendsign_cover"
    for block, text in zip(document.blocks, texts, strict=True):
        match = _LETTERHEAD.match(text) if block.kind is BlockKind.PAGE_HEADER else None
        if match and (day := _iso(match[1])):
            return day, "letterhead"
    if signed_on:
        return max(signed_on), "signed"
    named = [day for link in links for day in _file_name_dates(link.url)]
    if named:
        return max(named), "file_name"
    return None, None


def published_on(document: ParsedDocument) -> date | None:
    """When the document was first published, to tell which questions can be about it.

    TendSign's "Publicerad" date when the document has one, else the first date among
    its first blocks (a letterhead or a cover). The cover of a TendSign printout is the
    print date, which can come a month after publication: a0924d85e62c has the cover
    "Inbjudan 2023-10-03" and "Publicerad 2023-09-04 13:44" on every page.

    None when the document says it is a later version ("Version 3: publicerad
    2024-11-19 11:47", b2bf8baefe48): earlier versions were out before that date, so
    questions about them would wrongly look older than the document.
    """
    texts = [" ".join(block.text.split()) for block in document.blocks]
    published = [found for text in texts for found in _published(text)]
    if any(version > 1 for version, _ in published):
        return None
    if published:
        return min(day for _, day in published)
    for text in texts[:_PUBLISHED_WITHIN_BLOCKS]:
        for match in _ANY_DATE.finditer(text):
            if day := _iso(match[1]):
                return day
    return None


def _published(text: str) -> list[tuple[int, date]]:
    """The (version, date) of each "Publicerad" in a text; version 1 when none is given."""
    found = []
    for match in _PUBLISHED.finditer(text):
        day = _iso(match[2])
        if day:
            found.append((int(match[1]) if match[1] else 1, day))
    return found


def _file_name_dates(url: str) -> list[date]:
    name = _stem(_file_name(url))
    found = []
    for pattern, century in _FILE_NAME_DATES:
        for match in pattern.finditer(name):
            day = _iso(f"{century}{match[1]}-{match[2]}-{match[3]}")
            if day:
                found.append(day)
    return found


def _stem(file_name: str) -> str:
    return file_name.rsplit(".", 1)[0] if "." in file_name else file_name


def _first_group(match: re.Match[str]) -> str:
    return next(group for group in match.groups() if group)


def _iso(text: str) -> date | None:
    """The date an ISO string names, or None for an impossible one ("2023-02-30")."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
