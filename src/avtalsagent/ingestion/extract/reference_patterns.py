"""Ingestion step 4: the places where a section points at another section or document.

What:
    `find_mentions` reads the text of each section and returns a
    `ReferenceMention` for every place that points at a section ("punkt
    6.21.9", "enligt 10.4", "avsnitt Avtalsbrott och påföljder"), an annex
    ("bilaga 3", "bilaga Priser") or a document ("Allmänna villkor",
    "Kravkatalogen"), and in a questions-and-answers log at another question
    ("fråga 17"). Laws, regulations and standards ("17 kap. 17 § LOU", "art.
    32 GDPR", "ISO 27001") are mentions too, with status EXTERNAL: they are
    counted, never looked up among the files. Which file and section a mention
    points at is decided later, by `reference_resolver.py`.

Why:
    The agent must be able to follow "enligt punkt 6.21" to the text it points
    at, and the ingestion report measures how many references resolve (M4:
    "andelen upplösta hänvisningar är mätt"). The forms below are the ones
    found in the 13,175 sections of the 207 pilot files. Most references name
    a section by its title, not its number, so titles are read as well as
    numbers. A mention keeps its offsets and the rule that found it, so each
    one can be checked by hand.

How:
    Each section is read after its heading line, which only names the section
    itself ("Publik fråga 47", "Bilaga 5 Tillägg och förtydliganden ..."). The
    rules run in this order, and text that one rule has taken is not read
    again by a later one:
    1. LAW: laws, regulations and standards, overlapping matches merged into
       one span. A section or annex number inside such a span, or right before
       it ("kapitel 14 i LOU"), is part of the law and not a mention.
    2. PH: template placeholders, status PLACEHOLDER ("Bilaga nr x").
    3. R1 / R1x: section numbers after a keyword ("punkterna 6.4-6.9") or
       after "enligt", "se", "i" ... ("enligt 10.4"), one mention per number.
       A document named right after or right before the numbers ("p. 6.19.7 i
       Allmänna villkor", "Allmänna villkor punkt 6.17") is the mention's
       `document_name` (rule R1x) and not a mention of its own. A whole number
       after "punkt" or "p." is status LIST_ITEM when it numbers an item of a
       list: a line of the section starts with it, "ovan" or "nedan" follows
       it, or a section is named next to it by its number or title ("6.16.3,
       punkt 2", "punkterna 1-3 i avsnitt 7.17.3"). In a questions-and-answers
       log it always is. A number that counts ("7 gånger", "1 st") ends a list.
    4. R4: a section title after a keyword ("enligt avsnitt Avtalsbrott och
       påföljder"). Where a title ends is not known before it is looked up, so
       the key runs to the end of the sentence or to the next reference, and
       the resolver looks for the longest heading the key starts with.
    5. R3: "bilaga N", one mention per number.
    6. R5: "bilaga <Namn>", with the key ending like a title's. When the name
       starts with a document's name ("bilaga Allmänna villkor") that name is
       the mention's `document_name`.
    7. R2: a named document from `document_names.DOCUMENT_NAMES`, with the
       `topic` it is about when one follows ("Allmänna villkor gällande
       viten"). "detta/dessa <Dok>" and "Med <Dok> avses detta dokument" are
       status SELF.
    8. RQ: "fråga N", only in a questions-and-answers log.
    `replaces` is set on a mention in a sentence that changes its target, in an
    AMENDMENT, or in a questions-and-answers log where Kammarkollegiet writes:
    after "Publikt svar", or in a "Publikt informationsmeddelande", which has no
    question. The sentence has a word of change ("ersätter", "utgår i sin
    helhet", a sentence that ends in "utgår", "strykas", "justerar", "gör
    följande tillägg", "texten som gäller", or "Tillägg till" first in a
    sentence) and no negation ("ändrar inte", "gäller utan ändringar"); "ändras"
    after "om" is a condition, and "tas bort" after "kan" a possibility. A full
    stop before a small letter or a quote ends a number ("Punkt 2a. i
    Registreringen ersätts"), not the sentence. In an answer or a message the next
    sentence counts too, up to the next mention: Kammarkollegiet names the
    section, then says what changes. Of the 2,978 mentions in the pilot's
    amendments and logs (laws left out) 60 are marked, and each was read by
    hand.

    `key` is what the resolver looks up: a number in lower case ("6.21.9",
    "3a"), a title or annex name with its spaces normalised, or a document's
    name in `DOCUMENT_NAMES` ("allmänna villkor"). `raw` is the text from the
    keyword to the end of the key, so in a list only the first number carries
    the keyword ("punkterna 5.15.2.1", then "5.15.2.2").

    Not mentions, since they point at no other section or document: the
    section itself ("detta avsnitt"), directions ("enligt ovan"), the
    agreement as a whole ("enligt Ramavtalet"), a document name as a common
    noun ("mer vägledning") or as the label on a TendSign cover
    ("Upphandlingsdokument 2022-06-14").
    Not built, since each covers fewer than 50 references in the pilot:
    "<titel> i <Dok>" (R4x, 39: the document after a title is not read, the
    title is looked up like any other) and "kapitel <Dok>" where the document
    has no such chapter (46: it stays a title mention).
"""

import re
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from avtalsagent.domain.extracted import (
    DocumentType,
    ReferenceKind,
    ReferenceMention,
    ReferenceStatus,
)
from avtalsagent.domain.parsed import Section
from avtalsagent.ingestion.extract.document_names import ANY_DOCUMENT_NAME, canonical_name

