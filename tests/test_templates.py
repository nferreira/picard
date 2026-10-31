"""The scaffolded workflows and actions, checked as text: the security rules they must keep.

Standard library only, so no YAML parser: the checks read the files line by line, which is
enough for the shapes picard writes and fails loudly when a shape changes.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TEMPLATES = Path(__file__).parent.parent / "src" / "picard" / "templates" / "github"
PR_WORKFLOW = TEMPLATES / "workflows" / "pipeline-pr.yml"
PLAN_ACTION = TEMPLATES / "actions" / "pipeline-plan" / "action.yml"
YAML_FILES = sorted([*TEMPLATES.rglob("*.yml"), *TEMPLATES.rglob("*.yaml")])
RUN_KEY = re.compile(r"^(?P<indent>\s*)(?:- )?run:\s*(?P<value>.*)$")


def run_blocks(path: Path) -> list[tuple[int, str]]:
    """(line number, text) of every `run:` value, inline or a block scalar."""
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks = []
    for number, line in enumerate(lines, 1):
        match = RUN_KEY.match(line)
        if not match:
            continue
        value = match.group("value")
        if not value or value[0] not in "|>":
            blocks.append((number, value))
            continue
        indent = len(match.group("indent")) + (2 if line.lstrip().startswith("- ") else 0)
        body = []
        for following in lines[number:]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            body.append(following)
        blocks.append((number, "\n".join(body)))
    return blocks


def job(name: str) -> str:
    """The text of one job in the PR workflow, from its key to the next job."""
    text = PR_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", text)
    assert match, f"job {name} not found"
    return match.group(1)


def test_the_run_block_reader_sees_block_scalars():
    blocks = dict(run_blocks(PR_WORKFLOW))
    assert any("gh api" in body for body in blocks.values())
    assert any("pipeline-gate failed" in body for body in blocks.values())


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: str(p.relative_to(TEMPLATES)))
def test_no_expression_inside_a_run_block(path):
    # Untrusted text (titles, labels, bodies) reaches a shell only through env:.
    for number, body in run_blocks(path):
        assert "${{" not in body, f"{path.name}:{number} interpolates into a shell"


def test_plan_comment_runs_no_pr_code():
    comment = job("plan-comment")
    assert "actions/checkout" not in comment
    assert "uses: ./" not in comment
    assert re.search(r"(?m)^    permissions:\n      pull-requests: write\n(?!\s{6}\S)", comment)
    assert "needs: plan" in comment
    assert "if: github.event.pull_request.head.repo.full_name == github.repository" in comment
    assert "actions/download-artifact@" in comment
    assert "-F body=@body.md" in comment
    assert "<!-- picard-plan -->" in comment
    assert "github-actions[bot]" in comment


def test_only_the_comment_job_can_write():
    text = PR_WORKFLOW.read_text(encoding="utf-8")
    assert text.count(": write") == 1
    assert "pull-requests: write" in job("plan-comment")


def test_plan_comment_never_blocks_the_gate():
    needs = re.search(r"(?m)^    needs: \[(.*)\]$", job("pipeline-gate"))
    assert needs
    names = [n.strip() for n in needs.group(1).split(",")]
    assert "plan-comment" not in names
    assert {"title", "plan", "fast-gate", "baseline", "extras"} <= set(names)


def test_labels_reach_picard_through_env():
    action = PLAN_ACTION.read_text(encoding="utf-8")
    assert "PICARD_LABELS: ${{ inputs.labels }}" in action
    assert "IN_COMMENT: ${{ inputs.comment }}" in action
    assert "IN_METRICS: ${{ inputs.metrics }}" in action
    for name in ("labels", "comment", "metrics"):
        assert re.search(rf'(?m)^  {name}:\n(?:    .*\n)*?    default: ""$', action), name


def test_the_pr_plan_steps_pass_labels_comment_and_metrics():
    plan = job("plan")
    labels = "labels: ${{ toJSON(github.event.pull_request.labels.*.name) }}"
    assert plan.count(labels) == 2
    assert plan.count("comment: plan.md") == 2
    assert "metrics: metrics-1.json" in plan and "metrics: metrics-2.json" in plan
    assert "actions/upload-artifact@" in plan and "name: pipeline-plan" in plan
    # --comment appends, and the job checked out the PR: a plan.md from the PR must not
    # reach the comment. The files are cleared before the first plan step, and the upload
    # names exactly the files picard wrote.
    clear = plan.index("run: rm -f plan.md metrics-1.json metrics-2.json")
    assert clear < plan.index("id: t1")
    assert "metrics-*" not in plan


def test_label_changes_replan():
    text = PR_WORKFLOW.read_text(encoding="utf-8")
    types = re.search(r"(?m)^    types: \[(.*)\]$", text)
    assert types
    assert {"labeled", "unlabeled"} <= {t.strip() for t in types.group(1).split(",")}


@pytest.mark.parametrize("name", ["fast-gate", "baseline"])
def test_suite_checkouts_have_full_history(name):
    steps = job(name)
    checkout = re.search(r"(?ms)uses: actions/checkout@\S+\n\s+with:\n(.*?)(?=^\s+- )", steps)
    assert checkout and "fetch-depth: 0" in checkout.group(1)
