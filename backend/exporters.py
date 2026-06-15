"""
Datenexport — JSON (maschinenlesbar), Excel/CSV (25-Spalten-Tabelle nach
Programmierkonzept Abschnitt 7.3/7.4).
"""

import json
from io import BytesIO

import pandas as pd

from models import CondensedPlan, Task

EXPORT_COLUMNS = [
    "ID", "Phase", "Arbeitspaket", "Vorgang", "Typ", "Verantwortlicher",
    "Abteilung", "Plan Start", "Plan Ende", "Ist Start", "Ist Ende",
    "Forecast Start", "Forecast Ende", "Fortschritt %", "Status",
    "Vorgänger", "Nachfolger", "Puffer Tage", "Kritischer Pfad", "Risiko",
    "Maßnahme", "Gate", "Meilenstein", "Offene Entscheidung", "Kommentar",
]


def _format_predecessors(task: Task) -> str:
    parts = []
    for link in task.predecessor_links:
        if link.type.value == "FS" and link.lag_days == 0:
            parts.append(link.task_id)
        else:
            sign = "+" if link.lag_days >= 0 else ""
            parts.append(f"{link.task_id}({link.type.value}{sign}{link.lag_days})")
    return ", ".join(parts)


def _format_milestone(task: Task) -> str:
    if not task.milestone:
        return ""
    name = task.milestone_name or task.task_name
    return f"M{task.milestone_number}: {name}" if task.milestone_number else name


def _format_gate(task: Task) -> str:
    if not task.is_gate:
        return ""
    name = task.gate_name or task.task_name
    status = f" ({task.gate_status.value})" if task.gate_status else ""
    return f"{name}{status}"


def _format_decision(task: Task) -> str:
    if not task.decision_required:
        return ""
    return f"Ja – {task.decision_owner}" if task.decision_owner else "Ja"


def _task_row(task: Task) -> dict:
    return {
        "ID": task.task_id,
        "Phase": task.phase,
        "Arbeitspaket": task.work_package or "",
        "Vorgang": task.task_name,
        "Typ": task.task_type.value,
        "Verantwortlicher": task.owner,
        "Abteilung": task.department or "",
        "Plan Start": task.start_date,
        "Plan Ende": task.end_date,
        "Ist Start": task.actual_start,
        "Ist Ende": task.actual_end,
        "Forecast Start": task.forecast_start,
        "Forecast Ende": task.forecast_end,
        "Fortschritt %": task.progress_percent,
        "Status": task.status.value,
        "Vorgänger": _format_predecessors(task),
        "Nachfolger": ", ".join(task.successors),
        "Puffer Tage": task.buffer_days,
        "Kritischer Pfad": "Ja" if task.critical_path_flag else "Nein",
        "Risiko": task.risk_level.value if task.risk_level else "",
        "Maßnahme": task.mitigation or "",
        "Gate": _format_gate(task),
        "Meilenstein": _format_milestone(task),
        "Offene Entscheidung": _format_decision(task),
        "Kommentar": task.comments or "",
    }


def _build_dataframe(tasks: list[Task]) -> pd.DataFrame:
    rows = [_task_row(t) for t in tasks]
    return pd.DataFrame(rows, columns=EXPORT_COLUMNS)


def export_excel(tasks: list[Task]) -> bytes:
    df = _build_dataframe(tasks)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Vorgänge")
    return buffer.getvalue()


def export_csv(tasks: list[Task]) -> bytes:
    df = _build_dataframe(tasks)
    return df.to_csv(index=False, sep=";").encode("utf-8-sig")


def export_json(plan: CondensedPlan, tasks: list[Task]) -> bytes:
    payload = {
        "plan": plan.model_dump(mode="json"),
        "tasks": [t.model_dump(mode="json") for t in tasks],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
