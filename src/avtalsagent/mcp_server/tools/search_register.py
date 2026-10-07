"""search_register: the register's agreements, by supplier, number, area, sub-area or org number.

What:
    `search_register` returns one row per agreement and sub-area of the
    register ("Alla giltiga ramavtal"): agreement and procurement number,
    supplier name, organisation number and the supplier's former names,
    framework area, sub-area path and the dates the agreement is valid from
    and to and may be extended to.
    `total` counts every matching row, also beyond `limit`, and `offset`
    skips rows, so the model can page through a large area. `sub_area`
    narrows to the sub-areas or regions whose path contains each of its
    parts, so "IT-tjänster / Övre Norrland" gives one region's agreements.
    `register_org_number` turns an organisation number into the register's
    spelling.

Why:
    Which agreements and suppliers there are, and when an agreement ends,
    is answered by the register, the answer key for dates and parties
    (architecture plan, section 4): a document can be older than the
    register's latest extension. The agent also needs an agreement's number
    before it can limit a search to it. The register holds nothing the
    ingestion holds back, so no visibility applies.

How:
    One query joins `agreement`, `agreement_sub_area` and `sub_area`, with
    one WHERE condition per argument; a second counts the rows. A supplier
    is a case-insensitive substring (ILIKE, with % and _ escaped) of the
    agreement's supplier name or of any name or former name the register
    has for its organisation number. An agreement number keeps the rows of
    every spelling the register has for it (`visibility.register_agreements`:
    one agreement can be written two ways on different rows). A number the
    register does not have as an agreement but as a procurement, i.e. one
    without the supplier's sequence ("23.3-5890-2023"), gives all the
    procurement's agreements (`visibility.register_procurements`, by
    `procurement_key`, so "23.3.5890-23" finds it too). A framework area
    goes through `register_area`. A sub-area is split on "/" into parts,
    and the path must contain every part, in any order, case-insensitively
    (ILIKE, % and _ escaped): the model writes the levels it knows, as
    "IT-tjänster / Övre Norrland", while the register's path is
    "Bemanningstjänster / Bemanningstjänster - IT-tjänster upp till 1000
    timmar / Övre Norrland". Only when nothing matches does a fourth query
    ask whether any sub-area has those parts; if none has, the error lists
    the area's sub-areas, since an empty list would read as "no supplier
    there". An organisation number is
    normalised as M1 stores it (`normalize_org_number`: NNNNNN-NNNN, or a
    foreign number without spaces). Rows are sorted by framework area,
    agreement number and sub-area before `offset` and `limit` cut them. A
    third query reads the former names ("f.d." in the register) of the
    page's organisation numbers, so the model can answer "har bolaget hetat
    något annat?" from the row.
"""

from datetime import date

from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.identifiers import IdentifierError, normalize_org_number
from avtalsagent.mcp_server.arguments import (
    AgreementNumber,
    FrameworkArea,
    Limit,
    Offset,
    OrgNumber,
    SubAreaArg,
    Supplier,
    ValidOn,
)
from avtalsagent.mcp_server.errors import MissingArgumentError, NotFoundError
from avtalsagent.mcp_server.visibility import (
    register_agreements,
    register_area,
    register_procurements,
)


class RegisterRow(BaseModel):
    """One agreement in one sub-area, as the register has it."""

    agreement_number: str  # e.g. "23.3-5890-2023-003"
    procurement_number: str  # the procurement's case number, e.g. "23.3-5890-2023"
    supplier_name: str  # the name on the agreement's rows
    org_number: str  # NNNNNN-NNNN, or a foreign number as written
    former_names: list[str]  # the register's former names ("f.d.") for the org number, sorted
    framework_area: str
    sub_area: str  # the path, levels joined with " / "
    valid_from: date
    valid_to: date
    max_extension_to: date | None  # the latest date it may be extended to; None if not set


class RegisterResult(BaseModel):
    rows: list[RegisterRow]  # by framework area, agreement number and sub-area
    total: int  # every matching row, also those skipped by the offset or beyond the limit


