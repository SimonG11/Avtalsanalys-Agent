"""Tests for the demo's example contract (examples/uppladdning): it reads into its clauses.

The Markdown and the PDF its script renders are read by the API's parser
into the text before the first heading and the nine numbered clauses, the
PDF's with their pages; the clauses the README says deviate are those of
the contract; the script never writes into the repository.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from avtalsagent.uploads.parse import ParsedFile, parse_upload
from tests.unit.agent.uploaded import LIMITS

HERE = Path(__file__).parents[3] / "examples" / "uppladdning"
CONTRACT = HERE / "exempelavtal-it-konsult.md"
CLAUSES = [
    (None, "Text före första rubriken"),
    ("1", "Parter och bakgrund"),
    ("2", "Uppdrag och kontraktstid"),
    ("3", "Pris och ersättning"),
    ("4", "Prisjustering"),
    ("5", "Fakturering och betalning"),
    ("6", "Vite vid försening"),
    ("7", "Skadestånd och ansvarsbegränsning"),
    ("8", "Uppsägning"),
    ("9", "Tillämplig lag och tvister"),
]


def render_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("render_pdf", HERE / "render_pdf.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clauses(parsed: ParsedFile) -> list[tuple[str | None, str]]:
    return [(section.number, section.title) for section in parsed.sections]


def test_the_markdown_reads_into_its_clauses_and_says_it_is_fictional() -> None:
    parsed = parse_upload(CONTRACT.read_bytes(), CONTRACT.name, LIMITS)

    assert clauses(parsed) == CLAUSES
    assert "Fiktivt exempel" in parsed.sections[0].text
    assert "Begränsningen gäller även vid grov oaktsamhet." in " ".join(
        parsed.sections[7].text.split()
    )


def test_the_rendered_pdf_reads_into_the_same_clauses_with_their_pages(tmp_path: Path) -> None:
    out = tmp_path / "exempelavtal.pdf"
    render_script().render(CONTRACT.read_text(encoding="utf-8"), out)

    parsed = parse_upload(out.read_bytes(), out.name, LIMITS)

    assert clauses(parsed) == CLAUSES
    assert parsed.pages == 2
    assert parsed.warnings == ()
    assert [section.page_start for section in parsed.sections] == [1] * 8 + [2] * 2


def test_the_script_does_not_write_into_the_repository() -> None:
    with pytest.raises(SystemExit, match="utanför repot"):
        render_script().main([str(HERE / "exempelavtal-it-konsult.pdf")])

    assert not (HERE / "exempelavtal-it-konsult.pdf").exists()


def test_the_readme_names_clauses_the_contract_has() -> None:
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    titles = {f"{number} {title}" for number, title in CLAUSES if number}

    deviating = readme.split("## Klausuler som avviker")[1].split("## Klausuler som stämmer")[0]
    named = {row.split("|")[1].strip() for row in deviating.splitlines() if row.startswith("| ")}

    assert named - {"Exempelavtalet"} <= titles
    assert len(named - {"Exempelavtalet"}) == 5
