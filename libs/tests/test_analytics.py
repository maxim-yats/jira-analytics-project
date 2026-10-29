from datetime import datetime, timezone

from libs.utils.analytics import (
    build_analytics,
    parse_jira_datetime,
    resolved_at_for_issue,
    status_durations_for_issue,
)


def issue(key="DEMO-1", created="2025-01-01T00:00:00.000+0000", resolved="2025-01-04T00:00:00.000+0000", status="Closed", **extra):
    fields = {
        "created": created,
        "resolutiondate": resolved,
        "resolution": {"name": "Fixed"} if resolved else None,
        "status": {"name": status, "statusCategory": {"key": "done" if status.lower() == "closed" else "indeterminate"}},
        "priority": {"name": "Major"},
        "assignee": {"displayName": "Alice"},
        "reporter": {"displayName": "Bob"},
        "timespent": 3600,
    }
    fields.update(extra)
    return {"key": key, "fields": fields}


def history(at, old, new):
    return {"created": at, "items": [{"field": "status", "fromString": old, "toString": new}]}


def test_parse_jira_datetime_handles_utc_offset_and_z():
    parsed = parse_jira_datetime("2025-01-01T03:00:00+03:00")
    assert parsed == datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert parse_jira_datetime("2025-01-01T00:00:00Z").hour == 0
    assert parse_jira_datetime("not-a-date") is None


def test_resolved_at_uses_resolutiondate_first():
    item = issue(resolved="2025-01-02T00:00:00+0000")
    resolved = resolved_at_for_issue(item, [history("2025-01-03T00:00:00Z", "Open", "Closed")])
    assert resolved == datetime(2025, 1, 2, tzinfo=timezone.utc)


def test_resolved_at_can_infer_from_terminal_transition():
    item = issue(resolved=None, status="Closed", resolution={"name": "Fixed"})
    resolved = resolved_at_for_issue(item, [history("2025-01-03T00:00:00Z", "In Progress", "Closed")])
    assert resolved == datetime(2025, 1, 3, tzinfo=timezone.utc)


def test_status_durations_sum_repeated_visits_and_cover_full_lifecycle():
    item = issue(created="2025-01-01T00:00:00Z", resolved="2025-01-05T00:00:00Z")
    histories = [
        history("2025-01-02T00:00:00Z", "Open", "In Progress"),
        history("2025-01-03T00:00:00Z", "In Progress", "Open"),
        history("2025-01-04T00:00:00Z", "Open", "Closed"),
    ]
    durations, estimated = status_durations_for_issue(item, histories)
    assert durations == {"Open": 2.0, "In Progress": 1.0, "Closed": 1.0}
    assert estimated is False
    assert abs(sum(durations.values()) - 4.0) < 1e-9


def test_missing_history_uses_marked_estimate():
    item = issue(created="2025-01-01T00:00:00Z", resolved="2025-01-03T00:00:00Z", status="Closed")
    durations, estimated = status_durations_for_issue(item, [])
    assert durations == {"Closed": 2.0}
    assert estimated is True


def test_daily_counts_include_zeros_and_cumulative_totals():
    items = [
        issue("DEMO-1", "2025-01-01T10:00:00Z", "2025-01-03T10:00:00Z"),
        issue("DEMO-2", "2025-01-02T10:00:00Z", None, status="Open", resolution=None, assignee=None),
    ]
    data = build_analytics(items, {"DEMO-1": [], "DEMO-2": []})
    by_day = {row["date"]: row for row in data["daily_counts"]}
    assert by_day["2025-01-01"]["created"] == 1
    assert by_day["2025-01-02"]["created"] == 1
    assert by_day["2025-01-02"]["cumulative_created"] == 2
    assert by_day["2025-01-03"]["closed"] == 1
    assert by_day["2025-01-03"]["cumulative_closed"] == 1


def test_users_count_unique_tasks_and_role_counts():
    item1 = issue("DEMO-1")
    item2 = issue("DEMO-2", created="2025-01-02T00:00:00Z", resolved=None, status="Open", resolution=None,
                  assignee={"displayName": "Alice"}, reporter={"displayName": "Alice"})
    data = build_analytics([item1, item2], {"DEMO-1": [], "DEMO-2": []})
    alice = next(row for row in data["users"] if row["user"] == "Alice")
    assert alice["assigned"] == 2
    assert alice["reported"] == 1
    assert alice["total_unique"] == 2


def test_severity_distribution_and_timespent_use_closed_issues_only():
    closed = issue("DEMO-1", timespent=7200, priority={"name": "Critical"})
    open_issue = issue("DEMO-2", resolved=None, status="Open", resolution=None, timespent=9000,
                       priority={"name": "Minor"})
    data = build_analytics([closed, open_issue], {"DEMO-1": [], "DEMO-2": []})
    assert data["severity_counts"] == {"Critical": 1, "Minor": 1}
    assert data["worklog_days"] == [{"key": "DEMO-1", "days": 7200 / 86400}]
    assert data["closed_issue_count"] == 1


def test_reopened_issue_is_not_mistaken_for_closed_without_resolution_date():
    item = issue(resolved=None, status="Reopened", resolution=None)
    histories = [
        history("2025-01-02T00:00:00Z", "Open", "Closed"),
        history("2025-01-03T00:00:00Z", "Closed", "Reopened"),
    ]
    assert resolved_at_for_issue(item, histories) is None
