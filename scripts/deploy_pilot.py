"""Build, scan, and promote one immutable Factory image to ECS."""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast


class DeploymentError(RuntimeError):
    """A fail-closed release precondition or promotion step failed."""


class CommandFailure(DeploymentError):
    def __init__(self, operation: str, code: str | None) -> None:
        self.code = code
        suffix = f" ({code})" if code else ""
        super().__init__(f"release command failed: {operation}{suffix}")


class CommandRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        input_text: str | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class DeploymentConfig:
    region: str
    cluster: str
    service: str
    repository: str
    image_tag: str
    context_directory: str = "."
    container_cli: str = "podman"
    container_name: str = "factory"
    account_id: str = "441186133046"
    chat_model_turn_limit: int | None = None
    scan_poll_attempts: int = 90
    scan_poll_seconds: int = 10


@dataclass(frozen=True)
class DeploymentResult:
    image: str
    task_definition_arn: str


_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_RESPONSE_FIELDS = {
    "compatibilities",
    "deregisteredAt",
    "registeredAt",
    "registeredBy",
    "requiresAttributes",
    "revision",
    "status",
    "taskDefinitionArn",
}
_KNOWN_SEVERITIES = {
    "CRITICAL",
    "HIGH",
    "MEDIUM",
    "LOW",
    "INFORMATIONAL",
    "UNDEFINED",
}
_MAX_PILOT_CHAT_MODEL_TURN_LIMIT = 7


def validate_registry_configuration(repository: Mapping[str, object]) -> None:
    if repository.get("imageTagMutability") != "IMMUTABLE":
        raise DeploymentError("ECR repository must enforce immutable tags")
    scan = repository.get("imageScanningConfiguration")
    if not isinstance(scan, Mapping) or scan.get("scanOnPush") is not True:
        raise DeploymentError("ECR repository must enable scan-on-push")


def validate_scan_result(scan: Mapping[str, object]) -> None:
    status = _mapping(scan.get("imageScanStatus"), "ECR scan status")
    if status.get("status") != "COMPLETE":
        raise DeploymentError("deployment requires a completed scan")

    findings = _mapping(scan.get("imageScanFindings"), "ECR scan findings")
    rows = findings.get("findings")
    severity_counts = findings.get("findingSeverityCounts")
    if not isinstance(rows, list) or not isinstance(severity_counts, Mapping):
        raise DeploymentError("ECR scan result is incomplete")

    row_counts: dict[str, int] = {}
    for row in rows:
        finding = _mapping(row, "ECR finding")
        severity = finding.get("severity")
        if not isinstance(severity, str) or severity not in _KNOWN_SEVERITIES:
            raise DeploymentError("ECR scan contains an unrecognized severity")
        row_counts[severity] = row_counts.get(severity, 0) + 1

    for severity, raw_count in severity_counts.items():
        if not isinstance(severity, str) or severity not in _KNOWN_SEVERITIES:
            if raw_count != 0:
                raise DeploymentError("ECR scan contains an unrecognized severity")
            continue
        if not isinstance(raw_count, int) or isinstance(raw_count, bool) or raw_count < 0:
            raise DeploymentError("ECR scan severity count is invalid")

    for severity in ("CRITICAL", "HIGH", "UNDEFINED"):
        reported = severity_counts.get(severity, 0)
        if not isinstance(reported, int) or isinstance(reported, bool) or reported < 0:
            raise DeploymentError("ECR scan severity count is invalid")
        if max(reported, row_counts.get(severity, 0)) > 0:
            raise DeploymentError(f"ECR scan blocked by {severity} findings")


