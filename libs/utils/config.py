"""Configuration loading and validation for JIRA analytics."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration.

    Secrets are read from environment variables and are never stored in the
    JSON configuration file.
    """

    base_url: str = "https://issues.apache.org/jira"
    api_version: str = "2"
    project_key: str = "KAFKA"
    jql_filter: str = ""
    severity_field: str = "priority"
    output_dir: str = "output"
    page_size: int = 100
    timeout_seconds: float = 30.0
    request_retries: int = 3
    workers: int = 8
    max_issues: int = 0
    auth_mode: str = "anonymous"
    history_required: bool = True

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        """Load configuration from a JSON file and validate its values."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(
                f"Configuration file '{file_path}' does not exist. "
                "Copy config.example.json to config.json and edit it."
            )
        try:
            raw: dict[str, Any] = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in '{file_path}': {exc}") from exc

        allowed = {item.name for item in cls.__dataclass_fields__.values()}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(f"Unknown configuration key(s): {', '.join(unknown)}")

        values = {**raw}
        # Environment overrides make container/CI deployment convenient.
        values["base_url"] = os.getenv("JIRA_BASE_URL", values.get("base_url", cls.base_url))
        values["project_key"] = os.getenv("JIRA_PROJECT_KEY", values.get("project_key", cls.project_key))
        values["auth_mode"] = os.getenv("JIRA_AUTH_MODE", values.get("auth_mode", cls.auth_mode))
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        """Raise ValueError when a setting is outside its supported range."""
        # Python's match/case (3.10+) is used as a switch-style ordered check.
        # The first matching guard raises its corresponding validation error.
        match True:
            case _ if not isinstance(self.base_url, str) or not self.base_url.strip().startswith(("http://", "https://")):
                raise ValueError("base_url must be a URL starting with http:// or https://")
            case _ if self.api_version != "2":
                raise ValueError("This version targets Jira Server/Data Center REST API v2; api_version must be '2'.")
            case _ if not isinstance(self.project_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.project_key):
                raise ValueError("project_key must contain only letters, digits, underscores, or hyphens")
            case _ if not isinstance(self.jql_filter, str):
                raise ValueError("jql_filter must be a string")
            case _ if not isinstance(self.severity_field, str) or not self.severity_field.strip():
                raise ValueError("severity_field must be a non-empty string")
            case _ if not isinstance(self.output_dir, str) or not self.output_dir.strip():
                raise ValueError("output_dir must be a non-empty string")
            case _ if not isinstance(self.page_size, int) or isinstance(self.page_size, bool) or not 1 <= self.page_size <= 1000:
                raise ValueError("page_size must be an integer between 1 and 1000")
            case _ if not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool) or self.timeout_seconds <= 0:
                raise ValueError("timeout_seconds must be a positive number")
            case _ if not isinstance(self.request_retries, int) or isinstance(self.request_retries, bool) or not 0 <= self.request_retries <= 10:
                raise ValueError("request_retries must be an integer between 0 and 10")
            case _ if not isinstance(self.workers, int) or isinstance(self.workers, bool) or not 1 <= self.workers <= 32:
                raise ValueError("workers must be an integer between 1 and 32")
            case _ if not isinstance(self.max_issues, int) or isinstance(self.max_issues, bool) or self.max_issues < 0:
                raise ValueError("max_issues must be zero (no limit) or a positive integer")
            case _ if self.auth_mode not in {"anonymous", "basic", "bearer"}:
                raise ValueError("auth_mode must be anonymous, basic, or bearer")
            case _ if not isinstance(self.history_required, bool):
                raise ValueError("history_required must be true or false")
            case _ if self.auth_mode == "basic" and not (
                os.getenv("JIRA_USERNAME")
                and (os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_PASSWORD"))
            ):
                raise ValueError(
                    "Basic auth needs JIRA_USERNAME and JIRA_API_TOKEN "
                    "(or JIRA_PASSWORD) environment variables"
                )
            case _ if self.auth_mode == "bearer" and not os.getenv("JIRA_BEARER_TOKEN"):
                raise ValueError("Bearer auth needs JIRA_BEARER_TOKEN environment variable")
            case _:
                return

    @property
    def jql(self) -> str:
        """Build the query that selects all visible issues in the project."""
        query = f'project = "{self.project_key}"'
        if self.jql_filter.strip():
            query += f" AND ({self.jql_filter.strip()})"
        return query + " ORDER BY created ASC"
