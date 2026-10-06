"""Tests for avtalsagent.ingestion.extract.dates.

The lines are real, from the pilot files of the M4 survey (dates.md), cited as
sha[:12] §section or block. Signers' names and ids are replaced by made-up ones.
Three are changed on purpose and say so: a length in words (no period clause in
the pilot writes one), a date that does not exist and a table without labels.
"""

from datetime import date

from avtalsagent.domain.extracted import Fact, FactKind
from avtalsagent.domain.parsed import Block, BlockKind
from avtalsagent.ingestion.extract.dates import find_dates, parse_period

TABLE = BlockKind.TABLE
# 185872a6bb90 §1.8, the clause of a signed supplier card.
IN_FORCE = (
    "Ramavtalet blir bindande från och med det datumet som Parterna signerar det men träder "
    "i kraft 2023-02-27. Om Parterna signerar Ramavtalet vid ett senare datum träder "
    "Ramavtalet i kraft från och med det datumet. Att Ramavtalet träder i kraft innebär att "
    "Avrop kan göras."
)
LENGTH_AND_END = (
    "Ramavtalet löper under en period av 48 månader, dock längst till och med den "
    "2027-02-26. Ramavtalet upphör därefter att gälla utan uppsägning. Tilldelning av "
    "Kontrakt ska dock ske under Ramavtalets löptid."
)
# 14aa1cc8ee3d §9.6.2 and §9.6.3, an IT-konsulttjänster 2020 card.
EARLIEST = (
    "Ramavtalet träder i kraft, vilket innebär att Avrop kan göras under Ramavtalet, den dag "
    "det signerats av båda Parter samt tidigast från och med den 2022-12-01. Från 2022-12-01 "
    "löper ramavtalet därefter under en period av 24 månader. Ramavtalet upphör därefter att "
    "gälla utan uppsägning."
)
EXTENSION = (
    "Eventuell förlängning av Ramavtal sker på initiativ av Kammarkollegiet. "
    "Ramavtalsleverantören har inte rätt att motsätta sig förlängning. Oaktat vad som står i "
    "Ramavtalets sidhuvud, kan en eller flera förlängningar av Ramavtalets giltighetstid uppgå "
    "till maximalt 24 månader. Förlängning regleras skriftligen."
)


def block(text: str, page: int | None = 1, kind: BlockKind = BlockKind.TEXT) -> Block:
    return Block(kind=kind, text=text, page=page)


def found(*blocks: Block) -> list[tuple[str, str, str]]:
    """(kind, value, rule) of the facts in these blocks."""
    return [(f.kind.value, f.value, f.rule) for f in find_dates(blocks)]


