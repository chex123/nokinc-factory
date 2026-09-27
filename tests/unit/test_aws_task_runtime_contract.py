import re
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).parents[3]
AWS_TERRAFORM = WORKSPACE_ROOT / "nokinc-demo-infra" / "aws.tf"


def _aws_terraform() -> str:
    if not AWS_TERRAFORM.is_file():
        pytest.skip("cross-repository infrastructure checkout is unavailable")
    return AWS_TERRAFORM.read_text(encoding="utf-8")


def test_ecs_task_injects_runtime_secrets_without_embedding_values() -> None:
    content = _aws_terraform()

    assert 'variable "aws_auth_secret_arn"' in content
    assert 'variable "aws_database_worker_secret_arn"' in content
    assert 'variable "aws_github_app_id"' in content
    assert 'name = "FACTORY_DATABASE_SECRET_ARN"' in content
    assert 'name = "FACTORY_DATABASE_HOST"' in content
    assert 'name = "FACTORY_DATABASE_PORT"' in content
    assert 'name = "FACTORY_DATABASE_NAME"' in content
    assert 'value = var.aws_database_host' in content
    assert 'name = "FACTORY_AUTH_SECRET"' in content
    assert 'name = "FACTORY_DATABASE_URL"' not in content
    assert "aws_database_worker_secret_arn" in content
    assert "valueFrom" in content
    assert "tostring(var.aws_github_app_id)" in content


def test_aws_profile_provisions_private_managed_postgres() -> None:
    content = _aws_terraform()

    assert 'resource "aws_db_subnet_group" "factory"' in content
    assert 'resource "aws_security_group" "database"' in content
    assert 'resource "aws_db_instance" "factory"' in content
    assert 'engine' in content and '"postgres"' in content
    assert "manage_master_user_password = true" in content
    assert "publicly_accessible" in content
    assert "FACTORY_DATABASE_SECRET_ARN" in content
    assert "master_user_secret[0].secret_arn" in content


def test_ecs_execution_role_can_inject_runtime_secrets() -> None:
    content = _aws_terraform()

    assert 'data "aws_iam_policy_document" "execution_secrets"' in content
    assert 'resource "aws_iam_role_policy" "execution_secrets"' in content
    assert "aws_iam_role.execution[0].id" in content
    assert "secretsmanager:GetSecretValue" in content


def test_service_can_egress_to_private_postgres() -> None:
    content = _aws_terraform()

    assert 'resource "aws_security_group_rule" "service_database_egress"' in content
    assert "from_port                = 5432" in content
    assert "source_security_group_id = aws_security_group.database[0].id" in content


def test_oidc_profile_provisions_admin_managed_cognito_identity() -> None:
    content = _aws_terraform()

    assert 'resource "aws_cognito_user_pool" "factory"' in content
    assert 'resource "aws_cognito_user_pool_client" "factory"' in content
    assert 'resource "aws_cognito_user_group" "viewer"' in content
    assert 'resource "aws_cognito_user_group" "operator"' in content
    assert 'resource "aws_cognito_user_group" "admin"' in content
    assert 'allow_admin_create_user_only = true' in content
    assert 'mfa_configuration' in content and '"ON"' in content
    assert 'enabled = true' in content
    assert re.search(r'name\s+= "tenant_id"', content) is not None
    assert 'name = "FACTORY_OIDC_ISSUER"' in content
    assert 'name = "FACTORY_OIDC_AUDIENCE"' in content
    assert 'name = "FACTORY_OIDC_JWKS_URL"' in content
    assert 'value = local.aws_oidc_issuer_effective' in content
    assert 'value = local.aws_oidc_audience_effective' in content


def test_cognito_client_configures_browser_authorization_code_callback() -> None:
    content = _aws_terraform()

    assert 'resource "aws_cognito_user_pool_domain" "factory"' in content
    assert 'allowed_oauth_flows_user_pool_client = true' in content
    assert re.search(r'allowed_oauth_flows\s+= \["code"\]', content)
    assert re.search(r'allowed_oauth_scopes\s+= \["openid", "email", "profile"\]', content)
    assert re.search(r'callback_urls\s+= \[local\.aws_cognito_callback_url\]', content)
    assert re.search(r'logout_urls\s+= \[local\.aws_cognito_logout_url\]', content)
    assert 'name = "FACTORY_COGNITO_DOMAIN"' in content
    assert 'name = "FACTORY_COGNITO_CALLBACK_URL"' in content
    assert 'name = "FACTORY_PUBLIC_ORIGIN"' in content
