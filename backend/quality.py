"""
Qualitäts-Engine: Ampellogik, Datenbasis-Checks, Qualitäts-Score, Meilenstein-/Gate-
Aufbereitung sowie automatische PM-Bewertungstexte.

Schlagwort-basierte Umkategorisierung von "Sonstige Vorgänge" (für den Import) lebt
ebenfalls hier, da sie dieselben Konstanten (Standard-Gates, Schlagwort-Listen) nutzt.
"""

import re
from datetime import date
from typing import Optional

from models import (
    Task, TaskStatus, RiskLevel, TrafficLight, GateStatus, TaskType,
    GateDefinition, QualityWarning, DataBasisWarning, QualityScore,
    MilestoneLegendEntry, CriticalPathInfo, PMAssessment, ValidationWarning,
)
from cpm import MIN_PREDECESSOR_RATIO_FOR_CPM


SONSTIGE_LABEL = "Sonstige Vorgänge"
LEGEND_MAX_ENTRIES = 8


# ── Standard-Gates (Doc 4.5) ────────────────────────────────────────────────

STANDARD_GATES: dict[str, GateDefinition] = {
    "Planungsfreigabe": GateDefinition(
        name="Planungsfreigabe",
        criteria=[
            "Layout freigegeben",
            "Zeichnungen freigegeben",
            "Stückliste finalisiert",
            "Risiken aus Planung bewertet",
        ],
    ),
    "Beschaffungsfreigabe": GateDefinition(
        name="Beschaffungsfreigabe",
        criteria=[
            "Bedarf geprüft",
            "Lieferzeiten geklärt",
            "kritische Teile bestellt",
            "Alternativlieferanten bewertet",
        ],
    ),
    "Montagefreigabe": GateDefinition(
        name="Montagefreigabe",
        criteria=[
            "Material vollständig",
            "Voraufbau abgeschlossen",
            "offene Punkte bewertet",
            "Montagepersonal eingeplant",
        ],
    ),
    "Inbetriebnahmefreigabe": GateDefinition(
        name="Inbetriebnahmefreigabe",
        criteria=[
            "Mechanik abgeschlossen",
            "Elektrik abgeschlossen",
            "Sicherheitsprüfung vorbereitet",
            "Softwarestand verfügbar",
        ],
    ),
    "Abnahmefreigabe": GateDefinition(
        name="Abnahmefreigabe",
        criteria=[
            "Testlauf bestanden",
            "Sicherheitsprüfung bestanden",
            "Dokumentation vollständig",
            "Restpunkte bewertet",
        ],
    ),
}


# ── Standard-Meilensteine (Doc 4.4) ─────────────────────────────────────────

STANDARD_MILESTONE_NAMES: list[str] = [
    "Projektstart",
    "Planungsfreigabe",
    "Beschaffungsfreigabe",
    "Material vollständig",
    "Voraufbau Start",
    "Voraufbau abgeschlossen",
    "Montagefreigabe",
    "Montage abgeschlossen",
    "Inbetriebnahme abgeschlossen",
    "Abnahme",
    "Übergabe",
]


# ── Schlagwort-Umkategorisierung "Sonstige Vorgänge" (Doc 8.1) ─────────────

GATE_NAME_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"planungsfreigabe", re.IGNORECASE), "Planungsfreigabe"),
    (re.compile(r"beschaffungsfreigabe", re.IGNORECASE), "Beschaffungsfreigabe"),
    (re.compile(r"montagefreigabe", re.IGNORECASE), "Montagefreigabe"),
    (re.compile(r"inbetriebnahmefreigabe|ibn[\s-]?freigabe", re.IGNORECASE), "Inbetriebnahmefreigabe"),
    (re.compile(r"abnahmefreigabe|abnahme", re.IGNORECASE), "Abnahmefreigabe"),
]

MILESTONE_KEYWORDS = re.compile(r"freigabe|abnahme", re.IGNORECASE)

