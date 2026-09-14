"""Every status write must say what caused it, or the transition log has holes."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "leitstand_backend"

_CALL = re.compile(r"\.update_status\((?P<args>.*?)\)\s*$", re.S | re.M)


def _calls(source: str) -> list[str]:
    """Return the argument text of every ``.update_status(...)`` call, multi-line included."""
    found: list[str] = []
    for match in re.finditer(r"\.update_status\(", source):
        depth, i = 1, match.end()
        while depth and i < len(source):
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            i += 1
        found.append(source[match.end() : i - 1])
    return found


def test_every_status_write_passes_its_trigger() -> None:
    missing: list[str] = []
    for py_file in ROOT.rglob("*.py"):
        for args in _calls(py_file.read_text()):
            if "trigger=" not in args:
                missing.append(
                    f"{py_file.relative_to(ROOT)}: update_status({args.strip()[:60]}...)"
                )
    assert missing == [], "\n".join(missing)
