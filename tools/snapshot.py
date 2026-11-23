#!/usr/bin/env python3
"""Wait for the next publish slot, then copy the bridge's current data files
into a folder that is ready to publish.

Only the files the bridge's own manifest names are copied, and the manifest
is read first, so a published file is never older than the manifest that
points at it. Nothing else from the runner ever reaches the published site.

Writes ``publish=true|false`` and ``stop=true|false`` to $GITHUB_OUTPUT.

Standard library only.
"""

from __future__ import annotations

import argparse
import calendar
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

SAFE_PATH = re.compile(r"[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*\.json")
MAX_FILE_BYTES = 90 * 1024 * 1024
MAX_TOTAL_BYTES = 400 * 1024 * 1024
INDEX_HTML = """<!doctype html>
<meta charset="utf-8">
<title>ShadowBroker public data</title>
<p>Machine-written data files for the ShadowBroker public map.
See <a href="https://shadowbroker.dev/">shadowbroker.dev</a>.</p>
"""


def output(**values: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{name}={value}\n" for name, value in values.items())
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(lines)
    sys.stdout.write(lines)


def manifest_files(manifest: dict) -> list[str]:
    refs = list((manifest.get("bundles") or {}).values())
    refs += list((manifest.get("keys") or {}).values())
    refs += list(((manifest.get("index") or {}).get("files") or {}).values())
    out: list[str] = []
    for ref in refs:
        rel = str(ref.get("f", "")) if isinstance(ref, dict) else ""
        if not SAFE_PATH.fullmatch(rel) or rel == "manifest.json":
            raise ValueError(f"the manifest names an unexpected file path: {rel!r}")
        if rel not in out:
            out.append(rel)
    return out


def manifest_age_s(manifest: dict) -> float:
    stamp = time.strptime(str(manifest.get("generated_at", "")), "%Y-%m-%dT%H:%M:%SZ")
    return time.time() - calendar.timegm(stamp)


# A small thinned-out copy of the moving layers, for the website's front-page globe.
# It is built only from the files being published in this same snapshot.
GLOBE_CAPS = {"aircraft": 1800, "military": 300, "tracked": 400, "ships": 1800, "satellites": 600, "fires": 400}


def _sample(items, cap: int, extra=None) -> list:
    """Up to ``cap`` [lat, lng] pairs, taken evenly across the list."""
    if not isinstance(items, list) or not items:
        return []
    step = max(1, math.ceil(len(items) / cap))
    out = []
    for item in items[::step]:
        if not isinstance(item, dict):
            continue
        lat, lng = item.get("lat"), item.get("lng")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        row = [round(lat, 2), round(lng, 2)]
        if extra:
            row.append(extra(item))
        out.append(row)
    return out


def build_globe(manifest: dict, files: dict) -> bytes:
    """The globe file as JSON bytes. Raises on unexpected data; the caller treats that as 'no globe file'."""
    bundles: dict = {}

    def layer(name: str):
        ref = (manifest.get("keys") or {}).get(name)
        if isinstance(ref, dict) and ref.get("f") in files:
            return json.loads(files[ref["f"]].decode("utf-8"))
        for bundle_ref in (manifest.get("bundles") or {}).values():
            path = bundle_ref.get("f")
            if path not in bundles:
                bundles[path] = json.loads(files[path].decode("utf-8"))
            if isinstance(bundles[path], dict) and name in bundles[path]:
                return bundles[path][name]
        return None

    def count(value) -> int:
        return len(value) if isinstance(value, list) else 0

    civil = []
    for name in ("commercial_flights", "private_flights", "private_jets"):
        civil += layer(name) or []
    military, tracked = layer("military_flights"), layer("tracked_flights")
    ships, sats, fires = layer("ships"), layer("satellites"), layer("firms_fires")
    hot = sorted((f for f in fires or [] if isinstance(f, dict)), key=lambda f: -(f.get("frp") or 0))
    globe = {
        "schema": 1,
        "generated_at": manifest.get("generated_at"),
        "totals": {
            "aircraft": len(civil) + count(military) + count(tracked),
            "ships": count(ships),
            "satellites": count(sats),
            "fires": count(fires),
        },
        "aircraft": _sample(civil, GLOBE_CAPS["aircraft"]),
        "military": _sample(military, GLOBE_CAPS["military"]),
        "tracked": _sample(tracked, GLOBE_CAPS["tracked"]),
        "ships": _sample(ships, GLOBE_CAPS["ships"]),
        "satellites": _sample(sats, GLOBE_CAPS["satellites"], lambda s: round(float(s.get("alt_km") or 550))),
        "fires": _sample(hot[: GLOBE_CAPS["fires"]], GLOBE_CAPS["fires"]),
    }
    return json.dumps(globe, separators=(",", ":")).encode("utf-8")


def snapshot(data_dir: Path, site_dir: Path) -> dict:
    """Copy the manifest and the files it names. Returns the manifest."""
    blob = (data_dir / "manifest.json").read_bytes()
    manifest = json.loads(blob.decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("the manifest is not in the expected format")
    files = {"manifest.json": blob}
    for rel in manifest_files(manifest):
        files[rel] = (data_dir / rel).read_bytes()
    total = sum(len(b) for b in files.values())
    if any(len(b) > MAX_FILE_BYTES for b in files.values()) or total > MAX_TOTAL_BYTES:
        raise ValueError("the data is larger than this step will publish")
    if site_dir.exists():
        shutil.rmtree(site_dir)
    for rel, content in files.items():
        target = site_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (site_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    try:
        (site_dir / "globe.json").write_bytes(build_globe(manifest, files))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"no globe file this round: {type(exc).__name__}")
    manifest["_files"] = len(files)
    manifest["_bytes"] = total
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", required=True, help="the bridge's output folder")
    parser.add_argument("--site", required=True, help="folder to fill for publishing")
    parser.add_argument("--state", required=True, help="small file remembering the last publish")
    parser.add_argument("--every", type=int, default=180, help="seconds between publishes")
    parser.add_argument("--until", type=float, required=True, help="clock time (epoch seconds) to stop publishing")
    parser.add_argument("--max-age", type=int, default=180, help="skip if the bridge's manifest is older than this")
    options = parser.parse_args(argv)

    state_path = Path(options.state)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}

    if state.get("stopped"):
        output(publish="false", stop="true")
        return 0

    wait = float(state.get("last_start", 0)) + max(60, options.every) - time.time()
    if time.time() + max(0.0, wait) >= options.until:
        state["stopped"] = "time is up"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        output(publish="false", stop="true")
        return 0
    if wait > 0:
        time.sleep(wait)

    state["last_start"] = time.time()
    try:
        manifest = snapshot(Path(options.data), Path(options.site))
        age = manifest_age_s(manifest)
        seq = int(manifest.get("seq") or 0)
        if age > options.max_age:
            raise ValueError(f"the collector's data is {age:.0f} s old; it may have stopped")
        if seq == state.get("last_seq"):
            raise ValueError("the data has not changed since the last publish")
    except (OSError, ValueError) as exc:
        state["misses"] = int(state.get("misses", 0)) + 1
        if state["misses"] >= 5:  # a collector that stays silent is not coming back in this run
            state["stopped"] = "the collector went quiet"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        print(f"not publishing this round: {exc}")
        output(publish="false", stop="true" if state.get("stopped") else "false")
        return 0

    state.update({"last_seq": seq, "misses": 0})
    state_path.write_text(json.dumps(state), encoding="utf-8")
    counts = {name: ref.get("count") for name, ref in (manifest.get("keys") or {}).items() if "count" in ref}
    print(f"publishing seq {seq}: {manifest['_files']} files, {manifest['_bytes'] / 1e6:.1f} MB, counts {counts}")
    output(publish="true", stop="false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