PHASE_RECATEGORIZE_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"bestellung|lieferung", re.IGNORECASE), "Beschaffung"),
    (re.compile(r"montage", re.IGNORECASE), "Montage"),
    (re.compile(r"inbetriebnahme", re.IGNORECASE), "Inbetriebnahme"),
    (re.compile(r"dokumentation", re.IGNORECASE), "Dokumentation"),
    (re.compile(r"\btest", re.IGNORECASE), "Qualität / Prüfung"),
]


def _is_sonstige(task: Task) -> bool:
    return task.phase == SONSTIGE_LABEL or task.sub_phase == SONSTIGE_LABEL


def recategorize_sonstige(tasks: list[Task]) -> tuple[list[Task], list[QualityWarning]]:
    """Sortiert Vorgänge aus "Sonstige Vorgänge" anhand von Schlagworten im Namen
    in Gates, Meilensteine oder passendere Phasen um (Doc 8.1)."""
    warnings: list[QualityWarning] = []

    for t in tasks:
        if not _is_sonstige(t):
            continue
        if t.is_gate or t.milestone:
            # Bereits explizit klassifiziert (z.B. CSV-Spalten "Gate"/"Meilenstein") —
            # Schlagwort-Heuristik nicht anwenden, um diese Angaben nicht zu überschreiben.
            continue
        name = t.task_name

        gate_match = next((label for pat, label in GATE_NAME_PATTERNS if pat.search(name)), None)
        if gate_match is not None:
            t.is_gate = True
            t.task_type = TaskType.gate
            t.gate_name = gate_match
            t.gate_criteria = list(STANDARD_GATES[gate_match].criteria)
            if t.gate_status is None:
                t.gate_status = GateStatus.open
            warnings.append(QualityWarning(
                code="RECATEGORIZED",
                severity="info",
                message=(f"Vorgang \"{name}\" wurde als Gate \"{gate_match}\" erkannt "
                         f"und aus \"{SONSTIGE_LABEL}\" entfernt."),
                task_id=t.task_id,
            ))
            continue

        if MILESTONE_KEYWORDS.search(name):
            t.milestone = True
            t.task_type = TaskType.milestone
            warnings.append(QualityWarning(
                code="RECATEGORIZED",
                severity="info",
                message=(f"Vorgang \"{name}\" wurde als Meilenstein erkannt und aus "
                         f"\"{SONSTIGE_LABEL}\" entfernt."),
                task_id=t.task_id,
            ))
            continue

        for pattern, target_phase in PHASE_RECATEGORIZE_KEYWORDS:
            if pattern.search(name):
                old_phase = t.phase if t.phase != SONSTIGE_LABEL else t.sub_phase
                t.phase = target_phase
                t.sub_phase = target_phase
                warnings.append(QualityWarning(
                    code="RECATEGORIZED",
                    severity="info",
                    message=(f"Vorgang \"{name}\" wurde von \"{old_phase}\" nach "
                             f"\"{target_phase}\" umsortiert."),
                    task_id=t.task_id,
                ))
                break

    return tasks, warnings


# ── Datenbasis-Bewertung (Doc 8.2) ──────────────────────────────────────────

