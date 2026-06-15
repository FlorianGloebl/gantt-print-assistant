from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from datetime import datetime
import uuid

from models import CondensedPlan, ValidationResult
from parser import parse_file
from condensator import condense
from pdf_renderer import render_a4_executive, render_a3_gantt
from exporters import export_json, export_excel, export_csv

app = FastAPI(title="GANTT Print Assistant API", version="0.1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://floriangloebl.github.io", "null"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store (MVP: no persistence)
_sessions: dict[str, dict] = {}


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


@app.post("/upload", response_model=ValidationResult)
async def upload_file(file: UploadFile = File(...)):
    allowed = {".xlsx", ".xls", ".csv", ".xml"}
    suffix = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if suffix not in allowed:
        raise HTTPException(400, f"Dateiformat nicht unterstützt. Erlaubt: {', '.join(allowed)}")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Datei zu groß (max. 10 MB)")

    result = parse_file(content, file.filename)

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "filename": file.filename,
        "tasks": result.valid_tasks,
        "imported_at": datetime.now().isoformat(),
        "validation_warnings": result.warnings,
    }
    result_dict = result.model_dump(mode="json")
    result_dict["session_id"] = session_id
    return JSONResponse(result_dict)


@app.get("/condense/{session_id}")
def condense_plan(session_id: str, project_name: str = "Projektplan"):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session nicht gefunden. Bitte Datei erneut hochladen.")

    plan = condense(
        project_id=session_id[:8],
        project_name=project_name,
        tasks=session["tasks"],
        validation_warnings=session.get("validation_warnings", []),
    )
    session["plan"] = plan
    return plan.model_dump(mode="json")


VALID_SIZES = {"a0", "a1", "a2", "a3", "a4", "a5"}


@app.get("/pdf/a4/{session_id}")
def pdf_a4(session_id: str, project_name: str = "Projektplan",
           preview: bool = False, paper_size: str = "a4"):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session nicht gefunden.")

    size = paper_size.lower() if paper_size.lower() in VALID_SIZES else "a4"

    if "plan" not in session:
        plan = condense(session_id[:8], project_name, session["tasks"],
                         validation_warnings=session.get("validation_warnings", []))
        session["plan"] = plan
    else:
        plan = session["plan"]

    pdf_bytes = render_a4_executive(plan, session["filename"], paper_size=size)
    disposition = "inline" if preview else f'attachment; filename="executive-summary-{size}-{session_id[:8]}.pdf"'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition},
    )


@app.get("/pdf/a3/{session_id}")
def pdf_a3(session_id: str, project_name: str = "Projektplan",
           preview: bool = False, paper_size: str = "a3", detail: bool = False):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session nicht gefunden.")

    size = paper_size.lower() if paper_size.lower() in VALID_SIZES else "a3"

    if "plan" not in session:
        plan = condense(session_id[:8], project_name, session["tasks"],
                         validation_warnings=session.get("validation_warnings", []))
        session["plan"] = plan
    else:
        plan = session["plan"]

    pdf_bytes = render_a3_gantt(plan, session["filename"], paper_size=size, detail=detail)
    name_part = "vorgangsliste" if detail else "phasenplan"
    disposition = "inline" if preview else f'attachment; filename="{name_part}-{size}-{session_id[:8]}.pdf"'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition},
    )


def _get_plan(session: dict, session_id: str, project_name: str):
    if "plan" not in session:
        plan = condense(session_id[:8], project_name, session["tasks"],
                         validation_warnings=session.get("validation_warnings", []))
        session["plan"] = plan
    return session["plan"]


@app.get("/export/json/{session_id}")
def export_json_endpoint(session_id: str, project_name: str = "Projektplan"):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session nicht gefunden.")

    plan = _get_plan(session, session_id, project_name)
    data = export_json(plan, session["tasks"])
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="projektplan-{session_id[:8]}.json"'},
    )


@app.get("/export/excel/{session_id}")
def export_excel_endpoint(session_id: str, project_name: str = "Projektplan"):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session nicht gefunden.")

    _get_plan(session, session_id, project_name)
    data = export_excel(session["tasks"])
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="projektplan-{session_id[:8]}.xlsx"'},
    )


@app.get("/export/csv/{session_id}")
def export_csv_endpoint(session_id: str, project_name: str = "Projektplan"):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session nicht gefunden.")

    _get_plan(session, session_id, project_name)
    data = export_csv(session["tasks"])
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="projektplan-{session_id[:8]}.csv"'},
    )


@app.delete("/session/{session_id}")
def delete_session(session_id: str):
    _sessions.pop(session_id, None)
    return {"deleted": session_id}
