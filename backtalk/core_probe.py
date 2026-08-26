# SPDX-License-Identifier: AGPL-3.0-or-later
"""Print the selected core and validate the runtime before voice startup."""
from __future__ import annotations

import argparse
import asyncio
import json

from backtalk.brain import PROVIDER, SESSION_FILE, WarmBrain
from backtalk.core_registry import known_providers


async def _probe(do_start: bool) -> dict:
    brain = WarmBrain()
    caps = getattr(brain.capabilities, "as_dict", lambda: {})()
    result = {
        "provider": PROVIDER,
        "provider_name": brain.provider_name,
        "model": brain.model,
        "session_file": SESSION_FILE,
        "known_providers": list(known_providers()),
        "capabilities": caps,
        "runtime_check": "skipped",
    }
    if do_start:
        try:
            await brain.start()
            result["runtime_check"] = "ok"
        except Exception as exc:
            result["runtime_check"] = "failed"
            result["error"] = str(exc)
        finally:
            try:
                await brain.stop()
            except Exception:
                pass
    return result


def main():
    p = argparse.ArgumentParser(description="Inspect Backtalk's selected core")
    p.add_argument("--check", action="store_true",
                   help="also start the provider far enough to verify its binary/runtime")
    p.add_argument("--json", action="store_true", dest="as_json")
    args = p.parse_args()
    data = asyncio.run(_probe(args.check))
    if args.as_json:
        print(json.dumps(data, indent=2))
    else:
        print(f"provider: {data['provider']} ({data['provider_name']})")
        print(f"model: {data['model'] or '(provider default)'}")
        print(f"runtime check: {data['runtime_check']}")
        print("capabilities:")
        for key, value in sorted(data["capabilities"].items()):
            print(f"  {key}: {value}")
        if data.get("error"):
            print(f"error: {data['error']}")
    if args.check and data["runtime_check"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