def assess_data_basis(tasks: list[Task]) -> list[DataBasisWarning]:
    total = len(tasks)
    if total == 0:
        return []

    warnings: list[DataBasisWarning] = []

    with_owner = sum(1 for t in tasks if t.owner and t.owner != "Nicht zugewiesen")
    if with_owner == 0:
        warnings.append(DataBasisWarning(
            code="NO_OWNERS",
            message=("Hinweis: Die importierte Datei enthält keine Verantwortlichen. "
                     "Die Planqualität ist dadurch eingeschränkt."),
            affected_count=total, total_count=total,
        ))
    elif with_owner / total < 0.3:
        warnings.append(DataBasisWarning(
            code="FEW_OWNERS",
            message=(f"Hinweis: Nur {with_owner} von {total} Vorgängen haben einen "
                     f"Verantwortlichen hinterlegt. Die Planqualität ist dadurch "
                     f"eingeschränkt."),
            affected_count=total - with_owner, total_count=total,
        ))

    with_preds = sum(1 for t in tasks if t.predecessor_links)
    if with_preds == 0:
        warnings.append(DataBasisWarning(
            code="NO_PREDECESSORS",
            message=("Hinweis: Die importierte Datei enthält keine Abhängigkeiten. "
                     "Kritischer Pfad kann nur eingeschränkt berechnet werden."),
            affected_count=total, total_count=total,
        ))
    elif with_preds / total < MIN_PREDECESSOR_RATIO_FOR_CPM:
        warnings.append(DataBasisWarning(
            code="FEW_PREDECESSORS",
            message=(f"Hinweis: Nur {with_preds} von {total} Vorgängen haben "
                     f"Vorgängerbeziehungen hinterlegt. Kritischer Pfad kann nur "
                     f"eingeschränkt berechnet werden."),
            affected_count=total - with_preds, total_count=total,
        ))

    today = date.today()
    if all(t.progress_percent == 0 for t in tasks) and any(t.start_date <= today for t in tasks):
        warnings.append(DataBasisWarning(
            code="NO_PROGRESS",
            message=("Hinweis: Die importierte Datei enthält keine Fortschrittswerte. "
                     "Statusbewertung basiert nur auf Terminen."),
            affected_count=total, total_count=total,
        ))

    if all(t.actual_start is None and t.actual_end is None for t in tasks):
        warnings.append(DataBasisWarning(
            code="NO_ACTUALS",
            message=("Hinweis: Die importierte Datei enthält keine Ist-Termine. Der "
                     "Forecast basiert ausschließlich auf der ursprünglichen Planung."),
            affected_count=total, total_count=total,
        ))

    if all(t.baseline_start is None and t.baseline_end is None for t in tasks):
        warnings.append(DataBasisWarning(
            code="NO_BASELINE",
            message=("Hinweis: Die importierte Datei enthält keine Baseline-Termine. "
                     "Ein Plan/Ist-Vergleich gegenüber der Ursprungsplanung ist nicht "
                     "möglich."),
            affected_count=total, total_count=total,
        ))

    return warnings


# ── Ampellogik (Doc 4.3 + 4.9) ──────────────────────────────────────────────

def compute_traffic_light_task(task: Task) -> TrafficLight:
    if task.status in (TaskStatus.done, TaskStatus.cancelled):
        return TrafficLight.green

    delay = task.delay_days or 0
    buffer = task.buffer_days

    # Rot
    if task.status == TaskStatus.blocked:
        return TrafficLight.red
    if delay > 0 and task.critical_path_flag:
        return TrafficLight.red
    if task.risk_level == RiskLevel.critical:
        return TrafficLight.red
    if task.risk_level == RiskLevel.high and buffer is not None and buffer <= 2:
        return TrafficLight.red
    if buffer is not None and buffer < 0:
        return TrafficLight.red
    if task.is_gate and task.gate_status in (GateStatus.at_risk, GateStatus.failed):
        return TrafficLight.red

    # Gelb
    if delay > 0:
        return TrafficLight.yellow
    if task.risk_level in (RiskLevel.high, RiskLevel.medium):
        return TrafficLight.yellow
    if buffer is not None and buffer <= 2:
        return TrafficLight.yellow
    if task.decision_required:
        return TrafficLight.yellow

    return TrafficLight.green


def compute_traffic_light_phase(tasks: list[Task]) -> TrafficLight:
    lights = [t.traffic_light for t in tasks if t.traffic_light is not None]
    if any(l == TrafficLight.red for l in lights):
        return TrafficLight.red
    if any(l == TrafficLight.yellow for l in lights):
        return TrafficLight.yellow
    return TrafficLight.green


