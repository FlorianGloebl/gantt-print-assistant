from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from enum import Enum


class TaskStatus(str, Enum):
    not_started = "Nicht begonnen"
    in_progress = "In Arbeit"
    blocked = "Blockiert"
    done = "Erledigt"
    cancelled = "Entfallen"


class RiskLevel(str, Enum):
    low = "niedrig"
    medium = "mittel"
    high = "hoch"
    critical = "kritisch"


class DependencyType(str, Enum):
    finish_to_start = "FS"
    start_to_start = "SS"
    finish_to_finish = "FF"
    start_to_finish = "SF"


class TrafficLight(str, Enum):
    green = "grün"
    yellow = "gelb"
    red = "rot"


class GateStatus(str, Enum):
    open = "offen"
    on_track = "im_plan"
    at_risk = "gefährdet"
    passed = "erreicht"
    failed = "nicht_erreicht"


class TaskType(str, Enum):
    task = "task"
    milestone = "milestone"
    gate = "gate"


class Dependency(BaseModel):
    task_id: str
    type: DependencyType = DependencyType.finish_to_start
    lag_days: int = 0


class Task(BaseModel):
    task_id: str
    task_name: str
    phase: str
    sub_phase: str = ""
    start_date: date
    end_date: date
    owner: str
    status: TaskStatus = TaskStatus.not_started
    milestone: bool = False
    dependencies: List[str] = []
    risk_level: Optional[RiskLevel] = None
    notes: Optional[str] = None
    source_system: str = "Excel"
    source_reference: Optional[str] = None

    # Erweiterte Abhängigkeiten (Typ + Lag)
    predecessor_links: List[Dependency] = []
    successors: List[str] = []

    # Organisation / Klassifikation
    parent_id: Optional[str] = None
    work_package: Optional[str] = None
    task_type: TaskType = TaskType.task
    department: Optional[str] = None

    # Plan- (start_date/end_date) ergänzt um Baseline / Ist / Forecast
    baseline_start: Optional[date] = None
    baseline_end: Optional[date] = None
    actual_start: Optional[date] = None
    actual_end: Optional[date] = None
    forecast_start: Optional[date] = None
    forecast_end: Optional[date] = None
    duration_days: Optional[int] = None
    progress_percent: int = 0

    # CPM-Ergebnisse (vom Calc-Engine befüllt)
    earliest_start: Optional[date] = None
    earliest_finish: Optional[date] = None
    latest_start: Optional[date] = None
    latest_finish: Optional[date] = None
    float_days: Optional[int] = None
    critical_path_flag: bool = False

    # Plan/Ist/Forecast-Abweichung
    delay_days: Optional[int] = None

    # Puffer & Risiko
    buffer_days: Optional[int] = None
    risk_description: Optional[str] = None
    mitigation: Optional[str] = None
    impact_on_end_date: Optional[RiskLevel] = None

    # Meilensteine
    milestone_name: Optional[str] = None
    milestone_number: Optional[int] = None

    # Gates
    is_gate: bool = False
    gate_name: Optional[str] = None
    gate_criteria: List[str] = []
    gate_status: Optional[GateStatus] = None

    # Entscheidungen
    decision_required: bool = False
    decision_owner: Optional[str] = None

    # Ampel (vom Calc-Engine befüllt)
    traffic_light: Optional[TrafficLight] = None

    # Sonstige optionale Felder
    external_dependency: bool = False
    supplier: Optional[str] = None
    cost_relevance: Optional[RiskLevel] = None
    customer_visible_flag: bool = False
    comments: Optional[str] = None


class ProjectPlan(BaseModel):
    project_id: str
    project_name: str
    import_timestamp: str
    source_file: str
    tasks: List[Task]


class ValidationWarning(BaseModel):
    task_id: Optional[str]
    field: str
    message: str
    severity: str  # "error" | "warning"


class QualityWarning(BaseModel):
    code: str
    severity: str  # "error" | "warning" | "info"
    message: str
    task_id: Optional[str] = None
    count: Optional[int] = None


class DataBasisWarning(BaseModel):
    code: str
    message: str
    affected_count: int
    total_count: int


class ValidationResult(BaseModel):
    valid_tasks: List[Task]
    warnings: List[ValidationWarning]
    total_rows: int
    valid_count: int
    error_count: int
    data_basis_warnings: List[DataBasisWarning] = []
    quality_warnings: List[QualityWarning] = []


class GateDefinition(BaseModel):
    name: str
    criteria: List[str]


class QualityScore(BaseModel):
    score: int
    rating: str
    breakdown: dict
    warnings: List[QualityWarning] = []
    data_basis_warnings: List[DataBasisWarning] = []


class MilestoneLegendEntry(BaseModel):
    number: int
    name: str
    date: date
    status: TaskStatus
    owner: str


class CriticalPathInfo(BaseModel):
    task_ids: List[str] = []
    task_names: List[str] = []
    end_date_at_risk: bool = False
    computable: bool = False
    note: Optional[str] = None


class PMAssessment(BaseModel):
    overall_status: TrafficLight
    quality_score: int
    quality_rating: str
    end_date: date
    end_date_forecast_text: str
    critical_points: List[str] = []
    open_decisions: List[str] = []
    recommendations: List[str] = []
    notable_issues: List[str] = []


class Phase(BaseModel):
    name: str
    start_date: date
    end_date: date
    task_count: int
    milestones: List[Task]
    owners: List[str]
    status_summary: dict
    risk_summary: dict
    tasks: List[Task]
    traffic_light: Optional[TrafficLight] = None
    forecast_end: Optional[date] = None
    delay_days: Optional[int] = None


class CondensedPlan(BaseModel):
    project_id: str
    project_name: str
    phases: List[Phase]
    overall_start: date
    overall_end: date
    total_tasks: int
    open_risks: List[Task]
    next_milestones: List[Task]
    blocked_tasks: List[Task]
    overdue_tasks: List[Task]
    quality: Optional[QualityScore] = None
    critical_path: Optional[CriticalPathInfo] = None
    milestone_legend: List[MilestoneLegendEntry] = []
    gates: List[Task] = []
    pm_assessment: Optional[PMAssessment] = None
    overall_traffic_light: Optional[TrafficLight] = None
    overall_forecast_end: Optional[date] = None
    overall_delay_days: Optional[int] = None
    sonstige_warning: Optional[QualityWarning] = None
