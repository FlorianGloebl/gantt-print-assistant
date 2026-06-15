from datetime import date
from typing import List, Optional

import cpm
import quality
from models import Task, TaskStatus, RiskLevel, ValidationWarning, Phase, CondensedPlan


def condense(
    project_id: str,
    project_name: str,
    tasks: List[Task],
    validation_warnings: Optional[List[ValidationWarning]] = None,
) -> CondensedPlan:
    if not tasks:
        return CondensedPlan(
            project_id=project_id, project_name=project_name,
            phases=[], overall_start=date.today(), overall_end=date.today(),
            total_tasks=0, open_risks=[], next_milestones=[], blocked_tasks=[], overdue_tasks=[],
        )

    validation_warnings = validation_warnings or []
    today = date.today()

    # 1. Abhängigkeitsgraph, Zyklen, Nachfolger
    _, dangling_warnings = cpm.build_dependency_graph(tasks)
    cycles = cpm.detect_circular_dependencies(tasks)
    cpm.populate_successors(tasks)

    # 2. Critical Path Method
    cpm_info = cpm.compute_cpm(tasks)

    # 3./4. Forecast/Verzug + Ampel je Vorgang
    for t in tasks:
        quality.compute_delay_and_forecast(t)
        t.traffic_light = quality.compute_traffic_light_task(t)

    # 5. Meilenstein-Nummerierung/-Legende + Gates
    milestone_legend = quality.assign_milestone_numbers(tasks)
    gates = [t for t in tasks if t.is_gate]

    # 6. Phasen + bestehende Aggregationen
    phases = _build_phases(tasks)
    overdue = [
        t for t in tasks
        if t.end_date < today and t.status not in (TaskStatus.done, TaskStatus.cancelled)
    ]
    blocked = [t for t in tasks if t.status == TaskStatus.blocked]
    next_milestones = sorted(
        [t for t in tasks if t.milestone and t.status != TaskStatus.done],
        key=lambda t: t.end_date
    )[:5]
    open_risks = sorted(
        [t for t in tasks if t.risk_level in (RiskLevel.high, RiskLevel.critical)],
        key=lambda t: (0 if t.risk_level == RiskLevel.critical else 1, t.end_date)
    )[:10]

    all_starts = [t.start_date for t in tasks]
    all_ends = [t.end_date for t in tasks]
    overall_end = max(all_ends)

    # 7. Qualitäts-Checks und -Score
    sonstige_warning = quality.check_sonstige_ratio(tasks)
    checklist_warnings = quality.run_quality_checklist(
        tasks, cpm_info, cycles, dangling_warnings, validation_warnings,
    )
    if sonstige_warning is not None:
        checklist_warnings.append(sonstige_warning)
    data_basis_warnings = quality.assess_data_basis(tasks)
    quality_score = quality.compute_quality_score(
        tasks, checklist_warnings, data_basis_warnings, cpm_info, sonstige_warning,
    )

    # 8. Gesamt-Ampel, Gesamt-Forecast, Gesamt-Verzug
    overall_traffic_light = quality.compute_overall_traffic_light(phases, cpm_info)
    forecast_ends = [t.forecast_end for t in tasks if t.forecast_end is not None]
    overall_forecast_end = max(forecast_ends) if forecast_ends else overall_end
    overall_delay_days = (overall_forecast_end - overall_end).days

    # 9. Automatische PM-Bewertung
    pm_assessment = quality.generate_pm_assessment(
        tasks, overall_end, overall_forecast_end, overall_traffic_light,
        quality_score, cpm_info, open_risks, checklist_warnings,
    )

    return CondensedPlan(
        project_id=project_id,
        project_name=project_name,
        phases=phases,
        overall_start=min(all_starts),
        overall_end=overall_end,
        total_tasks=len(tasks),
        open_risks=open_risks,
        next_milestones=next_milestones,
        blocked_tasks=blocked,
        overdue_tasks=overdue,
        quality=quality_score,
        critical_path=cpm_info,
        milestone_legend=milestone_legend,
        gates=gates,
        pm_assessment=pm_assessment,
        overall_traffic_light=overall_traffic_light,
        overall_forecast_end=overall_forecast_end,
        overall_delay_days=overall_delay_days,
        sonstige_warning=sonstige_warning,
    )


def _build_phases(tasks: List[Task]) -> List[Phase]:
    phase_groups: dict[str, List[Task]] = {}
    for t in tasks:
        phase_groups.setdefault(t.phase, []).append(t)

    phases = []
    for phase_name, phase_tasks in phase_groups.items():
        starts = [t.start_date for t in phase_tasks]
        ends = [t.end_date for t in phase_tasks]
        milestones = [t for t in phase_tasks if t.milestone]
        owners = list(dict.fromkeys(t.owner for t in phase_tasks if t.owner != "Nicht zugewiesen"))

        status_summary = {}
        for t in phase_tasks:
            status_summary[t.status.value] = status_summary.get(t.status.value, 0) + 1

        risk_summary = {}
        for t in phase_tasks:
            if t.risk_level:
                risk_summary[t.risk_level.value] = risk_summary.get(t.risk_level.value, 0) + 1

        phase_end = max(ends)
        forecast_ends = [t.forecast_end for t in phase_tasks if t.forecast_end is not None]
        forecast_end = max(forecast_ends) if forecast_ends else phase_end

        phases.append(Phase(
            name=phase_name,
            start_date=min(starts),
            end_date=phase_end,
            task_count=len(phase_tasks),
            milestones=milestones,
            owners=owners[:5],
            status_summary=status_summary,
            risk_summary=risk_summary,
            tasks=phase_tasks,
            traffic_light=quality.compute_traffic_light_phase(phase_tasks),
            forecast_end=forecast_end,
            delay_days=(forecast_end - phase_end).days,
        ))

    return sorted(phases, key=lambda p: p.start_date)


def get_phase_completion(phase: Phase) -> int:
    done = phase.status_summary.get(TaskStatus.done.value, 0)
    total = phase.task_count
    return int((done / total) * 100) if total > 0 else 0
