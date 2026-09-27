from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "gates.yml"


def test_diff_coverage_job_provisions_disposable_postgres_owner_and_worker() -> None:
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = config["jobs"]["deterministic-gates"]

    postgres = job["services"]["postgres"]
    assert postgres["image"].startswith("postgres:16")
    assert job["env"]["FACTORY_TEST_DATABASE_URL"] == (
        "postgresql+psycopg://postgres:postgres@localhost:5432/factory_test"
    )
    assert job["env"]["FACTORY_TEST_WORKER_URL"] == (
        "postgresql+psycopg://factory_worker:factory_worker@localhost:5432/factory_test"
    )
    assert any(
        step.get("name") == "Create non-owner PostgreSQL test role"
        for step in job["steps"]
    )
    coverage_step = next(step for step in job["steps"] if step.get("name") == "diff_coverage")
    assert "--fail-under=90" in coverage_step["run"]