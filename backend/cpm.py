"""
Critical Path Method (CPM) — Vorwärts-/Rückwärtslauf mit Float/Puffer.

Operiert auf Tasks, die bereits `start_date`/`end_date` (= Plan-Termine) haben.
`compute_cpm()` beantwortet "welcher Puffer / welcher kritische Pfad, gegeben die
vorhandenen Termine + Abhängigkeiten" — im Unterschied zu `parser._compute_cpm_dates()`,
das fehlende Termine aus Dauer+Vorgängern *herleitet*.

Lag (`Dependency.lag_days`) wird als Kalendertage interpretiert (MSPDI LagFormat 7
= "elapsed days" = Kalendertage — die in der Praxis vorkommende Form).
"""

from datetime import date, timedelta

from models import Task, Dependency, DependencyType, CriticalPathInfo, QualityWarning

MIN_PREDECESSOR_RATIO_FOR_CPM = 0.3


def build_dependency_graph(tasks: list[Task]) -> tuple[dict[str, Task], list[QualityWarning]]:
    """Index tasks by ID; collect Warnungen für Vorgängerverweise auf unbekannte IDs."""
    by_id = {t.task_id: t for t in tasks}
    warnings: list[QualityWarning] = []
    for t in tasks:
        for link in t.predecessor_links:
            if link.task_id not in by_id:
                warnings.append(QualityWarning(
                    code="DANGLING_DEPENDENCY",
                    severity="error",
                    message=(f"Vorgang \"{t.task_name}\" verweist auf nicht "
                             f"existierende Vorgänger-ID \"{link.task_id}\"."),
                    task_id=t.task_id,
                ))
    return by_id, warnings


def detect_circular_dependencies(tasks: list[Task]) -> list[list[str]]:
    """DFS-Zyklenerkennung über predecessor_links (Kante: Vorgänger -> Vorgang)."""
    by_id = {t.task_id: t for t in tasks}
    graph: dict[str, list[str]] = {tid: [] for tid in by_id}
    for t in tasks:
        for link in t.predecessor_links:
            if link.task_id in graph:
                graph[link.task_id].append(t.task_id)

    cycles: list[list[str]] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in by_id}
    path: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for nxt in graph.get(node, []):
            if color[nxt] == GRAY:
                idx = path.index(nxt)
                cycles.append(path[idx:] + [nxt])
            elif color[nxt] == WHITE:
                dfs(nxt)
        path.pop()
        color[node] = BLACK

    for tid in by_id:
        if color[tid] == WHITE:
            dfs(tid)

    return cycles


def populate_successors(tasks: list[Task]) -> None:
    """Füllt `task.successors` aus den `predecessor_links` der jeweils anderen Tasks."""
    by_id = {t.task_id: t for t in tasks}
    for t in tasks:
        t.successors = []
    for t in tasks:
        for link in t.predecessor_links:
            pred = by_id.get(link.task_id)
            if pred is not None:
                pred.successors.append(t.task_id)


def _topological_order(tasks: list[Task]) -> list[Task]:
    """Kahn's-Algorithmus über predecessor_links; Vorgänge in Zyklen werden am
    Ende in Originalreihenfolge angehängt (Best-Effort, Zyklen werden separat
    gemeldet)."""
    by_id = {t.task_id: t for t in tasks}
    in_degree = {t.task_id: 0 for t in tasks}
    for t in tasks:
        for link in t.predecessor_links:
            if link.task_id in by_id:
                in_degree[t.task_id] += 1

    queue = [t for t in tasks if in_degree[t.task_id] == 0]
    ordered: list[Task] = []
    seen: set[str] = set()

    while queue:
        node = queue.pop(0)
        if node.task_id in seen:
            continue
        ordered.append(node)
        seen.add(node.task_id)
        for succ_id in node.successors:
            in_degree[succ_id] -= 1
            if in_degree[succ_id] == 0:
                queue.append(by_id[succ_id])

    # Restliche (zyklische) Tasks anhängen
    for t in tasks:
        if t.task_id not in seen:
            ordered.append(t)

    return ordered


