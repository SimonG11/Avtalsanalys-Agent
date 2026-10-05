"""Command line for the ingestion workflow.

What:
    `uv run python -m avtalsagent.ingestion fetch` runs step 1: it reads the
    agreement pages on avropa.se, downloads the documents of the chosen
    framework areas and prints a report. `--area` (repeatable) overrides the
    areas in the settings.

Why:
    One command per step makes each step easy to run and check on its own.
    Later milestones add the other steps here.

How:
    The register must be loaded first (M1), since the framework areas are
    turned into procurement numbers through it. Discovery and downloads happen
    before the database transaction, so a long download never holds a
    transaction open; the results are then saved in one transaction.
"""

import argparse
from collections import Counter

from avtalsagent.config import get_settings
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.ingestion.catalog import (
    load_stored_documents,
    procurements_for_areas,
    save_fetch,
)
from avtalsagent.ingestion.step1_fetch import (
    FetchStatus,
    create_client,
    discover_pages,
    fetch_documents,
    select_pages,
)


def fetch(areas: list[str]) -> None:
    settings = get_settings()
    factory = session_factory(create_db_engine())
    with factory() as session:
        by_area = procurements_for_areas(session, areas)
        stored = load_stored_documents(session)
    wanted = set().union(*by_area.values())

    http = create_client(settings.fetch_delay_seconds)
    pages, problems = discover_pages(http, settings.agreement_index_url)
    selected = select_pages(pages, wanted)
    links = [link for page in selected for link in page.documents]
    results = fetch_documents(http, links, stored, settings.data_dir, settings.fetch_file_types)

    with factory.begin() as session:
        save_fetch(session, selected, results)

    print(f"Read {len(pages)} agreement pages ({http.request_count} requests in total)")
    for problem in problems:
        print(f"  page not read: {problem.url}: {problem.message}")
    for area, numbers in by_area.items():
        area_pages = [page for page in selected if numbers.intersection(page.procurement_numbers)]
        found = set().union(*(page.procurement_numbers for page in area_pages))
        print(f"{area}: {len(area_pages)} pages, procurements {', '.join(sorted(numbers))}")
        for number in sorted(numbers - found):
            print(f"  no page found for procurement {number}")
    counts = Counter(result.status for result in results)
    print(f"Documents: {len(results)} unique links")
    for status in FetchStatus:
        print(f"  {status.value}: {counts.get(status, 0)}")
    for result in results:
        if result.status is FetchStatus.FAILED:
            print(f"  failed: {result.link.url}: {result.message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion workflow for avropa.se documents.")
    steps = parser.add_subparsers(dest="step", required=True)
    fetch_parser = steps.add_parser("fetch", help="step 1: download the documents")
    fetch_parser.add_argument(
        "--area",
        action="append",
        help="framework area as named in the register (repeatable); default from settings",
    )
    args = parser.parse_args()
    if args.step == "fetch":
        fetch(args.area or get_settings().fetch_areas)


if __name__ == "__main__":
    main()
