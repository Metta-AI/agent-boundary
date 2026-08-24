"""Build a minimal per-session kubeconfig from trusted local configuration."""

import configparser
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agent_boundary.models import AwsSettings, Profile

KUBECONFIG_FILENAME = "kubeconfig"
EKS_ARN = re.compile(r"arn:[^:]+:eks:(?P<region>[^:]+):(?P<account>\d{12}):cluster/(?P<name>.+)")


class KubeModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=True)


class Cluster(KubeModel):
    server: str
    certificate_authority_data: str | None = Field(
        default=None,
        validation_alias="certificate-authority-data",
        serialization_alias="certificate-authority-data",
    )
    certificate_authority: str | None = Field(
        default=None,
        validation_alias="certificate-authority",
        serialization_alias="certificate-authority",
    )
    insecure_skip_tls_verify: bool | None = Field(
        default=None,
        validation_alias="insecure-skip-tls-verify",
        serialization_alias="insecure-skip-tls-verify",
    )


class NamedCluster(KubeModel):
    name: str
    cluster: Cluster


class Context(KubeModel):
    cluster: str
    user: str
    namespace: str | None = None


class NamedContext(KubeModel):
    name: str
    context: Context


class ExecEnv(KubeModel):
    name: str
    value: str


class Exec(KubeModel):
    api_version: str = Field(validation_alias="apiVersion", serialization_alias="apiVersion")
    command: str
    args: list[str] = Field(default_factory=list)
    env: list[ExecEnv] = Field(default_factory=list)
    interactive_mode: Literal["Never", "IfAvailable", "Always"] | None = Field(
        default=None,
        validation_alias="interactiveMode",
        serialization_alias="interactiveMode",
    )


class User(KubeModel):
    exec: Exec | None = None
    client_certificate_data: str | None = Field(
        default=None,
        validation_alias="client-certificate-data",
        serialization_alias="client-certificate-data",
    )
    client_key_data: str | None = Field(
        default=None,
        validation_alias="client-key-data",
        serialization_alias="client-key-data",
    )


class NamedUser(KubeModel):
    name: str
    user: User


class Config(KubeModel):
    api_version: str = Field(validation_alias="apiVersion", serialization_alias="apiVersion")
    kind: str
    clusters: list[NamedCluster] = Field(default_factory=list)
    contexts: list[NamedContext] = Field(default_factory=list)
    users: list[NamedUser] = Field(default_factory=list)
    current_context: str = Field(
        default="",
        validation_alias="current-context",
        serialization_alias="current-context",
    )


@dataclass(frozen=True)
class BuildResult:
    config: Config | None
    warnings: tuple[str, ...]


def _account_id(settings: AwsSettings, path: Path) -> str | None:
    if not path.is_file():
        return None
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    section = "default" if settings.profile == "default" else f"profile {settings.profile}"
    if not parser.has_section(section):
        return None
    if account := parser[section].get("sso_account_id"):
        return account
    if match := re.fullmatch(r"arn:[^:]+:iam::(?P<account>\d{12}):role/.+", parser[section].get("role_arn", "")):
        return match.group("account")
    return None


def build(
    profile: Profile,
    *,
    aws_config_path: Path | None = None,
    source_path: Path | None = None,
) -> BuildResult:
    if not profile.kube.eks and not profile.kube.orbstack:
        return BuildResult(None, ())

    source_path = source_path or Path.home() / ".kube/config"
    if not source_path.is_file():
        return BuildResult(None, (f"no source kubeconfig at {source_path}",))
    source = Config.model_validate(yaml.safe_load(source_path.read_text()))
    clusters: list[NamedCluster] = []
    contexts: list[NamedContext] = []
    users: list[NamedUser] = []
    warnings: list[str] = []
    current_context = ""

    if profile.kube.eks:
        aws = cast(AwsSettings, profile.aws)
        aws_config_path = aws_config_path or Path(os.environ.get("AWS_CONFIG_FILE", Path.home() / ".aws/config"))
        account = _account_id(aws, aws_config_path)
        matches = []
        if account:
            matches = [
                (cluster, match)
                for cluster in source.clusters
                if (match := EKS_ARN.fullmatch(cluster.name)) and match.group("account") == account
            ]
        if len(matches) == 1:
            cluster, match = matches[0]
            cluster_name = match.group("name")
            context_name = f"agent-boundary-{cluster_name}"
            clusters.append(cluster)
            contexts.append(
                NamedContext(
                    name=context_name,
                    context=Context(cluster=cluster.name, user="agent-boundary-aws"),
                )
            )
            users.append(
                NamedUser(
                    name="agent-boundary-aws",
                    user=User(
                        exec=Exec(
                            api_version="client.authentication.k8s.io/v1beta1",
                            command="aws",
                            args=[
                                "--region",
                                match.group("region"),
                                "eks",
                                "get-token",
                                "--cluster-name",
                                cluster_name,
                                "--output",
                                "json",
                            ],
                            interactive_mode="Never",
                        )
                    ),
                )
            )
            current_context = context_name
        elif account:
            warnings.append(f"aws profile {aws.profile!r} maps to {len(matches)} EKS clusters in {source_path}")
        else:
            warnings.append(f"could not find an account ID for aws profile {aws.profile!r}")

    if profile.kube.orbstack:
        orb_clusters = [cluster for cluster in source.clusters if cluster.name == "orbstack"]
        orb_contexts = [context for context in source.contexts if context.name == "orbstack"]
        orb_users = [user for user in source.users if user.name == "orbstack"]
        if len(orb_clusters) == len(orb_contexts) == len(orb_users) == 1:
            clusters.append(orb_clusters[0])
            contexts.append(orb_contexts[0])
            users.append(orb_users[0])
            current_context = current_context or "orbstack"
        else:
            warnings.append(f"source kubeconfig {source_path} has no complete orbstack cluster/context/user")

    config = (
        Config(
            api_version="v1",
            kind="Config",
            clusters=clusters,
            contexts=contexts,
            users=users,
            current_context=current_context,
        )
        if current_context
        else None
    )
    return BuildResult(config, tuple(warnings))


def write(
    directory: Path,
    profile: Profile,
    *,
    aws_config_path: Path | None = None,
    source_path: Path | None = None,
) -> tuple[str, ...]:
    result = build(profile, aws_config_path=aws_config_path, source_path=source_path)
    path = directory / KUBECONFIG_FILENAME
    if result.config is None:
        path.unlink(missing_ok=True)
        return result.warnings

    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{KUBECONFIG_FILENAME}.{os.getpid()}.tmp")
    temporary.touch(mode=0o600)
    os.chmod(temporary, 0o600)
    temporary.write_text(
        yaml.safe_dump(result.config.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=False)
    )
    os.replace(temporary, path)
    return result.warnings