def compute_cpm(tasks: list[Task]) -> CriticalPathInfo:
    """Vorwärts-/Rückwärtslauf. Setzt earliest_*/latest_*/float_days/critical_path_flag
    und (sofern nicht manuell vorgegeben) buffer_days auf den Tasks."""
    if not tasks:
        return CriticalPathInfo(computable=False, note="Keine Vorgänge vorhanden.")

    total = len(tasks)
    with_preds = sum(1 for t in tasks if t.predecessor_links)

    for t in tasks:
        t.earliest_start = None
        t.earliest_finish = None
        t.latest_start = None
        t.latest_finish = None
        t.float_days = None

    if with_preds / total < MIN_PREDECESSOR_RATIO_FOR_CPM:
        note = (
            f"Datenbasis unzureichend für Critical-Path-Berechnung: nur "
            f"{with_preds}/{total} Vorgänge mit Vorgängerbeziehungen."
        )
        # critical_path_flag bleibt unverändert (z.B. <Critical>1</Critical> aus
        # dem Import) — wird nicht zurückgesetzt, da hier keine verlässliche
        # Neuberechnung stattfindet.
        return CriticalPathInfo(computable=False, note=note)

    by_id = {t.task_id: t for t in tasks}
    populate_successors(tasks)
    durations = {t.task_id: max((t.end_date - t.start_date).days, 0) for t in tasks}
    order = _topological_order(tasks)

    # ── Vorwärtslauf ────────────────────────────────────────────────────────
    for t in order:
        es_candidates: list[date] = [t.start_date]
        for link in t.predecessor_links:
            pred = by_id.get(link.task_id)
            if pred is None or pred.earliest_start is None:
                continue
            lag = timedelta(days=link.lag_days)
            if link.type == DependencyType.finish_to_start:
                es_candidates.append(pred.earliest_finish + lag)
            elif link.type == DependencyType.start_to_start:
                es_candidates.append(pred.earliest_start + lag)
            elif link.type == DependencyType.finish_to_finish:
                es_candidates.append(pred.earliest_finish + lag - timedelta(days=durations[t.task_id]))
            elif link.type == DependencyType.start_to_finish:
                es_candidates.append(pred.earliest_start + lag - timedelta(days=durations[t.task_id]))

        t.earliest_start = max(es_candidates)
        t.earliest_finish = t.earliest_start + timedelta(days=durations[t.task_id])

    # ── Anker für Rückwärtslauf: geplantes Gesamtende ──────────────────────
    project_end = max(t.end_date for t in tasks)
    end_tasks = [t for t in tasks if not t.successors]
    if not end_tasks:
        end_tasks = tasks
    forward_finish = max(t.earliest_finish for t in end_tasks)
    end_date_at_risk = forward_finish > project_end

    # ── Rückwärtslauf (in umgekehrter topologischer Reihenfolge) ───────────
    for t in reversed(order):
        if not t.successors:
            t.latest_finish = project_end
        else:
            lf_candidates: list[date] = []
            for succ_id in t.successors:
                succ = by_id.get(succ_id)
                if succ is None or succ.latest_start is None:
                    continue
                for link in succ.predecessor_links:
                    if link.task_id != t.task_id:
                        continue
                    lag = timedelta(days=link.lag_days)
                    if link.type == DependencyType.finish_to_start:
                        lf_candidates.append(succ.latest_start - lag)
                    elif link.type == DependencyType.start_to_start:
                        lf_candidates.append(succ.latest_start - lag + timedelta(days=durations[t.task_id]))
                    elif link.type == DependencyType.finish_to_finish:
                        lf_candidates.append(succ.latest_finish - lag)
                    elif link.type == DependencyType.start_to_finish:
                        lf_candidates.append(succ.latest_finish - lag + timedelta(days=durations[t.task_id]))
            t.latest_finish = min(lf_candidates) if lf_candidates else project_end
            t.latest_finish = min(t.latest_finish, project_end)

        t.latest_start = t.latest_finish - timedelta(days=durations[t.task_id])
        t.float_days = (t.latest_start - t.earliest_start).days
        t.critical_path_flag = t.float_days <= 0

        if t.buffer_days is None or t.buffer_days > t.float_days:
            t.buffer_days = t.float_days

    critical_tasks = sorted(
        (t for t in tasks if t.critical_path_flag),
        key=lambda t: t.earliest_start,
    )

    return CriticalPathInfo(
        task_ids=[t.task_id for t in critical_tasks],
        task_names=[t.task_name for t in critical_tasks],
        end_date_at_risk=end_date_at_risk,
        computable=True,
        note=None,
    )