# --- 1. Laws, regulations and standards -----------------------------------------------
# Ordinal words of "första stycket", "andra meningen".
_ORDINAL = r"(?:första|andra|tredje|fjärde|femte|sjätte|sjunde|åttonde|nionde|tionde|sista)"
# Each pattern is one kind of law reference; a match of any of them is a law span.
# 0692da436391 §1.2.2: "enligt 1 kap. 18 § och 22 § lagen (2016:1145) om offentlig
# upphandling (LOU)" gives two spans, "1 kap. 18 § och 22 § lagen (2016:1145)" (four matches
# merged) and "LOU".
_LAWS = (
    # A Swedish statute by chapter: "17 kap. 17 § punkten 3", "1 kap. 18 § och 22 §".
    re.compile(
        rf"\b\d+\s*kap\.?(?:\s*\d+\s*[a-z]?\s*§+(?:\s*(?:och|,|-|–)\s*\d+\s*[a-z]?\s*§*)*)?"
        rf"(?:\s*{_ORDINAL}\s+stycket)?(?:\s*\d+\s*p(?:\.|unkten)?)?"
        rf"(?:\s*{_ORDINAL}\s+meningen)?",
        re.IGNORECASE,
    ),
    # ...or by paragraph alone: "22 §", "4 § andra stycket", "17 § punkten 3".
    re.compile(
        rf"\b\d+\s*[a-z]?\s*§+(?:\s*{_ORDINAL}\s+stycket)?(?:\s*(?:\d+\s*p\.?|punkt(?:en)?\s*\d+))?"
    ),
    # An SFS number: "(2016:1145)", "SFS 2018:218".
    re.compile(r"\((?:SFS\s*)?\d{4}:\d{1,4}\)|\bSFS\s*\d{4}:\d{1,4}"),
    # An EU act: "förordning (EU) 2016/679", "direktiv 2014/24/EU".
    re.compile(
        r"\b(?:förordning|direktiv|regulation|directive|beslut)(?:en|et)?\s*"
        r"\((?:EU|EG|EEG|EC|EEC)\)\s*(?:nr\s*)?\d+/\d+(?:/(?:EU|EG|EEG|EC))?"
        r"|\b\d{4}/\d{2,4}/(?:EU|EG|EEG|EC)",
        re.IGNORECASE,
    ),
    # An article: 0486216326ec §8.1: "skyldigheterna enligt art. 32-36 Dataskyddsförordningen".
    re.compile(
        r"\b(?:artikel|artiklarna|artiklar|art\.|article|articles)\s*\d+(?:\.\d+)*"
        r"(?:\s*(?:\(\d+\)|\.\d+|[a-z]\)|led\s+[a-z]))*(?:\s*(?:-|–|och|,)\s*\d+(?:\.\d+)*)*",
        re.IGNORECASE,
    ),
    # A law by its name: "LOU", "GDPR", "säkerhetsskyddslagen", "lagen (2016:1145)".
    re.compile(
        r"\b(?:LOU|LUF|LUK|LOV|OSL|GDPR|LEK|PUL|NIS2?|DORA|AI-förordningen"
        r"|[Dd]ataskyddsförordning(?:en)?|[Ss]äkerhetsskyddslag(?:en)?"
        r"|[Ss]äkerhetsskyddsförordning(?:en)?|[Oo]ffentlighets- och sekretesslag(?:en)?"
        r"|[Dd]ataskyddslag(?:en)?|[Aa]rbetsmiljölag(?:en)?|[Ff]örvaltningslag(?:en)?"
        r"|[Rr]äntelag(?:en)?|[Uu]pphovsrättslag(?:en)?|[Kk]öplag(?:en)?|[Aa]vtalslag(?:en)?"
        r"|[Ll]ag(?:en)? om offentlig upphandling)\b|\b[Ll]agen\s*\(\d{4}:\d+\)"
    ),
    # A standard: "ISO/IEC 27001", "EN 301 549", "WCAG 2.1", "TLS 1.2", "SNI 2007".
    re.compile(
        r"\b(?:SS-)?(?:ISO|IEC|EN|SS|SIS)(?:/IEC)?\s*\d{3,5}(?:[-:]\d+)*(?:\s*:\s*\d{4})?"
        r"|\bWCAG\s*\d(?:\.\d)?|\bTLS\s*\d\.\d|\bSNI\s*\d{4}|\bITIL\s*\d?|\bPRINCE2|\bNUTS\s*\d"
        r"|\bAB\s*04|\bABK\s*09|\bIT\s*Avtal\s*\d{4}|\bCPV\b",
        re.IGNORECASE,
    ),
)
# Matches at most this far apart are one span: "lagen (2016:1145) om offentlig upphandling".
_LAW_GAP = 3
# A number right before a law span belongs to it: 20ddb9ebf9cc §42: "Kapitel 14 i LOU
# reglerar kvalificeringskrav".
_BEFORE_LAW = re.compile(r"\s*,?\s*(?:(?:i|enligt|av)\s+)?")

# --- 2. Placeholders -------------------------------------------------------------------
# 232f65cf161a §10: "Bilaga nr x Säkerhetsskyddsavtal"; 50edddbad6c7 §354: "avsnitt X.1.1,
# X.1.2 och X.1.3"; "punkt [X]", "bilaga ...". Unfilled fields of a template.
_PLACEHOLDER = re.compile(
    r"(?<!\w)(?:(?P<annex_nr>(?i:bilag(?:a|or))\s+nr\.?\s*)(?P<annex_x>x)\b"
    r"|(?P<keyword>(?i:punkt|avsnitt|bilaga|kapitel))\s*"
    r"(?P<field>\[[^\]]{0,30}\]|[Xx](?:\.\d|\.[Xx])+|[Xx]\b|\.\.\.|…|_{2,}))"
)

