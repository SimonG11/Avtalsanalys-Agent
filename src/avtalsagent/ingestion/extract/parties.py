"""Ingestion step 4: the parties named in a framework agreement's party clause.

What:
    `find_parties` finds the clause that names the parties of a framework
    agreement and gives a PARTY fact for the supplier it names (value: the
    organisation number, name: the supplier as written), or a PLACEHOLDER fact
    (value "party") when the supplier's slot is not filled in.

Why:
    Step 5 checks that a supplier card is signed by the supplier the register
    has for its agreement, and a card can name another legal entity: 7a49e1a61b31
    §9.1.1: "och ÅF Digital Solutions AB, organisationsnummer 556866-4444 nedan
    Ramavtalsleverantören" where the register has AFRY Sweden AB 556224-8012.
    The clause is the one place where the text says which organisation number
    is the supplier's. It has the same form in every Kammarkollegiet agreement,
    filled in the 26 signed ones and unfilled in the 25 templates and generic
    main documents of the pilot. Supplier names without an organisation number
    (price lists, guides) are not read: step 5 checks every organisation number
    instead (`identifiers.py`, ADR 0009).

How:
    Rule E1, `_PARTY_CLAUSE`: "mellan <customer>, organisationsnummer <org>,
    nedan <term>, och <supplier>, organisationsnummer <org>, nedan <term>".
    The commas before "nedan" are optional (the 2940 cards have none), "nedan"
    may stand in parentheses ("(nedan Microsoft)"), "Org nr:" may stand for
    "organisationsnummer", and case does not matter ("Mellan",
    "Organisationsnummer"). The clause is looked for in each block joined with
    the next one, since a block can end inside it (e3a24695fe04: "...
    organisationsnummer [xxxxxx-yyyy] nedan ⏎ Ramavtalsleverantören."); page
    headers and footers are skipped, as they can fall between the two. A slot
    is filled when it holds a number (`_SLOT_NUMBER`), and words after the
    number do not count ("556599-4307, med säte i Göteborg"). The number is
    read by `normalize_org_number`, a Swedish or a foreign one, without the
    check-digit test, and one it cannot read is kept as written: a mistyped
    number reaches step 5 instead of vanishing. A slot without a number
    ("Leverantörens organisationsnummer", "XXXXXX-XXXX") is a placeholder.
    The customer slot holds Kammarkollegiet's 202100-0829 in all 51 clauses of
    the pilot; a customer that is not gets a PARTY fact with rule "E1-customer".
    A fact's block is the block where the slot's organisation number starts, and
    its raw text is the clause, with the line breaks as spaces.
"""

import re
from collections.abc import Sequence

from avtalsagent.domain.extracted import CONTRACTING_AUTHORITY_ORG_NUMBER, Fact, FactKind
from avtalsagent.domain.identifiers import IdentifierError, normalize_org_number
from avtalsagent.domain.parsed import Block, BlockKind

