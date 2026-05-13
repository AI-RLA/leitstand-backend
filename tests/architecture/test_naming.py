"""Naming convention checks via AST analysis."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "leitstand_backend"


def _public_class_names(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text())
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    ]


def test_orm_model_classes_end_in_row() -> None:
    models_file = ROOT / "adapters/outbound/persistence/postgres/models.py"
    skip = {"Base"}
    violations = [
        name
        for name in _public_class_names(models_file)
        if name not in skip and not name.endswith("Row")
    ]
    assert violations == [], f"ORM classes without 'Row' suffix: {violations}"


def test_port_files_define_abstract_interface() -> None:
    violations: list[str] = []
    for py_file in (ROOT / "ports").rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        source = py_file.read_text()
        if "ABC" not in source and "abstractmethod" not in source:
            violations.append(str(py_file.relative_to(ROOT)))
    assert violations == [], f"Port files without ABC/abstractmethod: {violations}"
