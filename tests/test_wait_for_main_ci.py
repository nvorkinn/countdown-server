import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/wait-for-main-ci.sh"
RELEASE = yaml.load((ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)  # noqa: S506


PATHS = {
    "Tests": ".github/workflows/tests.yml",
    "Lint": ".github/workflows/lint.yml",
    "CodeQL": "dynamic/github-code-scanning/codeql",
}


def _run(name, status="completed", conclusion="success", event="push", run_id=1):
    return {
        "id": run_id,
        "name": "Push on main" if name == "CodeQL" else name,
        "path": PATHS[name],
        "status": status,
        "conclusion": conclusion,
        "event": event,
        "html_url": f"https://github.test/{name}/{run_id}",
    }


def _wait(tmp_path, *responses, timeout_s=0):
    """Run the script against a fake `gh` that answers each poll with the next of `responses` (the last repeats)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for number, runs in enumerate(responses):
        (tmp_path / f"{number}.json").write_text(json.dumps({"workflow_runs": runs}))
    (bin_dir / "gh").write_text(
        f"""#!/usr/bin/env bash
n=$(cat "{tmp_path}/count" 2>/dev/null || echo 0)
last={len(responses) - 1}
cat "{tmp_path}/$(( n > last ? last : n )).json"
echo $((n + 1)) > "{tmp_path}/count"
"""
    )
    (bin_dir / "gh").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "REPO": "o/r",
        "SHA": "abc",
        "TIMEOUT_S": str(timeout_s),
        "INTERVAL_S": "0",
    }
    return subprocess.run([SCRIPT], env=env, capture_output=True, text=True, check=False)  # noqa: S603


PASSED = [_run("Tests"), _run("Lint"), _run("CodeQL", event="dynamic")]


def test_passes_when_every_required_workflow_passed(tmp_path):
    result = _wait(tmp_path, PASSED)
    assert result.returncode == 0, result.stderr
    assert "CI passed" in result.stdout


def test_a_skipped_workflow_counts_as_passed(tmp_path):
    assert _wait(tmp_path, [*PASSED[:2], _run("CodeQL", conclusion="skipped", event="dynamic")]).returncode == 0


def test_fails_at_once_when_a_workflow_failed_even_if_others_still_run(tmp_path):
    result = _wait(tmp_path, [_run("Tests", conclusion="failure"), _run("Lint", "in_progress", None)], timeout_s=60)
    assert result.returncode == 1
    assert ".github/workflows/tests.yml: failure https://github.test/Tests/1" in result.stdout


def test_waits_for_a_running_workflow_then_passes(tmp_path):
    running = [*PASSED[:2], _run("CodeQL", "in_progress", None, event="dynamic")]
    assert _wait(tmp_path, running, running, PASSED, timeout_s=60).returncode == 0


def test_fails_at_once_when_a_workflow_has_no_run_on_the_commit(tmp_path):
    result = _wait(tmp_path, PASSED[:2], timeout_s=60)
    assert result.returncode == 1
    assert "dynamic/github-code-scanning/codeql: no run on this commit" in result.stdout


def test_a_run_whose_path_ends_in_a_ref_counts(tmp_path):
    tests = {**_run("Tests"), "path": ".github/workflows/tests.yml@refs/heads/main"}
    assert _wait(tmp_path, [tests, *PASSED[1:]]).returncode == 0


def test_times_out_when_a_workflow_keeps_running(tmp_path):
    result = _wait(tmp_path, [*PASSED[:2], _run("CodeQL", "in_progress", None, event="dynamic")])
    assert result.returncode == 1
    assert "did not finish" in result.stdout


def test_a_pull_request_run_does_not_count(tmp_path):
    result = _wait(tmp_path, [*PASSED[:2], _run("CodeQL", event="pull_request")])
    assert result.returncode == 1


def test_the_newest_run_of_a_workflow_decides(tmp_path):
    result = _wait(tmp_path, [_run("Tests", conclusion="failure", run_id=1), _run("Tests", run_id=2), *PASSED[1:]])
    assert result.returncode == 0, result.stdout


def test_release_waits_for_ci_before_tagging_anything():
    jobs = RELEASE["jobs"]
    assert jobs["tag"]["needs"] == "ci"
    assert jobs["ci"]["permissions"]["actions"] == "read"
    # Everything after the tag job is reached through it, so nothing builds, packages or deploys before CI passed.
    assert {name for name, job in jobs.items() if "needs" not in job} == {"ci"}


@pytest.mark.parametrize("path", PATHS.values())
def test_release_requires_the_workflow(path):
    assert path in RELEASE["jobs"]["ci"]["steps"][1]["env"]["REQUIRED"].split()
