"""Standalone diagnostic: dial localhost zenohd, dump what's there.

Use this when the server isn't seeing registrations to localize the
failure. Example::

    .venv/bin/python scripts/probe.py
    .venv/bin/python scripts/probe.py tcp/192.168.126.10:7447   # remote

The probe opens its own Zenoh client session, scouts for peers and
routers, queries existing liveliness tokens on
``**/leitstand/online``, and queries metadata for each robot it
finds. Output tells you which layer is broken.
"""

from __future__ import annotations

import json
import sys

import zenoh

DEFAULT_ENDPOINT = "tcp/127.0.0.1:7447"


def main() -> int:
    endpoint = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ENDPOINT
    print(f"[probe] dialing {endpoint}")

    cfg = zenoh.Config()
    cfg.insert_json5("mode", '"client"')
    cfg.insert_json5("connect/endpoints", json.dumps([endpoint]))
    cfg.insert_json5("scouting/multicast/enabled", "false")

    try:
        session = zenoh.open(cfg)
    except Exception as e:
        print(f"[probe] FAIL session open: {e}")
        print(f"[probe] is zenohd actually listening on {endpoint}?")
        return 1

    try:
        zid = session.info.zid()
        print(f"[probe] session zid={zid}")
    except Exception as e:
        print(f"[probe] could not read session info: {e}")

    print("[probe] querying liveliness tokens on leitstand/robot/**/online ...")
    found_robots: list[str] = []
    try:
        replies = session.liveliness().get("leitstand/robot/**/online", timeout=2.0)
        for reply in replies:
            ok = getattr(reply, "ok", reply)
            err = getattr(reply, "err", None)
            if err is not None:
                print(f"[probe]   liveliness reply error: {err}")
                continue
            key = str(getattr(ok, "key_expr", "?"))
            print(f"[probe]   token: {key}")
            _prefix = "leitstand/robot/"
            _suffix = "/online"
            if key.startswith(_prefix) and key.endswith(_suffix):
                found_robots.append(key[len(_prefix) : -len(_suffix)])
    except Exception as e:
        print(f"[probe] FAIL liveliness query: {e}")
        session.close()
        return 1

    if not found_robots:
        print("[probe] no liveliness tokens found.")
        print("[probe] either the registrar isn't running, isn't")
        print("[probe] connected to this zenohd, or is publishing under")
        print("[probe] a different key shape.")
        session.close()
        return 2

    for robot_id in found_robots:
        meta_key = f"leitstand/robot/{robot_id}/metadata"
        print(f"[probe] querying metadata: {meta_key}")
        try:
            replies = session.get(meta_key, timeout=2.0)
            for reply in replies:
                ok = getattr(reply, "ok", reply)
                err = getattr(reply, "err", None)
                if err is not None:
                    print(f"[probe]   metadata reply error: {err}")
                    continue
                payload = ok.payload.to_bytes()
                print(f"[probe]   payload: {payload!r}")
        except Exception as e:
            print(f"[probe]   FAIL metadata query: {e}")

    session.close()
    print("[probe] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
