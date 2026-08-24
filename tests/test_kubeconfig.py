from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent_boundary import kubeconfig
from agent_boundary.models import Profile


def write_source_configs(tmp_path: Path, clusters: list[dict] | None = None) -> tuple[Path, Path]:
    aws_config = tmp_path / "aws-config"
    aws_config.write_text("[profile MyOrg/ReadonlyAccess]\nsso_account_id = 123456789012\nregion = us-east-1\n")
    source = tmp_path / "kubeconfig"
    source.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": clusters
                or [
                    {
                        "name": "arn:aws:eks:us-east-1:123456789012:cluster/main",
                        "cluster": {
                            "server": "https://main.example",
                            "certificate-authority-data": "MAIN-CA",
                        },
                    },
                    {
                        "name": "arn:aws:eks:us-east-1:879816410702:cluster/staging",
                        "cluster": {
                            "server": "https://staging.example",
                            "certificate-authority-data": "STAGING-CA",
                        },
                    },
                    {
                        "name": "orbstack",
                        "cluster": {"server": "https://127.0.0.1:26443"},
                    },
                ],
                "contexts": [
                    {
                        "name": "example-readonly",
                        "context": {
                            "cluster": "arn:aws:eks:us-east-1:123456789012:cluster/main",
                            "user": "example-readonly",
                        },
                    },
                    {
                        "name": "orbstack",
                        "context": {"cluster": "orbstack", "user": "orbstack"},
                    },
                ],
                "users": [
                    {
                        "name": "example-readonly",
                        "user": {
                            "exec": {
                                "apiVersion": "client.authentication.k8s.io/v1beta1",
                                "command": "aws",
                                "args": ["eks", "get-token", "--cluster-name", "main"],
                                "env": [
                                    {
                                        "name": "AWS_PROFILE",
                                        "value": "MyOrg/ReadonlyAccess",
                                    }
                                ],
                            }
                        },
                    },
                    {
                        "name": "orbstack",
                        "user": {
                            "client-certificate-data": "ORBSTACK-CERT",
                            "client-key-data": "ORBSTACK-KEY",
                        },
                    },
                ],
                "current-context": "example-readonly",
            },
            sort_keys=False,
        )
    )
    return aws_config, source


def profile(*, eks: bool = True, orbstack: bool = True) -> Profile:
    return Profile.model_validate(
        {
            "name": "test",
            "description": "test profile",
            "aws": {"profile": "MyOrg/ReadonlyAccess"},
            "kube": {"eks": eks, "orbstack": orbstack},
        }
    )


def test_profile_uses_nested_aws_and_kube_settings() -> None:
    parsed = profile()
    assert parsed.aws and parsed.aws.profile == "MyOrg/ReadonlyAccess"
    assert parsed.kube.eks
    assert parsed.kube.orbstack

    with pytest.raises(ValidationError):
        Profile.model_validate({"name": "old", "description": "old", "aws_profile": "legacy"})
    with pytest.raises(ValidationError):
        Profile.model_validate({"name": "missing-aws", "description": "missing", "kube": {"eks": True}})


def test_builds_env_credential_eks_user_and_copies_orbstack(tmp_path: Path) -> None:
    aws_config, source = write_source_configs(tmp_path)
    result = kubeconfig.build(profile(), aws_config_path=aws_config, source_path=source)

    assert result.warnings == ()
    assert result.config is not None
    assert [cluster.name for cluster in result.config.clusters] == [
        "arn:aws:eks:us-east-1:123456789012:cluster/main",
        "orbstack",
    ]
    assert [context.name for context in result.config.contexts] == ["agent-boundary-main", "orbstack"]
    assert [user.name for user in result.config.users] == ["agent-boundary-aws", "orbstack"]
    assert result.config.current_context == "agent-boundary-main"

    user = result.config.users[0].user
    assert user.exec is not None
    assert user.exec.command == "aws"
    assert user.exec.args == [
        "--region",
        "us-east-1",
        "eks",
        "get-token",
        "--cluster-name",
        "main",
        "--output",
        "json",
    ]
    assert user.exec.env == []
    assert user.exec.interactive_mode == "Never"
    assert result.config.users[1].user.client_key_data == "ORBSTACK-KEY"


def test_eks_ambiguity_keeps_requested_orbstack(tmp_path: Path) -> None:
    clusters = [
        {
            "name": f"arn:aws:eks:us-east-1:123456789012:cluster/{name}",
            "cluster": {"server": f"https://{name}.example", "certificate-authority-data": f"{name}-CA"},
        }
        for name in ("main", "other")
    ]
    clusters.append({"name": "orbstack", "cluster": {"server": "https://127.0.0.1:26443"}})
    aws_config, source = write_source_configs(tmp_path, clusters)

    result = kubeconfig.build(profile(), aws_config_path=aws_config, source_path=source)

    assert len(result.warnings) == 1
    assert "2 EKS clusters" in result.warnings[0]
    assert result.config is not None
    assert result.config.current_context == "orbstack"
    assert [cluster.name for cluster in result.config.clusters] == ["orbstack"]


def test_write_uses_fixed_private_file_and_removes_stale_output(tmp_path: Path) -> None:
    aws_config, source = write_source_configs(tmp_path)
    directory = tmp_path / "session"

    warnings = kubeconfig.write(
        directory,
        profile(orbstack=False),
        aws_config_path=aws_config,
        source_path=source,
    )
    path = directory / "kubeconfig"
    assert warnings == ()
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    assert yaml.safe_load(path.read_text())["current-context"] == "agent-boundary-main"

    warnings = kubeconfig.write(
        directory,
        Profile.model_validate({"name": "none", "description": "no kube"}),
        aws_config_path=aws_config,
        source_path=source,
    )
    assert warnings == ()
    assert not path.exists()
