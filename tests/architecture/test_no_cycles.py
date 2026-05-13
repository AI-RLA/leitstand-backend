"""Detect import cycles within leitstand_backend via AST analysis."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "leitstand_backend"
PKG = "leitstand_backend"


def _module_name(py_file: Path) -> str:
    rel = py_file.relative_to(ROOT.parent)
    parts = list(rel.parts)
    parts[-1] = parts[-1][:-3]  # strip .py
    return ".".join(parts)


def _direct_imports(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text())
    deps: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PKG + "."):
                    deps.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(PKG + "."):
                deps.append(node.module)
    return deps


def _build_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for py_file in ROOT.rglob("*.py"):
        mod = _module_name(py_file)
        for dep in _direct_imports(py_file):
            if dep != mod:
                graph[mod].add(dep)
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    visited: set[str] = set()
    stack: set[str] = set()

    def dfs(node: str) -> list[str] | None:
        visited.add(node)
        stack.add(node)
        for neighbour in graph.get(node, ()):
            if neighbour not in visited:
                result = dfs(neighbour)
                if result is not None:
                    return result
            elif neighbour in stack:
                return [neighbour]
        stack.discard(node)
        return None

    for node in list(graph):
        if node not in visited:
            cycle = dfs(node)
            if cycle is not None:
                return cycle
    return None


def test_no_import_cycles() -> None:
    graph = _build_graph()
    cycle = _find_cycle(graph)
    assert cycle is None, f"Import cycle detected involving: {cycle}"