def compute_overall_traffic_light(phases: list, cpm_info: CriticalPathInfo) -> TrafficLight:
    if cpm_info.computable and cpm_info.end_date_at_risk:
        return TrafficLight.red
    lights = [p.traffic_light for p in phases if p.traffic_light is not None]
    if any(l == TrafficLight.red for l in lights):
        return TrafficLight.red
    if any(l == TrafficLight.yellow for l in lights):
        return TrafficLight.yellow
    return TrafficLight.green


# ── Plan/Ist/Forecast (Doc 4.8) ─────────────────────────────────────────────

def compute_delay_and_forecast(task: Task) -> None:
    """Mutiert task.delay_days (und ggf. task.forecast_start/end)."""
    if task.actual_end is not None:
        task.delay_days = (task.actual_end - task.end_date).days
        if task.forecast_end is None:
            task.forecast_end = task.actual_end
        if task.forecast_start is None:
            task.forecast_start = task.actual_start or task.start_date
    elif task.forecast_end is not None:
        task.delay_days = (task.forecast_end - task.end_date).days
    else:
        task.delay_days = None


# ── "Sonstige Vorgänge" > 10 % (Doc 4.10) ───────────────────────────────────

def check_sonstige_ratio(tasks: list[Task]) -> Optional[QualityWarning]:
    total = len(tasks)
    if total == 0:
        return None
    sonstige = sum(1 for t in tasks if _is_sonstige(t))
    ratio = sonstige / total
    if ratio > 0.10:
        return QualityWarning(
            code="SONSTIGE_OVERFLOW",
            severity="warning",
            message=(f"Mehr als 10 % der Vorgänge ({sonstige} von {total}) sind als "
                     f"\"{SONSTIGE_LABEL}\" klassifiziert. Bitte Arbeitspakete "
                     f"konkretisieren."),
            count=sonstige,
        )
    return None


# ── Pflichtprüfungen (Doc 5.1) ──────────────────────────────────────────────

