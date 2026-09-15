"""The drift gate: committed code must match the schema it claims to come from."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tdpy.codegen.__main__ import main, render

SCHEMA = Path("tools/td_api.tl")


def test_committed_api_matches_the_schema() -> None:
    # The whole point of --check: a schema bump that nobody regenerated should
    # fail CI rather than ship a silently stale API.
    assert main(["--check"]) == 0


def test_check_fails_when_the_output_is_stale(tmp_path: Path) -> None:
    (tmp_path / "types.py").write_text("# stale", encoding="utf-8")

    assert main(["--check", "--output", str(tmp_path)]) == 1


def test_check_fails_when_nothing_is_generated(tmp_path: Path) -> None:
    assert main(["--check", "--output", str(tmp_path)]) == 1


def test_a_missing_schema_is_reported(tmp_path: Path) -> None:
    assert main(["--schema", str(tmp_path / "absent.tl")]) == 2


def test_generation_is_deterministic() -> None:
    # Non-deterministic output would make --check fail at random.
    text = SCHEMA.read_text(encoding="utf-8")
    assert render(text) == render(text)


def test_every_module_records_the_schema_digest() -> None:
    from tdpy.codegen.emit import schema_digest

    digest = schema_digest(SCHEMA.read_text(encoding="utf-8"))
    for name in ("types.py", "functions.py", "methods.py"):
        header = (Path("src/tdpy/api") / name).read_text(encoding="utf-8")[:400]
        assert digest in header, f"{name} does not record which schema built it"


@pytest.mark.parametrize("module", ["tdpy", "tdpy.core"])
def test_importing_the_core_does_not_pull_the_generated_api(module: str) -> None:
    # types.py costs ~200ms to import. Paying that just to `import tdpy` would
    # be a tax on every user who never touches the typed layer.
    code = f"import {module}, sys; print('tdpy.api.types' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "False"


def test_generated_api_imports_within_budget() -> None:
    code = (
        "import time; t=time.perf_counter();"
        "import tdpy.api.types, tdpy.api.functions, tdpy.api.methods;"
        "print((time.perf_counter()-t)*1000)"
    )
    # Warm the bytecode cache first so this measures import, not compilation.
    subprocess.run([sys.executable, "-c", code], capture_output=True, check=True)
    best = min(
        float(
            subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, check=True
            ).stdout
        )
        for _ in range(3)
    )

    assert best < 1500, f"generated API import regressed to {best:.0f} ms"


def test_regeneration_round_trips_through_disk(tmp_path: Path) -> None:
    assert main(["--output", str(tmp_path)]) == 0
    assert main(["--check", "--output", str(tmp_path)]) == 0
    assert {p.name for p in tmp_path.iterdir()} == {"types.py", "functions.py", "methods.py"}
