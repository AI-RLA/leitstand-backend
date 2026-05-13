"""Write the FastAPI OpenAPI spec to openapi.json at the repo root.

The committed openapi.json is the wire-contract source the frontend repo
pulls from (via scripts/codegen.sh on a pinned ref). Run before committing
any wire-contract change.
"""

from __future__ import annotations

import json
import pathlib

from leitstand_backend.infrastructure.factory import create_app


def main() -> None:
    spec = create_app().openapi()
    out = pathlib.Path(__file__).resolve().parents[1] / "openapi.json"
    out.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"[dump_openapi] wrote {out}")


if __name__ == "__main__":
    main()
