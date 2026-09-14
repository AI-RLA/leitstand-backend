"""Content digest of a stage list, so two runs can be told to have driven the same ground."""

import hashlib
import json
from typing import Any, Sequence

from pydantic import BaseModel

# Neither identity nor how a stage was produced is part of what it does, so two plans of the same
# geometry digest the same whenever they were planned.
_UNDIGESTED_KEYS = frozenset({"stage_id", "provenance"})


def stages_digest(stages: Sequence[Any]) -> str:
    """Return a sha256 over what the stages do, ignoring identity and provenance.

    Both are stripped at every depth, cleanup stages included, so stages re-authored with fresh
    ids, or an edit later reverted, digest the same as the original. Keys are sorted and the
    serialisation is compact, so the digest does not depend on how the input was produced.
    """
    dumped = [
        stage.model_dump(mode="json") if isinstance(stage, BaseModel) else stage for stage in stages
    ]
    canonical = json.dumps(_undigested(dumped), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _undigested(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _undigested(v) for k, v in value.items() if k not in _UNDIGESTED_KEYS}
    if isinstance(value, list):
        return [_undigested(item) for item in value]
    return value
