#!/usr/bin/env python3
"""Fixed-repository upstream maintenance for Nyox-x/running_page_kith."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPOSITORY = "Nyox-x/running_page_kith"
UPSTREAM_URL = "https://github.com/yihong0618/running_page.git"
SYNC_WORKFLOW = "run_data_sync.yml"
ENV_KEYS = (
    "RUN_TYPE",
    "ATHLETE",
    "TITLE",
    "MIN_GRID_DISTANCE",
    "TITLE_GRID",
    "IGNORE_START_END_RANGE",
    "IGNORE_POLYLINE",
    "IGNORE_RANGE",
    "USE_CLOUDFLARE_WARP",
    "SAVE_DATA_IN_GITHUB_CACHE",
    "DATA_CACHE_PREFIX",
    "BUILD_GH_PAGES",
    "SAVE_TO_PARQENT",
    "GENERATE_MONTH_OF_LIFE",
    "BIRTHDAY_MONTH",
)


class MaintenanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeResult:
    updated: bool
    workflow_changes: bool


def run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args), cwd=repo, text=True, capture_output=True, check=False
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise MaintenanceError(f"{' '.join(args)} failed: {detail}")
    return result


def _one(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise MaintenanceError(f"cannot read protected value: {label}")
    return match.group(1).strip()


def owner_snapshot(repo: Path) -> dict[str, object]:
    workflow = (repo / ".github/workflows/run_data_sync.yml").read_text(
        encoding="utf-8"
    )
    env_block = _one(r"^(env:\s*\n[\s\S]*?)(?=^jobs:)", workflow, "workflow env")
    env: dict[str, str] = {}
    for key in ENV_KEYS:
        env[key] = _one(rf"^  {re.escape(key)}:\s*(.*?)(?:\s+#.*)?$", env_block, key)

    config = (repo / "config.yml").read_text(encoding="utf-8")
    constants = (repo / "src/themes/classic/utils/const.ts").read_text(encoding="utf-8")
    return {
        "env": env,
        "theme_preset": _one(r"^theme_preset:\s*([^#\s]+)", config, "theme_preset"),
        "PRIVACY_MODE": _one(
            r"^const PRIVACY_MODE\s*=\s*([^;]+);", constants, "PRIVACY_MODE"
        ),
        "message_first": _one(
            r"^const CHINESE_LOCATION_INFO_MESSAGE_FIRST\s*=\s*'([^']*)';",
            constants,
            "CHINESE_LOCATION_INFO_MESSAGE_FIRST",
        ),
        "message_second": _one(
            r"^const CHINESE_LOCATION_INFO_MESSAGE_SECOND\s*=\s*'([^']*)';",
            constants,
            "CHINESE_LOCATION_INFO_MESSAGE_SECOND",
        ),
    }


def _abort(repo: Path) -> None:
    run(repo, "git", "merge", "--abort", check=False)


def configure_bot(repo: Path) -> None:
    run(repo, "git", "config", "user.name", "github-actions[bot]")
    run(
        repo,
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )


def merge_ref(repo: Path, upstream_ref: str, has_workflow_token: bool) -> MergeResult:
    before = owner_snapshot(repo)
    ancestor = run(
        repo,
        "git",
        "merge-base",
        "--is-ancestor",
        upstream_ref,
        "HEAD",
        check=False,
    )
    if ancestor.returncode == 0:
        return MergeResult(False, False)
    if ancestor.returncode != 1:
        raise MaintenanceError(ancestor.stderr.strip() or "cannot compare upstream")

    configure_bot(repo)
    merged = run(
        repo, "git", "merge", "--no-ff", "--no-commit", upstream_ref, check=False
    )
    if merged.returncode:
        conflicts = run(
            repo, "git", "diff", "--name-only", "--diff-filter=U"
        ).stdout.splitlines()
        _abort(repo)
        paths = ", ".join(conflicts) or "unknown path"
        raise MaintenanceError(f"upstream merge conflict; stopped at: {paths}")

    try:
        after = owner_snapshot(repo)
        changed = [key for key in before if before[key] != after[key]]
        if changed:
            raise MaintenanceError(
                "protected owner values changed: " + ", ".join(changed)
            )
        paths = run(repo, "git", "diff", "--name-only", "HEAD").stdout.splitlines()
        workflow_changes = any(path.startswith(".github/workflows/") for path in paths)
        if workflow_changes and not has_workflow_token:
            raise MaintenanceError(
                "upstream changes .github/workflows; add UPSTREAM_SYNC_TOKEN "
                "with this repository's Contents, Workflows and Actions write permissions"
            )
        return MergeResult(True, workflow_changes)
    except Exception:
        _abort(repo)
        raise


def push_only(repo: Path) -> None:
    pushed = run(
        repo,
        "git",
        "push",
        "origin",
        "HEAD:refs/heads/master",
        check=False,
    )
    if pushed.returncode:
        raise MaintenanceError(
            "regular push rejected; remote changed and no force was attempted: "
            + (pushed.stderr or pushed.stdout).strip()
        )


def push_then_dispatch(repo: Path, dispatch: Callable[[], None]) -> None:
    push_only(repo)
    dispatch()


def api(method: str, path: str, token: str, payload: object | None = None) -> object:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/{path}", data=data, method=method
    )
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise MaintenanceError(f"GitHub API {method} {path} failed: {exc.code} {body}")
    return json.loads(body) if body else {}


def enable_if_inactive(token: str) -> None:
    workflow = api("GET", f"actions/workflows/{SYNC_WORKFLOW}", token)
    if isinstance(workflow, dict) and workflow.get("state") == "disabled_inactivity":
        api("PUT", f"actions/workflows/{SYNC_WORKFLOW}/enable", token)


def dispatch_sync(token: str) -> None:
    enable_if_inactive(token)
    api(
        "POST",
        f"actions/workflows/{SYNC_WORKFLOW}/dispatches",
        token,
        {"ref": "master"},
    )


def require_context() -> tuple[Path, str]:
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise MaintenanceError(f"refusing to run outside {REPOSITORY}")
    token = os.environ.get("MAINTENANCE_TOKEN", "")
    if not token:
        raise MaintenanceError("MAINTENANCE_TOKEN is required")
    return Path.cwd(), token


def write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def command_merge(repo: Path) -> None:
    remotes = run(repo, "git", "remote").stdout.splitlines()
    if "upstream" in remotes:
        run(repo, "git", "remote", "set-url", "upstream", UPSTREAM_URL)
    else:
        run(repo, "git", "remote", "add", "upstream", UPSTREAM_URL)
    run(repo, "git", "fetch", "--no-tags", "upstream", "master")
    has_token = os.environ.get("HAS_UPSTREAM_SYNC_TOKEN") == "true"
    result = merge_ref(repo, "upstream/master", has_token)
    write_output("updated", str(result.updated).lower())
    write_output("workflow_changes", str(result.workflow_changes).lower())


def command_publish(repo: Path, token: str) -> None:
    configure_bot(repo)
    run(repo, "git", "commit", "-m", "Merge upstream master [skip ci]")
    push_then_dispatch(repo, lambda: dispatch_sync(token))


def command_monthly(repo: Path, token: str) -> None:
    marker = repo / ".github/last-maintenance-month"
    month = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == month:
        enable_if_inactive(token)
        return
    marker.write_text(month + "\n", encoding="utf-8")
    configure_bot(repo)
    run(repo, "git", "add", str(marker.relative_to(repo)))
    run(repo, "git", "commit", "-m", "chore: monthly maintenance [skip ci]")
    push_only(repo)
    enable_if_inactive(token)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("merge", "publish", "monthly", "rerun"))
    parser.add_argument("--run-id", type=int)
    args = parser.parse_args()
    try:
        repo, token = require_context()
        if args.command == "merge":
            command_merge(repo)
        elif args.command == "publish":
            command_publish(repo, token)
        elif args.command == "monthly":
            command_monthly(repo, token)
        elif args.command == "rerun":
            if not args.run_id:
                raise MaintenanceError("--run-id is required")
            api("POST", f"actions/runs/{args.run_id}/rerun-failed-jobs", token)
    except MaintenanceError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
