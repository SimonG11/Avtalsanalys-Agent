"""Tests for avtalsagent.register.load.build_tables: grouping rows, no database."""

from pathlib import Path

from avtalsagent.register.load import build_tables
from avtalsagent.register.normalize import normalize_rows
from avtalsagent.register.read_excel import read_register


def test_sample_rows_are_grouped_into_tables(sample_register_xlsx: Path) -> None:
    rows = normalize_rows(read_register(sample_register_xlsx).rows).rows

    tables = build_tables(rows)

    # 18 Excel rows, but only 9 agreements: A Hub Group has 8 rows for one agreement.
    assert len(tables.agreements) == 9
    assert len(tables.agreement_sub_areas) == 18
    assert len(tables.procurements) == 4
    assert len(tables.suppliers) == 4
    # 556337-2381 appears as both "2Home Hotel Gävle" and "2Home Sthlm South".
    assert len(tables.supplier_names) == 5


def test_sub_area_hierarchy_has_one_node_per_level(sample_register_xlsx: Path) -> None:
    rows = normalize_rows(read_register(sample_register_xlsx).rows).rows

    sub_areas = build_tables(rows).sub_areas
    by_path = {(s["framework_area"], s["path"]): s for s in sub_areas}

    leaf = by_path[("Hotelltjänster Longstay", "Gävleborgs län / Gävle / Gävle zon 1 - Longstay")]
    parent = by_path[("Hotelltjänster Longstay", "Gävleborgs län / Gävle")]
    root = by_path[("Hotelltjänster Longstay", "Gävleborgs län")]
    assert (leaf["level"], leaf["name"], leaf["parent_id"]) == (
        3,
        "Gävle zon 1 - Longstay",
        parent["id"],
    )
    assert (parent["level"], parent["parent_id"]) == (2, root["id"])
    assert (root["level"], root["parent_id"]) == (1, None)


def test_same_path_in_two_framework_areas_gives_two_nodes(sample_register_xlsx: Path) -> None:
    rows = normalize_rows(read_register(sample_register_xlsx).rows).rows

    sub_areas = build_tables(rows).sub_areas

    gavleborg = [s for s in sub_areas if s["path"] == "Gävleborgs län"]
    assert {s["framework_area"] for s in gavleborg} == {"Hotelltjänster", "Hotelltjänster Longstay"}