def search_register(
    session: Session,
    supplier: Supplier = None,
    agreement_number: AgreementNumber = None,
    framework_area: FrameworkArea = None,
    sub_area: SubAreaArg = None,
    org_number: OrgNumber = None,
    valid_on: ValidOn = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> RegisterResult:
    """Sök i registret över giltiga ramavtal: avtal, leverantörer, områden och giltighetstider.

    Ange minst ett av supplier (namnet eller en del av det), agreement_number, framework_area,
    sub_area och org_number; valid_on behåller bara avtal som gäller det datumet. Ett nummer
    utan leverantörens löpnummer, som '23.3-5890-2023', ger alla avtal i den upphandlingen.
    sub_area behåller delområden eller regioner som innehåller texten, t.ex. 'Övre Norrland';
    'IT-tjänster / Övre Norrland' kräver båda delarna. Använd det när frågan gäller ett
    delområde eller en region, så att du får alla leverantörer där och inte bara de första. Datum
    och leverantörer kommer från registret, som går före dokumenten; vad avtalen säger
    (villkor, priser, viten) söker du med search_documents. Varje rad är ett avtal i ett
    delområde, och total är antalet rader, även de som inte ryms i limit. Är total större än
    raderna du fått, hämta nästa sida med offset (t.ex. offset=20 för rad 21–40).
    former_names är leverantörens tidigare namn enligt registret.
    """
    # Spaces as the register stores names (`parse_supplier_name`); blank is no name.
    name = None if supplier is None else " ".join(supplier.split()) or None
    parts = [] if sub_area is None else sub_area_parts(sub_area)
    if (
        name is None
        and agreement_number is None
        and framework_area is None
        and not parts
        and org_number is None
    ):
        raise MissingArgumentError(
            "Ange minst ett av supplier, agreement_number, framework_area, sub_area och "
            "org_number; valid_on begränsar bara de andra."
        )
    agreement, link, area = models.Agreement, models.AgreementSubArea, models.SubArea
    area_name: str | None = None
    conditions: list[ColumnElement[bool]] = []
    if org_number is not None:
        conditions.append(agreement.org_number == register_org_number(org_number))
    if name is not None:
        conditions.append(_supplier_matches(name))
    if agreement_number is not None:
        conditions.append(_agreement_matches(session, agreement_number))
    if framework_area is not None:
        area_name = register_area(session, framework_area)
        conditions.append(area.framework_area == area_name)
    conditions += _path_matches(parts)
    if valid_on is not None:
        conditions += [link.valid_from <= valid_on, link.valid_to >= valid_on]
    query = (
        select(
            agreement.agreement_number,
            agreement.procurement_number,
            agreement.supplier_name,
            agreement.org_number,
            area.framework_area,
            area.path.label("sub_area"),
            link.valid_from,
            link.valid_to,
            link.max_extension_to,
        )
        .join(link, link.agreement_number == agreement.agreement_number)
        .join(area, area.id == link.sub_area_id)
        .where(*conditions)
    )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    if total == 0 and parts:
        _require_sub_area(session, " / ".join(parts), parts, area_name)
    rows = list(
        session.execute(
            query.order_by(area.framework_area, agreement.agreement_number, area.path)
            .offset(offset)
            .limit(limit)
        )
    )
    former_names = _former_names(session, {row.org_number for row in rows})
    return RegisterResult(
        rows=[
            RegisterRow(
                agreement_number=row.agreement_number,
                procurement_number=row.procurement_number,
                supplier_name=row.supplier_name,
                org_number=row.org_number,
                former_names=former_names.get(row.org_number, []),
                framework_area=row.framework_area,
                sub_area=row.sub_area,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                max_extension_to=row.max_extension_to,
            )
            for row in rows
        ],
        total=total,
    )


def register_org_number(org_number: str) -> str:
    """The organisation number as the register stores it; `NotFoundError` when unreadable."""
    try:
        return normalize_org_number(org_number)
    except IdentifierError:
        raise NotFoundError(
            f"Organisationsnumret '{org_number}' kan inte läsas. Skriv ett svenskt nummer som "
            "NNNNNN-NNNN, eller sök på leverantörens namn med supplier."
        ) from None


def sub_area_parts(sub_area: str) -> list[str]:
    """The parts of a sub-area filter: split on "/", spaces collapsed, blank parts dropped.

    Example: "IT-tjänster /  Övre Norrland" -> ["IT-tjänster", "Övre Norrland"].
    A "/" inside a level ("Arlanda/Arlandastad") splits it too, which is
    harmless: both halves are still in the same path.
    """
    return [part for raw in sub_area.split("/") if (part := " ".join(raw.split()))]


def _path_matches(parts: list[str]) -> list[ColumnElement[bool]]:
    """One condition per part: the sub-area's full path contains it, in any case."""
    # autoescape: a % or _ in the text is that character, not a LIKE wildcard.
    return [models.SubArea.path.icontains(part, autoescape=True) for part in parts]


# Listing more sub-area names than this in an error would flood the model.
_MAX_LISTED_SUB_AREAS = 40


def _require_sub_area(
    session: Session, sub_area: str, parts: list[str], framework_area: str | None
) -> None:
    """`NotFoundError` when no sub-area path has every part; returns when one has.

    Called only when the search found no rows, so a sub-area that exists but
    is excluded by the other filters still gives an empty list.
    """
    area = models.SubArea
    in_area = [] if framework_area is None else [area.framework_area == framework_area]
    if session.scalar(select(area.id).where(*in_area, *_path_matches(parts)).limit(1)):
        return
    where = "" if framework_area is None else f" i {framework_area}"
    message = f"Inget delområde{where} innehåller '{sub_area}'."
    if framework_area is None:
        raise NotFoundError(
            f"{message} Ange framework_area, så listas områdets delområden, eller sök med "
            "en kortare del av namnet."
        )
    names = sorted(
        set(session.scalars(select(area.name).where(*in_area, area.level > 1).distinct()))
    )
    if not names:
        raise NotFoundError(f"{message} Området har inga delområden; sök utan sub_area.")
    if len(names) > _MAX_LISTED_SUB_AREAS:
        raise NotFoundError(
            f"{message} Området har {len(names)} delområden; sök med en kortare del av namnet, "
            f"eller med search_register(framework_area='{framework_area}') och läs sub_area."
        )
    raise NotFoundError(f"{message} Delområden: {'; '.join(names)}.")


def _supplier_matches(name: str) -> ColumnElement[bool]:
    """The agreement's supplier name, or a name its org number has, contains `name`."""
    agreement, names = models.Agreement, models.SupplierName
    # autoescape: a % or _ in the name is that character, not a LIKE wildcard.
    org_numbers = select(names.org_number).where(
        or_(
            names.name.icontains(name, autoescape=True),
            names.former_name.icontains(name, autoescape=True),
        )
    )
    return or_(
        agreement.supplier_name.icontains(name, autoescape=True),
        agreement.org_number.in_(org_numbers),
    )


def _former_names(session: Session, org_numbers: set[str]) -> dict[str, list[str]]:
    """The former names the register has for each organisation number, sorted."""
    if not org_numbers:
        return {}
    names = models.SupplierName
    found: dict[str, list[str]] = {}
    for org_number, former_name in session.execute(
        select(names.org_number, names.former_name)
        .where(names.org_number.in_(sorted(org_numbers)), names.former_name.is_not(None))
        .distinct()
        .order_by(names.org_number, names.former_name)
    ):
        if former_name is not None:  # never, after the WHERE; the column allows it
            found.setdefault(org_number, []).append(former_name)
    return found


def _agreement_matches(session: Session, number: str) -> ColumnElement[bool]:
    """The agreement with this number in any spelling, or every agreement of the procurement."""
    agreement = models.Agreement
    if spellings := register_agreements(session, number):
        return agreement.agreement_number.in_(spellings)
    procurements = register_procurements(session, number)
    if not procurements:
        raise NotFoundError(
            f"Numret {number} finns inte i registret, varken som avtal eller som upphandling. "
            "Sök på leverantörens namn med supplier eller på ramavtalsområdet med framework_area."
        )
    return agreement.procurement_number.in_(procurements)
