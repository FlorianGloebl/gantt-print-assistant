import re
import io
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Tuple, List
from models import Task, TaskStatus, RiskLevel, ValidationResult, ValidationWarning

COLUMN_ALIASES = {
    "task_id":       ["id", "task_id", "vorgangs-id", "nr", "nummer", "no", "psp_code"],
    "task_name":     ["aufgabe", "task", "task_name", "vorgangsname", "titel", "name",
                      "vorgang_oder_meilenstein", "beschreibung"],
    "phase":         ["phase", "projektphase", "abschnitt", "bereich"],
    "start_date":    ["start", "start_date", "startdatum", "beginn", "von"],
    "end_date":      ["end", "end_date", "enddatum", "ende", "bis", "fälligkeitsdatum"],
    "owner":         ["owner", "verantwortlich", "verantwortlicher", "zuständig", "responsible"],
    "status":        ["status", "zustand"],
    "milestone":     ["meilenstein", "milestone", "ms", "typ"],
    "risk_level":    ["risiko", "risk", "risk_level", "risikostufe"],
    "notes":         ["notizen", "notes", "kommentar", "anmerkung",
                      "risiko_hinweis", "beschreibung_ergebnis"],
    "duration_days": ["dauer_arbeitstage", "dauer", "duration", "arbeitstage", "dauer_tage",
                      "dauer_at"],
    "predecessors":  ["vorgaenger_ids", "vorgänger_ids", "vorgaenger", "predecessors",
                      "vorgänger", "pred"],
    "time_offset":   ["zeitversatz_at", "zeitversatz", "lag", "offset"],
}

STATUS_MAP = {
    "nicht begonnen": TaskStatus.not_started,
    "not started":    TaskStatus.not_started,
    "offen":          TaskStatus.not_started,
    "open":           TaskStatus.not_started,
    "in arbeit":      TaskStatus.in_progress,
    "in progress":    TaskStatus.in_progress,
    "laufend":        TaskStatus.in_progress,
    "active":         TaskStatus.in_progress,
    "blockiert":      TaskStatus.blocked,
    "blocked":        TaskStatus.blocked,
    "on hold":        TaskStatus.blocked,
    "erledigt":       TaskStatus.done,
    "done":           TaskStatus.done,
    "abgeschlossen":  TaskStatus.done,
    "complete":       TaskStatus.done,
    "completed":      TaskStatus.done,
    "fertig":         TaskStatus.done,
}

RISK_MAP = {
    "niedrig":  RiskLevel.low,
    "low":      RiskLevel.low,
    "gering":   RiskLevel.low,
    "mittel":   RiskLevel.medium,
    "medium":   RiskLevel.medium,
    "hoch":     RiskLevel.high,
    "high":     RiskLevel.high,
    "kritisch": RiskLevel.critical,
    "critical": RiskLevel.critical,
}


def _normalize_columns(df: pd.DataFrame) -> dict:
    col_map = {}
    lower_cols = {c.lower().strip(): c for c in df.columns}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_cols:
                col_map[canonical] = lower_cols[alias]
                break
    return col_map


def _parse_date(val) -> date | None:
    if pd.isna(val):
        return None
    if isinstance(val, (datetime, pd.Timestamp)):
        return val.date()
    if isinstance(val, date):
        return val
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_bool(val) -> bool:
    if pd.isna(val):
        return False
    s = str(val).lower().strip()
    return s in ("ja", "yes", "true", "1", "x", "meilenstein", "milestone", "ms", "m")


def _is_zero_duration(duration: str | None) -> bool:
    """ISO-8601 duration (z.B. 'PT0H0M0S') ohne Stunden/Minuten/Sekunden?"""
    nums = re.findall(r"\d+", duration or "")
    return all(n == "0" for n in nums) if nums else True


