"""Jira Python Library adapter for Jira Server/Data Center analytics."""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from typing import Any, TypeVar

import requests

try:
    from jira import JIRA
    from jira.exceptions import JIRAError as JiraLibraryError
except ImportError:  # Keep non-network analytics tests runnable without optional install.
    JIRA = None  # type: ignore[assignment,misc]

    class JiraLibraryError(Exception):  # type: ignore[no-redef]
        """Fallback type used when the jira package is not installed."""

from .config import Config

LOG = logging.getLogger(__name__)
CORE_FIELDS = (
    "created",
    "resolutiondate",
    "resolution",
    "status",
    "priority",
    "assignee",
    "reporter",
    "timespent",
    "updated",
)

T = TypeVar("T")


class JiraAPIError(RuntimeError):
    """Raised when an operation through Jira Python Library fails."""


class JiraClient:
    """Small adapter around ``jira.JIRA`` that preserves the app's data contract.

    A separate Jira library client is lazily created per thread because report
    generation fetches issue histories concurrently. All HTTP operations are
    performed by Jira Python Library; this class only normalizes the responses
    and translates library/transport errors into ``JiraAPIError``.
    """

    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self._local = threading.local()

    def _create_jira_client(self) -> Any:
        """Create an authenticated Jira Python Library client for this thread."""
        if JIRA is None:
            raise JiraAPIError(
                "The 'jira' package is not installed. Install dependencies with: "
                "python -m pip install -r requirements.txt"
            )

        options: dict[str, Any] = {
            "rest_api_version": self.config.api_version,
            "verify": True,
            "headers": {
                "Accept": "application/json",
                "User-Agent": "jira-project-analytics/1.1.0",
            },
        }
        auth_kwargs: dict[str, Any] = {}
        if self.config.auth_mode == "basic":
            username = os.environ["JIRA_USERNAME"]
            password = os.getenv("JIRA_API_TOKEN") or os.environ["JIRA_PASSWORD"]
            auth_kwargs["basic_auth"] = (username, password)
        elif self.config.auth_mode == "bearer":
            auth_kwargs["token_auth"] = os.environ["JIRA_BEARER_TOKEN"]

        return JIRA(
            server=self.base_url,
            options=options,
            validate=False,
            get_server_info=False,
            timeout=self.config.timeout_seconds,
            max_retries=self.config.request_retries,
            logging=False,
            **auth_kwargs,
        )

    def _get_jira(self) -> Any:
        """Return the Jira library client associated with the current thread."""
        client = getattr(self._local, "jira_client", None)
        if client is None:
            client = self._create_jira_client()
            self._local.jira_client = client
        return client

    def _call_library(self, operation: str, callback: Callable[[Any], T]) -> T:
        """Run a Jira-library operation and normalize expected API failures."""
        try:
            return callback(self._get_jira())
        except JiraAPIError:
            raise
        except JiraLibraryError as exc:
            status_code = getattr(exc, "status_code", None)
            details = getattr(exc, "text", None) or str(exc)
            if isinstance(details, bytes):
                details = details.decode("utf-8", errors="replace")
            details = " ".join(str(details).split())[:500]
            status = f" (HTTP {status_code})" if status_code is not None else ""
            raise JiraAPIError(
                f"Jira Python Library operation '{operation}' failed{status}: {details}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise JiraAPIError(
                f"Could not reach Jira API at {self.base_url} during '{operation}': {exc}"
            ) from exc

    def fetch_issues(self) -> list[dict[str, Any]]:
        """Fetch every visible issue matching configured project JQL.

        ``search_issues`` is used in JSON-result mode so the analytics layer
        receives the same plain-dictionary shape it used before the refactor.
        ``use_post=True`` asks the library to use Jira's POST search endpoint.
        Pagination stays explicit so ``max_issues`` and server-side page-size
        clamping behave predictably.
        """
        fields = list(dict.fromkeys((*CORE_FIELDS, self.config.severity_field)))
        issues: list[dict[str, Any]] = []
        start_at = 0
        total: int | None = None

        while True:
            payload = self._call_library(
                "search_issues",
                lambda jira: jira.search_issues(
                    self.config.jql,
                    startAt=start_at,
                    maxResults=self.config.page_size,
                    fields=list(fields),
                    validate_query=True,
                    json_result=True,
                    use_post=True,
                ),
            )
            if not isinstance(payload, dict):
                raise JiraAPIError(
                    "Unexpected response: Jira Python Library search_issues "
                    "did not return a JSON object"
                )
            page = payload.get("issues", [])
            if not isinstance(page, list):
                raise JiraAPIError("Unexpected response: 'issues' is not a list")

            issues.extend(item for item in page if isinstance(item, dict))
            total_value = payload.get("total")
            total = total_value if isinstance(total_value, int) else total
            LOG.info(
                "Loaded %d issues%s",
                len(issues),
                f" of {total}" if total is not None else "",
            )

            if self.config.max_issues and len(issues) >= self.config.max_issues:
                issues = issues[: self.config.max_issues]
                break
            if not page:
                break

            start_at += len(page)
            if total is not None:
                if start_at >= total:
                    break
                # Continue even if the server clamps maxResults to a smaller page.
                continue
            if len(page) < self.config.page_size:
                break

        return issues

    def fetch_issue_history(self, issue_key: str) -> dict[str, Any]:
        """Fetch one issue and the changelog exposed by Jira's issue resource."""
        issue = self._call_library(
            "issue",
            lambda jira: jira.issue(
                issue_key,
                fields="created,status,resolutiondate,updated",
                expand="changelog",
            ),
        )
        raw = getattr(issue, "raw", None)
        if not isinstance(raw, dict):
            raise JiraAPIError(
                f"Jira Python Library returned no raw JSON for issue {issue_key}"
            )
        return raw

    def fetch_field_names(self) -> dict[str, str]:
        """Return a best-effort map of Jira field IDs to their display names."""
        try:
            payload = self._call_library("fields", lambda jira: jira.fields())
            if not isinstance(payload, list):
                return {}
            return {
                str(item["id"]): str(item.get("name", item["id"]))
                for item in payload
                if isinstance(item, dict) and "id" in item
            }
        except (JiraAPIError, JiraLibraryError, requests.exceptions.RequestException) as exc:
            LOG.debug("Could not retrieve Jira field labels: %s", exc)
            return {}
