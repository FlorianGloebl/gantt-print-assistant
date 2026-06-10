from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from enum import Enum


class TaskStatus(str, Enum):
    not_started = "Nicht begonnen"
    in_progress = "In Arbeit"
    blocked = "Blockiert"
    done = "Erledigt"


class RiskLevel(str, Enum):
    low = "niedrig"
    medium = "mittel"
    high = "hoch"
    critical = "kritisch"


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


class ValidationResult(BaseModel):
    valid_tasks: List[Task]
    warnings: List[ValidationWarning]
    total_rows: int
    valid_count: int
    error_count: int


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
