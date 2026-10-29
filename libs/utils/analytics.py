"""Pure analytics functions for Jira issue records."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable


SECONDS_PER_DAY = 86400.0


def parse_jira_datetime(value: Any) -> datetime | None:
    """Parse a Jira ISO-8601 timestamp into a timezone-aware datetime."""
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def user_display_name(user: Any, unknown: str = "Unknown") -> str | None:
    """Extract a stable human-readable user label from a Jira user object."""
    if not isinstance(user, dict):
        return None
    for key in ("displayName", "name", "emailAddress", "accountId", "key"):
        value = user.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return unknown


def issue_fields(issue: dict[str, Any]) -> dict[str, Any]:
    fields = issue.get("fields")
    return fields if isinstance(fields, dict) else {}


def issue_key(issue: dict[str, Any]) -> str:
    return str(issue.get("key") or "<unknown>")


def status_name(status: Any) -> str | None:
    return str(status.get("name")) if isinstance(status, dict) and status.get("name") else None


def status_category(status: Any) -> str | None:
    category = status.get("statusCategory") if isinstance(status, dict) else None
    key = category.get("key") if isinstance(category, dict) else None
    return str(key).lower() if key else None


def extract_status_events(histories: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract chronological status transition events from Jira changelog."""
    events: list[dict[str, Any]] = []
    for history in histories:
        if not isinstance(history, dict):
            continue
        changed_at = parse_jira_datetime(history.get("created"))
        if changed_at is None:
            continue
        items = history.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or item.get("fieldId") or "").lower()
            if field != "status":
                continue
            events.append({
                "at": changed_at,
                "from": item.get("fromString"),
                "to": item.get("toString"),
            })
    events.sort(key=lambda event: event["at"])
    return events


def resolved_at_for_issue(issue: dict[str, Any], histories: Iterable[dict[str, Any]] = ()) -> datetime | None:
    """Return resolution time, inferring it from a terminal status change if needed.

    Jira's `resolutiondate` is preferred. The fallback is limited to a status
    history transition into the issue's current Done-category status or a common
    terminal status name; `updated` is deliberately not used as a close date.
    """
    fields = issue_fields(issue)
    resolved = parse_jira_datetime(fields.get("resolutiondate"))
    if resolved is not None:
        return resolved
    current_status = status_name(fields.get("status"))
    current_category = status_category(fields.get("status"))
    terminal_names = {"closed", "resolved", "done", "complete", "completed"}
    events = extract_status_events(histories)
    current_is_terminal = current_category == "done" or (
        current_status is not None and current_status.strip().lower() in terminal_names
    )
    # Do not mistake an old Closed -> Reopened cycle for a currently closed issue.
    if not current_is_terminal:
        return None
    candidates = [
        event["at"]
        for event in events
        if isinstance(event.get("to"), str)
        and current_status is not None
        and event["to"].strip() == current_status.strip()
    ]
    if candidates:
        return max(candidates)
    return None


def is_closed_issue(issue: dict[str, Any], histories: Iterable[dict[str, Any]] = ()) -> bool:
    """Treat an issue as closed only if Jira supplies or history supports a close date."""
    fields = issue_fields(issue)
    return resolved_at_for_issue(issue, histories) is not None


def status_durations_for_issue(
    issue: dict[str, Any], histories: Iterable[dict[str, Any]], end_at: datetime | None = None
) -> tuple[dict[str, float], bool]:
    """Return aggregate days spent in each state and whether the result is estimated.

    The durations of repeated visits to one status are added. If no status
    transitions are available, the issue's current status is used for the whole
    lifetime as a marked estimate rather than silently dropping the issue.
    """
    fields = issue_fields(issue)
    created = parse_jira_datetime(fields.get("created"))
    end = end_at or resolved_at_for_issue(issue, histories)
    if created is None or end is None or end <= created:
        return {}, False

    events = [e for e in extract_status_events(histories) if created <= e["at"] <= end]
    durations_seconds: defaultdict[str, float] = defaultdict(float)
    estimated = not events
    current_status = status_name(fields.get("status")) or "Unknown status"
    if not events:
        durations_seconds[current_status] += (end - created).total_seconds()
    else:
        first_from = events[0].get("from")
        state = str(first_from).strip() if first_from else current_status
        cursor = created
        for event in events:
            when = event["at"]
            if when < cursor:
                continue
            if when > cursor and state:
                durations_seconds[state] += (when - cursor).total_seconds()
            cursor = when
            next_state = event.get("to")
            if isinstance(next_state, str) and next_state.strip():
                state = next_state.strip()
        if end > cursor and state:
            durations_seconds[state] += (end - cursor).total_seconds()

    return {name: seconds / SECONDS_PER_DAY for name, seconds in durations_seconds.items() if seconds > 0}, estimated


