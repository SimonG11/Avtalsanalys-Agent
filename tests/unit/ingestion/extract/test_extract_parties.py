"""Tests for avtalsagent.ingestion.extract.parties.

The clauses are real, from the pilot files of the M4 survey (suppliers.md §2),
cited as sha[:12] §section. "Exempelkommunen" and its number, the mistyped
number and the page footer are made up.
"""

from avtalsagent.domain.extracted import Fact, FactKind
from avtalsagent.domain.parsed import Block, BlockKind
from avtalsagent.ingestion.extract.parties import find_parties

# 185872a6bb90 §1.3.1, a supplier card of 23.3-2649-2022.
CRAYON = (
    "Ramavtal med avtalsnummer 23.3.2649-22-003, har träffats för Avropsberättigades "
    "räkning, mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer "
    "202100-0829, nedan Kammarkollegiet, och Crayon AB, organisationsnummer 556635-9799, "
    "nedan Ramavtalsleverantören."
)
# 7a49e1a61b31 §9.1.1: the 2940 cards have no comma before "nedan".
AF = (
    "Ramavtal med avtalsnummer 23.3.2940-20:033 har träffats för statens och "
    "Avropsberättigades räkning, mellan Statens inköpscentral vid Kammarkollegiet, "
    "organisationsnummer 202100-0829 nedan Kammarkollegiet, och ÅF Digital Solutions AB, "
    "organisationsnummer 556866-4444 nedan Ramavtalsleverantören."
)
# 171a3cacf5fd §pos0: "nedan" in parentheses, a foreign company with a Swedish number.
MICROSOFT = (
    "Detta avtal (nedan Volymavtalet) har ingåtts mellan Statens inköpscentral vid "
    "Kammarkollegiet, organisationsnummer 202100-0829 (nedan Kammarkollegiet) och Microsoft "
    "Ireland Operations Ltd, organisationsnummer 502052-1307 (nedan Microsoft)."
)
# 0692da436391 §1.2.1 and 124cb5f66cc3 §5.2.1: the generic main documents.
TEMPLATE_X = (
    "Ramavtal med avtalsnummer 23.3-1688-2024:[XXX], har träffats för Avropsberättigades "
    "räkning, mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer "
    "202100-0829, nedan Kammarkollegiet, och Leverantör organisationsnummer XXXXXX-XXXX, "
    "nedan Ramavtalsleverantören."
)
TEMPLATE_NAMES = (
    "mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer 202100-0829, nedan "
    "Kammarkollegiet, och Leverantörens namn, organisationsnummer Leverantörens "
    "organisationsnummer, nedan Ramavtalsleverantören."
)
# e3a24695fe04 §9.1.1: the clause ends in the next block.
SPLIT = (
    "Ramavtal med avtalsnummer [XXXX], har träffats för statens och Avropsberättigades "
    "räkning, mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer "
    "202100-0829 nedan Kammarkollegiet, och [Ramavtalsleverantören], organisationsnummer "
    "[xxxxxx-yyyy] nedan"
)


def block(text: str, page: int | None = 1, kind: BlockKind = BlockKind.TEXT) -> Block:
    return Block(kind=kind, text=text, page=page)


def parties(*blocks: Block) -> list[tuple[str, str, str | None, str, int]]:
    return [(f.kind.value, f.value, f.name, f.rule, f.block) for f in find_parties(blocks)]


def test_supplier_of_a_signed_card() -> None:
    facts = find_parties([block("1.3.1 Parter", page=3), block(CRAYON, page=3)])

    assert facts == [
        Fact(
            kind=FactKind.PARTY,
            value="556635-9799",
            raw=CRAYON[CRAYON.index("mellan") :].removesuffix("."),
            rule="E1",
            block=1,
            page=3,
            name="Crayon AB",
        )
    ]


def test_clause_without_commas_before_nedan() -> None:
    assert parties(block(AF)) == [("party", "556866-4444", "ÅF Digital Solutions AB", "E1", 0)]


def test_clause_with_nedan_in_parentheses() -> None:
    assert parties(block(MICROSOFT)) == [
        ("party", "502052-1307", "Microsoft Ireland Operations Ltd", "E1", 0)
    ]


def test_unfilled_supplier_slot_is_a_placeholder() -> None:
    assert parties(block(TEMPLATE_X), block(TEMPLATE_NAMES)) == [
        ("placeholder", "party", "Leverantör", "E1", 0),
        ("placeholder", "party", "Leverantörens namn", "E1", 1),
    ]


def test_clause_may_end_in_the_next_block() -> None:
    facts = parties(block(SPLIT), block("Ramavtalsleverantören."))

    assert facts == [("placeholder", "party", "[Ramavtalsleverantören]", "E1", 0)]


def test_page_footer_between_the_parts_of_a_clause_is_skipped() -> None:
    # A page break inside 7a49e1a61b31's clause (made up: in the pilot none falls there).
    first, second = AF.split(" organisationsnummer 556866-4444")
    facts = parties(
        block(first, page=7),
        block("Ramavtal Ledning av IT-projekt Sida 7 (27)", page=7, kind=BlockKind.PAGE_FOOTER),
        block(f"organisationsnummer 556866-4444{second}", page=8),
    )

    assert facts == [("party", "556866-4444", "ÅF Digital Solutions AB", "E1", 2)]


def test_clause_is_not_looked_for_beyond_the_next_block() -> None:
    customer, supplier = AF.split(" ÅF Digital Solutions AB,")
    heading = block("9.1.2 Avropsberättigade")
    assert parties(block(customer), heading, block(f"ÅF Digital Solutions AB,{supplier}")) == []
    assert parties(block(customer), block(f"ÅF Digital Solutions AB,{supplier}")) == [
        ("party", "556866-4444", "ÅF Digital Solutions AB", "E1", 1)
    ]


def test_mistyped_supplier_number_is_kept_for_step_5() -> None:
    clause = AF.replace("556866-4444", "556866-4445")  # the check digit is wrong

    assert parties(block(clause)) == [("party", "556866-4445", "ÅF Digital Solutions AB", "E1", 0)]


def test_customer_other_than_kammarkollegiet_is_a_party() -> None:
    clause = AF.replace("Statens inköpscentral vid Kammarkollegiet", "Exempelkommunen").replace(
        "202100-0829", "212000-9999"
    )

    assert parties(block(clause)) == [
        ("party", "212000-9999", "Exempelkommunen", "E1-customer", 0),
        ("party", "556866-4444", "ÅF Digital Solutions AB", "E1", 0),
    ]


def test_texts_that_name_parties_otherwise_are_not_read() -> None:
    assert (
        parties(
            # 7c093254b164 §pos0: the enrolment form names no party.
            block(
                "Detta Microsoft Enterprise-avtal har ingåtts mellan de bolag som anges i "
                "formuläret för undertecknande."
            ),
            # fb9447f0b8bf §3: IBM's two columns, interleaved (rule E3, not built).
            block(
                "Överenskommes: Kammarkollegiet, Statens Inköpscentral IBM Svenska AB "
                "Org nr: 202100-0829 Org nr: 556026-6883"
            ),
        )
        == []
    )
