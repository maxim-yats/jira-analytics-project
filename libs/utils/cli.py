"""Command-line orchestration for the Jira analytics report."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .analytics import build_analytics, issue_fields, issue_key
from .charts import build_html_report
from .client import JiraAPIError, JiraClient
from .config import Config

LOG = logging.getLogger("jira_analytics")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load_histories(client: JiraClient, issues: list[dict[str, Any]], config: Config) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Fetch changelog for closed candidates concurrently and record partial failures."""
    history_by_key: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    for issue in issues:
        fields = issue_fields(issue)
        status = fields.get("status")
        category = status.get("statusCategory") if isinstance(status, dict) else None
        is_done_category = isinstance(category, dict) and category.get("key") == "done"
        if fields.get("resolutiondate") or fields.get("resolution") or is_done_category:
            candidates.append(issue)
    if not candidates:
        LOG.info("No likely closed issues found; skipping changelog requests")
        return history_by_key, warnings

    LOG.info("Loading status history for up to %d closed issues using %d workers", len(candidates), config.workers)
    with ThreadPoolExecutor(max_workers=config.workers, thread_name_prefix="jira-history") as pool:
        futures = {pool.submit(client.fetch_issue_history, issue_key(issue)): issue for issue in candidates}
        completed = 0
        for future in as_completed(futures):
            issue = futures[future]
            key = issue_key(issue)
            completed += 1
            if completed % 100 == 0 or completed == len(futures):
                LOG.info("Fetched history %d/%d", completed, len(futures))
            try:
                history_payload = future.result()
                changelog = history_payload.get("changelog", {})
                histories = changelog.get("histories", []) if isinstance(changelog, dict) else []
                if not isinstance(histories, list):
                    histories = []
                normalized_histories = [item for item in histories if isinstance(item, dict)]
                total = changelog.get("total") if isinstance(changelog, dict) else None
                if isinstance(total, int) and total > len(normalized_histories):
                    warnings.append(
                        f"{key}: API вернул {len(normalized_histories)} из {total} записей changelog; "
                        "история неполная, длительность будет помечена как оценочная."
                    )
                    # A truncated history must not be treated as complete from
                    # creation time: that would yield a misleading timeline.
                    history_by_key[key] = []
                else:
                    history_by_key[key] = normalized_histories
            except Exception as exc:
                if config.history_required:
                    warnings.append(f"{key}: не удалось загрузить историю переходов ({exc}).")
                else:
                    LOG.debug("History unavailable for %s: %s", key, exc)
                history_by_key[key] = []
    return history_by_key, warnings


def run(config_path: str | Path, output_override: str | None = None, verbose: bool = False) -> Path:
    """Load Jira data, calculate analytics, and generate the HTML report."""
    _configure_logging(verbose)
    config = Config.from_file(config_path)
    client = JiraClient(config)
    LOG.info("Connecting to %s; project=%s", config.base_url, config.project_key)
    issues = client.fetch_issues()
    if not issues:
        LOG.warning("Jira returned no issues for JQL: %s", config.jql)
    LOG.info("Loaded %d issue records", len(issues))
    histories_by_key, history_warnings = _load_histories(client, issues, config)
    data = build_analytics(issues, histories_by_key, config.severity_field)

    field_names = client.fetch_field_names()
    severity_label = field_names.get(config.severity_field, config.severity_field)
    if config.severity_field == "priority" and severity_label == "priority":
        severity_label = "Priority (proxy for severity)"
    output_file = Path(output_override) if output_override else Path(config.output_dir) / f"jira-analytics-{config.project_key.lower()}.html"
    result_path = build_html_report(data, output_file, config.project_key, severity_label)

    if history_warnings:
        warning_path = result_path.with_suffix(".warnings.txt")
        warning_path.write_text("Предупреждения при загрузке данных JIRA:\n\n" + "\n".join(history_warnings) + "\n", encoding="utf-8")
        LOG.warning("Some changelog requests were incomplete. Details: %s", warning_path)
    summary_path = result_path.with_suffix(".summary.json")
    summary = {key: value for key, value in data.items() if key not in {"lifecycle_days", "status_days", "daily_counts", "users", "severity_counts", "worklog_days"}}
    summary.update({
        "project_key": config.project_key,
        "jql": config.jql,
        "severity_field": config.severity_field,
        "report_file": str(result_path),
    })
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("Report saved to %s", result_path.resolve())
    LOG.info("Summary saved to %s", summary_path.resolve())
    return result_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build analytics charts from Jira Server/Data Center project issues.")
    parser.add_argument("--config", default="config.json", help="Path to JSON config (default: config.json)")
    parser.add_argument("--output", help="Output HTML file path")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)
    try:
        run(args.config, args.output, args.verbose)
        return 0
    except (FileNotFoundError, ValueError, JiraAPIError) as exc:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
        LOG.error("Analytics report failed: %s", exc)
        return 2
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