def build_analytics(issues: list[dict[str, Any]], histories_by_key: dict[str, list[dict[str, Any]]], severity_field: str = "priority") -> dict[str, Any]:
    """Calculate all datasets needed by the six required report chart types."""
    created_by_day: Counter[date] = Counter()
    closed_by_day: Counter[date] = Counter()
    lifecycle_days: list[dict[str, Any]] = []
    worklog_days: list[dict[str, Any]] = []
    status_observations: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    user_assigned_keys: defaultdict[str, set[str]] = defaultdict(set)
    user_reported_keys: defaultdict[str, set[str]] = defaultdict(set)
    severity_counts: Counter[str] = Counter()
    unresolved_without_close_date = 0
    estimated_status_issues = 0
    issues_with_status_data = 0
    valid_created_dates: list[date] = []

    for issue in issues:
        key = issue_key(issue)
        fields = issue_fields(issue)
        created = parse_jira_datetime(fields.get("created"))
        histories = histories_by_key.get(key, [])
        resolved = resolved_at_for_issue(issue, histories)
        if created:
            created_date = created.date()
            created_by_day[created_date] += 1
            valid_created_dates.append(created_date)
        if resolved:
            closed_by_day[resolved.date()] += 1
            valid_created_dates.append(resolved.date())
            if created and resolved >= created:
                lifecycle_days.append({"key": key, "days": (resolved - created).total_seconds() / SECONDS_PER_DAY})
            time_spent = fields.get("timespent")
            if isinstance(time_spent, (int, float)) and time_spent >= 0:
                worklog_days.append({"key": key, "days": float(time_spent) / SECONDS_PER_DAY})
            durations, estimated = status_durations_for_issue(issue, histories, resolved)
            if durations:
                issues_with_status_data += 1
            if estimated:
                estimated_status_issues += 1
            for state, days in durations.items():
                status_observations[state].append({"key": key, "days": days})
        else:
            # A Done category without resolutiondate and without a terminal
            # transition cannot safely be placed on the daily-close time axis.
            if status_category(fields.get("status")) == "done":
                unresolved_without_close_date += 1

        assignee = user_display_name(fields.get("assignee"))
        reporter = user_display_name(fields.get("reporter"))
        if assignee:
            user_assigned_keys[assignee].add(key)
        if reporter:
            user_reported_keys[reporter].add(key)

        priority_value = fields.get(severity_field)
        severity_label = "Not set"
        if isinstance(priority_value, dict):
            severity_label = str(priority_value.get("name") or priority_value.get("value") or priority_value.get("id") or "Not set")
        elif priority_value is not None and str(priority_value).strip():
            severity_label = str(priority_value)
        severity_counts[severity_label] += 1

    user_rows: list[dict[str, Any]] = []
    all_users = set(user_assigned_keys) | set(user_reported_keys)
    for name in all_users:
        assigned = user_assigned_keys[name]
        reported = user_reported_keys[name]
        total_unique = assigned | reported
        user_rows.append({
            "user": name,
            "assigned": len(assigned),
            "reported": len(reported),
            "total_unique": len(total_unique),
        })
    user_rows.sort(key=lambda row: (-row["total_unique"], row["user"].casefold()))

    daily_rows: list[dict[str, Any]] = []
    if valid_created_dates:
        day = min(valid_created_dates)
        last_day = max(valid_created_dates)
        cumulative_created = 0
        cumulative_closed = 0
        while day <= last_day:
            created_count = created_by_day[day]
            closed_count = closed_by_day[day]
            cumulative_created += created_count
            cumulative_closed += closed_count
            daily_rows.append({
                "date": day.isoformat(),
                "created": created_count,
                "closed": closed_count,
                "cumulative_created": cumulative_created,
                "cumulative_closed": cumulative_closed,
            })
            day += timedelta(days=1)

    return {
        "issue_count": len(issues),
        "closed_issue_count": len(lifecycle_days),
        "lifecycle_days": lifecycle_days,
        "status_days": dict(status_observations),
        "daily_counts": daily_rows,
        "users": user_rows[:30],
        "severity_counts": dict(severity_counts),
        "worklog_days": worklog_days,
        "estimated_status_issues": estimated_status_issues,
        "issues_with_status_data": issues_with_status_data,
        "done_without_close_date": unresolved_without_close_date,
        "issues_missing_created": sum(1 for issue in issues if parse_jira_datetime(issue_fields(issue).get("created")) is None),
    }