def _parse_predecessors(val) -> list[str]:
    if pd.isna(val) or not str(val).strip() or str(val).strip() in ("-", "—", ""):
        return []
    parts = re.split(r"[;,\s|]+", str(val).strip())
    return [p.strip() for p in parts if p.strip() and p.strip() not in ("-", "—")]


def _add_workdays(d: date, n: int) -> date:
    """Add n working days (Mon–Fri) to date d."""
    if n <= 0:
        return d
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _compute_cpm_dates(
    raw_tasks: list[dict],
    project_start: date,
) -> dict[str, tuple[date, date]]:
    """
    Forward-pass CPM: compute (start, end) for each task from its predecessors.
    raw_tasks items: {id, duration (int workdays), predecessors (list[str]), time_offset (int)}
    """
    task_map = {t["id"]: t for t in raw_tasks}
    computed: dict[str, tuple[date, date]] = {}
    in_progress: set[str] = set()  # cycle guard

    def _compute(tid: str) -> tuple[date, date] | None:
        if tid in computed:
            return computed[tid]
        if tid in in_progress:
            return None  # break cycle
        in_progress.add(tid)

        t = task_map.get(tid)
        if t is None:
            in_progress.discard(tid)
            return None

        duration = max(int(t.get("duration") or 1), 0)
        preds = t.get("predecessors", [])
        offset = int(t.get("time_offset") or 0)

        if not preds:
            start = project_start
        else:
            pred_ends = []
            for pid in preds:
                result = _compute(pid)
                if result:
                    pred_ends.append(result[1])
            if pred_ends:
                latest = max(pred_ends)
                # next working day after latest predecessor, plus any lag
                start = _add_workdays(latest, 1 + max(offset, 0))
            else:
                start = project_start

        end = _add_workdays(start, max(duration - 1, 0))
        in_progress.discard(tid)
        computed[tid] = (start, end)
        return computed[tid]

    for t in raw_tasks:
        _compute(t["id"])

    return computed


