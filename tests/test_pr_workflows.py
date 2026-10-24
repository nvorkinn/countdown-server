import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / ".github/workflows"
# BaseLoader only loads strings, preserving GitHub's "on" key instead of a YAML 1.1 boolean.
WORKFLOWS = {
    name: yaml.load((WORKFLOW_DIR / name).read_text(), Loader=yaml.BaseLoader)  # noqa: S506
    for name in ("all-checks.yml", "create_global_pr_status.yml")
}
GATE = WORKFLOWS["all-checks.yml"]
CREATE = WORKFLOWS["create_global_pr_status.yml"]


def test_the_gate_runs_after_other_workflows_and_may_approve_and_complete_the_status_check():
    assert GATE["on"].keys() == {"workflow_run"}
    assert GATE["on"]["workflow_run"]["types"] == ["completed"]
    # What the shared workflow declares, which a caller has to grant: checks to complete the status check,
    # pull requests to approve.
    assert GATE["permissions"] == {
        "actions": "read",
        "checks": "write",
        "contents": "read",
        "pull-requests": "write",
    }
    assert GATE["jobs"]["all-checks"]["with"] == {"approve": "true"}


def test_the_status_check_is_opened_on_pull_requests():
    assert CREATE["on"].keys() == {"pull_request"}
    assert CREATE["permissions"] == {"checks": "write"}


def test_the_shared_workflows_are_pinned_to_a_commit_not_a_branch():
    for caller, job in ((GATE, "all-checks"), (CREATE, "create-global-pr-status")):
        assert re.fullmatch(
            r"nvorkinn/\.github/\.github/workflows/[\w-]+\.yml@[0-9a-f]{40}", caller["jobs"][job]["uses"]
        )


def test_every_workflow_that_runs_on_pull_requests_triggers_the_gate():
    listed = set(GATE["on"]["workflow_run"]["workflows"])
    on_pull_requests = set()
    for path in WORKFLOW_DIR.glob("*.yml"):
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)  # noqa: S506
        if "pull_request" in workflow["on"]:
            on_pull_requests.add(workflow["name"])
    # The gate can't wait for itself.
    assert on_pull_requests - {"All checks"} <= listed
    assert "Create Global PR Status" in listed
