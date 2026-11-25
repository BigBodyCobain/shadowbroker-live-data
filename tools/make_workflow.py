#!/usr/bin/env python3
"""Write .github/workflows/collect.yml.

A workflow cannot loop over steps, so the publish step is written out once
per slot. Run this again after changing the template or the slot count:

    python tools/make_workflow.py
"""

from pathlib import Path

SLOTS = 180  # more than one run can use; each waits for its turn and the extra ones skip

HEAD = """\
# Written by tools/make_workflow.py. Edit that file, not this one.
#
# Runs the ShadowBroker public collector on a GitHub machine and publishes its
# data files to this repository's GitHub Pages site every few minutes.
#
# Nothing here is reachable from outside: the collector listens on the
# runner's loopback only, the runner is thrown away after the run, and the
# only thing that leaves it is the set of public data files.
name: Collect and publish

on:
  workflow_dispatch:
  schedule:
    # A safety net: each run starts the next one itself when it ends.
    - cron: "17 */5 * * *"

permissions:
  contents: read

concurrency:
  group: collect
  cancel-in-progress: false

jobs:
  collect:
    runs-on: ubuntu-latest
    timeout-minutes: 355
    permissions:
      contents: read
      pages: write      # publish the data files
      id-token: write   # required by the Pages deploy step
      actions: write    # remove its own uploads
    outputs:
      restart: ${{ steps.decide.outputs.restart }}
    environment:
      name: github-pages
    env:
      RUN_MINUTES: "325"                                   # stop publishing after this long
      PUBLISH_EVERY: ${{ vars.PUBLISH_EVERY_S || '180' }}  # seconds between publishes
      SOURCE_REF: ${{ vars.SOURCE_REF || 'main' }}         # which ShadowBroker version to run
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Get ShadowBroker
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          repository: BigBodyCobain/Shadowbroker
          ref: ${{ env.SOURCE_REF }}
          path: sb
          persist-credentials: false

      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3
        with:
          enable-cache: true
          cache-dependency-glob: sb/uv.lock
      - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0
        with:
          python-version: "3.11"
      - uses: actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0
        with:
          node-version: 24

      - name: Install
        run: |
          cd sb/backend
          uv sync --frozen --no-dev
          npm ci --omit=dev --no-audit --no-fund

      - name: Start the collector
        # The feed keys exist only in this step's environment. The collector's
        # own output goes to a file on the runner and is never printed.
        env:
          OPENSKY_CLIENT_ID: ${{ secrets.OPENSKY_CLIENT_ID }}
          OPENSKY_CLIENT_SECRET: ${{ secrets.OPENSKY_CLIENT_SECRET }}
          AIS_API_KEY: ${{ secrets.AIS_API_KEY }}
          FIRMS_MAP_KEY: ${{ secrets.FIRMS_MAP_KEY }}
          GFW_API_TOKEN: ${{ secrets.GFW_API_TOKEN }}
          # Optional extras. A secret that is not set leaves its layer empty and nothing else changes.
          OPENAQ_API_KEY: ${{ secrets.OPENAQ_API_KEY }}            # air quality
          ALERTS_IN_UA_TOKEN: ${{ secrets.ALERTS_IN_UA_TOKEN }}    # Ukraine air-raid alerts
          LTA_ACCOUNT_KEY: ${{ secrets.LTA_ACCOUNT_KEY }}          # Singapore traffic cameras
          AISHUB_USERNAME: ${{ secrets.AISHUB_USERNAME }}          # backup ship source
        run: |
          cd sb/backend
          nohup uv run --no-sync python ../viewer/engine/run_public_engine.py --port 8011 \\
            > "$RUNNER_TEMP/engine.log" 2>&1 &
          nohup uv run --no-sync python ../viewer/bridge/bridge.py --engine http://127.0.0.1:8011 \\
            --out "$RUNNER_TEMP/data" --lookup-index 120 > "$RUNNER_TEMP/bridge.log" 2>&1 &
          echo "PUBLISH_UNTIL=$(( $(date +%s) + RUN_MINUTES * 60 ))" >> "$GITHUB_ENV"
          echo "PUBLISH_EVERY_S=$PUBLISH_EVERY" >> "$GITHUB_ENV"

      - name: Warm up
        run: python3 tools/warmup.py --data "$RUNNER_TEMP/data" __NEEDS__

"""

SLOT = """\
      - uses: ./.github/actions/publish
        with: { slot: "{n:03d}" }
"""

TAIL = """
      - name: Decide whether to start the next run
        id: decide
        # Only after a run that worked for a while. If something is broken, the
        # timer above tries again later instead of restarting in a tight loop.
        run: |
          if [ "$(date +%s)" -ge "$(( PUBLISH_UNTIL - RUN_MINUTES * 60 + 1800 ))" ]; then
            echo "restart=true" >> "$GITHUB_OUTPUT"
          else
            echo "restart=false" >> "$GITHUB_OUTPUT"
            echo "This run ended early; leaving the restart to the timer."
          fi

      - name: Collector status (on failure)
        if: ${{ failure() }}
        # The bridge only ever talks to the runner's own loopback, so its log is safe to show.
        run: tail -n 40 "$RUNNER_TEMP/bridge.log" || true

  next:
    # Starts the next run on a new commit. After about 190 deploys of the same
    # commit (6.5 hours at one every 2 minutes), Pages keeps reporting success
    # but stops serving the new files, so every run needs its own commit.
    # This job is separate so the collector never holds write access to the repo.
    needs: collect
    if: needs.collect.outputs.restart == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: write   # one small commit to restart-marker.txt
      actions: write    # start the next run
    steps:
      - name: Start the next run on a new commit
        env:
          GH_TOKEN: ${{ github.token }}
          MARKER: restart-marker.txt
        run: |
          old=$(gh api "repos/$GITHUB_REPOSITORY/contents/$MARKER?ref=$GITHUB_REF_NAME" --jq .sha 2>/dev/null || true)
          text=$(printf 'Collector restarted at %s after run %s.\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$GITHUB_RUN_ID" | base64 -w0)
          if gh api -X PUT "repos/$GITHUB_REPOSITORY/contents/$MARKER" --silent \\
              -f message="Collector restart" -f content="$text" -f branch="$GITHUB_REF_NAME" \\
              ${old:+-f sha="$old"}; then
            echo "new commit: $(gh api "repos/$GITHUB_REPOSITORY/commits/$GITHUB_REF_NAME" --jq .sha)"
          else
            echo "::warning::Could not write the restart marker; the next run reuses this commit."
          fi
          gh workflow run collect.yml --repo "$GITHUB_REPOSITORY" --ref "$GITHUB_REF_NAME"
"""


def main() -> None:
    needs = "--need ships=12000 --need commercial_flights=3000 --need cctv=20000 --min-wait 120 --max-wait 900"
    text = HEAD.replace("__NEEDS__", needs) + "".join(SLOT.replace("{n:03d}", f"{n:03d}") for n in range(1, SLOTS + 1)) + TAIL
    target = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "collect.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({SLOTS} publish slots, {len(text) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
