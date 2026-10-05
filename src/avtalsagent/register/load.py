"""Write normalised register rows to the database.

What:
    `load_register` groups `RegisterRow` objects into procurements, suppliers,
    supplier names, agreements and sub-areas, and replaces the register tables
    with them in one transaction. It returns a `LoadReport`.

Why:
    The register tables must always mirror one complete edition of the master
    list. Replacing everything in one transaction makes the load idempotent:
    loading the same file twice gives the same tables, and a failed load
    leaves the previous edition untouched.

How:
    1. `build_tables` (pure) turns the rows into plain dicts, one list per table.
       The first row for an agreement decides its supplier and dates; rows that
       contradict it are listed by `find_conflicts` in the report.
    2. `load_register` deletes the old rows (children first) and inserts the
       new ones, then records the load in `register_version`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.register import RegisterRow, RegisterVersion
from avtalsagent.register.normalize import Finding, find_conflicts

SUB_AREA_SEPARATOR = " / "

Rows = list[dict[str, Any]]


@dataclass
class RegisterTables:
    procurements: Rows = field(default_factory=list)
    suppliers: Rows = field(default_factory=list)
    supplier_names: Rows = field(default_factory=list)
    agreements: Rows = field(default_factory=list)
    sub_areas: Rows = field(default_factory=list)
    agreement_sub_areas: Rows = field(default_factory=list)


@dataclass(frozen=True)
class LoadReport:
    list_date: str
    rows_loaded: int
    rejected: list[Finding]
    conflicts: list[Finding]
    table_counts: dict[str, int]

    def summary(self) -> str:
        lines = [
            f"Register {self.list_date}: {self.rows_loaded} rows loaded, "
            f"{len(self.rejected)} rejected, {len(self.conflicts)} conflicts",
            *(f"  {table}: {count}" for table, count in self.table_counts.items()),
            *(f"  rejected row {f.source_row}: {f.message}" for f in self.rejected),
            *(f"  conflict row {f.source_row}: {f.message}" for f in self.conflicts),
        ]
        return "\n".join(lines)


def build_tables(rows: Sequence[RegisterRow]) -> RegisterTables:
    """Group rows into one list of plain dicts per table. Pure; no database."""
    tables = RegisterTables()
    procurements: dict[str, str] = {}
    suppliers: set[str] = set()
    supplier_names: dict[tuple[str, str], str | None] = {}
    agreements: dict[str, RegisterRow] = {}
    sub_area_ids: dict[tuple[str, tuple[str, ...]], int] = {}
    links: set[tuple[str, int]] = set()

    for row in rows:
        procurements.setdefault(row.procurement_number, row.framework_area)
        suppliers.add(row.org_number)
        supplier_names.setdefault((row.org_number, row.supplier_name), row.former_supplier_name)
        agreements.setdefault(row.agreement_number, row)

        # Create every level of the path, e.g. "Gävleborgs län", then "Gävleborgs län / Gävle".
        parent_id: int | None = None
        for level in range(1, len(row.sub_area_path) + 1):
            path = row.sub_area_path[:level]
            key = (row.framework_area, path)
            if key not in sub_area_ids:
                sub_area_ids[key] = len(sub_area_ids) + 1
                tables.sub_areas.append(
                    {
                        "id": sub_area_ids[key],
                        "framework_area": row.framework_area,
                        "path": SUB_AREA_SEPARATOR.join(path),
                        "name": path[-1],
                        "level": level,
                        "parent_id": parent_id,
                    }
                )
            parent_id = sub_area_ids[key]
        if parent_id is None:
            raise ValueError(f"row {row.source_row} has an empty sub-area path")
        links.add((row.agreement_number, parent_id))

    tables.procurements = [
        {"procurement_number": number, "framework_area": area}
        for number, area in procurements.items()
    ]
    tables.suppliers = [{"org_number": org} for org in sorted(suppliers)]
    tables.supplier_names = [
        {"org_number": org, "name": name, "former_name": former}
        for (org, name), former in supplier_names.items()
    ]
    tables.agreements = [
        {
            "agreement_number": row.agreement_number,
            "procurement_number": row.procurement_number,
            "sequence": row.sequence,
            "org_number": row.org_number,
            "supplier_name": row.supplier_name,
            "valid_from": row.valid_from,
            "valid_to": row.valid_to,
            "max_extension_to": row.max_extension_to,
        }
        for row in agreements.values()
    ]
    tables.agreement_sub_areas = [
        {"agreement_number": number, "sub_area_id": sub_area_id}
        for number, sub_area_id in sorted(links)
    ]
    return tables


def load_register(
    session: Session,
    version: RegisterVersion,
    rows: Sequence[RegisterRow],
    rejected: Sequence[Finding] = (),
) -> LoadReport:
    """Replace the register tables with `rows`. Call inside `factory.begin()`."""
    tables = build_tables(rows)

    # Children before parents, so no foreign key is violated.
    for model in (
        models.AgreementSubArea,
        models.SubArea,
        models.Agreement,
        models.SupplierName,
        models.Supplier,
        models.Procurement,
    ):
        session.execute(delete(model))

    # Parents before children.
    for model, values in (
        (models.Procurement, tables.procurements),
        (models.Supplier, tables.suppliers),
        (models.SupplierName, tables.supplier_names),
        (models.Agreement, tables.agreements),
        (models.SubArea, tables.sub_areas),
        (models.AgreementSubArea, tables.agreement_sub_areas),
    ):
        if values:
            session.execute(insert(model), values)

    # Record the load; loading the same edition again updates its row.
    record = {
        "list_date": version.list_date,
        "title": version.title,
        "row_count": len(rows),
        "rejected_count": len(rejected),
    }
    session.execute(
        pg_insert(models.RegisterVersion)
        .values(record)
        .on_conflict_do_update(
            index_elements=["list_date"], set_={**record, "loaded_at": func.now()}
        )
    )

    return LoadReport(
        list_date=version.list_date.isoformat(),
        rows_loaded=len(rows),
        rejected=list(rejected),
        conflicts=find_conflicts(rows),
        table_counts={
            "procurement": len(tables.procurements),
            "supplier": len(tables.suppliers),
            "supplier_name": len(tables.supplier_names),
            "agreement": len(tables.agreements),
            "sub_area": len(tables.sub_areas),
            "agreement_sub_area": len(tables.agreement_sub_areas),
        },
    )
