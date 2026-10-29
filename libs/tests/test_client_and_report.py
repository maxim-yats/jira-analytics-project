import json
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import libs.utils.client as client_module
from libs.utils.charts import build_html_report
from libs.utils.client import JiraAPIError, JiraClient
from libs.utils.config import Config


def test_config_loads_example_and_builds_jql(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"project_key": "HDFS", "jql_filter": 'created >= "2024-01-01"'}),
        encoding="utf-8",
    )
    config = Config.from_file(path)
    assert config.jql == 'project = "HDFS" AND (created >= "2024-01-01") ORDER BY created ASC'


def test_config_rejects_invalid_project_key(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"project_key": "KAFKA OR 1=1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="project_key"):
        Config.from_file(path)


def test_fetch_issues_paginates_and_respects_max_issues():
    config = Config(project_key="KAFKA", page_size=2, max_issues=3)
    client = JiraClient(config)
    jira_api = Mock()
    jira_api.search_issues.side_effect = [
        {"total": 5, "issues": [{"key": "A-1"}, {"key": "A-2"}]},
        {"total": 5, "issues": [{"key": "A-3"}, {"key": "A-4"}]},
    ]
    client._get_jira = Mock(return_value=jira_api)

    found = client.fetch_issues()

    assert [item["key"] for item in found] == ["A-1", "A-2", "A-3"]
    assert jira_api.search_issues.call_count == 2
    first, second = jira_api.search_issues.call_args_list
    assert first.kwargs["startAt"] == 0
    assert second.kwargs["startAt"] == 2
    assert first.kwargs["maxResults"] == 2
    assert first.kwargs["fields"] == list(client_module.CORE_FIELDS)
    assert first.kwargs["json_result"] is True
    assert first.kwargs["use_post"] is True


def test_search_uses_jira_python_library_post_json_result():
    config = Config(project_key="KAFKA", page_size=25, severity_field="customfield_12345")
    client = JiraClient(config)
    jira_api = Mock()
    jira_api.search_issues.return_value = {"total": 0, "issues": []}
    client._get_jira = Mock(return_value=jira_api)

    assert client.fetch_issues() == []
    jira_api.search_issues.assert_called_once_with(
        config.jql,
        startAt=0,
        maxResults=25,
        fields=[
            "created", "resolutiondate", "resolution", "status", "priority",
            "assignee", "reporter", "timespent", "updated", "customfield_12345",
        ],
        validate_query=True,
        json_result=True,
        use_post=True,
    )


def test_fetch_issues_continues_when_library_returns_short_page():
    config = Config(project_key="KAFKA", page_size=100)
    client = JiraClient(config)
    jira_api = Mock()
    jira_api.search_issues.side_effect = [
        {"total": 3, "issues": [{"key": "A-1"}, {"key": "A-2"}]},
        {"total": 3, "issues": [{"key": "A-3"}]},
    ]
    client._get_jira = Mock(return_value=jira_api)

    found = client.fetch_issues()

    assert [item["key"] for item in found] == ["A-1", "A-2", "A-3"]
    assert jira_api.search_issues.call_count == 2
    assert [item.kwargs["startAt"] for item in jira_api.search_issues.call_args_list] == [0, 2]


def test_fetch_issue_history_returns_raw_issue_json():
    client = JiraClient(Config())
    raw = {
        "key": "KAFKA-123",
        "fields": {"status": {"name": "Closed"}},
        "changelog": {"total": 1, "histories": []},
    }
    jira_api = Mock()
    jira_api.issue.return_value = SimpleNamespace(raw=raw)
    client._get_jira = Mock(return_value=jira_api)

    assert client.fetch_issue_history("KAFKA-123") == raw
    jira_api.issue.assert_called_once_with(
        "KAFKA-123",
        fields="created,status,resolutiondate,updated",
        expand="changelog",
    )


def test_fetch_field_names_uses_jira_library_fields_method():
    client = JiraClient(Config())
    jira_api = Mock()
    jira_api.fields.return_value = [
        {"id": "priority", "name": "Priority"},
        {"id": "customfield_12345", "name": "Severity"},
    ]
    client._get_jira = Mock(return_value=jira_api)

    assert client.fetch_field_names() == {
        "priority": "Priority",
        "customfield_12345": "Severity",
    }
    jira_api.fields.assert_called_once_with()


def test_jira_library_client_is_configured_for_url_timeout_and_retries(monkeypatch):
    created = Mock()
    monkeypatch.setattr(client_module, "JIRA", created)
    config = Config(
        base_url="https://jira.example.test/",
        timeout_seconds=12,
        request_retries=4,
        auth_mode="anonymous",
    )
    client = JiraClient(config)

    returned = client._get_jira()

    assert returned is created.return_value
    created.assert_called_once_with(
        server="https://jira.example.test",
        options={
            "rest_api_version": "2",
            "verify": True,
            "headers": {
                "Accept": "application/json",
                "User-Agent": "jira-project-analytics/1.1.0",
            },
        },
        validate=False,
        get_server_info=False,
        timeout=12,
        max_retries=4,
        logging=False,
    )


def test_jira_library_auth_is_passed_to_library(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(client_module, "JIRA", factory)
    monkeypatch.setenv("JIRA_USERNAME", "student")
    monkeypatch.setenv("JIRA_API_TOKEN", "secret-token")
    client = JiraClient(Config(auth_mode="basic"))

    client._get_jira()

    assert factory.call_args.kwargs["basic_auth"] == ("student", "secret-token")


def test_library_failure_is_wrapped_as_jira_api_error():
    client = JiraClient(Config())
    jira_api = Mock()
    jira_api.search_issues.side_effect = client_module.JiraLibraryError("mock Jira failure")
    client._get_jira = Mock(return_value=jira_api)

    with pytest.raises(JiraAPIError, match="search_issues"):
        client.fetch_issues()


def test_html_report_contains_all_main_sections_and_inline_plotly(tmp_path):
    data = {
        "issue_count": 1,
        "closed_issue_count": 1,
        "lifecycle_days": [{"key": "A-1", "days": 2.0}],
        "status_days": {"Open": [{"key": "A-1", "days": 1.0}]},
        "daily_counts": [{"date": "2025-01-01", "created": 1, "closed": 0, "cumulative_created": 1, "cumulative_closed": 0}],
        "users": [{"user": "Alice", "assigned": 1, "reported": 0, "total_unique": 1}],
        "severity_counts": {"Major": 1},
        "worklog_days": [{"key": "A-1", "days": 1 / 24}],
        "estimated_status_issues": 0,
        "issues_with_status_data": 1,
        "done_without_close_date": 0,
        "issues_missing_created": 0,
    }
    output = tmp_path / "report.html"
    build_html_report(data, output, "DEMO", "Priority")
    text = output.read_text(encoding="utf-8")
    assert output.exists()
    assert "Время жизни закрытой задачи" in text
    assert "Создание и закрытие задач по дням" in text
    assert "Топ-30 пользователей" in text
    assert "Залогированное время" in text
    assert "Plotly.newPlot" in text