def run_quality_checklist(
    tasks: list[Task],
    cpm_info: CriticalPathInfo,
    cycles: list[list[str]],
    dangling_warnings: list[QualityWarning],
    validation_warnings: list[ValidationWarning],
) -> list[QualityWarning]:
    warnings: list[QualityWarning] = list(dangling_warnings)
    total = len(tasks)
    if total == 0:
        return warnings
    today = date.today()

    # 1. Vorgang ohne Verantwortlichen
    no_owner = [t for t in tasks if not t.owner or t.owner == "Nicht zugewiesen"]
    if no_owner:
        warnings.append(QualityWarning(
            code="MISSING_OWNER", severity="warning",
            message=f"{len(no_owner)} Vorgänge ohne Verantwortlichen.",
            count=len(no_owner),
        ))

    # 2./3. Vorgang ohne Start-/Enddatum (Datum wurde beim Import automatisch ergänzt)
    no_start_ids = {w.task_id for w in validation_warnings if w.field == "start_date" and w.task_id}
    no_end_ids = {w.task_id for w in validation_warnings if w.field == "end_date" and w.task_id}
    if no_start_ids:
        warnings.append(QualityWarning(
            code="MISSING_START_DATE", severity="warning",
            message=f"{len(no_start_ids)} Vorgänge ohne Startdatum (automatisch ergänzt).",
            count=len(no_start_ids),
        ))
    if no_end_ids:
        warnings.append(QualityWarning(
            code="MISSING_END_DATE", severity="warning",
            message=f"{len(no_end_ids)} Vorgänge ohne Enddatum (automatisch ergänzt).",
            count=len(no_end_ids),
        ))

    # 4. Vorgang mit Enddatum vor Startdatum
    end_before_start = [t for t in tasks if t.end_date < t.start_date]
    if end_before_start:
        warnings.append(QualityWarning(
            code="END_BEFORE_START", severity="error",
            message=f"{len(end_before_start)} Vorgänge mit Enddatum vor Startdatum.",
            count=len(end_before_start),
        ))

    # 5. Status-/Fortschritt-Inkonsistenz (Status ist im Modell immer gesetzt)
    inconsistent = [
        t for t in tasks
        if (t.progress_percent == 100 and t.status not in (TaskStatus.done, TaskStatus.cancelled))
        or (t.progress_percent > 0 and t.status == TaskStatus.not_started)
    ]
    if inconsistent:
        warnings.append(QualityWarning(
            code="STATUS_INCONSISTENT", severity="info",
            message=f"{len(inconsistent)} Vorgänge mit Status/Fortschritt-Inkonsistenz.",
            count=len(inconsistent),
        ))

    # 6. 0 % Fortschritt trotz Start in der Vergangenheit
    overdue_progress = [
        t for t in tasks
        if t.start_date < today and t.progress_percent == 0
        and t.status not in (TaskStatus.done, TaskStatus.cancelled)
    ]
    if overdue_progress:
        warnings.append(QualityWarning(
            code="NO_PROGRESS_DESPITE_STARTED", severity="warning",
            message=(f"{len(overdue_progress)} Vorgänge mit 0 % Fortschritt, obwohl "
                     f"das Startdatum in der Vergangenheit liegt."),
            count=len(overdue_progress),
        ))

    # 7./8. Meilenstein ohne Name / ohne belastbares Datum
    milestones = [t for t in tasks if t.milestone]
    no_name = [t for t in milestones if not t.milestone_name]
    if no_name:
        warnings.append(QualityWarning(
            code="MILESTONE_NO_NAME", severity="warning",
            message=f"{len(no_name)} Meilensteine ohne Bezeichnung.",
            count=len(no_name),
        ))
    no_date = [t for t in milestones if t.task_id in no_end_ids]
    if no_date:
        warnings.append(QualityWarning(
            code="MILESTONE_NO_DATE", severity="warning",
            message=f"{len(no_date)} Meilensteine ohne belastbares Datum.",
            count=len(no_date),
        ))

    # 9./10. Gate ohne Kriterien / ohne Verantwortlichen
    gates = [t for t in tasks if t.is_gate]
    gate_no_criteria = [g for g in gates if not g.gate_criteria]
    if gate_no_criteria:
        warnings.append(QualityWarning(
            code="GATE_NO_CRITERIA", severity="warning",
            message=f"{len(gate_no_criteria)} Gates ohne klare Freigabekriterien.",
            count=len(gate_no_criteria),
        ))
    gate_no_owner = [
        g for g in gates
        if (not g.owner or g.owner == "Nicht zugewiesen") and not g.decision_owner
    ]
    if gate_no_owner:
        warnings.append(QualityWarning(
            code="GATE_NO_OWNER", severity="warning",
            message=f"{len(gate_no_owner)} Gates ohne Verantwortlichen.",
            count=len(gate_no_owner),
        ))

    # 11. Vorgang ohne Phase
    no_phase = [t for t in tasks if not t.phase or t.phase == "Ohne Phase"]
    if no_phase:
        warnings.append(QualityWarning(
            code="MISSING_PHASE", severity="warning",
            message=f"{len(no_phase)} Vorgänge ohne Phase.",
            count=len(no_phase),
        ))

    # 12. Vorgang in "Sonstige" trotz erkennbarer Schlagworte
    leftover = [
        t for t in tasks
        if _is_sonstige(t) and (
            MILESTONE_KEYWORDS.search(t.task_name)
            or any(p.search(t.task_name) for p, _ in PHASE_RECATEGORIZE_KEYWORDS)
        )
    ]
    if leftover:
        warnings.append(QualityWarning(
            code="SONSTIGE_WITH_KEYWORDS", severity="info",
            message=(f"{len(leftover)} Vorgänge in \"{SONSTIGE_LABEL}\" trotz "
                     f"erkennbarer Schlagworte im Namen."),
            count=len(leftover),
        ))

    # 13. Aufgabe ohne Abhängigkeiten (informativ; Meilensteine/Gates ausgenommen)
    no_deps = [
        t for t in tasks
        if not t.predecessor_links and not t.milestone and not t.is_gate
    ]
    if no_deps:
        warnings.append(QualityWarning(
            code="TASK_NO_DEPENDENCIES", severity="info",
            message=f"{len(no_deps)} Vorgänge ohne hinterlegte Vorgängerbeziehungen.",
            count=len(no_deps),
        ))

    # 14. Abhängigkeit auf nicht existierende Aufgabe -> bereits in dangling_warnings

    # 15. Zirkuläre Abhängigkeit
    by_id = {t.task_id: t for t in tasks}
    for cycle in cycles:
        names = " -> ".join(by_id[tid].task_name for tid in cycle if tid in by_id)
        warnings.append(QualityWarning(
            code="CIRCULAR_DEPENDENCY", severity="error",
            message=f"Zirkuläre Abhängigkeit erkannt: {names}.",
        ))

    # 16. Vorgang auf kritischem Pfad mit negativem Puffer
    if cpm_info.computable:
        neg_buffer = [t for t in tasks if t.critical_path_flag and (t.buffer_days or 0) < 0]
        if neg_buffer:
            warnings.append(QualityWarning(
                code="CRITICAL_NO_BUFFER", severity="error",
                message=f"{len(neg_buffer)} Vorgänge auf dem kritischen Pfad mit negativem Puffer.",
                count=len(neg_buffer),
            ))

    # 17. Endtermin überschritten oder gefährdet
    overdue_end = [
        t for t in tasks
        if t.end_date < today and t.status not in (TaskStatus.done, TaskStatus.cancelled)
    ]
    if overdue_end or (cpm_info.computable and cpm_info.end_date_at_risk):
        msg = f"{len(overdue_end)} Vorgänge mit überschrittenem Endtermin."
        if cpm_info.computable and cpm_info.end_date_at_risk:
            msg += " Der geplante Gesamt-Endtermin ist laut Critical-Path-Berechnung gefährdet."
        warnings.append(QualityWarning(
            code="END_DATE_AT_RISK", severity="error" if overdue_end else "warning",
            message=msg,
            count=len(overdue_end),
        ))

    return warnings


