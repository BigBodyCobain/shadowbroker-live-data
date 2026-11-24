"""Deploy one uploaded snapshot to GitHub Pages, with a version that is unique.

actions/deploy-pages always sends the commit SHA as the deployment's version
(`pages_build_version`). Every publish in a run has the same SHA, and after a
while Pages stops applying the repeats while still answering "success", so the
site silently freezes. A step cannot override GITHUB_SHA, so this script makes
the same two calls the action makes (create the deployment, then wait for its
status) with a version that is unique for each publish.

Reads from the environment:
  ARTIFACT_ID            the uploaded artifact's id (upload-pages-artifact output)
  BUILD_VERSION          a version string unique to this publish
  GITHUB_TOKEN           the job token (needs pages: write)
  ACTIONS_ID_TOKEN_REQUEST_URL / _TOKEN   set by Actions when id-token: write
  GITHUB_API_URL, GITHUB_REPOSITORY       set by Actions

Exit 0 only when Pages reports this deployment as live.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

FAILED = {
    "deployment_failed",
    "deployment_content_failed",
    "deployment_cancelled",
    "deployment_lost",
    "deployment_perms_error",
    "deployment_attempt_error",
    "not_found",
    "unknown_status",
}
CREATE_TRIES = 3
STATUS_TIMEOUT_S = 180


def _request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read()
    return json.loads(raw) if raw else {}


def _oidc_token() -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    token = os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as res:
        value = json.loads(res.read())["value"]
    print(f"::add-mask::{value}")
    return value


def main() -> int:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    version = os.environ["BUILD_VERSION"]
    artifact_id = int(os.environ["ARTIFACT_ID"])
    base = f"{api}/repos/{repo}/pages/deployments"

    created = None
    for attempt in range(1, CREATE_TRIES + 1):
        try:
            created = _request(
                "POST",
                base,
                token,
                {"artifact_id": artifact_id, "pages_build_version": version, "oidc_token": _oidc_token()},
            )
            break
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:300]
            print(f"create try {attempt} failed: HTTP {err.code} {detail}")
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as err:
            print(f"create try {attempt} failed: {err}")
        time.sleep(5)
    if created is None:
        print("::warning::Pages deployment was not created; this slot is lost")
        return 1

    dep_id = created.get("id") or str(created.get("status_url", "")).rstrip("/").split("/")[-1]
    print(f"created deployment {dep_id} for version {version}")
    if not dep_id or dep_id == version.split("-")[0]:
        print("::warning::Pages did not return a new deployment id")

    deadline = time.time() + STATUS_TIMEOUT_S
    status = "unknown"
    while time.time() < deadline:
        time.sleep(3)
        try:
            status = _request("GET", f"{base}/{dep_id}", token).get("status", "unknown")
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            print(f"status check failed: {err}")
            continue
        if status == "succeed":
            print(f"deployment {dep_id} is live")
            return 0
        if status in FAILED:
            break
    print(f"::warning::deployment {dep_id} ended as {status}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