# --- 3. Section numbers ----------------------------------------------------------------
# A section number: "6.21.9", "3a"; a dot after it ends a sentence ("punkt 6.").
_NUMBER = r"\d{1,3}(?:\.\d{1,3})*(?:\.(?=\s))?[a-z]?(?!\w)"
_ONE_NUMBER = re.compile(r"\d{1,3}(?:\.\d{1,3})*[a-z]?")
# Between the numbers of a list or range: "5.15.2.1, 5.15.2.2 och 5.15.2.3", "6.4-6.9".
_LIST_SEPARATOR = r"\s*(?:,|och|samt|eller|respektive|resp\.|-|–|till|\+|/|&)\s*"
# A number that counts something ends the list: bdf58b81d100 §88: "under punkt 1 och 2, 7
# gånger under en 12 månaders period"; 50edddbad6c7 §322: "på punkt 10.18.1, 1 st".
_NOT_A_COUNT = r"(?!\s+(?:st|gång|gånger)\b)"
_NUMBER_LIST = rf"{_NUMBER}(?:{_LIST_SEPARATOR}{_NUMBER}{_NOT_A_COUNT})*"
_SECTION_KEYWORD = (
    r"punkt(?:en|erna|er)?|p\.|pp\.|avsnitt(?:et|en)?|kapit(?:el|let|len|lena)"
    r"|underavsnitt(?:et)?|sections?|clauses?"
)
# R1. 0486216326ec §6.3: "vidta de säkerhetsåtgärder som framgår nedan, punkterna 6.4-6.9."
# A number followed by a table cell is a version table, not a reference: 4f886a784c5d §pos0:
# "Versioner | Publicerat datum | Uppdaterat avsnitt ⏎ 1.0 | 2024-09-27 | Första versionen".
_KEYWORD_NUMBERS = re.compile(
    rf"(?<!\w)(?P<keyword>(?i:{_SECTION_KEYWORD}))\s*(?P<numbers>{_NUMBER_LIST})(?![.\d]*\s*\|)"
)
# "0 p. 0 p.": "p." after a number is poäng (points), not punkt (c59dbfeeb576 §3.3.2.2).
_POINTS = re.compile(r"\d\s*$")
# R1, bare number. 0486216326ec §4.2: "I samband med revision enligt 10.4 svarar den
# personuppgiftsansvarige för kostnaden"; 20c753d88340 §70: "Kammarkollegiet justerar 4.1.2,
# punkt 9, enligt följande". Only numbers with a dot: "enligt 4" is not a section. 524 of
# the 567 in the pilot are in questions-and-answers logs.
_BARE_NUMBERS = re.compile(
    r"(?<![\w.])(?P<keyword>enligt|se|jfr|jämför|under|i|av|från|justerar)\s+"
    rf"(?P<numbers>\d{{1,3}}\.\d{{1,3}}(?:\.\d{{1,3}})*"
    rf"(?:(?:{_LIST_SEPARATOR})\d{{1,3}}(?:\.\d{{1,3}})+)*)"
    r"(?![\w%]|\.\d|\s*\|)",
    re.IGNORECASE,
)
# R1x. A document named right after the numbers: bdf58b81d100 §42: "p. 6.19.7 i Allmänna
# villkor"; d54ed0900be5 §pos1: "i punkt 1 i Microsoft Business and Services Agreement";
# 50edddbad6c7 §255: "avsnitt 14.4 i mallen för personuppgiftsbiträdesavtal".
_DOCUMENT_AFTER = re.compile(
    r"\s*,?\s*(?:i|enligt|av|under|till|inom)\s+(?:(?:den|det|de|gällande|aktuella?)\s+)?"
    r"(?:[Bb]ilag(?:a|an)\s+)?[\"”“]?(?:(?:[Uu]tkast till|[Mm]all(?:en)? för)\s+)?"
)
# ...or right before them: 49f36699a469 §2.7: "I Allmänna villkor punkt 6.17 finns en
# valutaklausul"; bdf58b81d100 §57: "Ramavtalets Huvuddokument, punkt 5.9.11"; c58d93eb5c85
# §pos11: "beskrivs i bilaga allmänna villkor under avsnitt 6.15".
# Not across a line break: "Datadelningsavtal ⏎ Enligt 13.2" is a heading and a new paragraph.
_DOCUMENT_BEFORE = re.compile(
    rf"(?P<document>{ANY_DOCUMENT_NAME.pattern})[^\S\n]*[,:]?[^\S\n]*(?:(?:i|under)[^\S\n]+)?$"
)
_DOCUMENT_BEFORE_CHARS = 80
# The list-item filter applies to a whole number after these keywords.
_ITEM_KEYWORDS = ("punkt", "punkten", "punkterna", "punkter", "p.", "pp.")
# A list item: 997bef854b06 §1.16.3: "till följd av uppsägning enligt punkterna 1-6 i detta
# avsnitt", where the section numbers its own items "1." to "6.".
_ITEM_AFTER = re.compile(
    r"\s*(?:-\s*\d+\s*)?(?:ovan|nedan|i detta avsnitt|i denna punkt|i föregående"
    r"|i första stycket|i andra stycket)"
)
# ...or an item of the section named just after, by a keyword or a section number:
# 087f9c5a2f56 §49: "punkt 2 i 6.16.3"; 39d8c1efe373 §117: "punkterna 1-3 i avsnitt 7.17.3".
_SECTION_AFTER_ITEM = re.compile(
    r"\s+(?:i|under)\s+(?:(?i:avsnitt(?:et)?|kapit(?:el|let)|punkt(?:en)?|p\.)\s*"
    r"(?:\d|[\"”“]?[A-ZÅÄÖ])|\d{1,3}(?:\.\d{1,3})+)"
)
# ...or of the section named just before, by its title: 34d71a7e4da0 §8.16.4: "enligt
# avsnitt Kammarkollegiets uppsägningsrätt punkten 1."
_SECTION_BEFORE_ITEM = re.compile(r"(?i:avsnitt|punkt|kapitel)\s+[^.;:\n]{2,90}?[\s,]+$")
# ...or by its number, with or without a title after it, in the same clause: e083c368b16a
# §43: "6.16.3, punkt 2."; bdf58b81d100 §204: '"5.15.3 Kammarkollegiets uppsägningsrätt" punkt
# 2'; 20c753d88340 §38: "Enligt 4.1.4.1 under punkt 4".
_NUMBER_BEFORE_ITEM = re.compile(
    r"(?<![\w.])\d{1,3}(?:\.\d{1,3})+(?![\w-])[^.;:|\n]{0,90}?[\s,:\"”]+$"
)
_SECTION_BEFORE_ITEM_CHARS = 100