# ── Qualitäts-Score (Doc 5.2) ───────────────────────────────────────────────

def compute_quality_score(
    tasks: list[Task],
    checklist_warnings: list[QualityWarning],
    data_basis_warnings: list[DataBasisWarning],
    cpm_info: CriticalPathInfo,
    sonstige_warning: Optional[QualityWarning],
) -> QualityScore:
    total = len(tasks)
    if total == 0:
        return QualityScore(score=0, rating="nicht steuerbar", breakdown={},
                             warnings=checklist_warnings, data_basis_warnings=data_basis_warnings)

    def _warning_count(code: str) -> int:
        for w in checklist_warnings:
            if w.code == code:
                return w.count if w.count is not None else total
        return 0

    breakdown: dict[str, int] = {}

    # +15 alle Vorgänge haben Verantwortliche
    with_owner = total - _warning_count("MISSING_OWNER")
    breakdown["Verantwortliche"] = round(15 * max(with_owner, 0) / total)

    # +15 alle Vorgänge haben Start- und Enddatum
    dates_issues = min(total, _warning_count("MISSING_START_DATE")
                        + _warning_count("MISSING_END_DATE")
                        + _warning_count("END_BEFORE_START"))
    breakdown["Termine"] = round(15 * (total - dates_issues) / total)

    # +15 Abhängigkeiten sind gepflegt
    with_preds = sum(1 for t in tasks if t.predecessor_links)
    breakdown["Abhängigkeiten"] = round(15 * min(with_preds / total, 1.0))

    # +10 Meilensteine sind benannt
    milestones = [t for t in tasks if t.milestone]
    if not milestones:
        breakdown["Meilensteine"] = 10
    else:
        named = sum(1 for m in milestones if m.milestone_name)
        breakdown["Meilensteine"] = round(10 * named / len(milestones))

    # +10 Gates sind definiert
    gates_with_criteria = sum(
        1 for name in STANDARD_GATES
        if any(t.is_gate and t.gate_name == name and t.gate_criteria for t in tasks)
    )
    breakdown["Gates"] = round(10 * gates_with_criteria / len(STANDARD_GATES))

    # +10 Fortschritt ist gepflegt
    if any(w.code == "NO_PROGRESS" for w in data_basis_warnings):
        breakdown["Fortschritt"] = 0
    else:
        with_progress = sum(
            1 for t in tasks
            if t.progress_percent > 0 or t.status in (TaskStatus.done, TaskStatus.cancelled)
        )
        breakdown["Fortschritt"] = round(10 * with_progress / total)

    # +10 Risiken sind gepflegt
    with_risk = sum(1 for t in tasks if t.risk_level is not None)
    breakdown["Risiken"] = round(10 * with_risk / total)

    # +10 kritischer Pfad berechnet
    breakdown["KritischerPfad"] = 10 if cpm_info.computable else 0

    # +5 Sonstige Vorgänge unter 10 %
    breakdown["Sonstige"] = 0 if sonstige_warning is not None else 5

    score = max(0, min(100, sum(breakdown.values())))

    if score >= 80:
        rating = "gut steuerbar"
    elif score >= 60:
        rating = "eingeschränkt steuerbar"
    elif score >= 40:
        rating = "grobe Übersicht"
    else:
        rating = "nicht steuerbar"

    return QualityScore(
        score=score, rating=rating, breakdown=breakdown,
        warnings=checklist_warnings, data_basis_warnings=data_basis_warnings,
    )


