from datetime import date
from typing import List
from models import Task, TaskStatus, RiskLevel, Phase, CondensedPlan


def condense(project_id: str, project_name: str, tasks: List[Task]) -> CondensedPlan:
    if not tasks:
        return CondensedPlan(
            project_id=project_id, project_name=project_name,
            phases=[], overall_start=date.today(), overall_end=date.today(),
            total_tasks=0, open_risks=[], next_milestones=[], blocked_tasks=[], overdue_tasks=[],
        )

    today = date.today()
    phases = _build_phases(tasks)
    overdue = [t for t in tasks if t.end_date < today and t.status != TaskStatus.done]
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

    return CondensedPlan(
        project_id=project_id,
        project_name=project_name,
        phases=phases,
        overall_start=min(all_starts),
        overall_end=max(all_ends),
        total_tasks=len(tasks),
        open_risks=open_risks,
        next_milestones=next_milestones,
        blocked_tasks=blocked,
        overdue_tasks=overdue,
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

        phases.append(Phase(
            name=phase_name,
            start_date=min(starts),
            end_date=max(ends),
            task_count=len(phase_tasks),
            milestones=milestones,
            owners=owners[:5],
            status_summary=status_summary,
            risk_summary=risk_summary,
            tasks=phase_tasks,
        ))

    return sorted(phases, key=lambda p: p.start_date)


def get_phase_completion(phase: Phase) -> int:
    done = phase.status_summary.get(TaskStatus.done.value, 0)
    total = phase.task_count
    return int((done / total) * 100) if total > 0 else 0
