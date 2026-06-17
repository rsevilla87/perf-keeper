"""Centralized configuration loaded from a YAML file."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    github_token: str = ""
    google_api_key: str = ""
    model_name: str = "gemini-2.5-flash"
    model_temperature: float = 0.0
    log_level: str = "INFO"

    prow_artifacts_url: str = "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com"
    prow_domain: str = "https://prow.ci.openshift.org"
    github_api_url: str = "https://api.github.com"
    sippy_base_url: str = "https://sippy.dptools.openshift.org/api"
    ocp_release_api_url: str = "https://amd64.ocp.releases.ci.openshift.org/api/v1"

    job_names: list[str] = field(default_factory=list)
    poll_interval: int = 15
    output_dir: str = "./reports"

    es_server: str = ""
    es_index: str = "ripsaw-kube-burner-*"
    es_verify: bool = False


_config = Config()


def load_config(path: str | Path | None = None) -> Config:
    global _config
    if path is None:
        return _config
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    for key, value in data.items():
        if hasattr(_config, key):
            setattr(_config, key, value)
    return _config


def get_config() -> Config:
    return _config