# ── Meilenstein-Nummerierung & Legende (Doc 4.4) ────────────────────────────

def _match_standard_milestone_name(task_name: str) -> Optional[str]:
    name_lower = task_name.lower()
    for std in STANDARD_MILESTONE_NAMES:
        if std.lower() in name_lower:
            return std
    return None


def assign_milestone_numbers(tasks: list[Task]) -> list[MilestoneLegendEntry]:
    """Nummeriert alle Meilensteine chronologisch (M1, M2, ...) und liefert die
    Legende (auf LEGEND_MAX_ENTRIES begrenzt für die A3-Darstellung)."""
    milestones = sorted((t for t in tasks if t.milestone), key=lambda t: t.end_date)
    legend: list[MilestoneLegendEntry] = []
    for i, t in enumerate(milestones, start=1):
        t.milestone_number = i
        if not t.milestone_name:
            t.milestone_name = _match_standard_milestone_name(t.task_name) or t.task_name
        if i <= LEGEND_MAX_ENTRIES:
            legend.append(MilestoneLegendEntry(
                number=i, name=t.milestone_name, date=t.end_date,
                status=t.status, owner=t.owner,
            ))
    return legend


# ── Automatische PM-Bewertung (Doc 6 / 13) ──────────────────────────────────

RECOMMENDATION_MAP: dict[str, str] = {
    "MISSING_OWNER": "Verantwortliche für offene Vorgänge ergänzen.",
    "SONSTIGE_OVERFLOW": "\"Sonstige Vorgänge\" fachlich zuordnen.",
    "GATE_NO_CRITERIA": "Gate-Kriterien für offene Gates definieren.",
    "MILESTONE_NO_NAME": "Meilensteine benennen.",
    "CRITICAL_NO_BUFFER": "Kritischen Pfad wöchentlich prüfen und Pufferzeiten schaffen.",
    "NO_PROGRESS_DESPITE_STARTED": "Fortschritt je Vorgang aktualisieren.",
    "DANGLING_DEPENDENCY": "Abhängigkeiten auf gültige Vorgänger-IDs korrigieren.",
    "CIRCULAR_DEPENDENCY": "Zirkuläre Abhängigkeiten auflösen.",
    "END_DATE_AT_RISK": "Endtermin-gefährdende Vorgänge aktiv nachverfolgen.",
}