# --- 4. Section titles -----------------------------------------------------------------
# R4. 4f5a5c8becf6 §1.12.3: "kan det resultera i vite och andra påföljder enligt avsnitt
# Avtalsbrott och påföljder."; quoted: 386122e82b7b §pos1: 'underrubriken "Tillämplig lag"'.
# A title starts with a capital letter, which keeps "punkten ovan" out.
_TITLE = re.compile(
    r"(?<!\w)(?P<keyword>(?i:avsnitt(?:et|en)?|kapit(?:el|let|len)|punkt(?:en)?"
    r"|underavsnitt(?:et)?|rubrik(?:en)?|underrubrik(?:en)?))\s+(?P<quote>[\"”“'])?"
    r"(?=[A-ZÅÄÖ])"
)
# --- 5. Annex numbers ------------------------------------------------------------------
# R3. 171a3cacf5fd §10.1: "Tillägg och förtydliganden till bilagorna 5.1-5.4".
_ANNEX_NUMBERS = re.compile(
    r"(?<!\w)(?P<keyword>[Bb]ilag(?:a|an|orna|or))\s+(?:nr\.?\s*)?"
    r"(?P<numbers>\d{1,2}[a-z]?(?:\.\d{1,2})*(?:\s*(?:-|–|och|,)\s*\d{1,2}[a-z]?(?:\.\d{1,2})*)?)"
    r"(?!\w)"
)
_ONE_ANNEX_NUMBER = re.compile(r"\d{1,2}[a-z]?(?:\.\d{1,2})*")
# "Kontraktet med bilagor 6. Kontraktet ...": after plural "bilagor" a number followed by
# a capital is the next item of a numbered list (116 such hits in the pilot).
_LIST_NUMBER_AFTER = re.compile(r"\.\s+[A-ZÅÄÖ]")
# --- 6. Annex names --------------------------------------------------------------------
# R5. 34d71a7e4da0 §8.10.1: "Ramavtalsleverantörens priser för Upphandlingsföremålet anges i
# bilaga Priser." A Roman numeral is an annex of an EU act ("bilaga XI till direktiv
# 2014/24/EU"), counted by its law span.
_ANNEX_NAME = re.compile(
    r"(?<!\w)(?P<keyword>[Bb]ilag(?:a|an))\s+(?P<quote>[\"”“])?(?=[A-ZÅÄÖ])(?![IVXL]+\b)"
)
# A name may start with "Utkast till": "bilaga Utkast till Säkerhetsskyddsavtal".
_DRAFT = re.compile(r"(?:[Uu]tkast till|[Mm]all(?:en)? för)\s+")

# --- 7. Named documents ----------------------------------------------------------------
# Written in lower case these names are common nouns, not references: 1d58dc2e8387 §4: "För
# mer vägledning, se på Avropa"; "som en checklista", "frågor och svar" (394 in the pilot).
_COMMON_NOUNS = frozenset(
    (
        "vägledning",
        "checklista",
        "frågor och svar",
        "kravspecifikation",
        "timpriser",
        "avtalsstruktur",
        "avropsrutin",
        "exempelroller",
        "avropsblankett",
    )
)
# "dessa Allmänna villkor", "detta Personuppgiftsbiträdesavtal": the document itself.
_THIS_BEFORE = re.compile(r"\b(?:[Dd]etta|[Dd]enna|[Dd]essa|[Dd]enne)\s+$")
# A definition row: 0692da436391 §1.3: "Med Huvuddokument avses detta dokument som innehåller
# krav och villkor" (53 such rows; "Med Allmänna villkor avses dessa villkor", 22).
_DEFINED_AS_THIS_BEFORE = re.compile(r"\b[Mm]ed\s+$")
_DEFINED_AS_THIS_AFTER = re.compile(r"\s+avses\s+(?:detta|denna|dessa)\b")
# The cover of a TendSign printout starts with the kind of document and a date, c8aecf2b554c
# §pos0: "Upphandlingsdokument ⏎ 2022-06-14 ⏎ Upphandlande organisation" (36 files). It is a
# label, not a reference.
_COVER_DATE = re.compile(r"\s*\d{4}-\d{2}-\d{2}\b")
# A topic instead of a section: 0692da436391 §1.10.2: "villkor i Allmänna villkor gällande
# viten, skadestånd och ersättningar som är kostnadsdrivande". The topic ends at the
# sentence or before a relative clause. "om" is left out: after a document name it mostly
# means "if" ("Säkerhetsskyddsavtal om inte något annat avtalas").
_TOPIC = re.compile(r"\s*,?\s*(?:gällande|avseende|rörande)\s+(?=[a-zåäö])")
_RELATIVE_CLAUSE = re.compile(r",?\s+(?:som|vilket|vilka|där)\s")
_TOPIC_MAX_CHARS = 100

# --- 8. Questions ----------------------------------------------------------------------
# RQ. 39d8c1efe373 §7: "Följdfråga på fråga 1 avseende 5.6.3.1 Kvalitetsledningssystem".
_QUESTION = re.compile(
    r"(?<!\w)(?i:fråga|frågorna|frågan)\s+(?:nr\.?\s*)?(?P<number>\d{1,4})(?![\d.])"
)

