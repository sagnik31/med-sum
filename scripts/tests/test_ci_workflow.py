"""
The GitHub Actions workflow (.github/workflows/ci.yml) runs every check the
repository has, and none of them can reach a model or a secret.

GitHub is the only place it really runs; these tests keep its shape from
drifting: a check removed, a live eval added, a job that skips its
database tests because Postgres is missing.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _runs(job: dict) -> str:
    """Every shell command of a job, joined."""
    return "\n".join(s.get("run", "") for s in job["steps"])


def _triggers(workflow: dict) -> dict:
    # YAML 1.1 reads the bare key `on` as True.
    return workflow.get("on", workflow.get(True))


def test_runs_on_push_to_main_and_on_pull_requests(workflow):
    on = _triggers(workflow)
    assert on["push"]["branches"] == ["main"]
    assert "pull_request" in on


def test_the_token_can_only_read(workflow):
    assert workflow["permissions"] == {"contents": "read"}


def test_the_expected_jobs_exist(workflow):
    assert set(workflow["jobs"]) == {"go", "python", "frontend", "eval-replay"}


def test_go_builds_vets_formats_and_tests(workflow):
    runs = _runs(workflow["jobs"]["go"])
    for command in ("go build ./...", "go vet ./...", "gofmt -l", "go test ./..."):
        assert command in runs


def test_python_runs_ruff_pytest_and_the_fault_check(workflow):
    job = workflow["jobs"]["python"]
    runs = _runs(job)
    for command in (
        "pip install -r requirements.txt -r requirements-dev.txt",
        "ruff check src tests",
        "ruff format --check src tests",
        "pytest",
        "python -m evals.faultcheck",
    ):
        assert command in runs


def test_the_plan_reference_check_sees_the_git_index(workflow):
    # test_no_plan_references lists files with `git ls-files`; a shallow
    # default checkout keeps .git, a sparse or archive one would not.
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert "sparse-checkout" not in step.get("with", {})


def test_frontend_lints_tests_and_builds(workflow):
    runs = _runs(workflow["jobs"]["frontend"])
    for command in ("npm ci", "npm run lint", "npm test", "npm run build"):
        assert command in runs


@pytest.mark.parametrize("name", ["go", "python", "eval-replay"])
def test_database_jobs_have_postgres_and_fail_without_it(workflow, name):
    job = workflow["jobs"][name]
    assert job["services"]["postgres"]["image"].startswith("postgres:")
    assert workflow["env"]["MEDSUM_REQUIRE_DB"] == "1"
    assert workflow["env"]["MEDSUM_TEST_ADMIN_URL"].endswith("@localhost:5432/postgres")


def test_eval_replay_only_replays_and_waits_for_cassettes(workflow):
    job = workflow["jobs"]["eval-replay"]
    assert "scripts/evals/cassettes/" in job["if"]
    runs = _runs(job)
    assert "python -m evals.run --replay" in runs
    assert "python -m evals.compare" in runs


def test_no_job_calls_a_model_or_uses_a_secret():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--live" not in text
    assert "ollama" not in text.lower()
    assert "secrets." not in text
