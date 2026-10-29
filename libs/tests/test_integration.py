import json
from pathlib import Path

from ..utils import cli


def test_offline_end_to_end_run_creates_report_and_summary(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "base_url": "https://jira.example.invalid",
        "project_key": "DEMO",
        "output_dir": str(tmp_path / "output"),
        "workers": 2,
    }), encoding="utf-8")

    sample_issue = {
        "key": "DEMO-1",
        "fields": {
            "created": "2025-01-01T00:00:00.000+0000",
            "resolutiondate": "2025-01-03T00:00:00.000+0000",
            "resolution": {"name": "Fixed"},
            "status": {"name": "Closed", "statusCategory": {"key": "done"}},
            "priority": {"name": "Major"},
            "assignee": {"displayName": "Alice"},
            "reporter": {"displayName": "Bob"},
            "timespent": 3600,
        },
    }

    class FakeJiraClient:
        def __init__(self, config):
            self.config = config

        def fetch_issues(self):
            return [sample_issue]

        def fetch_issue_history(self, key):
            assert key == "DEMO-1"
            return {"changelog": {"total": 1, "histories": [{
                "created": "2025-01-02T00:00:00.000+0000",
                "items": [{"field": "status", "fromString": "Open", "toString": "Closed"}],
            }]}}

        def fetch_field_names(self):
            return {"priority": "Priority"}

    monkeypatch.setattr(cli, "JiraClient", FakeJiraClient)
    output = tmp_path / "dashboard.html"
    actual_path = cli.run(config_path, str(output))

    assert actual_path == output
    assert output.exists()
    assert "DEMO" in output.read_text(encoding="utf-8")
    summary = json.loads(output.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["issue_count"] == 1
    assert summary["closed_issue_count"] == 1