def task_definition_for_image(
    task_definition: Mapping[str, object],
    *,
    container_name: str,
    image: str,
    chat_model_turn_limit: int | None = None,
) -> dict[str, object]:
    if "@" not in image or not _DIGEST_PATTERN.fullmatch(image.rsplit("@", 1)[-1]):
        raise DeploymentError("task definition image must be pinned by sha256 digest")

    payload = copy.deepcopy(dict(task_definition))
    for field in _RESPONSE_FIELDS:
        payload.pop(field, None)
    raw_containers = payload.get("containerDefinitions")
    if not isinstance(raw_containers, list):
        raise DeploymentError("task definition has no container definitions")

    containers: list[dict[str, object]] = []
    for raw_container in raw_containers:
        containers.append(_mapping(raw_container, "task container"))
    matches = [container for container in containers if container.get("name") == container_name]
    if len(matches) != 1:
        raise DeploymentError("task definition must contain exactly one target container")
    matches[0]["image"] = image
    if chat_model_turn_limit is not None:
        _validate_chat_turn_limit(chat_model_turn_limit)
        environment = matches[0].get("environment")
        if not isinstance(environment, list):
            raise DeploymentError("target container environment is unavailable")
        turn_limit_entries = [
            _mapping(entry, "task environment variable")
            for entry in environment
            if isinstance(entry, Mapping)
            and entry.get("name") == "FACTORY_CHAT_MODEL_TURN_LIMIT"
        ]
        if len(turn_limit_entries) != 1:
            raise DeploymentError("task definition must have exactly one chat turn limit")
        turn_limit_entries[0]["value"] = str(chat_model_turn_limit)
    payload["containerDefinitions"] = containers
    return payload


def deploy_pilot(
    config: DeploymentConfig,
    *,
    run: CommandRunner,
    sleep: Callable[[float], None] = time.sleep,
) -> DeploymentResult:
    if not _TAG_PATTERN.fullmatch(config.image_tag):
        raise DeploymentError("image tag must be a unique ECR-compatible value")
    _validate_chat_turn_limit(config.chat_model_turn_limit)
    if config.scan_poll_attempts < 1 or config.scan_poll_seconds < 0:
        raise DeploymentError("scan wait bounds are invalid")
    context = Path(config.context_directory).resolve()
    if not (context / "Dockerfile").is_file():
        raise DeploymentError("deployment context must contain a Dockerfile")

    identity = _aws_json(run, config.region, ("sts", "get-caller-identity"))
    arn = identity.get("Arn")
    if identity.get("Account") != config.account_id or not isinstance(arn, str):
        raise DeploymentError("AWS identity does not match the pilot account")
    if arn.endswith(":root"):
        raise DeploymentError("root credentials are not permitted for deployment")

    repository_response = _aws_json(
        run,
        config.region,
        ("ecr", "describe-repositories", "--repository-names", config.repository),
    )
    repositories = repository_response.get("repositories")
    if not isinstance(repositories, list) or len(repositories) != 1:
        raise DeploymentError("ECR repository lookup did not return exactly one repository")
    repository = _mapping(repositories[0], "ECR repository")
    validate_registry_configuration(repository)
    repository_uri = repository.get("repositoryUri")
    registry_prefix = f"{config.account_id}.dkr.ecr.{config.region}.amazonaws.com/"
    if not isinstance(repository_uri, str) or not repository_uri.startswith(registry_prefix):
        raise DeploymentError("ECR repository URI does not match the pilot account and region")

    existing = _aws_json(
        run,
        config.region,
        ("ecr", "list-images", "--repository-name", config.repository,
         "--filter", "tagStatus=TAGGED"),
    ).get("imageIds")
    if not isinstance(existing, list):
        raise DeploymentError("ECR tag inventory is incomplete")
    for image_id in existing:
        if _mapping(image_id, "ECR image identity").get("imageTag") == config.image_tag:
            raise DeploymentError("image tag already exists; deployment tags are immutable")

    registry = repository_uri.split("/", 1)[0]
    password = run(("aws", "ecr", "get-login-password", "--region", config.region)).strip()
    if not password:
        raise DeploymentError("ECR authentication returned no token")
    run(
        (config.container_cli, "login", "--username", "AWS", "--password-stdin", registry),
        password + "\n",
    )

    tagged_image = f"{repository_uri}:{config.image_tag}"
    run((
        config.container_cli,
        "build",
        "--platform",
        "linux/amd64",
        "--tag",
        tagged_image,
        str(context),
    ))
    run((config.container_cli, "push", tagged_image))

    pushed = _aws_json(
        run,
        config.region,
        ("ecr", "describe-images", "--repository-name", config.repository,
         "--image-ids", f"imageTag={config.image_tag}"),
    ).get("imageDetails")
    if not isinstance(pushed, list) or len(pushed) != 1:
        raise DeploymentError("pushed ECR tag did not resolve to exactly one image")
    digest = _mapping(pushed[0], "ECR image").get("imageDigest")
    if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
        raise DeploymentError("pushed image did not resolve to a valid sha256 digest")
    pinned_image = f"{repository_uri}@{digest}"

    _wait_for_clean_scan(config, run=run, sleep=sleep, digest=digest)

    service_response = _aws_json(
        run,
        config.region,
        ("ecs", "describe-services", "--cluster", config.cluster,
         "--services", config.service),
    )
    service = _single_service(service_response)
    _require_rollback(service)
    previous_task_definition = _required_string(service, "taskDefinition")
    task_response = _aws_json(
        run,
        config.region,
        ("ecs", "describe-task-definition", "--task-definition", previous_task_definition),
    )
    original_definition = _mapping(task_response.get("taskDefinition"), "ECS task definition")
    registration = task_definition_for_image(
        original_definition,
        container_name=config.container_name,
        image=pinned_image,
        chat_model_turn_limit=config.chat_model_turn_limit,
    )
    registered_arn = _register_task_definition(config, run, registration)

    run(("aws", "ecs", "update-service", "--cluster", config.cluster,
         "--service", config.service, "--task-definition", registered_arn,
         "--region", config.region, "--output", "json", "--no-cli-pager"))
    run(("aws", "ecs", "wait", "services-stable", "--cluster", config.cluster,
         "--services", config.service, "--region", config.region))

    final_service = _single_service(_aws_json(
        run,
        config.region,
        ("ecs", "describe-services", "--cluster", config.cluster,
         "--services", config.service),
    ))
    _require_rollback(final_service)
    _verify_deployment(final_service, registered_arn)
    return DeploymentResult(image=pinned_image, task_definition_arn=registered_arn)


