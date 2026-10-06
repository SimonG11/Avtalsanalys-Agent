"""The names the documents use for each other, and the link titles they stand for.

What:
    `DOCUMENT_NAMES` maps each name a document uses for another document
    ("Allmänna villkor", "Kravkatalogen", "Personuppgiftsbiträdesavtalet") to
    a pattern for its inflected forms. `ALIASES` lists the link titles on
    avropa.se a name stands for where they differ: the text says "bilaga
    Priser", the page links "Prisbilaga" or "Timpriser". `canonical_name`
    turns a mention into its name, and `normalise_title` makes a link title
    comparable with it.

Why:
    Finding references (`reference_patterns.py`) and resolving them
    (`reference_resolver.py`) need the same list, so a name added for one is
    known to the other. The names are those the M4 survey found in the
    sections of the 207 pilot files, with the link titles they point to.

How:
    Plain data and two small functions. A pattern allows the definite and
    plural forms; a genitive -s is allowed after any name ("Huvuddokumentets
    avsnitt"). Titles are compared in lower case, without soft hyphens and
    without the prefix "Utkast till", with two typos in link titles fixed.
"""

import re

# Name -> the pattern for its forms. Ordered from the most specific name to the
# most general, so "Ramavtalets huvuddokument" is found before "Huvuddokument".
DOCUMENT_NAMES: dict[str, str] = {
    "ramavtalets huvuddokument": r"[Rr]amavtalets\s+[Hh]uvuddokument(?:et)?",
    "huvuddokument": r"[Hh]uvuddokument(?:et)?",
    "volymavtalets huvudavtal": r"[Vv]olymavtalets\s+huvudavtal(?:et)?",
    "allmänna villkor": r"[Aa]llmänna\s+villkor(?:en)?",
    "kravspecifikation": r"[Kk]ravspecifikation(?:en|er|erna)?",
    "kravkatalog": r"[Kk]ravkatalog(?:en|er|erna)?",
    "prisbilaga": r"[Pp]risbilag(?:a|an|or|orna)",
    "upphandlingsdokument": r"[Uu]pphandlingsdokument(?:et|en)?",
    "anbudsinbjudan": r"[Aa]nbudsinbjudan",
    "ansökningsinbjudan": r"[Aa]nsökningsinbjudan",
    "personuppgiftsbiträdesavtal": r"[Pp]ersonuppgifts?biträdesavtal(?:et|en)?",
    "säkerhetsskyddsavtal": r"[Ss]äkerhetsskyddsavtal(?:et|en)?",
    "datadelningsavtal": r"[Dd]atadelningsavtal(?:et|en)?",
    "avropsmall": r"[Aa]vropsmall(?:en|ar|arna)?",
    "kontraktsmall": r"[Kk]ontraktsmall(?:en|ar|arna)?",
    "avropsrutin": r"[Aa]vropsrutin(?:en)?",
    "avropsvägledning": r"[Aa]vropsvägledning(?:en)?",
    "vägledning": r"[Vv]ägledning(?:en)?",
    "krav på kompetenser": r"[Kk]rav på kompetenser",
    "frågor och svar": r"[Ff]rågor och svar",
    "exempelroller": r"[Ee]xempelroller(?:na)?",
    "snabbguide": r"[Ss]nabbguide(?:n)?",
    "redovisning av hållbarhetskrav": r"[Rr]edovisning av hållbarhetskrav(?:en)?",
    "redovisning av informationssäkerhet": r"[Rr]edovisning av informationssäkerhet(?:skrav)?",
    "avbokningsmatris": r"[Aa]vbokningsmatris(?:en)?",
    "avropsblankett": r"[Aa]vropsblankett(?:en)?",
    "finansiella villkor": r"[Ff]inansiella villkor(?:en)?",
    "kvalificering av sökande": r"[Kk]valificering av sökande",
    "nuts 2 indelning": r"[Nn]uts\s*2(?:-| )?indelning(?:en)?",
    "timpriser": r"[Tt]impriser",
    "checklista": r"[Cc]hecklista(?:n)?",
    "microsoft business and services agreement": (
        r"Microsoft Business and Services[- ](?:Agreement|Avtal(?:et)?)|\bMBSA\b"
    ),
    "produktvillkor": r"[Pp]roduktvillkor(?:en)?|Product Terms",
    "enterprise-avtal": r"Enterprise[- ]?(?:avtal(?:et)?|Agreement)",
    "select plus-avtal": r"Select Plus[- ]?(?:avtal(?:et)?|Agreement)",
    "dataskyddstillägg": r"[Dd]ataskyddstillägg(?:et)?|Data Protection Addendum|\bDPA\b",
    "avtalsstruktur": r"[Aa]vtalsstruktur(?:en)?",
    "passport advantage": r"(?:International )?Passport Advantage(?: Agreement| Avtal(?:et)?)?",
    "ipla": r"\bIPLA\b",
}

