import re
import io
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime, date
from typing import Tuple, List
from models import (
    Task, TaskStatus, RiskLevel, ValidationResult, ValidationWarning,
    Dependency, DependencyType, GateStatus, TaskType,
)
from dateutils import add_workdays
from quality import STANDARD_GATES, recategorize_sonstige, assess_data_basis

COLUMN_ALIASES = {
    "task_id":       ["id", "task_id", "vorgangs-id", "nr", "nummer", "no", "psp_code"],
    "task_name":     ["aufgabe", "task", "task_name", "vorgangsname", "titel", "name",
                      "vorgang_oder_meilenstein", "beschreibung", "vorgang"],
    "phase":         ["phase", "projektphase", "abschnitt", "bereich"],
    "work_package":  ["arbeitspaket", "work_package", "workpackage", "teilprojekt"],
    "task_type":     ["task_type", "vorgangstyp"],
    "department":    ["abteilung", "department"],
    "start_date":    ["start", "start_date", "startdatum", "beginn", "von",
                      "plan start", "plan_start"],
    "end_date":      ["end", "end_date", "enddatum", "ende", "bis", "fälligkeitsdatum",
                      "plan ende", "plan_ende", "plan_end"],
    "actual_start":  ["ist start", "ist_start", "actual_start", "actual start"],
    "actual_end":    ["ist ende", "ist_ende", "actual_end", "actual end"],
    "forecast_start": ["forecast start", "forecast_start", "prognose_start"],
    "forecast_end":  ["forecast ende", "forecast_ende", "forecast_end", "forecast end",
                      "prognose_ende"],
    "baseline_start": ["baseline start", "baseline_start"],
    "baseline_end":  ["baseline ende", "baseline_ende", "baseline_end", "baseline end"],
    "progress_percent": ["fortschritt %", "fortschritt_%", "fortschritt", "progress",
                         "progress_percent", "% complete", "percentcomplete"],
    "owner":         ["owner", "verantwortlich", "verantwortlicher", "zuständig", "responsible"],
    "status":        ["status", "zustand"],
    "milestone":     ["meilenstein", "milestone", "ms", "typ"],
    "milestone_name": ["meilenstein_name", "milestone_name"],
    "risk_level":    ["risiko", "risk", "risk_level", "risikostufe"],
    "risk_description": ["risiko_beschreibung", "risikobeschreibung", "risk_description"],
    "mitigation":    ["maßnahme", "massnahme", "mitigation"],
    "buffer_days":   ["puffer_tage", "puffer", "buffer_days", "buffer"],
    "is_gate":       ["gate", "is_gate"],
    "gate_criteria": ["gate_kriterien", "gate kriterien", "gate_criteria"],
    "gate_status":   ["gate_status"],
    "decision_required": ["offene_entscheidung", "offene entscheidung", "decision_required",
                          "entscheidung_erforderlich"],
    "decision_owner": ["entscheidungsverantwortlicher", "decision_owner"],
    "parent_id":     ["parent_id", "übergeordnete_id", "uebergeordnete_id"],
    "supplier":      ["lieferant", "supplier"],
    "external_dependency": ["externe_abhängigkeit", "externe_abhaengigkeit", "external_dependency"],
    "cost_relevance": ["kostenrelevanz", "cost_relevance"],
    "customer_visible_flag": ["kundensichtbar", "customer_visible", "customer_visible_flag"],
    "comments":      ["kommentar", "comments"],
    "notes":         ["notizen", "notes", "anmerkung",
                      "risiko_hinweis", "beschreibung_ergebnis"],
    "duration_days": ["dauer_arbeitstage", "dauer", "duration", "arbeitstage", "dauer_tage",
                      "dauer_at"],
    "predecessors":  ["vorgaenger_ids", "vorgänger_ids", "vorgaenger", "predecessors",
                      "vorgänger", "pred"],
    "dependency_type": ["abhängigkeitstyp", "abhaengigkeitstyp", "dependency_type"],
    "time_offset":   ["zeitversatz_at", "zeitversatz", "lag", "offset", "lag_days"],
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
    "entfallen":      TaskStatus.cancelled,
    "cancelled":      TaskStatus.cancelled,
    "canceled":       TaskStatus.cancelled,
    "abgebrochen":    TaskStatus.cancelled,
}

DEPENDENCY_TYPE_MAP = {
    "fs": DependencyType.finish_to_start, "finish_to_start": DependencyType.finish_to_start,
    "ss": DependencyType.start_to_start, "start_to_start": DependencyType.start_to_start,
    "ff": DependencyType.finish_to_finish, "finish_to_finish": DependencyType.finish_to_finish,
    "sf": DependencyType.start_to_finish, "start_to_finish": DependencyType.start_to_finish,
}