# --- Replacement -----------------------------------------------------------------------
# A word of change. 39d8c1efe373 §pos3: "Kammarkollegiet ersätter avsnitt 7.19.1.3 till
# följande skrivning"; 386122e82b7b §pos1: 'underrubriken "Tillämplig lag" i Campus- och
# School-avtal ändras härmed enligt följande', "Punkt 8.a skall i sin helhet strykas och
# ersättas med följande ordalydelse"; 7a765d649e25 §9: "Rättelse. Texten som gäller är
# följande för punkt 3.2:"; 20c753d88340 §15: "Kammarkollegiet gör följande tillägg till
# andra stycket i 4.2.4:", §60: "Kammarkollegiet gör följande justering i avsnitt 4.1.2",
# §70: "Kammarkollegiet justerar 4.1.2, punkt 9"; bdf58b81d100 §120: "Tredje stycket i
# avsnitt 6.6 utgår."; 20ddb9ebf9cc §28: "Tillägg till avsnitt 5.15 andra stycket:". "utgår"
# inside a sentence is mostly a penalty that is due ("utgår vite med 2 500 SEK").
_REPLACEMENT = re.compile(
    r"\b(?:ersätter|ersätts(?:\s+(?:av|med))?|upphör att gälla"
    r"|ändras(?:\s+(?:till|härmed|enligt följande))|får följande lydelse|ny lydelse|stryks"
    r"|strykas|ersättas med|utgår(?=\s+(?:och|ur|i sin helhet|helt)|\s*(?:[.;]|$))"
    r"|tas bort|läggs till|ska läggas till|skall läggas till|ändrar|justerar|justeras till"
    r"|korrigeras|rättas|texten som gäller|gör följande (?:tillägg|ändring|justering)(?:ar)?"
    r"|tillägg till)\b",
    re.IGNORECASE,
)
# A negation means nothing changes: 39d8c1efe373 §51: "Nej, Kammarkollegiet ändrar inte
# avtalsvillkoret i 7.19.11 Avropsberättigads uppsägningsrätt."; d54ed0900be5 §pos1:
# "Definitionen av Koncernbolag i punkt 1 i Microsoft Business and Services Agreement gäller
# utan ändringar", §pos6: "Avseende Microsoft gäller punkt oförändrad."
_NO_CHANGE = re.compile(
    r"\b(?:(?:ändrar|ändras|justerar|justeras) inte|inte (?:ändras|ändrar|justeras|justerar)"
    r"|kvarstår(?: därmed)? oförändrad|gäller(?: \w+)? oförändrad|ingen ändring"
    r"|utan ändringar?)\b",
    re.IGNORECASE,
)
# "ändrar", "ändras" or "justerar" after "om", "när" or "oavsett om" is a condition:
# 3a316e27aadf §135: "I avsnitt 6.21.1 andra stycket framgår att om pris ändras till följd av
# begärd ändring"; 50edddbad6c7 §433: "Om avropsberättigad använder egna mallar ... eller
# ändrar de förkryssade alternativen i personuppgiftsbiträdesavtalet gäller inte angivna
# takpriser."
_CONDITION = re.compile(r"\b(?:[Oo]m|[Nn]är|[Oo]avsett om)\b")
# "tas bort" or "läggs till" at most three words after "kan", "får", "kunna" or "inte" is
# what may be done, and before "genom" how it is done; neither is a change. 50edddbad6c7
# §162: "Av avsnitt 10.14.1 framgår också att ett åberopat företag inte kan bytas ut eller
# tas bort utan godkännande"; 20c753d88340 §63: "Underleverantör läggs till genom att
# Underleverantör signerar berörd bilaga Konsultkompetens."
_MAY_BEFORE = re.compile(r"\b(?:kan|får|kunna|inte)\b(?:\s+\S+){0,3}\s+$")
_HOW_AFTER = re.compile(r" genom")
_MAY_OR_HOW = ("tas bort", "läggs till")
# The end of a sentence, a line or a bullet right before a word: "Tillägg till" starts there.
_STARTS_SENTENCE = re.compile(r"(?:[.!?;:]\s+|\n\s*|[·•]\s*)$")
# For `replaces`, a sentence ends as by `_SENTENCE_END`, except that a full stop before a
# small letter or a quote ends a number or a list letter, not the sentence: 5aab54c5a4b1
# §pos0: "A. Punkt 2a. i Registreringen ersätts med följande", 'B. Punkten 3.b. "Exemplar för
# utbildning/utvärdering och säkerhetskopiering" i Avtalet ändras härmed', §4: 'F. Punkten
# "Prissättning" i Registrering 3b. ersätts i sin helhet med följande'.
_CHANGE_SENTENCE_END = re.compile(
    r"\.(?=\s+[^a-zåäö\s\"”“']|\s*$)|[!?;:](?=\s|$)|\s*[|·•]|\n(?=\s*[·•\-–A-ZÅÄÖ0-9(])"
)
# Where Kammarkollegiet's answer starts in a TendSign questions log. A message that
# Kammarkollegiet posts in the log has no question: all of it is theirs (39d8c1efe373 §pos3,
# "Publikt informationsmeddelande": "Kammarkollegiet ersätter avsnitt 7.19.1.3 till följande
# skrivning").
_ANSWER = "Publikt svar"
_NOTICE = "Publikt informationsmeddelande"

# A sentence ends at a full stop, question mark, exclamation mark, colon or semicolon
# before a space, at a table cell or bullet, or at a line break before a bullet, a digit or
# a capital. The parser also breaks lines inside sentences (20c753d88340 §47: "beskriver de
# ⏎ förutsättningar"), so a line break before a small letter does not end one.
_SENTENCE_END = re.compile(r"[.!?;:](?=\s|$)|\s*[|·•]|\n(?=\s*[·•\-–A-ZÅÄÖ0-9(])")
# A title or annex name also ends where the next one starts: "kraven och villkoren i avsnitt
# Miljöhänsyn, och avsnitt Sociala ...", "bilaga Pris och bilaga Konsultkompetens".
_NEXT_KEYWORD = re.compile(
    r"[\s,]+(?:och|samt|eller)?\s*(?<!\w)(?i:avsnitt(?:et|en)?|kapit(?:el|let|len)|punkt(?:en|erna)?"
    r"|bilag(?:a|an|or|orna))(?!\w)"
)
# A name cut where another reference starts loses the word that joins them: "bilaga Priser
# och Allmänna villkor punkt 6.17" gives "Priser".
_TRAILING_JOIN = re.compile(r"(?:[\s,]+(?:och|samt|eller))?[\s,]*$")
# A title or annex name longer than this is cut; the longest heading is 120 characters.
_NAME_MAX_CHARS = 200
_QUOTES = "\"”“'"