# "organisationsnummer", "org.nr", "Org nr:" (the clause is read without regard to case).
_ORG_LABEL = r"org(?:anisations)?\.?\s*(?:nummer|nr)\.?:?"
# "nedan Kammarkollegiet", "(nedan Microsoft)", 'nedan kallad "Leverantören"'.
_HEREAFTER = r"\(?nedan\s+(?:kallad\s+)?[\"”]?(?P<{term}>\w+)"
# E1. 185872a6bb90 §1.3.1: "Ramavtal med avtalsnummer 23.3.2649-22-003, har träffats
# för Avropsberättigades räkning, mellan Statens inköpscentral vid Kammarkollegiet,
# organisationsnummer 202100-0829, nedan Kammarkollegiet, och <Leverantör AB>,
# organisationsnummer <NNNNNN-NNNN>, nedan Ramavtalsleverantören."
_PARTY_CLAUSE = re.compile(
    rf"mellan\s+(?P<customer>[^,]+?),?\s+{_ORG_LABEL}\s*(?P<customer_org>[^,]+?),?\s+"
    rf"{_HEREAFTER.format(term='customer_term')}[\"”]?\)?,?\s+"
    rf"och\s+(?P<supplier>.+?),?\s+{_ORG_LABEL}\s*(?P<supplier_org>.+?),?\s+"
    rf"{_HEREAFTER.format(term='supplier_term')}",
    re.DOTALL | re.IGNORECASE,
)
# The number in a slot: Swedish ("556866-4444") or foreign ("FI01148912", "CVR:37120928"),
# at least five digits and separators. Searched for, since words can follow it.
_SLOT_NUMBER = re.compile(r"(?:[A-Z]{2,3}:?)?\d[\d\s–-]{3,}\d")
# A clause may run over into the next block, not further.
_WINDOW_BLOCKS = 2
_BLOCK_SEPARATOR = "\n\n"
_HEADER_KINDS = (BlockKind.PAGE_HEADER, BlockKind.PAGE_FOOTER)
_PARTY_PLACEHOLDER = "party"
# The rules of the two slots of the clause; step 5's supplier_party check reads the first.
SUPPLIER_SLOT_RULE = "E1"
CUSTOMER_SLOT_RULE = "E1-customer"


def find_parties(blocks: Sequence[Block]) -> list[Fact]:
    """PARTY and PLACEHOLDER facts for the party clauses of a document, in block order."""
    body = [(index, block) for index, block in enumerate(blocks) if block.kind not in _HEADER_KINDS]
    facts: list[Fact] = []
    for position, (_, block) in enumerate(body):
        window = body[position : position + _WINDOW_BLOCKS]
        text = _BLOCK_SEPARATOR.join(part.text for _, part in window)
        for match in _PARTY_CLAUSE.finditer(text):
            if match.start() >= len(block.text):
                break  # starts in the next block: found from there
            for slot in ("customer", "supplier"):
                fact = _slot_fact(match, window, slot)
                if fact is not None:
                    facts.append(fact)
    return facts


def _slot_fact(match: re.Match[str], window: Sequence[tuple[int, Block]], slot: str) -> Fact | None:
    org_number = _slot_number(match[f"{slot}_org"])
    if slot == "customer":
        # Kammarkollegiet is a party to every agreement, and its slot is filled in all
        # 51 clauses of the pilot; only another customer is worth a fact.
        if org_number is None or org_number == CONTRACTING_AUTHORITY_ORG_NUMBER:
            return None
        kind, value, rule = FactKind.PARTY, org_number, CUSTOMER_SLOT_RULE
    elif org_number is None:
        # "Leverantörens organisationsnummer", "XXXXXX-XXXX", "[xxxxxx-yyyy]": unfilled.
        kind, value, rule = FactKind.PLACEHOLDER, _PARTY_PLACEHOLDER, SUPPLIER_SLOT_RULE
    else:
        kind, value, rule = FactKind.PARTY, org_number, SUPPLIER_SLOT_RULE
    index, block = _block_at(window, match.start(f"{slot}_org"))
    return Fact(
        kind=kind,
        value=value,
        raw=" ".join(match.group().split()),  # the clause, also when it spans two blocks
        rule=rule,
        block=index,
        page=block.page,
        name=" ".join(match[slot].split()),
    )


def _slot_number(slot: str) -> str | None:
    """The organisation number a slot is filled with, or None when it holds no number."""
    number = _SLOT_NUMBER.search(slot)
    if number is None:
        return None
    try:
        return normalize_org_number(number.group().replace("–", "-"))
    except IdentifierError:
        return " ".join(number.group().split())  # mistyped: kept as written


def _block_at(window: Sequence[tuple[int, Block]], offset: int) -> tuple[int, Block]:
    """The block of the window that holds the character at `offset` of the joined text."""
    end = 0
    for index, block in window:
        end += len(block.text) + len(_BLOCK_SEPARATOR)
        if offset < end:
            return index, block
    return window[-1]
