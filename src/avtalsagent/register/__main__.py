"""Command line: load the master list into the database.

What:
    `uv run python -m avtalsagent.register --download` downloads the list from
    avropa.se and loads it; `--file path.xlsx` loads a file already on disk.

Why:
    One command runs the whole chain, so it is easy to refresh the register
    and to see the import report.

How:
    download (optional) -> read_excel -> normalize -> load, in one database
    transaction. The report is printed at the end. Run `uv run alembic upgrade
    head` first so the tables exist.
"""

import argparse
from pathlib import Path

from avtalsagent.config import get_settings
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.register.download import download_register
from avtalsagent.register.load import load_register
from avtalsagent.register.normalize import normalize_rows
from avtalsagent.register.read_excel import read_register


def main() -> None:
    parser = argparse.ArgumentParser(description="Load 'Alla giltiga ramavtal' into Postgres.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="an xlsx file already on disk")
    source.add_argument("--download", action="store_true", help="download from avropa.se")
    args = parser.parse_args()

    settings = get_settings()
    path: Path = (
        download_register(settings.register_excel_url, settings.data_dir / "register")
        if args.download
        else args.file
    )

    raw = read_register(path)
    normalized = normalize_rows(raw.rows)

    engine = create_db_engine()
    with session_factory(engine).begin() as session:
        report = load_register(session, raw.version, normalized.rows, normalized.rejected)
    print(f"Loaded {path}")
    print(report.summary())


if __name__ == "__main__":
    main()