@dataclass(frozen=True)
class _Found:
    start: int
    end: int
    kind: ReferenceKind
    key: str
    rule: str
    document_name: str | None = None
    topic: str | None = None
    status: ReferenceStatus | None = None


@dataclass
class _Text:
    """The text of one section and the stretches the rules have taken so far."""

    text: str
    start: int  # where the body starts, after the heading line
    first: bool  # the first section of the document
    taken: list[tuple[int, int]]

    def is_taken(self, start: int, end: int) -> bool:
        return any(start < taken_end and taken_start < end for taken_start, taken_end in self.taken)

    def take(self, start: int, end: int) -> None:
        self.taken.append((start, end))


def find_mentions(
    sections: Sequence[Section], document_type: DocumentType, questions_log: bool
) -> list[ReferenceMention]:
    """The reference mentions of a document, ordered by section and offset.

    Args:
        sections: The document's sections from step 3.
        document_type: Its type (`document_type.classify`); `replaces` depends on it.
        questions_log: Whether step 3 split it as a questions-and-answers log
            (outline QUESTIONS); only then is "fråga N" read.
    """
    mentions: list[ReferenceMention] = []
    for section in sections:
        mentions.extend(_section_mentions(section, document_type, questions_log))
    return mentions


def _section_mentions(
    section: Section, document_type: DocumentType, questions_log: bool
) -> list[ReferenceMention]:
    text = _Text(section.text, _body_start(section), section.position == 0, [])
    found: list[_Found] = []
    laws = _law_spans(text)
    found.extend(_law_mentions(text, laws))
    found.extend(_placeholders(text))
    found.extend(_section_numbers(text, laws, questions_log))
    found.extend(_titles(text))
    found.extend(_annex_numbers(text, laws))
    found.extend(_annex_names(text))
    found.extend(_documents(text))
    if questions_log:
        found.extend(_questions(text))
    answer = section.text.find(_ANSWER, text.start)
    if answer < 0 and section.title.startswith(_NOTICE):
        answer = text.start
    ordered = sorted(found, key=lambda item: (item.start, item.end))
    starts = [item.start for item in ordered]
    mentions = []
    for item in ordered:
        following = bisect_right(starts, item.start)
        next_start = starts[following] if following < len(starts) else len(section.text)
        replaces = item.kind is not ReferenceKind.LAW and _replaces(
            section.text, item.start, item.end, document_type, answer, next_start
        )
        mentions.append(
            ReferenceMention(
                section=section.position,
                start=item.start,
                end=item.end,
                raw=section.text[item.start : item.end],
                kind=item.kind,
                key=item.key,
                rule=item.rule,
                document_name=item.document_name,
                topic=item.topic,
                replaces=replaces,
                status=item.status,
            )
        )
    return mentions


def _body_start(section: Section) -> int:
    """Where the text after the heading line starts (0 when the text has no heading line)."""
    first_line, _, _ = section.text.partition("\n")
    title = section.title[:30]
    if title and title in first_line:
        return len(first_line)
    return 0


# --- 1. Laws -----------------------------------------------------------------------------


def _law_spans(text: _Text) -> list[tuple[int, int]]:
    """The law spans of the body, overlapping or nearly touching matches merged."""
    matches = sorted(
        (match.start(), match.end())
        for pattern in _LAWS
        for match in pattern.finditer(text.text, text.start)
    )
    spans: list[tuple[int, int]] = []
    for start, end in matches:
        if spans and start <= spans[-1][1] + _LAW_GAP:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


def _law_mentions(text: _Text, laws: Sequence[tuple[int, int]]) -> Iterator[_Found]:
    for start, end in laws:
        yield _Found(
            start,
            end,
            ReferenceKind.LAW,
            " ".join(text.text[start:end].split()),
            "LAW",
            status=ReferenceStatus.EXTERNAL,
        )


def _in_or_before_law(text: _Text, start: int, end: int, laws: Sequence[tuple[int, int]]) -> bool:
    """Whether a number match lies in a law span or right before one ("kapitel 14 i LOU")."""
    after = _BEFORE_LAW.match(text.text, end)
    next_start = after.end() if after else end
    return any(
        start < law_end and law_start < end or law_start == next_start
        for law_start, law_end in laws
    )


# --- 2. Placeholders ---------------------------------------------------------------------


def _placeholders(text: _Text) -> Iterator[_Found]:
    for match in _PLACEHOLDER.finditer(text.text, text.start):
        if match["annex_x"]:
            kind, key = ReferenceKind.ANNEX_NUMBER, match["annex_x"]
        else:
            is_annex = match["keyword"].lower() == "bilaga"
            kind = ReferenceKind.ANNEX_NUMBER if is_annex else ReferenceKind.SECTION_NUMBER
            key = match["field"]
        text.take(match.start(), match.end())
        yield _Found(
            match.start(),
            match.end(),
            kind,
            key,
            "PH",
            status=ReferenceStatus.PLACEHOLDER,
        )


# --- 3. Section numbers ------------------------------------------------------------------


def _section_numbers(
    text: _Text, laws: Sequence[tuple[int, int]], questions_log: bool
) -> Iterator[_Found]:
    for pattern in (_KEYWORD_NUMBERS, _BARE_NUMBERS):
        for match in pattern.finditer(text.text, text.start):
            numbers_start, numbers_end = match.span("numbers")
            if text.is_taken(match.start(), numbers_end):
                continue
            keyword = match["keyword"].lower()
            before = text.text[max(0, match.start() - 4) : match.start()]
            if pattern is _KEYWORD_NUMBERS and keyword == "p." and _POINTS.search(before):
                continue
            text.take(match.start(), numbers_end)  # also when it is part of a law
            if _in_or_before_law(text, match.start(), numbers_end, laws):
                continue
            document = _document_named_with(text, match.start(), numbers_end)
            for index, number in enumerate(_ONE_NUMBER.finditer(match["numbers"])):
                start = match.start() if index == 0 else numbers_start + number.start()
                end = numbers_start + number.end()
                key = number.group().lower()
                status = None
                if document is None and _is_list_item(text, keyword, key, match, questions_log):
                    status = ReferenceStatus.LIST_ITEM
                yield _Found(
                    start,
                    end,
                    ReferenceKind.SECTION_NUMBER,
                    key,
                    "R1x" if document else "R1",
                    document_name=document,
                    status=status,
                )


