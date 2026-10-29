"""Interactive, self-contained HTML chart report using Plotly."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import plotly.io as pio


_LAYOUT = {
    "template": "plotly_white",
    "font": {"family": "Arial, sans-serif", "size": 12},
    "margin": {"l": 70, "r": 30, "t": 65, "b": 65},
    "hovermode": "closest",
    "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
}


def _histogram(values: list[float], title: str, x_title: str, name: str = "Tasks") -> go.Figure:
    fig = go.Figure()
    if values:
        fig.add_trace(go.Histogram(x=values, name=name, hovertemplate=f"{x_title}: %{{x}}<br>Tasks: %{{y}}<extra></extra>"))
    else:
        fig.add_annotation(text="Нет данных для построения", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    fig.update_layout(title=title, xaxis_title=x_title, yaxis_title="Количество задач", bargap=0.08, **_LAYOUT)
    return fig


def _section(title: str, description: str, figure: go.Figure, include_js: bool) -> tuple[str, bool]:
    plot_html = pio.to_html(
        figure,
        full_html=False,
        include_plotlyjs="inline" if include_js else False,
        config={"responsive": True, "displaylogo": False},
    )
    return (
        f'<section class="chart-section"><h2>{escape(title)}</h2><p>{escape(description)}</p>{plot_html}</section>',
        False,
    )


def build_html_report(data: dict[str, Any], output_file: str | Path, project_key: str, severity_field_label: str = "Priority") -> Path:
    """Write one fully self-contained HTML report containing all required charts."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    include_js = True

    lifecycles = [float(row["days"]) for row in data["lifecycle_days"]]
    html, _ = _section(
        "1. Время жизни закрытой задачи",
        "Распределение длительности от создания до даты resolution/закрытия; учитываются только задачи с установленной датой закрытия.",
        _histogram(lifecycles, "Время от создания до закрытия", "Длительность, дней"), include_js)
    sections.append(html); include_js = False

    state_names = sorted(data["status_days"], key=str.casefold)
    if state_names:
        for state in state_names:
            values = [float(row["days"]) for row in data["status_days"][state]]
            html, _ = _section(
                f"2. Время в состоянии: {state}",
                "Для каждой закрытой задачи суммируется время всех посещений этого состояния по истории переходов.",
                _histogram(values, f"Распределение времени в состоянии «{state}»", "Суммарное время в состоянии, дней"), include_js)
            sections.append(html)
    else:
        empty = go.Figure(); empty.add_annotation(text="История состояний отсутствует", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        empty.update_layout(title="2. Время по состояниям", xaxis_title="Время, дней", yaxis_title="Количество задач", **_LAYOUT)
        html, _ = _section("2. Время по состояниям", "Отдельная диаграмма для каждого состояния, обнаруженного в истории закрытых задач.", empty, include_js)
        sections.append(html)

    daily = data["daily_counts"]
    fig = go.Figure()
    for key, label, mode in [
        ("created", "Создано за день", "lines+markers"),
        ("closed", "Закрыто за день", "lines+markers"),
        ("cumulative_created", "Накопительно создано", "lines"),
        ("cumulative_closed", "Накопительно закрыто", "lines"),
    ]:
        fig.add_trace(go.Scatter(x=[row["date"] for row in daily], y=[row[key] for row in daily], mode=mode, name=label))
    if not daily:
        fig.add_annotation(text="Нет дат создания/закрытия", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    fig.update_layout(title="3. Создание и закрытие задач по календарным дням", xaxis_title="Календарный день", yaxis_title="Количество задач", **_LAYOUT)
    html, _ = _section("3. Создание и закрытие задач по дням", "Показаны дневные значения и накопительные итоги по всем загруженным задачам проекта.", fig, include_js)
    sections.append(html)

    users = list(reversed(data["users"]))
    fig = go.Figure()
    for key, label in [("assigned", "Исполнитель"), ("reported", "Репортёр"), ("total_unique", "Уникальные задачи пользователя")]:
        fig.add_trace(go.Bar(y=[row["user"] for row in users], x=[row[key] for row in users], orientation="h", name=label))
    if not users:
        fig.add_annotation(text="Нет данных о пользователях", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    fig.update_layout(title="4. Топ-30 пользователей по числу связанных задач", xaxis_title="Количество задач", yaxis_title="Пользователь", barmode="group", height=max(520, 25 * len(users) + 180), **_LAYOUT)
    html, _ = _section("4. Топ-30 пользователей", "Пользователи ранжированы по числу уникальных задач, где они исполнитель или репортёр; роли показаны отдельно.", fig, include_js)
    sections.append(html)

    worklogs = [float(row["days"]) for row in data["worklog_days"]]
    html, _ = _section(
        "5. Залогированное время выполнения закрытых задач",
        "Используется Jira-поле timespent — суммарное залогированное время задачи; задачи без зарегистрированного времени не образуют наблюдение.",
        _histogram(worklogs, "Распределение залогированного времени", "Залогированное время, дней"), include_js)
    sections.append(html)

    severity = data["severity_counts"]
    labels = sorted(severity, key=lambda value: (-severity[value], value.casefold()))
    fig = go.Figure(go.Bar(x=labels, y=[severity[label] for label in labels], text=[severity[label] for label in labels], textposition="auto", name="Задачи"))
    if not labels:
        fig.add_annotation(text="Нет данных о степени серьёзности", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    fig.update_layout(title=f"6. Распределение задач по полю «{severity_field_label}»", xaxis_title="Степень серьёзности / значение поля", yaxis_title="Количество задач", **_LAYOUT)
    html, _ = _section("6. Задачи по степени серьёзности", f"Подсчёт значений Jira-поля {severity_field_label}; поле настраивается в config.json.", fig, include_js)
    sections.append(html)

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    notices = []
    if data["estimated_status_issues"]:
        notices.append(f"Для {data['estimated_status_issues']} закрытых задач история переходов не была доступна; длительность отнесена к текущему состоянию как оценка.")
    if data["done_without_close_date"]:
        notices.append(f"У {data['done_without_close_date']} задач текущая категория Done, но дату закрытия вывести не удалось; они не включены в временные графики закрытия.")
    if data["issues_missing_created"]:
        notices.append(f"У {data['issues_missing_created']} задач отсутствует корректная дата создания; они исключены из дневного графика создания.")
    if notices:
        notice_html = '<div class="notices"><h2>Ограничения данных</h2><ul>' + ''.join(f'<li>{escape(note)}</li>' for note in notices) + '</ul></div>'
    else:
        notice_html = '<div class="notices"><h2>Качество данных</h2><p>Предупреждений по датам закрытия и доступности истории состояний нет.</p></div>'

    page = f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>JIRA Analytics — {escape(project_key)}</title>
<style>
:root{{--bg:#f4f6f9;--paper:#fff;--ink:#172b4d;--sub:#44546f;--line:#dfe1e6;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Arial,sans-serif}}
header{{background:#172b4d;color:#fff;padding:28px max(20px,calc((100% - 1200px)/2))}}
header h1{{margin:0 0 6px;font-size:27px}}header p{{margin:0;color:#dfe1e6}}
main{{max-width:1240px;margin:24px auto;padding:0 18px 50px}}.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:22px}}
.metric,.chart-section,.notices{{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:18px;box-shadow:0 1px 3px rgba(9,30,66,.06)}}
.metric strong{{display:block;font-size:28px;margin-top:6px}}.metric span{{color:var(--sub)}}.chart-section{{margin:16px 0}}h2{{margin:0 0 7px;font-size:20px}}.chart-section p{{color:var(--sub);margin:0 0 8px}}.notices{{margin:16px 0}}footer{{color:var(--sub);font-size:12px;margin-top:28px}}
@media(max-width:650px){{header h1{{font-size:22px}}.chart-section{{padding:10px}}}}
</style></head><body>
<header><h1>Аналитика задач JIRA: {escape(project_key)}</h1><p>Создано: {generated} · Источник: {escape('project = ' + project_key)}</p></header>
<main><div class="summary">
<div class="metric"><span>Загружено задач</span><strong>{data['issue_count']:,}</strong></div>
<div class="metric"><span>Задач с датой закрытия</span><strong>{data['closed_issue_count']:,}</strong></div>
<div class="metric"><span>Статусов в отчёте</span><strong>{len(state_names)}</strong></div>
<div class="metric"><span>Пользователей в топ-30</span><strong>{len(data['users'])}</strong></div>
</div>{notice_html}{''.join(sections)}<footer>Отчёт сформирован jira-project-analytics. Гистограммы построены по всем доступным задачам проекта с учётом указанных на графиках ограничений.</footer></main></body></html>'''
    output_path.write_text(page, encoding="utf-8")
    return output_path