def _parse_xml_to_df(content: bytes) -> pd.DataFrame:
    """
    Parse Microsoft Project XML (.xml) into a normalised DataFrame.
    Supports MSPDI namespace and namespace-less exports.
    OutlineLevel=1 tasks are treated as phase headers (skipped as individual tasks).
    """
    root = ET.fromstring(content)

    # Detect namespace (MS Project uses http://schemas.microsoft.com/project)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    def _find(el, tag):
        return el.find(f"{ns}{tag}")

    def _findall(el, tag):
        return el.findall(f"{ns}{tag}")

    def _text(el, tag) -> str | None:
        child = _find(el, tag)
        return child.text.strip() if child is not None and child.text else None

    # ── Resources: UID → name ──────────────────────────────────────────────────
    resources: dict[str, str] = {}
    res_root = _find(root, "Resources")
    if res_root is not None:
        for res in _findall(res_root, "Resource"):
            uid = _text(res, "UID")
            name = _text(res, "Name")
            if uid and name and uid != "0":
                resources[uid] = name

    # ── Assignments: task_uid → [resource names] ───────────────────────────────
    assignments: dict[str, list[str]] = {}
    asgn_root = _find(root, "Assignments")
    if asgn_root is not None:
        for asgn in _findall(asgn_root, "Assignment"):
            t_uid = _text(asgn, "TaskUID")
            r_uid = _text(asgn, "ResourceUID")
            if t_uid and r_uid and r_uid in resources:
                assignments.setdefault(t_uid, []).append(resources[r_uid])

    # ── Tasks: collect phase names from OutlineLevel=1 ─────────────────────────
    tasks_root = _find(root, "Tasks")
    if tasks_root is None:
        return pd.DataFrame()

    all_tasks = _findall(tasks_root, "Task")

    # Map top-level WBS prefix → phase name
    phase_names: dict[str, str] = {}
    for task in all_tasks:
        level = int(_text(task, "OutlineLevel") or "1")
        wbs   = _text(task, "WBS") or ""
        name  = _text(task, "Name") or ""
        if level == 1 and name:
            phase_names[wbs] = name

    # ── Build DataFrame rows ───────────────────────────────────────────────────
    rows = []
    for task in all_tasks:
        uid          = _text(task, "UID") or ""
        task_id      = _text(task, "ID") or uid
        name         = _text(task, "Name")
        level        = int(_text(task, "OutlineLevel") or "1")
        wbs          = _text(task, "WBS") or ""
        start_raw    = _text(task, "Start")
        finish_raw   = _text(task, "Finish")
        pct_raw      = _text(task, "PercentComplete") or "0"
        milestone    = _text(task, "Milestone") or "0"
        summary      = _text(task, "Summary") or "0"
        duration_raw = _text(task, "Duration")
        notes        = _text(task, "Notes")

        # Gruppenkopf ohne eigene Termine (Duration=0, kein Start/Finish, kein
        # Meilenstein) — z.B. WBS-Abschnittsüberschriften, deren Summary-Flag
        # im Export fälschlich auf 0 steht. Wie OutlineLevel=1 überspringen.
        is_group_header = (
            summary == "0" and milestone == "0"
            and not start_raw and not finish_raw
            and _is_zero_duration(duration_raw)
        )

        # Skip project summary row, summary/phase rows and group headers
        if task_id == "0" or not name or summary == "1" or level == 1 or is_group_header:
            continue

        # Phase: parent WBS level (e.g. "1.2.3" → look up "1" in phase_names)
        top_wbs = wbs.split(".")[0] if wbs else ""
        phase   = phase_names.get(top_wbs) or (f"Phase {top_wbs}" if top_wbs else "Ohne Phase")

        # Dates: strip time component ("2026-06-09T08:00:00" → "2026-06-09")
        start_date = start_raw[:10] if start_raw else None
        end_date   = finish_raw[:10] if finish_raw else None

        # Status from % complete
        try:
            pct = int(float(pct_raw))
        except (ValueError, TypeError):
            pct = 0
        if pct == 100:
            status = "erledigt"
        elif pct > 0:
            status = "in arbeit"
        else:
            status = "nicht begonnen"

        owner = ", ".join(assignments.get(uid, [])[:2]) or None

        rows.append({
            "task_id":    task_id,
            "task_name":  name,
            "phase":      phase,
            "start_date": start_date,
            "end_date":   end_date,
            "status":     status,
            "milestone":  "1" if milestone == "1" else "0",
            "owner":      owner,
            "notes":      notes,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def parse_file(content: bytes, filename: str) -> ValidationResult:
    warnings: List[ValidationWarning] = []
    tasks: List[Task] = []

    try:
        fname_lower = filename.lower()
        if fname_lower.endswith(".csv"):
            for sep in [";", ",", "\t"]:
                try:
                    df = pd.read_csv(io.BytesIO(content), sep=sep, dtype=str)
                    if len(df.columns) > 1:
                        break
                except Exception:
                    continue
        elif fname_lower.endswith(".xml"):
            df = _parse_xml_to_df(content)
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
    except Exception as e:
        return ValidationResult(
            valid_tasks=[],
            warnings=[ValidationWarning(task_id=None, field="file",
                                        message=f"Datei konnte nicht gelesen werden: {e}",
                                        severity="error")],
            total_rows=0, valid_count=0, error_count=1,
        )

    df = df.dropna(how="all")
    total_rows = len(df)
    col_map = _normalize_columns(df)

    # Only task_name is truly required; dates can be derived from CPM
    if "task_name" not in col_map:
        warnings.append(ValidationWarning(
            task_id=None, field="task_name",
            message=f"Pflichtfeld 'task_name' nicht gefunden. Gefundene Spalten: {list(df.columns)}",
            severity="error",
        ))
        return ValidationResult(valid_tasks=[], warnings=warnings,
                                total_rows=total_rows, valid_count=0, error_count=total_rows)

    has_dates = "start_date" in col_map and "end_date" in col_map
    has_duration = "duration_days" in col_map

    # --- First pass: collect raw task data for CPM ---
    raw_for_cpm: list[dict] = []
    if not has_dates and has_duration:
        for idx, row in df.iterrows():
            row_num = idx + 2

            def _get(field, default=None):
                col = col_map.get(field)
                if col is None:
                    return default
                v = row.get(col)
                return None if pd.isna(v) else str(v).strip()

            tid = _get("task_id") or f"T-{row_num}"
            try:
                duration = int(float(_get("duration_days") or 1))
            except (ValueError, TypeError):
                duration = 1
            try:
                offset = int(float(_get("time_offset") or 0))
            except (ValueError, TypeError):
                offset = 0
            preds = _parse_predecessors(_get("predecessors"))
            raw_for_cpm.append({"id": tid, "duration": duration,
                                 "predecessors": preds, "time_offset": offset})

        # Use next Monday (or today if today is a workday) as project start
        today = date.today()
        project_start = today if today.weekday() < 5 else _add_workdays(today, 1)
        cpm_dates = _compute_cpm_dates(raw_for_cpm, project_start)
    else:
        cpm_dates = {}

    # --- Second pass: build Task objects ---
    seen_ids: set[str] = set()
    error_count = 0

    for idx, row in df.iterrows():
        row_num = idx + 2

        def get(field, default=None):
            col = col_map.get(field)
            if col is None:
                return default
            v = row.get(col)
            return None if pd.isna(v) else str(v).strip()

        task_id = get("task_id") or f"T-{row_num}"
        task_name = get("task_name")
        if not task_name:
            warnings.append(ValidationWarning(task_id=task_id, field="task_name",
                                              message="Aufgabenname fehlt", severity="error"))
            error_count += 1
            continue

        # Dates: direct columns take priority; CPM is fallback
        if has_dates:
            start = _parse_date(get("start_date"))
            end = _parse_date(get("end_date"))
        elif task_id in cpm_dates:
            start, end = cpm_dates[task_id]
        else:
            start = end = None

        if not start:
            warnings.append(ValidationWarning(task_id=task_id, field="start_date",
                                              message="Startdatum fehlt oder konnte nicht berechnet werden",
                                              severity="warning"))
        if not end:
            warnings.append(ValidationWarning(task_id=task_id, field="end_date",
                                              message="Enddatum fehlt oder konnte nicht berechnet werden",
                                              severity="warning"))
        if start and end and end < start:
            warnings.append(ValidationWarning(task_id=task_id, field="end_date",
                                              message="Enddatum liegt vor Startdatum",
                                              severity="warning"))

        if task_id in seen_ids:
            warnings.append(ValidationWarning(task_id=task_id, field="task_id",
                                              message="Doppelte ID gefunden", severity="warning"))
        seen_ids.add(task_id)

        if not start or not end:
            error_count += 1
            if not start:
                start = date.today()
            if not end:
                end = date.today()

        status_raw = (get("status") or "").lower()
        status = STATUS_MAP.get(status_raw, TaskStatus.not_started)

        risk_raw = (get("risk_level") or "").lower()
        risk = RISK_MAP.get(risk_raw)

        milestone = _parse_bool(get("milestone"))

        preds_raw = get("predecessors") or ""
        dependencies = _parse_predecessors(preds_raw)

        tasks.append(Task(
            task_id=task_id,
            task_name=task_name,
            phase=get("phase") or "Ohne Phase",
            start_date=start,
            end_date=end,
            owner=get("owner") or "Nicht zugewiesen",
            status=status,
            milestone=milestone,
            dependencies=dependencies,
            risk_level=risk,
            notes=get("notes"),
            source_system="Excel",
            source_reference=str(row_num),
        ))

    return ValidationResult(
        valid_tasks=tasks,
        warnings=warnings,
        total_rows=total_rows,
        valid_count=len(tasks),
        error_count=error_count,
    )