def _wait_for_clean_scan(
    config: DeploymentConfig,
    *,
    run: CommandRunner,
    sleep: Callable[[float], None],
    digest: str,
) -> None:
    for attempt in range(config.scan_poll_attempts):
        try:
            scan = _aws_json(
                run,
                config.region,
                ("ecr", "describe-image-scan-findings", "--repository-name",
                 config.repository, "--image-id", f"imageDigest={digest}"),
            )
        except CommandFailure as error:
            if error.code != "ScanNotFoundException":
                raise
            scan = None

        if scan is not None:
            status = _mapping(scan.get("imageScanStatus"), "ECR scan status").get("status")
            if status == "COMPLETE":
                validate_scan_result(scan)
                return
            if status == "FAILED":
                raise DeploymentError("ECR image scan failed")
            if status not in (None, "IN_PROGRESS", "PENDING"):
                raise DeploymentError("ECR returned an unknown image scan status")
        if attempt + 1 < config.scan_poll_attempts:
            sleep(config.scan_poll_seconds)
    raise DeploymentError("ECR scan did not complete within the configured wait bound")


def _register_task_definition(
    config: DeploymentConfig,
    run: CommandRunner,
    payload: Mapping[str, object],
) -> str:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        ) as temporary_file:
            json.dump(payload, temporary_file, separators=(",", ":"))
            temporary_path = Path(temporary_file.name)
        response = _load_json(run((
            "aws", "ecs", "register-task-definition", "--cli-input-json",
            temporary_path.as_uri(), "--region", config.region,
            "--output", "json", "--no-cli-pager",
        )))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    task = _mapping(response.get("taskDefinition"), "registered task definition")
    return _required_string(task, "taskDefinitionArn")


