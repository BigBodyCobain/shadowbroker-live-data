#!/usr/bin/env python3
"""Wait until the freshly started collector has enough data to be worth
publishing, so a new run never replaces a full map with a half-empty one.

Ready means: the bridge has written a manifest, and either every layer in
--need has at least its minimum count, or --max-wait seconds have passed
(then it publishes what there is). Exits 1 only if no data appears at all.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def counts(data_dir: Path) -> dict[str, int]:
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    found = {name: int(ref.get("count") or 0) for name, ref in (manifest.get("keys") or {}).items()}
    for ref in (manifest.get("bundles") or {}).values():
        bundle = json.loads((data_dir / ref["f"]).read_text(encoding="utf-8"))
        for name, value in bundle.items():
            if isinstance(value, (list, dict)):
                found.setdefault(name, len(value))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", required=True)
    parser.add_argument("--need", action="append", default=[], help="layer=minimum, e.g. ships=8000")
    parser.add_argument("--min-wait", type=int, default=60)
    parser.add_argument("--max-wait", type=int, default=900)
    parser.add_argument("--give-up", type=int, default=900, help="fail if there is no data at all by then")
    options = parser.parse_args(argv)
    need = {name: int(minimum) for name, minimum in (item.split("=", 1) for item in options.need)}

    started = time.time()
    last: dict[str, int] = {}
    while True:
        waited = time.time() - started
        try:
            last = counts(Path(options.data))
        except (OSError, ValueError, KeyError):
            last = {}
        short = {name: last.get(name, 0) for name, minimum in need.items() if last.get(name, 0) < minimum}
        shown = {name: last.get(name, 0) for name in need}
        if last and waited >= options.min_wait and not short:
            print(f"ready after {waited:.0f} s: {shown}")
            return 0
        if last and waited >= options.max_wait:
            print(f"publishing what there is after {waited:.0f} s; still short: {short}")
            return 0
        if not last and waited >= options.give_up:
            print("the collector produced no data; stopping this run")
            return 1
        if int(waited) % 60 < 10:
            print(f"warming up, {waited:.0f} s: {shown or 'no data yet'}")
        time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
