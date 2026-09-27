import json
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from scripts.deploy_pilot import (
    DeploymentConfig,
    DeploymentError,
    deploy_pilot,
    task_definition_for_image,
    validate_registry_configuration,
    validate_scan_result,
)


def test_deployment_requires_immutable_scan_on_push_repository() -> None:
    validate_registry_configuration({
        "imageTagMutability": "IMMUTABLE",
        "imageScanningConfiguration": {"scanOnPush": True},
    })

    with pytest.raises(DeploymentError, match="immutable tags"):
        validate_registry_configuration({
            "imageTagMutability": "MUTABLE",
            "imageScanningConfiguration": {"scanOnPush": True},
        })

    with pytest.raises(DeploymentError, match="scan-on-push"):
        validate_registry_configuration({
            "imageTagMutability": "IMMUTABLE",
            "imageScanningConfiguration": {"scanOnPush": False},
        })


def test_scan_must_complete_and_have_no_critical_high_or_unclassified_findings() -> None:
    validate_scan_result({
        "imageScanStatus": {"status": "COMPLETE"},
        "imageScanFindings": {
            "findingSeverityCounts": {"MEDIUM": 2, "LOW": 1},
            "findings": [{"severity": "MEDIUM"}, {"severity": "LOW"}],
        },
    })

    for status in ("IN_PROGRESS", "FAILED"):
        with pytest.raises(DeploymentError, match="completed scan"):
            validate_scan_result({
                "imageScanStatus": {"status": status},
                "imageScanFindings": {"findingSeverityCounts": {}, "findings": []},
            })

    for severity in ("CRITICAL", "HIGH", "UNDEFINED"):
        with pytest.raises(DeploymentError, match=severity):
            validate_scan_result({
                "imageScanStatus": {"status": "COMPLETE"},
                "imageScanFindings": {
                    "findingSeverityCounts": {severity: 1},
                    "findings": [{"severity": severity}],
                },
            })


def test_task_definition_replaces_only_the_target_container_image() -> None:
    task_definition = {
        "taskDefinitionArn": "arn:aws:ecs:us-east-1:123:task-definition/factory:17",
        "revision": 17,
        "status": "ACTIVE",
        "requiresAttributes": [],
        "compatibilities": ["FARGATE"],
        "registeredAt": "2026-09-27T00:00:00Z",
        "family": "factory",
        "taskRoleArn": "arn:aws:iam::123:role/task",
        "containerDefinitions": [
            {
                "name": "factory",
                "image": "repo@sha256:" + "b" * 64,
                "environment": [{"name": "FACTORY_CHAT_MODEL_TURN_LIMIT", "value": "2"}],
            },
            {"name": "telemetry", "image": "repo@sha256:" + "c" * 64},
        ],
    }

    result = task_definition_for_image(
        task_definition,
        container_name="factory",
        image="repo@sha256:" + "d" * 64,
        chat_model_turn_limit=7,
    )

    assert result["containerDefinitions"][0]["image"] == "repo@sha256:" + "d" * 64
    assert result["containerDefinitions"][1]["image"] == "repo@sha256:" + "c" * 64
    assert result["taskRoleArn"] == task_definition["taskRoleArn"]
    assert result["containerDefinitions"][0]["environment"] == [
        {"name": "FACTORY_CHAT_MODEL_TURN_LIMIT", "value": "7"},
    ]
    assert "taskDefinitionArn" not in result
    assert "revision" not in result
    assert task_definition["containerDefinitions"][0]["image"] == "repo@sha256:" + "b" * 64
    preserved = task_definition_for_image(
        task_definition,
        container_name="factory",
        image="repo@sha256:" + "e" * 64,
    )
    assert preserved["containerDefinitions"][0]["environment"] == [
        {"name": "FACTORY_CHAT_MODEL_TURN_LIMIT", "value": "2"},
    ]
    with pytest.raises(DeploymentError, match="maximum cumulative turn limit is 7"):
        task_definition_for_image(
            task_definition,
            container_name="factory",
            image="repo@sha256:" + "f" * 64,
            chat_model_turn_limit=8,
        )