GATE_STATUS_MAP = {
    "offen": GateStatus.open, "open": GateStatus.open,
    "im_plan": GateStatus.on_track, "im plan": GateStatus.on_track, "on_track": GateStatus.on_track,
    "gefährdet": GateStatus.at_risk, "gefaehrdet": GateStatus.at_risk, "at_risk": GateStatus.at_risk,
    "erreicht": GateStatus.passed, "passed": GateStatus.passed,
    "nicht_erreicht": GateStatus.failed, "nicht erreicht": GateStatus.failed, "failed": GateStatus.failed,
}

# MSPDI <PredecessorLink><Type>: 0=FF, 1=FS, 2=SF, 3=SS
MSPDI_DEPENDENCY_TYPE = {
    "0": DependencyType.finish_to_finish,
    "1": DependencyType.finish_to_start,
    "2": DependencyType.start_to_finish,
    "3": DependencyType.start_to_start,
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


def _parse_list(val, sep: str = ";") -> list[str]:
    """Trennzeichen-getrennte Liste (z.B. Gate-Kriterien) -> list[str]."""
    if pd.isna(val) or not str(val).strip():
        return []
    return [p.strip() for p in str(val).split(sep) if p.strip()]


def _parse_int(val) -> int | None:
    if val is None or (isinstance(val, float) and pd.isna(val)) or str(val).strip() == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _build_predecessor_links(ids: list[str], dep_type: DependencyType, lag: int) -> list[Dependency]:
    """CSV-Vereinfachung: ein Abhängigkeitstyp + Zeitversatz gilt für alle
    Vorgänger einer Zeile."""
    return [Dependency(task_id=tid, type=dep_type, lag_days=lag) for tid in ids]


def _link_lag_to_days(lag_raw: str | None, format_raw: str | None) -> int:
    """MSPDI <LinkLag> (Zehntel-Minuten) -> Tage.

    Format 7/35 = "Elapsed days" (24h-Tag, 1440 min/Tag); andere Formate
    (z.B. 3 = "Tage" im Projektkalender) gehen von einem 8h-Arbeitstag
    (480 min/Tag) aus."""
    try:
        lag = int(lag_raw or "0")
    except ValueError:
        return 0
    if lag == 0:
        return 0
    try:
        fmt = int(format_raw or "7")
    except ValueError:
        fmt = 7
    minutes_per_day = 1440 if fmt in (7, 35) else 480
    return round(lag / 10 / minutes_per_day)


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
                start = add_workdays(latest, 1 + max(offset, 0))
            else:
                start = project_start

        end = add_workdays(start, max(duration - 1, 0))
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

    # ── UID → ID-Mapping (für Auflösung von PredecessorUID) ─────────────────────
    uid_to_id: dict[str, str] = {}
    for task in all_tasks:
        uid = _text(task, "UID") or ""
        tid = _text(task, "ID") or uid
        if uid:
            uid_to_id[uid] = tid

    # ── WBS-Hierarchie für Phasen-Zuordnung ─────────────────────────────────────
    # Phase = nächster Vorfahre, der direktes Kind der Projektwurzel (OutlineLevel=1)
    # und selbst ein Gruppenkopf ist (z.B. "in-house planning and engineering").
    # Gibt es keine solche Zwischenebene, fällt alles auf eine Phase
    # (= Projektname der Wurzel) zurück.
    phase_names: dict[str, str] = {}
    phase_of: dict[str, str] = {}
    # Unterphase = nächste Gliederungsebene unterhalb der Phase (z.B. Arbeitspakete).
    # Vorgänge ohne eigene Unterphasen-Überschrift fallen in den Sammeleintrag
    # "Sonstige Vorgänge" der jeweiligen Phase.
    subphase_names: dict[str, str] = {}
    subphase_of: dict[str, str] = {}
    root_wbs = ""
    stack: list[tuple[int, str]] = []

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
        active       = _text(task, "Active")
        critical     = _text(task, "Critical")

        # Gruppenkopf ohne eigene Termine (Duration=0, kein Start/Finish, kein
        # Meilenstein) — z.B. WBS-Abschnittsüberschriften, deren Summary-Flag
        # im Export fälschlich auf 0 steht. Wie OutlineLevel=1 überspringen.
        is_group_header = (
            summary == "0" and milestone == "0"
            and not start_raw and not finish_raw
            and _is_zero_duration(duration_raw)
        )

        # WBS-Stack pflegen und Phase/Unterphase für diesen Knoten ableiten
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent_wbs = stack[-1][1] if stack else None

        if level == 1:
            root_wbs = wbs
            phase_names[wbs] = name or "Ohne Phase"
        elif parent_wbs is None or stack[-1][0] == 1:
            if is_group_header:
                phase_of[wbs] = wbs
                phase_names[wbs] = name or "Ohne Phase"
            else:
                phase_of[wbs] = root_wbs
        else:
            phase_of[wbs] = phase_of.get(parent_wbs, root_wbs)

        # Unterphase: eine Gliederungsebene unterhalb der Phase
        if level > 1:
            this_phase = phase_of.get(wbs, root_wbs)
            if this_phase == wbs:
                # Knoten IST die Phase -> Sammeleintrag für nicht weiter gruppierte Vorgänge
                subphase_of[wbs] = wbs
                subphase_names[wbs] = "Sonstige Vorgänge"
            elif parent_wbs == this_phase and is_group_header:
                subphase_of[wbs] = wbs
                subphase_names[wbs] = name or "Ohne Unterphase"
            else:
                subphase_of[wbs] = subphase_of.get(parent_wbs, this_phase)

        stack.append((level, wbs))

        # Skip project summary row, summary/phase rows and group headers
        if task_id == "0" or not name or summary == "1" or level == 1 or is_group_header:
            continue

        phase = phase_names.get(phase_of.get(wbs, root_wbs), "Ohne Phase")
        sub_wbs = subphase_of.get(wbs, phase_of.get(wbs, root_wbs))
        sub_phase = subphase_names.get(sub_wbs, phase)

        # Dates: strip time component ("2026-06-09T08:00:00" → "2026-06-09")
        start_date = start_raw[:10] if start_raw else None
        end_date   = finish_raw[:10] if finish_raw else None

        # Status from % complete (Active=0 -> "entfallen", überstimmt Fortschritt)
        try:
            pct = int(float(pct_raw))
        except (ValueError, TypeError):
            pct = 0
        if active == "0":
            status = "entfallen"
        elif pct == 100:
            status = "erledigt"
        elif pct > 0:
            status = "in arbeit"
        else:
            status = "nicht begonnen"

        owner = ", ".join(assignments.get(uid, [])[:2]) or None

        # Manche Exporte setzen <Milestone> nicht, obwohl der Vorgang als
        # Einzeltermin geplant ist (Start == Finish exakt, inkl. Uhrzeit).
        # Solche Vorgänge funktional als Meilenstein behandeln.
        is_instant = bool(start_raw) and start_raw == finish_raw

        # Ist-Termine ("NA" oder fehlend = nicht gesetzt)
        actual_start_raw = _text(task, "ActualStart")
        actual_finish_raw = _text(task, "ActualFinish")
        actual_start = actual_start_raw[:10] if actual_start_raw and actual_start_raw != "NA" else None
        actual_end = actual_finish_raw[:10] if actual_finish_raw and actual_finish_raw != "NA" else None

        # Baseline-Termine (Kind-Element <Baseline><Start>/<Finish>)
        baseline_start = baseline_end = None
        baseline_el = _find(task, "Baseline")
        if baseline_el is not None:
            bs = _text(baseline_el, "Start")
            bf = _text(baseline_el, "Finish")
            baseline_start = bs[:10] if bs and bs != "NA" else None
            baseline_end = bf[:10] if bf and bf != "NA" else None

        # Vorgängerbeziehungen (Typ + Lag)
        predecessor_links_raw: list[dict] = []
        for link in _findall(task, "PredecessorLink"):
            pred_uid = _text(link, "PredecessorUID")
            if not pred_uid:
                continue
            pred_id = uid_to_id.get(pred_uid, pred_uid)
            link_type = MSPDI_DEPENDENCY_TYPE.get(_text(link, "Type") or "1", DependencyType.finish_to_start)
            lag_days = _link_lag_to_days(_text(link, "LinkLag"), _text(link, "LagFormat"))
            predecessor_links_raw.append({"task_id": pred_id, "type": link_type, "lag_days": lag_days})

        rows.append({
            "task_id":    task_id,
            "task_name":  name,
            "phase":      phase,
            "sub_phase":  sub_phase,
            "start_date": start_date,
            "end_date":   end_date,
            "status":     status,
            "milestone":  "1" if (milestone == "1" or is_instant) else "0",
            "owner":      owner,
            "notes":      notes,
            "actual_start": actual_start,
            "actual_end": actual_end,
            "baseline_start": baseline_start,
            "baseline_end": baseline_end,
            "progress_percent": pct,
            "predecessor_links_raw": predecessor_links_raw,
            "critical_path_flag": critical == "1",
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def parse_file(content: bytes, filename: str) -> ValidationResult:
    warnings: List[ValidationWarning] = []
    tasks: List[Task] = []

    fname_lower = filename.lower()
    if fname_lower.endswith(".csv"):
        source_system = "CSV"
    elif fname_lower.endswith(".xml"):
        source_system = "MS Project XML"
    else:
        source_system = "Excel"

    try:
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
        project_start = today if today.weekday() < 5 else add_workdays(today, 1)
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
            continue

        status_raw = (get("status") or "").lower()
        status = STATUS_MAP.get(status_raw, TaskStatus.not_started)

        risk_raw = (get("risk_level") or "").lower()
        risk = RISK_MAP.get(risk_raw)

        milestone = _parse_bool(get("milestone"))

        # Vorgängerbeziehungen: aus XML (Typ+Lag je Verknüpfung) übernehmen,
        # sonst aus CSV-Spalten ableiten (ein Typ+Lag gilt für alle Vorgänger
        # einer Zeile).
        pred_links_raw = row.get("predecessor_links_raw")
        if pred_links_raw:
            predecessor_links = [
                Dependency(task_id=pl["task_id"], type=pl["type"], lag_days=pl["lag_days"])
                for pl in pred_links_raw
            ]
            dependencies = [pl["task_id"] for pl in pred_links_raw]
        else:
            dependencies = _parse_predecessors(get("predecessors"))
            dep_type = DEPENDENCY_TYPE_MAP.get((get("dependency_type") or "").lower(),
                                                DependencyType.finish_to_start)
            lag = _parse_int(get("time_offset")) or 0
            predecessor_links = _build_predecessor_links(dependencies, dep_type, lag)

        phase_val = get("phase") or "Ohne Phase"
        sub_phase_val = row.get("sub_phase")
        if pd.isna(sub_phase_val) or not sub_phase_val:
            sub_phase_val = phase_val

        # Gate-Erkennung aus "Gate"-Spalte: Wert = Gate-Name, bei Treffer gegen
        # STANDARD_GATES Kriterien übernehmen (separate gate_criteria-Spalte
        # überschreibt diese).
        gate_raw = get("is_gate")
        is_gate = False
        gate_name = None
        gate_criteria: list[str] = []
        if gate_raw:
            is_gate = True
            gate_name = gate_raw
            for std_name, gate_def in STANDARD_GATES.items():
                if std_name.lower() == gate_raw.lower():
                    gate_name = std_name
                    gate_criteria = list(gate_def.criteria)
                    break

        gate_criteria_raw = get("gate_criteria")
        if gate_criteria_raw:
            gate_criteria = _parse_list(gate_criteria_raw)

        gate_status = GATE_STATUS_MAP.get((get("gate_status") or "").lower())
        if is_gate and gate_status is None:
            gate_status = GateStatus.open

        task_type_raw = (get("task_type") or "").lower()
        if task_type_raw == "gate" or is_gate:
            task_type = TaskType.gate
        elif task_type_raw in ("milestone", "meilenstein") or milestone:
            task_type = TaskType.milestone
        else:
            task_type = TaskType.task

        cost_relevance_raw = (get("cost_relevance") or "").lower()

        tasks.append(Task(
            task_id=task_id,
            task_name=task_name,
            phase=phase_val,
            sub_phase=sub_phase_val,
            start_date=start,
            end_date=end,
            owner=get("owner") or "Nicht zugewiesen",
            status=status,
            milestone=milestone,
            dependencies=dependencies,
            risk_level=risk,
            notes=get("notes"),
            source_system=source_system,
            source_reference=str(row_num),
            predecessor_links=predecessor_links,
            parent_id=get("parent_id"),
            work_package=get("work_package"),
            task_type=task_type,
            department=get("department"),
            baseline_start=_parse_date(get("baseline_start")),
            baseline_end=_parse_date(get("baseline_end")),
            actual_start=_parse_date(get("actual_start")),
            actual_end=_parse_date(get("actual_end")),
            forecast_start=_parse_date(get("forecast_start")),
            forecast_end=_parse_date(get("forecast_end")),
            progress_percent=_parse_int(get("progress_percent")) or 0,
            critical_path_flag=bool(row.get("critical_path_flag")),
            buffer_days=_parse_int(get("buffer_days")),
            risk_description=get("risk_description"),
            mitigation=get("mitigation"),
            milestone_name=get("milestone_name"),
            is_gate=is_gate,
            gate_name=gate_name,
            gate_criteria=gate_criteria,
            gate_status=gate_status,
            decision_required=_parse_bool(get("decision_required")),
            decision_owner=get("decision_owner"),
            external_dependency=_parse_bool(get("external_dependency")),
            supplier=get("supplier"),
            cost_relevance=RISK_MAP.get(cost_relevance_raw),
            customer_visible_flag=_parse_bool(get("customer_visible_flag")),
            comments=get("comments"),
        ))

    tasks, recategorize_warnings = recategorize_sonstige(tasks)
    data_basis_warnings = assess_data_basis(tasks)

    return ValidationResult(
        valid_tasks=tasks,
        warnings=warnings,
        total_rows=total_rows,
        valid_count=len(tasks),
        error_count=error_count,
        data_basis_warnings=data_basis_warnings,
        quality_warnings=recategorize_warnings,
    )
