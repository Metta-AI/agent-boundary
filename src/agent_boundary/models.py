"""Strict models for authored profiles and generated session state."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

State = Literal["on", "off"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SymlinkSpec(StrictModel):
    path: str
    access: Literal["allow", "read", "write"] = "read"
    bypass_protection: bool = False


class AwsSettings(StrictModel):
    profile: str = Field(min_length=1)


class KubeSettings(StrictModel):
    eks: bool = False
    orbstack: bool = False


class GithubSettings(StrictModel):
    push: bool = False


class Profile(StrictModel):
    name: str
    description: str
    self_edit: bool = False
    git_common_dir: bool = True
    aws: AwsSettings | None = None
    kube: KubeSettings = Field(default_factory=KubeSettings)
    github: GithubSettings | None = None
    allow: list[str] = Field(default_factory=list)
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    resolve_symlinks: list[str | SymlinkSpec] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    nono: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def eks_requires_aws(self) -> "Profile":
        if self.kube.eks and self.aws is None:
            raise ValueError("kube.eks requires aws.profile")
        return self


class SessionConfig(StrictModel):
    profile: str
    state: State
    self_edit: bool
    workdir: Path
    profiles_dir: Path
    protected_paths: list[Path]
    aws_profile: str | None = None
    github: GithubSettings | None = None