def _document_named_with(text: _Text, start: int, end: int) -> str | None:
    """The document named right after or right before a section number, if any (R1x).

    The document's name is taken, so it is not also a mention of its own.
    """
    after = _DOCUMENT_AFTER.match(text.text, end)
    if after:
        name = ANY_DOCUMENT_NAME.match(text.text, after.end())
        if name and not text.is_taken(name.start(), name.end()):
            text.take(name.start(), name.end())
            return canonical_name(name.group())
    window_start = max(text.start, start - _DOCUMENT_BEFORE_CHARS)
    before = _DOCUMENT_BEFORE.search(text.text[window_start:start])
    if before:
        name_start, name_end = (window_start + offset for offset in before.span("document"))
        if not text.is_taken(name_start, name_end):
            text.take(name_start, name_end)
            return canonical_name(before["document"])
    return None


def _is_list_item(
    text: _Text, keyword: str, number: str, match: re.Match[str], questions_log: bool
) -> bool:
    """Whether a whole number after "punkt" is an item of a list, not a section.

    A whole number has no dot; it may have a letter: 9a0b5eaeef4b §pos72: "enligt punkt 1a)
    eller 2a) nedan". In a questions log it always is one: the questions cite items of the
    tender documents' sections ("Enligt 5.15.3, punkt 2"). In the pilot's logs only this
    makes 171 numbers list items, and each was read: none is a chapter.
    """
    if keyword not in _ITEM_KEYWORDS or "." in number:
        return False
    if questions_log:
        return True
    item_line = re.compile(rf"(?:^|\n)\s*(?:\(?{number}[.)]|{number}\s*-)\s")
    before = text.text[max(0, match.start() - _SECTION_BEFORE_ITEM_CHARS) : match.start()]
    return bool(
        item_line.search(text.text)
        or _ITEM_AFTER.match(text.text, match.end())
        or _SECTION_AFTER_ITEM.match(text.text, match.end())
        or _SECTION_BEFORE_ITEM.search(before)
        or _NUMBER_BEFORE_ITEM.search(before)
    )


# --- 4. Section titles -------------------------------------------------------------------


def _titles(text: _Text) -> Iterator[_Found]:
    for match in _TITLE.finditer(text.text, text.start):
        if text.is_taken(match.start(), match.end() + 1):
            continue
        end, key = _name(text, match.end(), quoted=bool(match["quote"]))
        if not key:
            continue
        # The keyword and the first letter are taken, the rest of the key is not: another
        # title, annex or document named later in the sentence is a mention of its own. A
        # document that starts the title ("i kapitel Kravkatalog") is the title, so it is not.
        text.take(match.start(), match.end() + 1)
        yield _Found(match.start(), end, ReferenceKind.SECTION_TITLE, key, "R4")


def _name(text: _Text, start: int, *, quoted: bool) -> tuple[int, str]:
    """The end and the key of a title or annex name that starts at `start`.

    A quoted name ends at the closing quote; any other at the end of the sentence, before
    the next title or annex keyword, or where a stretch taken by an earlier rule starts.
    """
    limit = min(len(text.text), start + _NAME_MAX_CHARS)
    if quoted:
        close = min((i for q in _QUOTES if (i := text.text.find(q, start, limit)) >= 0), default=-1)
        if close >= 0:
            return close, " ".join(text.text[start:close].split())
    end = limit
    for pattern in (_SENTENCE_END, _NEXT_KEYWORD):
        found = pattern.search(text.text, start, limit)
        if found:
            end = min(end, found.start())
    for taken_start, _ in text.taken:
        if start < taken_start < end:
            end = taken_start
    return end, _TRAILING_JOIN.sub("", " ".join(text.text[start:end].split()))


# --- 5. Annex numbers --------------------------------------------------------------------


def _annex_numbers(text: _Text, laws: Sequence[tuple[int, int]]) -> Iterator[_Found]:
    for match in _ANNEX_NUMBERS.finditer(text.text, text.start):
        if text.is_taken(match.start(), match.end()):
            continue
        keyword = match["keyword"].lower()
        numbers = list(_ONE_ANNEX_NUMBER.finditer(match["numbers"]))
        if (
            keyword == "bilagor"
            and len(numbers) == 1
            and _LIST_NUMBER_AFTER.match(text.text, match.end())
        ):
            continue
        text.take(match.start(), match.end())
        if _in_or_before_law(text, match.start(), match.end(), laws):
            continue
        numbers_start = match.start("numbers")
        for index, number in enumerate(numbers):
            start = match.start() if index == 0 else numbers_start + number.start()
            yield _Found(
                start,
                numbers_start + number.end(),
                ReferenceKind.ANNEX_NUMBER,
                number.group().lower(),
                "R3",
            )


# --- 6. Annex names ----------------------------------------------------------------------


def _annex_names(text: _Text) -> Iterator[_Found]:
    for match in _ANNEX_NAME.finditer(text.text, text.start):
        if text.is_taken(match.start(), match.end() + 1):
            continue
        end, key = _name(text, match.end(), quoted=bool(match["quote"]))
        if not key:
            continue
        text.take(match.start(), match.end() + 1)
        yield _Found(
            match.start(),
            end,
            ReferenceKind.ANNEX_NAME,
            key,
            "R5",
            document_name=_leading_document(text, match.end()),
        )


