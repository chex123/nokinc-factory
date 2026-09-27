import json

from nokinc_factory.cli import main


def test_chat_without_model_is_explicitly_unavailable(monkeypatch, capsys) -> None:
    monkeypatch.delenv("FACTORY_MODEL", raising=False)

    result = main(["chat", "--message", "build refunds"])

    assert result == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "NOT_AVAILABLE",
        "reason": "MODEL_PROVIDER_NOT_CONFIGURED",
        "authorizes_merge": False,
    }


def test_gate_without_provider_never_fabricates_approval(capsys) -> None:
    result = main(["gate", "work-1", "--approve"])

    assert result == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "NOT_AVAILABLE",
        "reason": "APPROVAL_PROVIDER_NOT_CONFIGURED",
        "work_item_id": "work-1",
        "authorizes_merge": False,
    }
