"""Verify hexagonal layer boundaries via AST import analysis.

Rules enforced:
  domain       — no imports from ports/adapters/application/infrastructure
  ports        — no imports from adapters/application/infrastructure
  application  — no imports from adapters/infrastructure
  domain/ports/application — only stdlib, leitstand_backend internals,
                             or explicitly-allowlisted external libraries
                             (currently: pydantic). Future external
                             dependencies are caught by default and
                             require deliberate allowlist amendment.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "leitstand_backend"


def _leitstand_imports(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("leitstand_backend."):
                    modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("leitstand_backend."):
                modules.append(node.module)
    return modules


def _layer(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if len(parts) >= 2 else None


def _violations(subdir: str, forbidden: set[str]) -> list[str]:
    found: list[str] = []
    for py_file in (ROOT / subdir).rglob("*.py"):
        for mod in _leitstand_imports(py_file):
            if _layer(mod) in forbidden:
                found.append(f"{py_file.relative_to(ROOT)}: imports {mod}")
    return found


def test_domain_does_not_import_other_layers() -> None:
    violations = _violations("domain", {"ports", "adapters", "application", "infrastructure"})
    assert violations == [], "\n".join(violations)


def test_ports_do_not_import_adapters_application_or_infrastructure() -> None:
    violations = _violations("ports", {"adapters", "application", "infrastructure"})
    assert violations == [], "\n".join(violations)


def test_application_does_not_import_adapters_or_infrastructure() -> None:
    violations = _violations("application", {"adapters", "infrastructure"})
    assert violations == [], "\n".join(violations)


# Explicit allowlist of external libraries that core layers may import.
# Stdlib and leitstand_backend internals are always permitted; anything
# else here is a deliberate architectural decision. Add entries only
# after architectural review.
_CORE_LAYER_ALLOWED_EXTERNALS = frozenset(
    {
        "pydantic",  # domain modeling (Robot, Field, Commands, etc.)
        "geojson_pydantic",  # Pydantic-typed GeoJSON for Field.geometry
        "structlog",  # logging — cross-cutting; ADR 0007 "earn it" excludes
    }
)

_CORE_LAYERS = ("domain", "ports", "application")


def _top_level_imports(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module.split(".")[0])
    return modules


def _is_permitted_in_core(top_level_module: str) -> bool:
    """A top-level module name is permitted in core layers if it is:
    - The package itself (`leitstand_backend`)
    - A Python stdlib module (sys.stdlib_module_names; Python 3.10+)
    - Explicitly allowlisted in _CORE_LAYER_ALLOWED_EXTERNALS
    """
    if top_level_module == "leitstand_backend":
        return True
    if top_level_module in sys.stdlib_module_names:
        return True
    if top_level_module in _CORE_LAYER_ALLOWED_EXTERNALS:
        return True
    return False


def test_core_layers_only_import_allowlisted_externals() -> None:
    """domain/ports/application may only import stdlib, leitstand_backend
    internals, or explicitly-allowlisted external libraries.

    Future-proof against new infrastructure frameworks (kafka, redis,
    httpx, anthropic, etc.) — they are caught by default rather than
    requiring the blocklist to be expanded. Adding a new external
    library to a core layer requires a deliberate allowlist amendment
    in this file, which forces architectural review.

    Locks ADR 0017 + 0018's intent machine-side; prevents drift back to
    the AsyncSession-in-service pattern fixed by the transactional_scope
    boundary-owned-transaction refactor.
    """
    violations: list[str] = []
    for layer in _CORE_LAYERS:
        for py_file in (ROOT / layer).rglob("*.py"):
            for mod in _top_level_imports(py_file):
                if not _is_permitted_in_core(mod):
                    violations.append(f"{py_file.relative_to(ROOT)}: imports {mod}")
    assert violations == [], "\n".join(violations)