def _verify_deployment(service: Mapping[str, object], expected_task_definition: str) -> None:
    if service.get("taskDefinition") != expected_task_definition:
        raise DeploymentError("ECS stabilized on a different task definition; promotion failed")
    desired = service.get("desiredCount")
    running = service.get("runningCount")
    pending = service.get("pendingCount")
    if not isinstance(desired, int) or not isinstance(running, int) or not isinstance(pending, int):
        raise DeploymentError("ECS service counts are unavailable")
    if desired < 1 or running != desired or pending != 0:
        raise DeploymentError("ECS service is not fully healthy after promotion")
    deployments = service.get("deployments")
    if not isinstance(deployments, list):
        raise DeploymentError("ECS deployment state is unavailable")
    primary = [
        _mapping(deployment, "ECS deployment")
        for deployment in deployments
        if isinstance(deployment, Mapping) and deployment.get("status") == "PRIMARY"
    ]
    if len(primary) != 1 or primary[0].get("rolloutState") != "COMPLETED":
        raise DeploymentError("ECS primary rollout did not complete")


def _require_rollback(service: Mapping[str, object]) -> None:
    configuration = _mapping(
        service.get("deploymentConfiguration"), "ECS deployment configuration"
    )
    breaker = _mapping(
        configuration.get("deploymentCircuitBreaker"), "ECS deployment circuit breaker"
    )
    if breaker.get("enable") is not True or breaker.get("rollback") is not True:
        raise DeploymentError("ECS circuit-breaker rollback must be enabled before deployment")


def _validate_chat_turn_limit(limit: int | None) -> None:
    if limit is None:
        return
    if isinstance(limit, bool) or limit < 1:
        raise DeploymentError("chat model turn limit must be a positive integer")
    if limit > _MAX_PILOT_CHAT_MODEL_TURN_LIMIT:
        raise DeploymentError("maximum cumulative turn limit is 7")


def _single_service(response: Mapping[str, object]) -> dict[str, object]:
    failures = response.get("failures", [])
    if failures:
        raise DeploymentError("ECS service lookup reported failures")
    services = response.get("services")
    if not isinstance(services, list) or len(services) != 1:
        raise DeploymentError("ECS lookup did not return exactly one service")
    service = _mapping(services[0], "ECS service")
    if service.get("status") != "ACTIVE":
        raise DeploymentError("ECS service is not active")
    return service


def _aws_json(run: CommandRunner, region: str, arguments: Sequence[str]) -> dict[str, object]:
    output = run(("aws", *arguments, "--region", region, "--output", "json", "--no-cli-pager"))
    return _load_json(output)


def _load_json(output: str) -> dict[str, object]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        raise DeploymentError("AWS CLI returned invalid JSON") from None
    return _mapping(value, "AWS response")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise DeploymentError(f"{label} is invalid")
    return cast(dict[str, object], value)


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise DeploymentError(f"AWS response is missing {key}")
    return item


def _run_command(command: Sequence[str], input_text: str | None = None) -> str:
    try:
        result = subprocess.run(
            list(command),
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        raise DeploymentError(f"could not start release command: {command[0]}") from None
    if result.returncode != 0:
        match = re.search(r"([A-Za-z]+Exception)", result.stderr)
        code = match.group(1) if match else None
        operation = " ".join(command[:3])
        raise CommandFailure(operation, code)
    return result.stdout.strip()


def _arguments() -> DeploymentConfig:
    parser = argparse.ArgumentParser(description="Build, scan, and deploy the Factory pilot image")
    parser.add_argument("--tag", required=True, dest="image_tag")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--cluster", default="pilot-factory")
    parser.add_argument("--service", default="pilot-factory")
    parser.add_argument("--repository", default="pilot/nokinc-factory")
    parser.add_argument("--context", default=".", dest="context_directory")
    parser.add_argument("--engine", default="podman", dest="container_cli")
    parser.add_argument("--container", default="factory", dest="container_name")
    parser.add_argument("--account-id", default="441186133046")
    parser.add_argument("--chat-model-turn-limit", type=int, default=None)
    parser.add_argument("--scan-poll-attempts", type=int, default=90)
    parser.add_argument("--scan-poll-seconds", type=int, default=10)
    parsed = parser.parse_args()
    return DeploymentConfig(**vars(parsed))


def main() -> int:
    try:
        result = deploy_pilot(_arguments(), run=_run_command)
    except DeploymentError as error:
        print(f"deployment blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps({
        "image": result.image,
        "task_definition_arn": result.task_definition_arn,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())