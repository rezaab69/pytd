"""The documentation must keep working.

Docs rot quietly: a rename lands, the guide still shows the old name, and the
first person to hit it is a new user following the quickstart. These tests walk
the markdown and check that what it tells people to import is really there.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

DOCS = [*sorted(Path("docs").glob("*.md")), Path("README.md")]
CODE_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)


def python_blocks(path: Path) -> list[str]:
    return CODE_BLOCK.findall(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_every_example_is_valid_python(path: Path) -> None:
    for index, block in enumerate(python_blocks(path)):
        try:
            ast.parse(block)
        except SyntaxError as exc:
            pytest.fail(f"{path}: code block {index + 1} does not parse: {exc}")


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_every_documented_import_resolves(path: Path) -> None:
    missing: list[str] = []
    for block in python_blocks(path):
        for node in ast.walk(ast.parse(block)):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("tdpy"):
                continue
            module = importlib.import_module(node.module)
            missing += [
                f"{node.module}.{alias.name}"
                for alias in node.names
                if not hasattr(module, alias.name)
            ]
    assert not missing, f"{path} documents names that do not exist: {missing}"


def test_the_docs_index_links_to_real_files() -> None:
    quickstart = Path("docs/quickstart.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\((\w[\w-]*\.md)\)", quickstart)

    absent = [name for name in links if not (Path("docs") / name).is_file()]
    assert not absent, f"quickstart links to missing docs: {absent}"


def test_the_readme_links_to_real_paths() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\((src/[^)]+|docs/[^)]+|tools/[^)]+)\)", readme)

    absent = [target for target in links if not Path(target).exists()]
    assert not absent, f"README links to missing paths: {absent}"