class TestPeriodRules:
    def test_cover_table_gives_start_end_and_no_extension(self) -> None:
        # P1, 76dfb5d1ae1f b2: the TendSign cover of a signed main document.
        cover = (
            "Avtalsnamn IT-drift 2023, område Större | Startdatum 2024-11-14\n"
            "Ref. nr. 23.3-10639-2023 | Slutdatum 2028-11-13\n"
            " | Förlängning Ingen förlängning"
        )
        facts = find_dates([block(cover, kind=TABLE)])

        assert [(f.kind.value, f.value, f.rule) for f in facts] == [
            ("period_start", "2024-11-14", "P1"),
            ("period_end", "2028-11-13", "P1"),
            ("extension_months", "0", "P1"),
        ]
        assert {(f.statement, f.scope) for f in facts} == {
            (1, None)
        }  # "område Större" is no number
        assert facts[0].raw.startswith("Startdatum 2024-11-14 Ref. nr.")

    def test_cover_cells_read_as_separate_blocks_are_one_cover(self) -> None:
        # P1 window, 0692da436391 b7-b9.
        facts = find_dates(
            [
                block("Kontaktperson Startdatum 2025-08-19"),
                block("Slutdatum 2029-08-18"),
                block("Förlängning Ingen förlängning"),
            ]
        )

        assert [(f.kind.value, f.value, f.block) for f in facts] == [
            ("period_start", "2025-08-19", 0),
            ("period_end", "2029-08-18", 0),
            ("extension_months", "0", 0),
        ]

    def test_extension_cell_alone_is_no_extension(self) -> None:
        # The free-standing "Förlängning X" gave 21 false hits (14aa1cc8ee3d §9.6.3).
        assert found(block("Förlängning regleras skriftligen.")) == []

    def test_in_force_date(self) -> None:
        # P2.
        assert found(block(IN_FORCE)) == [("period_start", "2023-02-27", "P2")]

    def test_length_and_latest_end(self) -> None:
        # P7 and P3, the paragraph after the one above.
        assert found(block(LENGTH_AND_END)) == [
            ("period_months", "48", "P7"),
            ("period_end", "2027-02-26", "P3"),
        ]

    def test_bid_validity_is_no_end(self) -> None:
        # 11db2f3d1852 §2.10.5: "till och med D" without "längst" (25 of 41 such hits).
        assert found(block("Anbudet ska vara giltigt till och med 2023-03-01.")) == []

    def test_notice_period_is_no_length(self) -> None:
        # 185872a6bb90 §1.8: "24 månader" and "löper" in one sentence, not "löper ... 24 månader".
        notice = (
            "Kammarkollegiet har en ensidig rätt att säga upp Ramavtalet tidigast 24 månader "
            "innan Ramavtalet löper ut med minst tre (3) månaders uppsägningstid."
        )
        assert found(block(notice)) == []

    def test_earliest_start_and_length_after_it(self) -> None:
        # P4 and P7.
        assert found(block(EARLIEST)) == [
            ("period_start", "2022-12-01", "P4"),
            ("period_months", "24", "P7"),
        ]

    def test_price_lock_from_a_date_is_no_start(self) -> None:
        # 34d71a7e4da0 §8.10.2: "från och med D" without "tidigast".
        text = (
            "Priserna som gäller vid Avrop genom rangordning är fasta i ett (1) år från och med "
            "2025-04-03."
        )
        assert found(block(text)) == []

    def test_longest_extension_within_one_sentence(self) -> None:
        # P8: the first "förlängning" is in a sentence without a length.
        facts = find_dates([block(EXTENSION)])

        assert [(f.kind.value, f.value, f.rule) for f in facts] == [
            ("extension_months", "24", "P8")
        ]
        assert facts[0].raw == (
            "förlängningar av Ramavtalets giltighetstid uppgå till maximalt 24 månader"
        )

    def test_range_in_words_with_its_area(self) -> None:
        # P5, 4b6c2a533fae b39-b40: one list item per area.
        facts = find_dates(
            [
                block(
                    "- Ramavtalet för område 1 - Verksamhetens IT-behov är giltigt från och med "
                    "2025-08-19 till och med 2029-08-18.",
                    kind=BlockKind.LIST_ITEM,
                ),
                block(
                    "- Ramavtalet för område 2 - Ledning av IT-projekt är giltigt från och med "
                    "2022-12-01 till och med 2026-11-30.",
                    kind=BlockKind.LIST_ITEM,
                ),
            ]
        )

        assert [(f.value, f.rule, f.statement, f.scope) for f in facts] == [
            ("2025-08-19", "P5", 1, "område 1 - Verksamhetens IT-behov"),
            ("2029-08-18", "P5", 1, "område 1 - Verksamhetens IT-behov"),
            ("2022-12-01", "P5", 2, "område 2 - Ledning av IT-projekt"),
            ("2026-11-30", "P5", 2, "område 2 - Ledning av IT-projekt"),
        ]

    def test_range_with_start_and_end_in_words(self) -> None:
        # P5, c27b833f338d §2.1, the Microsoft guide.
        text = "Volymavtalet gäller under tre år med start från 2024-05-01 och slutar 2027-04-30."
        assert found(block(text)) == [
            ("period_start", "2024-05-01", "P5"),
            ("period_end", "2027-04-30", "P5"),
        ]

    def test_bare_range_in_a_page_footer(self) -> None:
        # P6, 171a3cacf5fd: the only place the Microsoft volume agreement states its period.
        footer = "Volymavtalets huvuddokument 1.0 för avtalsperiod 2024-05-01 - 2027-04-30"
        assert found(block(footer, kind=BlockKind.PAGE_FOOTER)) == [
            ("period_start", "2024-05-01", "P6"),
            ("period_end", "2027-04-30", "P6"),
        ]

    def test_table_rows_are_statements_scoped_by_their_label(self) -> None:
        # P6, 49f36699a469 b83 (two of its four rows).
        table = (
            "Ramavtalområde | Avtalsperiod\n"
            "Programvaror och Tjänster Licenser och licenstjänster | 2023-02-20 - 2027-02-19\n"
            "Programvaror och Tjänster Systemutveckling | 2023-11-01 - 2027-10-31"
        )
        facts = find_dates([block(table, kind=TABLE)])

        assert [(f.value, f.statement, f.scope) for f in facts] == [
            ("2023-02-20", 1, "Programvaror och Tjänster Licenser och licenstjänster"),
            ("2027-02-19", 1, "Programvaror och Tjänster Licenser och licenstjänster"),
            ("2023-11-01", 2, "Programvaror och Tjänster Systemutveckling"),
            ("2027-10-31", 2, "Programvaror och Tjänster Systemutveckling"),
        ]

    def test_rows_without_a_label_are_still_statements_of_their_own(self) -> None:
        # The table above without its label column (changed on purpose).
        table = "Avtalsperiod\n2023-02-20 - 2027-02-19\n2023-11-01 - 2027-10-31"
        facts = find_dates([block(table, kind=TABLE)])
        assert [(f.value, f.statement, f.scope) for f in facts] == [
            ("2023-02-20", 1, None),
            ("2027-02-19", 1, None),
            ("2023-11-01", 2, None),
            ("2027-10-31", 2, None),
        ]

    def test_area_codes_scope_their_own_row(self) -> None:
        # P2 and P6, 4b6c2a533fae b16 (the version table, cut): the row of version 1.02
        # names AO3 before its dates and AO1 and AO5 after them.
        table = (
            "| Publicerat datum | Uppdaterat avsnitt\n"
            "1.00 | 2025-08-19 | Första slutversion\n"
            "1.01 | 2025-08-23 | Uppdaterad version när nya AO5 träder i kraft per 2025-08-23.\n"
            "1.02 | 2026-03-10 | Uppdaterad version för nya ramavtalet IT-säkerhet, AO3 träder i "
            "kraft per 2026-03-10. Nytt för IT-säkerhet (AO3): - avtalstiden (2026-03-10- "
            "2030-03-09) - prisjustering i Allmänna Villkor som även finns i AO1 & AO5."
        )
        facts = find_dates([block(table, kind=TABLE)])

        assert [(f.kind.value, f.value, f.rule, f.statement, f.scope) for f in facts] == [
            ("period_start", "2025-08-23", "P2", 1, "AO5"),
            ("period_start", "2026-03-10", "P2", 2, "AO3"),
            ("period_start", "2026-03-10", "P6", 2, "AO3"),
            ("period_end", "2030-03-09", "P6", 2, "AO3"),
        ]

    def test_paragraphs_of_one_clause_are_separate_statements(self) -> None:
        # 185872a6bb90 §1.8: the start and the end are in two paragraphs.
        facts = find_dates([block(IN_FORCE), block(LENGTH_AND_END)])
        assert [(f.value, f.statement) for f in facts] == [
            ("2023-02-27", 1),
            ("48", 2),
            ("2027-02-26", 2),
        ]

    def test_planned_start_has_no_statement(self) -> None:
        # P9, c59dbfeeb576 §1.8: the procurement's plan; the length is a period fact.
        text = (
            "Ramavtalet beräknas träda i kraft 2025-04-03, om upphandlingen inte blir föremål "
            "för överprövning, och löper därefter under en period av högst 48 månader."
        )
        facts = find_dates([block(text)])

        assert [(f.kind.value, f.value, f.rule, f.statement) for f in facts] == [
            ("planned_start", "2025-04-03", "P9", None),
            ("period_months", "48", "P7", 1),
        ]

    def test_length_in_words_and_years(self) -> None:
        # LENGTH_AND_END with the length in words and in years (changed on purpose).
        assert found(block("Ramavtalet löper under en period av fyrtioåtta (48) månader.")) == [
            ("period_months", "48", "P7")
        ]
        assert found(block("Ramavtalet löper under en period av fyra år.")) == [
            ("period_months", "48", "P7")
        ]

    def test_a_date_that_does_not_exist_gives_no_fact(self) -> None:
        # IN_FORCE with the day changed to 30 February (on purpose).
        assert found(block(IN_FORCE.replace("2023-02-27", "2023-02-30"))) == []