DATA_BASIS_RECOMMENDATIONS: dict[str, str] = {
    "NO_PREDECESSORS": "Vorgängerbeziehungen ergänzen, um den kritischen Pfad berechnen zu können.",
    "FEW_PREDECESSORS": "Vorgängerbeziehungen ergänzen, um den kritischen Pfad vollständig berechnen zu können.",
    "NO_OWNERS": "Verantwortliche für alle Vorgänge hinterlegen.",
    "FEW_OWNERS": "Verantwortliche für alle Vorgänge hinterlegen.",
    "NO_PROGRESS": "Fortschritt je Vorgang pflegen.",
    "NO_ACTUALS": "Ist-Termine erfassen, um Forecast-/Plan-Abweichungen zu ermitteln.",
    "NO_BASELINE": "Baseline-Termine hinterlegen für einen Plan/Ist-Vergleich.",
}


def generate_pm_assessment(
    tasks: list[Task],
    overall_end: date,
    overall_forecast_end: date,
    overall_traffic_light: TrafficLight,
    quality_score: QualityScore,
    cpm_info: CriticalPathInfo,
    open_risks: list[Task],
    checklist_warnings: list[QualityWarning],
) -> PMAssessment:
    by_id = {t.task_id: t for t in tasks}
    delay = (overall_forecast_end - overall_end).days

    if delay <= 0:
        forecast_text = "aktuell haltbar"
    else:
        names: list[str] = []
        for t in open_risks[:2]:
            if t.task_name not in names:
                names.append(t.task_name)
        if cpm_info.computable:
            for tid, name in zip(cpm_info.task_ids, cpm_info.task_names):
                if name in names:
                    continue
                t = by_id.get(tid)
                if t is not None and t.status in (TaskStatus.done, TaskStatus.cancelled):
                    continue
                names.append(name)
                if len(names) >= 2:
                    break
        if not names:
            names = ["kritische Vorgänge"]
        forecast_text = (
            f"gefährdet, wenn {', '.join(names)} nicht bis "
            f"{overall_forecast_end.strftime('%d.%m.%Y')} verfügbar sind."
        )

    critical_points: list[str] = []
    if cpm_info.computable:
        for tid, name in zip(cpm_info.task_ids, cpm_info.task_names):
            t = by_id.get(tid)
            if t is not None and t.status in (TaskStatus.done, TaskStatus.cancelled):
                continue
            if name not in critical_points:
                critical_points.append(name)
            if len(critical_points) >= 3:
                break
    for t in open_risks:
        if len(critical_points) >= 3:
            break
        if t.task_name not in critical_points:
            critical_points.append(t.task_name)

    open_decisions: list[str] = []
    for t in tasks:
        if not t.decision_required:
            continue
        if t.decision_owner:
            open_decisions.append(f"{t.task_name} ({t.decision_owner})")
        else:
            open_decisions.append(t.task_name)
        if len(open_decisions) >= 5:
            break

    notable_issues = [w.message for w in checklist_warnings if w.code != "RECATEGORIZED"][:6]
    notable_issues += [w.message for w in quality_score.data_basis_warnings]
    notable_issues = notable_issues[:8]

    recommendations: list[str] = []
    for w in checklist_warnings:
        text = RECOMMENDATION_MAP.get(w.code)
        if text and text not in recommendations:
            recommendations.append(text)
    for w in quality_score.data_basis_warnings:
        text = DATA_BASIS_RECOMMENDATIONS.get(w.code)
        if text and text not in recommendations:
            recommendations.append(text)
    recommendations = recommendations[:5]

    return PMAssessment(
        overall_status=overall_traffic_light,
        quality_score=quality_score.score,
        quality_rating=quality_score.rating,
        end_date=overall_end,
        end_date_forecast_text=forecast_text,
        critical_points=critical_points,
        open_decisions=open_decisions,
        recommendations=recommendations,
        notable_issues=notable_issues,
    )