class FakeRunner:
    def __init__(self, *, high_finding: bool = True) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.high_finding = high_finding
        self.service_descriptions = 0
        self.registered_definition: dict[str, object] | None = None

    def __call__(
        self,
        command: Sequence[str],
        input_text: str | None = None,
    ) -> str:
        parts = tuple(command)
        self.calls.append((parts, input_text))
        if parts[:3] == ("aws", "sts", "get-caller-identity"):
            return json.dumps({
                "Account": "123456789012",
                "Arn": "arn:aws:iam::123456789012:user/deployer",
            })
        if parts[:3] == ("aws", "ecr", "describe-repositories"):
            return json.dumps({
                "repositories": [{
                    "repositoryUri": (
                        "123456789012.dkr.ecr.us-east-1.amazonaws.com/pilot/factory"
                    ),
                    "imageTagMutability": "IMMUTABLE",
                    "imageScanningConfiguration": {"scanOnPush": True},
                }],
            })
        if parts[:3] == ("aws", "ecr", "list-images"):
            return json.dumps({"imageIds": []})
        if parts[:3] == ("aws", "ecr", "get-login-password"):
            return "ephemeral-test-password"
        if parts[:3] == ("podman", "build", "--platform"):
            return ""
        if parts[:2] == ("podman", "login"):
            assert input_text == "ephemeral-test-password\n"
            return ""
        if parts[:2] == ("podman", "push"):
            return ""
        if parts[:3] == ("aws", "ecr", "describe-images"):
            return json.dumps({
                "imageDetails": [{"imageDigest": "sha256:" + "a" * 64}],
            })
        if parts[:3] == ("aws", "ecr", "describe-image-scan-findings"):
            counts = {"HIGH": 1} if self.high_finding else {}
            findings = [{"severity": "HIGH"}] if self.high_finding else []
            return json.dumps({
                "imageScanStatus": {"status": "COMPLETE"},
                "imageScanFindings": {
                    "findingSeverityCounts": counts,
                    "findings": findings,
                },
            })
        if parts[:3] == ("aws", "ecs", "describe-services"):
            self.service_descriptions += 1
            task_arn = (
                "arn:aws:ecs:us-east-1:123456789012:task-definition/factory:17"
                if self.service_descriptions == 1
                else "arn:aws:ecs:us-east-1:123456789012:task-definition/factory:18"
            )
            return json.dumps({
                "failures": [],
                "services": [{
                    "status": "ACTIVE",
                    "taskDefinition": task_arn,
                    "desiredCount": 1,
                    "runningCount": 1,
                    "pendingCount": 0,
                    "deploymentConfiguration": {
                        "deploymentCircuitBreaker": {"enable": True, "rollback": True},
                    },
                    "deployments": [{
                        "status": "PRIMARY",
                        "taskDefinition": task_arn,
                        "rolloutState": "COMPLETED",
                    }],
                }],
            })
        if parts[:3] == ("aws", "ecs", "describe-task-definition"):
            return json.dumps({"taskDefinition": {
                "taskDefinitionArn": (
                    "arn:aws:ecs:us-east-1:123456789012:task-definition/factory:17"
                ),
                "revision": 17,
                "status": "ACTIVE",
                "requiresAttributes": [],
                "compatibilities": ["FARGATE"],
                "registeredAt": "2026-09-27T00:00:00Z",
                "family": "factory",
                "taskRoleArn": "arn:aws:iam::123456789012:role/task",
                "containerDefinitions": [
                    {
                        "name": "factory",
                        "image": "repo@sha256:" + "b" * 64,
                        "environment": [{"name": "FACTORY_CHAT_MODEL_TURN_LIMIT", "value": "2"}],
                    },
                    {"name": "telemetry", "image": "repo@sha256:" + "c" * 64},
                ],
            }})
        if parts[:3] == ("aws", "ecs", "register-task-definition"):
            uri = parts[parts.index("--cli-input-json") + 1]
            path_text = unquote(urlsplit(uri).path)
            if len(path_text) > 2 and path_text[0] == "/" and path_text[2] == ":":
                path_text = path_text[1:]
            self.registered_definition = json.loads(Path(path_text).read_text(encoding="utf-8"))
            return json.dumps({"taskDefinition": {
                "taskDefinitionArn": (
                    "arn:aws:ecs:us-east-1:123456789012:task-definition/factory:18"
                ),
            }})
        if parts[:3] == ("aws", "ecs", "update-service"):
            return "{}"
        if parts[:4] == ("aws", "ecs", "wait", "services-stable"):
            return ""
        raise AssertionError(f"unexpected command: {parts[:3]}")