class TestPlaceholders:
    def test_unfilled_date_field_of_a_template(self) -> None:
        # P10, 34d71a7e4da0 §8.7.
        text = (
            "Ramavtalet blir bindande från och med det datumet som Parterna signerar det men "
            "träder i kraft den [DATUM (dag-mån-år)]."
        )
        assert find_dates([block(text)]) == [
            Fact(
                kind=FactKind.PLACEHOLDER,
                value="date",
                raw="[DATUM (dag-mån-år)]",
                rule="P10",
                block=0,
                page=1,
            )
        ]

    def test_other_spellings_of_a_date_field(self) -> None:
        # 37f6a4caa617 §2.6 (Word, no pages) and 5b38873c2b7a §1.1.
        assert found(
            block("Avropssvaret ska vara giltigt till och med ÅÅ-MM-DD", page=None),
            block("Microsoft Enterprise Support Services börjar gälla den 20xx-xx-xx"),
        ) == [("placeholder", "date", "P10"), ("placeholder", "date", "P10")]

    def test_unfilled_agreement_number_is_no_date_field(self) -> None:
        # 5d6e948959cc §5.2.1: a typo in the year, then an unfilled supplier sequence.
        text = "Ramavtal med avtalsnummer 23.3-5890-20263-XXX, har träffats för Avropsberättigades"
        assert found(block(text)) == []


