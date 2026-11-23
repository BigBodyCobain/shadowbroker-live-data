# ShadowBroker public data

This repository runs the collector for the [ShadowBroker](https://github.com/BigBodyCobain/Shadowbroker)
public map and publishes its data files to this repository's GitHub Pages site.
The map itself is at https://shadowbroker.dev/map/.

## How it works

`.github/workflows/collect.yml` starts a GitHub-hosted runner, checks out
ShadowBroker, and runs its public engine and bridge (`viewer/engine`,
`viewer/bridge`) on the runner's loopback. Every few minutes the bridge's
output folder is published as the Pages site. When a run nears GitHub's time
limit it starts the next one.

- Nothing listens for outside connections. The runner is discarded after
  each run.
- Only the files named in the bridge's `manifest.json` are published. The
  bridge decides what is public from ShadowBroker's `viewer/public-layers.json`
  and refuses to write a file that contains any of the feed keys.
- The collector's own log is never printed.
- The workflow only runs on a timer or when the owner starts it. It does not
  run for pull requests.

## Settings

Repository secrets (data-feed keys, all optional; a missing one only empties
its layers): `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET`, `AIS_API_KEY`,
`FIRMS_MAP_KEY`, `GFW_API_TOKEN`.

Repository variables (optional): `PUBLISH_EVERY_S` (seconds between
publishes, default 180) and `SOURCE_REF` (ShadowBroker branch, tag or commit
to run, default `main`).

After changing the number of publish slots or the workflow template, run
`python tools/make_workflow.py` and commit the result.

Everything published here is public by design.
