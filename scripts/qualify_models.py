"""Run a bounded synthetic qualification probe for configured model ports."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import boto3  # type: ignore[import-untyped]
import yaml

from nokinc_factory.adapters.model_providers import (
    BedrockModelPort,
    GoogleGeminiModelPort,
    OpenAIModelPort,
)
from nokinc_factory.application.model_qualification_runner import (
    QualificationCallLimit,
    QualificationCase,
    QualificationRunner,
)
from nokinc_factory.ports.model import ModelPort, ModelRequest


class BotoSecrets:
    """Retrieve named provider credentials without exposing values to output."""

    def get(self, secret_ref: str) -> str:
        parsed = urlsplit(secret_ref)
        parts = parsed.path.lstrip("/").split("/", 1)
        if parsed.scheme != "aws-secretsmanager" or not parsed.netloc:
            raise ValueError("secret reference must use aws-secretsmanager://")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1]:
            raise ValueError("secret reference is malformed")
        response = boto3.client(
            "secretsmanager", region_name=parsed.netloc,
        ).get_secret_value(SecretId=parts[1])
        value = response.get("SecretString")
        if not isinstance(value, str) or not value:
            raise ValueError("secret does not contain a string value")
        return value


def _model_specs(config: Mapping[str, object]) -> list[dict[str, str]]:
    models = config.get("models")
    if not isinstance(models, dict):
        raise ValueError("pilot config models section is missing")
    specs: list[dict[str, str]] = []
    for capability in ("coding", "architecture_and_business"):
        group = models.get(capability)
        if not isinstance(group, dict):
            raise ValueError("pilot config model capability is missing")
        for role in ("doer", "reviewer"):
            entry = group.get(role)
            if not isinstance(entry, dict):
                raise ValueError("pilot config model role is missing")
            values = {
                key: entry.get(key, "")
                for key in ("provider", "family", "credential_ref", "exact_model_id", "runtime")
            }
            if any(not isinstance(value, str) or not value.strip() for value in values.values()):
                raise ValueError("pilot config model identity is incomplete")
            values["capability"] = capability
            values["role"] = role
            specs.append(values)
    return specs


def _cases(
    config: Mapping[str, object], *, region: str, max_output_tokens: int,
) -> tuple[QualificationCase, ...]:
    secrets = BotoSecrets()
    cases: list[QualificationCase] = []
    for spec in _model_specs(config):
        provider = spec["provider"]
        model = spec["exact_model_id"]
        family = spec["family"]
        port: ModelPort
        if provider == "openai":
            port = OpenAIModelPort(
                model=model,
                family=family,
                credential_ref=spec["credential_ref"],
                secrets=secrets,
                max_output_tokens=max_output_tokens,
            )
        elif provider == "google":
            port = GoogleGeminiModelPort(
                model=model,
                family=family,
                credential_ref=spec["credential_ref"],
                secrets=secrets,
                max_output_tokens=max_output_tokens,
            )
        elif provider == "aws-bedrock":
            port = BedrockModelPort(
                model=model,
                family=family,
                region=region,
                max_output_tokens=max_output_tokens,
            )
        else:
            raise ValueError("unsupported model provider in pilot config")
        cases.append(QualificationCase(
            provider=provider,
            model=model,
            family=family,
            port=port,
        ))
    return tuple(cases)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--max-calls", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("pilot config must be a YAML object")
        cases = _cases(config, region=args.region, max_output_tokens=args.max_output_tokens)
        call_limit = QualificationCallLimit(max_calls=args.max_calls)
        request = ModelRequest(
            role="qualification",
            prompt=(
                "This is a synthetic qualification probe with no customer data. "
                "Reply exactly with QUALIFICATION_OK and nothing else."
            ),
            context_digest="sha256:" + "3" * 64,
        )
        report = QualificationRunner(
            call_limit,
            pricing_region=args.region,
        ).run(cases, request)
        output = report.as_dict()
        output["region"] = args.region
        output["models_configured"] = len(cases)
        print(json.dumps(output, sort_keys=True))
        return 0 if report.passed else 2
    except (OSError, TypeError, ValueError, KeyError):
        print(json.dumps({"passed": False, "error_code": "QUALIFICATION_CONFIGURATION_ERROR"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())