# Name -> the link titles (normalised, see `normalise_title`) it can stand for, when
# they do not start with the name itself. The text says "bilaga Priser", the page
# links "Prisbilaga - sammanställning Delområde 1"; the Microsoft annexes are linked
# with their number first ("Bilaga 4.1 Microsoft Business and Services Agreement").
ALIASES: dict[str, tuple[str, ...]] = {
    "huvuddokument": ("ramavtalets huvuddokument", "volymavtalets huvudavtal"),
    "prisbilaga": ("prisbilaga", "timpriser"),
    "priser": ("prisbilaga", "timpriser"),
    "avropsvägledning": ("vägledning",),
    "upphandlingsdokument": ("upphandlingsdokument", "anbudsinbjudan", "ansökningsinbjudan"),
    "microsoft business and services agreement": ("bilaga 4.1 microsoft business",),
    "produktvillkor": ("bilaga 9 produktvillkor",),
    "enterprise-avtal": ("bilaga 5.1 enterprise-avtal",),
    "select plus-avtal": ("bilaga 6.1 select plus-avtal",),
    "dataskyddstillägg": ("bilaga 10.1 villkor för dataskyddstillägg",),
    "avtalsstruktur": ("bilaga 11 avtalsstruktur", "avtalstruktur"),
    "passport advantage": ("international passport advantage agreement",),
}

_LETTER = r"\wåäöÅÄÖ"
# Any name, longest pattern first so the longer of two overlapping names wins.
ANY_DOCUMENT_NAME = re.compile(
    rf"(?<![{_LETTER}-])(?:"
    + "|".join(
        f"(?:{pattern})" for pattern in sorted(DOCUMENT_NAMES.values(), key=len, reverse=True)
    )
    + rf")s?(?![{_LETTER}])"
)
_NAME_PATTERNS = {name: re.compile(rf"(?:{pattern})s?") for name, pattern in DOCUMENT_NAMES.items()}

# Typos in link titles on avropa.se (2026-10-05): "Personuppgiftbiträdesavtal",
# "Utkast till Säkerhetssyddsavtal (Nivå 1)".
_TITLE_TYPOS = {
    "personuppgiftbiträdesavtal": "personuppgiftsbiträdesavtal",
    "säkerhetssyddsavtal": "säkerhetsskyddsavtal",
}
_DRAFT_PREFIX = re.compile(r"^utkast till\s+")
_LINE_BREAK_HYPHEN = re.compile(r"(?<=[a-zåäö])-\s?(?=[a-zåäö])")


def canonical_name(mention: str) -> str | None:
    """The name in `DOCUMENT_NAMES` that a whole mention is a form of, or None."""
    text = " ".join(mention.split())
    for name, pattern in _NAME_PATTERNS.items():
        if pattern.fullmatch(text):
            return name
    return None


def normalise_title(title: str, *, join_hyphenated: bool = False) -> str:
    """A link title or mention in lower case, without "Utkast till" and soft hyphens.

    With `join_hyphenated`, a hyphen between two letters is removed as well:
    a word broken over two lines ("personuppgiftsbiträdes-avtal").
    """
    text = title.lower().replace("­", "")
    if join_hyphenated:
        text = _LINE_BREAK_HYPHEN.sub("", text)
    for typo, right in _TITLE_TYPOS.items():
        text = text.replace(typo, right)
    text = " ".join(text.split())
    return _DRAFT_PREFIX.sub("", text)