def test_critical_scan_finding_prevents_all_ecs_mutations() -> None:
    runner = FakeRunner()
    config = DeploymentConfig(
        region="us-east-1",
        cluster="pilot-factory",
        service="pilot-factory",
        repository="pilot/factory",
        image_tag="pilot-test-unique-20260927-01",
        context_directory=".",
        container_cli="podman",
        account_id="123456789012",
        chat_model_turn_limit=7,
        scan_poll_attempts=1,
        scan_poll_seconds=0,
    )

    with pytest.raises(DeploymentError, match="HIGH"):
        deploy_pilot(config, run=runner, sleep=lambda _seconds: None)

    assert not any(
        command[:2] == ("aws", "ecs")
        for command, _input_text in runner.calls
    )


def test_clean_scan_promotes_only_the_digest_pinned_factory_container() -> None:
    runner = FakeRunner(high_finding=False)
    config = DeploymentConfig(
        region="us-east-1",
        cluster="pilot-factory",
        service="pilot-factory",
        repository="pilot/factory",
        image_tag="pilot-test-unique-20260927-02",
        context_directory=".",
        container_cli="podman",
        account_id="123456789012",
        chat_model_turn_limit=7,
        scan_poll_attempts=1,
        scan_poll_seconds=0,
    )

    result = deploy_pilot(config, run=runner, sleep=lambda _seconds: None)

    commands = [command for command, _input_text in runner.calls]
    scan_index = next(i for i, command in enumerate(commands) if command[:3] == (
        "aws", "ecr", "describe-image-scan-findings"
    ))
    register_index = next(i for i, command in enumerate(commands) if command[:3] == (
        "aws", "ecs", "register-task-definition"
    ))
    update_index = next(i for i, command in enumerate(commands) if command[:3] == (
        "aws", "ecs", "update-service"
    ))
    expected_image = (
        "123456789012.dkr.ecr.us-east-1.amazonaws.com/pilot/factory@sha256:"
        + "a" * 64
    )

    assert scan_index < register_index < update_index
    assert result.image == expected_image
    assert runner.registered_definition is not None
    containers = runner.registered_definition["containerDefinitions"]
    assert containers[0]["image"] == expected_image
    assert containers[1]["image"] == "repo@sha256:" + "c" * 64
    assert containers[0]["environment"] == [
        {"name": "FACTORY_CHAT_MODEL_TURN_LIMIT", "value": "7"},
    ]
    assert runner.registered_definition["taskRoleArn"] == "arn:aws:iam::123456789012:role/task"


def test_deployment_workflow_is_manual_main_only_and_uses_oidc() -> None:
    repository_root = Path(__file__).parents[2]
    workflow = repository_root / ".github" / "workflows" / "deploy-pilot.yml"
    content = workflow.read_text(encoding="utf-8")
    skill = repository_root / ".github" / "skills" / "factory-image-deployment" / "SKILL.md"
    instructions = repository_root / ".github" / "copilot-instructions.md"

    assert "workflow_dispatch:" in content
    assert "github.ref == 'refs/heads/main'" in content
    assert "id-token: write" in content
    assert "AWS_PILOT_DEPLOY_ROLE_ARN" in content
    assert "environment: pilot-deploy" in content
    assert "python scripts/deploy_pilot.py" in content
    assert "model_turn_limit:" in content
    assert "--chat-model-turn-limit" in content
    assert "python -m pytest" in content
    skill_text = skill.read_text(encoding="utf-8")
    assert "completed ECR scan" in skill_text
    assert "cumulative ceiling" in skill_text
    assert "never refunded" in skill_text
    assert "factory-image-deployment/SKILL.md" in instructions.read_text(encoding="utf-8")


def test_dev_extra_contains_full_runtime_and_analysis_test_dependencies() -> None:
    import re
    import tomllib

    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dev_dependencies = config["project"]["optional-dependencies"]["dev"]
    names = {
        re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0].casefold().replace("_", "-")
        for requirement in dev_dependencies
    }

    assert {
        "fastapi",
        "uvicorn",
        "alembic",
        "boto3",
        "pyjwt",
        "mcp",
        "tree-sitter",
        "tree-sitter-language-pack",
        "multilspy",
    } <= names