from pathlib import Path

import yaml

CONFIG = Path(__file__).parents[2] / "config" / "pilot.yaml"


def test_pilot_uses_github_issues_and_declares_human_users() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    github = config["github"]

    assert github["alm"] == "github-issues"
    assert github["issue_writes"] is True
    assert github["human_users"] == ["chexudeze", "triplexapps", "chex123"]
    assert github["approver_order"] == ["triplexapps", "chex123"]
    assert github["app"]["repositories"] == [
        "NOK-Apps/flur-sdk",
        "NOK-Apps/flur-frontend",
        "NOK-Apps/flur-backend",
    ]


def test_pilot_records_selected_exact_model_routes() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    models = config["models"]

    assert models["coding"]["doer"]["exact_model_id"] == "gpt-5.6-luna"
    assert models["coding"]["reviewer"]["exact_model_id"] == "gemini-3.8-flash"
    assert (
        models["architecture_and_business"]["doer"]["exact_model_id"]
        == "gpt-6-astra"
    )
    assert (
        models["architecture_and_business"]["reviewer"]["exact_model_id"]
        == "amazon.nova-pro-v1:0"
    )
    assert models["architecture_and_business"]["reviewer"]["provider"] == "aws-bedrock"
    assert models["architecture_and_business"]["reviewer"]["family"] == "amazon-nova-pro"


def test_pilot_declares_provider_specific_context_and_input_ceilings() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    expected = {
        ("coding", "doer"): (1_050_000, 922_000),
        ("coding", "reviewer"): (1_048_576, 1_048_576),
        ("architecture_and_business", "doer"): (1_050_000, 922_000),
        ("architecture_and_business", "reviewer"): (300_000, 290_000),
    }

    for (capability, role), limits in expected.items():
        route = config["models"][capability][role]
        assert (route["context_window_tokens"], route["max_input_tokens"]) == limits