class TestSignatures:
    def certificate(self, page: int) -> list[Block]:
        """185872a6bb90 p18, the e-signature certificate (name and id made up)."""
        return [
            block(
                "Signaturerna i detta dokument är juridiskt bindande. Dokumentet är signerat med "
                "Addo Sign säkra digitala signatur.",
                page,
            ),
            block("Undertecknare", page, BlockKind.HEADING),
            block("ERIK EXEMPELSSON", page),
            block("AbCdEfGhIjKlMnOpQrStUv 2023-02-22 15:14", page),
            block(
                "Dokumentet är skyddat med ett Adobe CDS-certifikat. När dokumentet öppnas i "
                "Adobe Reader ser det ut att vara signerat genom Addo Sign signeringstjänst.",
                page,
            ),
        ]

    def test_signature_dates_on_the_certificate_keep_only_date_and_time(self) -> None:
        # P11: the raw text holds no signer id.
        body = block("Rättigheter och skyldigheter enligt Ramavtalet regleras av svensk rätt.", 17)
        facts = find_dates([body, *self.certificate(18)])

        assert [(f.kind.value, f.value, f.raw, f.block, f.statement) for f in facts] == [
            ("signed_on", "2023-02-22", "2023-02-22 15:14", 4, None)
        ]

    def test_a_timestamp_outside_a_certificate_is_no_signature(self) -> None:
        # A TendSign print footer ("Utskrivet: 2021-02-09 12:21 Sida 5 av 111") is no signature.
        footer = block("Utskrivet: 2021-02-09 12:21 Sida 5 av 111", 5, BlockKind.PAGE_FOOTER)
        assert found(footer, block("AbCdEfGhIjKlMnOpQrStUv 2023-02-22 15:14", 6)) == []


class TestParsePeriod:
    def test_the_period_of_an_agreement_page(self) -> None:
        assert parse_period("2024-11-14 - 2028-11-13") == (date(2024, 11, 14), date(2028, 11, 13))

    def test_anything_else_is_none(self) -> None:
        assert parse_period("2024-11-14") is None
        assert parse_period("2028-11-13 - 2024-11-14") is None  # ends before it starts
        assert parse_period("2024-02-30 - 2028-11-13") is None  # no such day
        assert parse_period("Startdatum 2024-11-14 - 2028-11-13") is None