def _leading_document(text: _Text, start: int) -> str | None:
    """The document an annex name starts with ("bilaga Utkast till Datadelningsavtal"), if any.

    Its name is taken, so it is not also a mention of its own.
    """
    draft = _DRAFT.match(text.text, start)
    name = ANY_DOCUMENT_NAME.match(text.text, draft.end() if draft else start)
    if name is None:
        return None
    text.take(name.start(), name.end())
    return canonical_name(name.group())


# --- 7. Named documents ------------------------------------------------------------------


def _documents(text: _Text) -> Iterator[_Found]:
    for match in ANY_DOCUMENT_NAME.finditer(text.text, text.start):
        if text.is_taken(match.start(), match.end()):
            continue
        name = canonical_name(match.group())
        if name is None:
            continue
        before = text.text[max(0, match.start() - 40) : match.start()]
        lower_case = match.group()[0].islower()
        if lower_case and name in _COMMON_NOUNS:
            continue
        is_cover = text.first and not text.text[: match.start()].strip()
        if is_cover and _COVER_DATE.match(text.text, match.end()):
            continue
        status = None
        if _THIS_BEFORE.search(before) or (
            _DEFINED_AS_THIS_BEFORE.search(before)
            and _DEFINED_AS_THIS_AFTER.match(text.text, match.end())
        ):
            status = ReferenceStatus.SELF
        text.take(match.start(), match.end())
        yield _Found(
            match.start(),
            match.end(),
            ReferenceKind.DOCUMENT,
            name,
            "R2",
            topic=_topic(text, match.end()),
            status=status,
        )


def _topic(text: _Text, start: int) -> str | None:
    """The topic after a document name ("gällande viten, skadestånd och ..."), if any."""
    lead = _TOPIC.match(text.text, start)
    if lead is None:
        return None
    limit = min(len(text.text), lead.end() + _TOPIC_MAX_CHARS)
    ends = [
        found.start()
        for pattern in (_SENTENCE_END, _RELATIVE_CLAUSE)
        if (found := pattern.search(text.text, lead.end(), limit))
    ]
    topic = " ".join(text.text[lead.end() : min(ends, default=limit)].split()).rstrip(" ,")
    return topic or None


# --- 8. Questions ------------------------------------------------------------------------


def _questions(text: _Text) -> Iterator[_Found]:
    for match in _QUESTION.finditer(text.text, text.start):
        if text.is_taken(match.start(), match.end()):
            continue
        text.take(match.start(), match.end())
        yield _Found(match.start(), match.end(), ReferenceKind.QUESTION, match["number"], "RQ")


# --- Replacement -------------------------------------------------------------------------


def _replaces(
    text: str,
    start: int,
    end: int,
    document_type: DocumentType,
    answer: int,
    next_start: int,
) -> bool:
    """Whether the sentence of a mention replaces, removes or adds to its target.

    Args:
        text: The section's text.
        start: Where the mention starts in it.
        end: Where the mention ends.
        document_type: Only an AMENDMENT or a questions-and-answers log changes anything.
        answer: Where Kammarkollegiet's text starts in a log's section: "Publikt svar",
            or the body of a "Publikt informationsmeddelande"; -1 when it has neither.
        next_start: Where the next mention of the section starts (`len(text)` after the
            last); in an answer the sentence after the mention's is read up to there.
    """
    if document_type is DocumentType.QUESTIONS_AND_ANSWERS:
        if answer < 0 or start < answer:
            return False  # in the supplier's question: a proposal, not a change
    elif document_type is not DocumentType.AMENDMENT:
        return False
    sentence_start = max(
        (found.end() for found in _CHANGE_SENTENCE_END.finditer(text, 0, start)), default=0
    )
    in_answer = document_type is DocumentType.QUESTIONS_AND_ANSWERS
    window = text[sentence_start : _window_end(text, end, next_start, in_answer=in_answer)]
    if _NO_CHANGE.search(window):
        return False
    return any(_is_change(window, found) for found in _REPLACEMENT.finditer(window))


def _window_end(text: str, end: int, next_start: int, *, in_answer: bool) -> int:
    """Where the text read for `replaces` ends: the end of the mention's sentence.

    In an answer, the end of the next sentence that is not empty, but not past the next
    mention: Kammarkollegiet often names the section, then says what changes. 20c753d88340
    §21: "Gällande avsnitt 4.2.4, bokstaven L: ⏎ Kammarkollegiet ersätter härmed tidigare
    text"; bdf58b81d100 §127: "Avsnitt 6.18 Uppföljning ⏎ Följande mening läggs till i första
    stycket". But not 39d8c1efe373 §32: "samtliga viten under avsnitt 7.19. ⏎ Avsnitt 7.19.7
    Begränsning av vite justeras till följande", where the next sentence is about 7.19.7.
    """
    first = _CHANGE_SENTENCE_END.search(text, end)
    if first is None:
        return len(text)
    if not in_answer:
        return first.start()
    stop = min(next_start, len(text))
    for second in _CHANGE_SENTENCE_END.finditer(text, first.end()):
        if text[first.end() : second.start()].strip():
            stop = min(second.start(), stop)
            break
    return max(stop, first.start())


def _is_change(window: str, found: re.Match[str]) -> bool:
    """Whether a word of change in the text read for `replaces` changes something.

    Not "ändrar", "ändras" or "justerar" after a condition ("om pris ändras"), nor "tas
    bort" or "läggs till" after "kan" or before "genom". "Tillägg till" only first in a
    sentence: "Tillägg till avsnitt 5.15", not "ett tillägg till Kontraktet".
    """
    word = found.group().lower()
    before = window[: found.start()]
    conditional = word in ("ändrar", "justerar") or word.startswith("ändras")
    if conditional and _CONDITION.search(before):
        return False
    if word == "tillägg till":
        return not before.strip() or _STARTS_SENTENCE.search(before) is not None
    return not (
        word in _MAY_OR_HOW
        and (_MAY_BEFORE.search(before) or _HOW_AFTER.match(window, found.end()))
    )
