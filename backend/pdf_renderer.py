import math
import io
from datetime import date, timedelta

from reportlab.lib.pagesizes import A0, A1, A2, A3, A4, A5, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether,
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.graphics.shapes import Drawing, Rect, Polygon, String
from reportlab.graphics import renderPDF

from models import CondensedPlan, Phase, Task, TaskStatus, RiskLevel, TrafficLight, GateStatus
from condensator import get_phase_completion

# ── Paper sizes ────────────────────────────────────────────────────────────────
PAPER_SIZES = {"a0": A0, "a1": A1, "a2": A2, "a3": A3, "a4": A4, "a5": A5}

def _scale(target, base=A4) -> float:
    """Linear scale factor relative to a base page size."""
    return math.sqrt((target[0] * target[1]) / (base[0] * base[1]))

# ── ASIS brand colours ─────────────────────────────────────────────────────────
C_BRAND  = colors.HexColor("#8B1C2C")
C_DARK   = colors.HexColor("#8B1C2C")
C_LIGHT  = colors.HexColor("#fdf2f4")
C_GREEN  = colors.HexColor("#16a34a")
C_YELLOW = colors.HexColor("#d97706")
C_RED    = colors.HexColor("#dc2626")
C_GREY   = colors.HexColor("#6b7280")
C_BORDER = colors.HexColor("#e5e7eb")
C_WHITE  = colors.white

PHASE_BAR_COLORS = [
    colors.HexColor("#8B1C2C"), colors.HexColor("#7c3aed"),
    colors.HexColor("#0891b2"), colors.HexColor("#059669"),
    colors.HexColor("#d97706"), colors.HexColor("#dc2626"),
]

STATUS_COLORS = {
    TaskStatus.done.value:        C_GREEN,
    TaskStatus.in_progress.value: C_BRAND,
    TaskStatus.blocked.value:     C_RED,
    TaskStatus.not_started.value: C_GREY,
    TaskStatus.cancelled.value:   colors.HexColor("#d1d5db"),
}

TRAFFIC_LIGHT_COLORS = {
    TrafficLight.green:  "#16a34a",
    TrafficLight.yellow: "#d97706",
    TrafficLight.red:    "#dc2626",
}

# ── Style factory (font sizes scale with paper) ────────────────────────────────
def _styles(sc: float = 1.0) -> dict:
    def sz(n):
        return max(5, round(n * sc * 2) / 2)   # round to nearest 0.5
    def sp(n):
        return max(2, round(n * sc))            # spacing values scale with paper

    return {
        "title":    ParagraphStyle("title",    fontName="Helvetica-Bold",  fontSize=sz(16), leading=sz(21), textColor=C_DARK,  spaceAfter=sp(6)),
        "subtitle": ParagraphStyle("subtitle", fontName="Helvetica",       fontSize=sz(9),  leading=sz(13), textColor=C_GREY,  spaceAfter=sp(8)),
        "section":  ParagraphStyle("section",  fontName="Helvetica-Bold",  fontSize=sz(10), leading=sz(13), textColor=C_DARK,  spaceBefore=sp(8), spaceAfter=sp(4)),
        "body":     ParagraphStyle("body",     fontName="Helvetica",       fontSize=sz(8),  textColor=C_DARK,  leading=sz(11)),
        "small":    ParagraphStyle("small",    fontName="Helvetica",       fontSize=sz(7),  textColor=C_GREY,  leading=sz(9)),
        "footer":   ParagraphStyle("footer",   fontName="Helvetica",       fontSize=sz(7),  leading=sz(9),  textColor=C_GREY,  alignment=TA_CENTER),
        "ms":       ParagraphStyle("ms",       fontName="Helvetica",       fontSize=sz(7),  leading=sz(9),  textColor=C_GREY,  alignment=TA_CENTER),
    }

# ── Helpers ────────────────────────────────────────────────────────────────────
def _risk_badge(risk_val: str | None) -> str:
    if not risk_val:
        return ""
    color_map = {"kritisch": "#7f1d1d", "hoch": "#dc2626", "mittel": "#d97706", "niedrig": "#16a34a"}
    bg = color_map.get(risk_val, "#6b7280")
    return f'<font color="{bg}"><b>▲ {risk_val.upper()}</b></font>'

def _status_dot(status_val: str) -> str:
    color_map = {
        TaskStatus.done.value:        "#16a34a",
        TaskStatus.in_progress.value: "#8B1C2C",
        TaskStatus.blocked.value:     "#dc2626",
        TaskStatus.not_started.value: "#6b7280",
        TaskStatus.cancelled.value:   "#d1d5db",
    }
    c = color_map.get(status_val, "#6b7280")
    return f'<font color="{c}">●</font>'

def _traffic_light_dot(tl: TrafficLight | None) -> str:
    """Ampel-Punkt (●) in Grün/Amber/Rot — bewusst kein ⚠ (U+26A0), das in
    Helvetica/WinAnsi als schwarzer Kasten gerendert wird."""
    if tl is None:
        return ""
    return f'<font color="{TRAFFIC_LIGHT_COLORS[tl]}">●</font>'

def _get_months(start: date, end: date):
    months, cur = [], start.replace(day=1)
    while cur <= end:
        months.append((cur, cur.strftime("%b %Y")))
        cur = cur.replace(month=cur.month + 1) if cur.month < 12 else cur.replace(year=cur.year + 1, month=1)
    return months

def _month_end(m_start: date) -> date:
    if m_start.month == 12:
        return m_start.replace(year=m_start.year + 1, month=1, day=1) - timedelta(days=1)
    return m_start.replace(month=m_start.month + 1, day=1) - timedelta(days=1)

def _gantt_bar(task_start: date, task_end: date,
               month_start: date, month_end: date,
               bar_color, cell_w: float, bar_h: float = 12) -> Drawing | str:
    """Clean vector rectangle for a GANTT bar — no character artifacts."""
    overlap_start = max(task_start, month_start)
    overlap_end   = min(task_end,   month_end)
    if overlap_start > overlap_end:
        return ""

    month_days = (month_end   - month_start).days + 1
    left_frac  = (overlap_start - month_start).days / month_days
    width_frac = ((overlap_end - overlap_start).days + 1) / month_days

    cell_h = bar_h + 8
    d = Drawing(cell_w, cell_h)
    x = left_frac  * cell_w
    w = max(width_frac * cell_w, 2)
    d.add(Rect(x, 4, w, bar_h, fillColor=bar_color, strokeColor=None, strokeWidth=0))
    return d


def _marker_label_size(rad: float, label: str) -> float:
    """Schriftgröße für eine Zahl innerhalb eines Diamanten/Quadrats — etwas
    kleiner bei zweistelligen Nummern, damit sie noch hineinpassen."""
    fsize = max(4, rad * 0.95)
    if len(label) > 1:
        fsize = max(3.5, fsize * 0.7)
    return fsize


def _milestone_diamond(cx: float, cy: float, rad: float, label: str = "") -> list:
    """Vektor-Raute für einen Meilenstein, optional mit weißer Nummer (z.B. "2")."""
    shapes = [Polygon(points=[cx, cy + rad, cx + rad, cy, cx, cy - rad, cx - rad, cy],
                       fillColor=C_BRAND, strokeColor=C_WHITE, strokeWidth=0.5)]
    if label:
        shapes.append(String(cx, cy - _marker_label_size(rad, label) * 0.35, label,
                              fontName="Helvetica-Bold", fontSize=_marker_label_size(rad, label),
                              fillColor=C_WHITE, textAnchor="middle"))
    return shapes


def _gate_square(cx: float, cy: float, rad: float, label: str = "") -> list:
    """Vektor-Quadrat für ein Gate, optional mit weißer Nummer (z.B. "1") —
    unterscheidet sich bewusst von der Meilenstein-Raute (Form statt Farbe)."""
    shapes = [Rect(cx - rad, cy - rad, rad * 2, rad * 2,
                    fillColor=C_DARK, strokeColor=C_WHITE, strokeWidth=0.5)]
    if label:
        shapes.append(String(cx, cy - _marker_label_size(rad, label) * 0.35, label,
                              fontName="Helvetica-Bold", fontSize=_marker_label_size(rad, label),
                              fillColor=C_WHITE, textAnchor="middle"))
    return shapes


def _gantt_cell(tasks: list[Task], bar_start: date, bar_end: date,
                month_start: date, month_end: date,
                bar_color, cell_w: float, bar_h: float = 12,
                gate_numbers: dict[str, int] | None = None,
                milestone_numbers: set[int] | None = None) -> Drawing | str:
    """Aggregate-Balken (bar_start–bar_end) plus Meilenstein-Diamanten/Gate-Quadrate
    (mit Nummer) der übergebenen `tasks` in dieser Monatszelle — alles in einer Drawing."""
    gate_numbers = gate_numbers or {}
    month_days = (month_end - month_start).days + 1

    elements = []
    overlap_start = max(bar_start, month_start)
    overlap_end   = min(bar_end,   month_end)
    if overlap_start <= overlap_end:
        left_frac  = (overlap_start - month_start).days / month_days
        width_frac = ((overlap_end - overlap_start).days + 1) / month_days
        x = left_frac  * cell_w
        w = max(width_frac * cell_w, 2)
        elements.append(Rect(x, 4, w, bar_h, fillColor=bar_color, strokeColor=None, strokeWidth=0))

    # (cx, ist_gate, Beschriftung) — Gate hat Vorrang vor Meilenstein-Diamant
    markers: list[tuple[float, bool, str]] = []
    for t in tasks:
        if not (month_start <= t.end_date <= month_end):
            continue
        cx = ((t.end_date - month_start).days + 0.5) / month_days * cell_w
        if t.is_gate:
            markers.append((cx, True, str(gate_numbers.get(t.task_id, ""))))
        elif t.milestone:
            label = t.milestone_number if (milestone_numbers is None or t.milestone_number in milestone_numbers) else None
            markers.append((cx, False, str(label or "")))

    if not elements and not markers:
        return ""

    cell_h = bar_h + 8
    d = Drawing(cell_w, cell_h)
    for el in elements:
        d.add(el)
    rad = bar_h * 0.6
    cy  = 4 + bar_h / 2
    for cx, is_gate, label in markers:
        shapes = _gate_square(cx, cy, rad, label) if is_gate else _milestone_diamond(cx, cy, rad, label)
        for shp in shapes:
            d.add(shp)
    return d


def _marker_cell(t: Task, gate_numbers: dict[str, int],
                 month_start: date, month_end: date,
                 cell_w: float, bar_h: float = 12,
                 milestone_numbers: set[int] | None = None) -> Drawing | str:
    """Meilenstein-Diamant bzw. Gate-Quadrat (mit Nummer) für genau einen Vorgang —
    ohne Balken; für Detailzeilen in `render_a3_gantt`."""
    if not (month_start <= t.end_date <= month_end):
        return ""
    month_days = (month_end - month_start).days + 1
    cx = ((t.end_date - month_start).days + 0.5) / month_days * cell_w
    rad = bar_h * 0.6
    cy  = 4 + bar_h / 2
    d = Drawing(cell_w, bar_h + 8)
    if t.is_gate:
        shapes = _gate_square(cx, cy, rad, str(gate_numbers.get(t.task_id, "")))
    else:
        label = t.milestone_number if (milestone_numbers is None or t.milestone_number in milestone_numbers) else None
        shapes = _milestone_diamond(cx, cy, rad, str(label or ""))
    for shp in shapes:
        d.add(shp)
    return d

# ── A4/Executive-Summary renderer ─────────────────────────────────────────────
def render_a4_executive(plan: CondensedPlan, source_file: str,
                        paper_size: str = "a4") -> bytes:
    size = PAPER_SIZES.get(paper_size, A4)
    sc   = _scale(size, A4)
    margin = 18 * mm * max(sc, 0.7)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=size,
                            leftMargin=margin, rightMargin=margin,
                            topMargin=margin,  bottomMargin=margin)
    s = _styles(sc)
    avail_w = size[0] - 2 * margin   # total usable width — all tables must fit within this
    story = []

    # Header
    story.append(Paragraph(f"Executive Summary: {plan.project_name}", s["title"]))
    story.append(Paragraph(
        f"Erstellt: {date.today().strftime('%d.%m.%Y')} · Quelle: {source_file} · "
        f"Zeitraum: {plan.overall_start.strftime('%d.%m.%Y')} – {plan.overall_end.strftime('%d.%m.%Y')} · "
        f"{plan.total_tasks} Vorgänge in {len(plan.phases)} Phasen",
        s["subtitle"],
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=C_BORDER))

    # Status KPIs
    all_tasks = [t for p in plan.phases for t in p.tasks]
    done_c  = sum(1 for t in all_tasks if t.status == TaskStatus.done)
    prog_c  = sum(1 for t in all_tasks if t.status == TaskStatus.in_progress)
    block_c = len(plan.blocked_tasks)
    over_c  = len(plan.overdue_tasks)
    big = round(18 * sc)

    kpi_data = [[
        Paragraph(f"<font size='{big}'><b>{done_c}</b></font><br/><font size='7' color='#6b7280'>Erledigt</font>",    s["body"]),
        Paragraph(f"<font size='{big}'><b>{prog_c}</b></font><br/><font size='7' color='#6b7280'>In Arbeit</font>",   s["body"]),
        Paragraph(f"<font size='{big}' color='#dc2626'><b>{block_c}</b></font><br/><font size='7' color='#6b7280'>Blockiert</font>",  s["body"]),
        Paragraph(f"<font size='{big}' color='#d97706'><b>{over_c}</b></font><br/><font size='7' color='#6b7280'>Überfällig</font>",  s["body"]),
    ]]
    kpi_t = Table(kpi_data, colWidths=[avail_w * 0.25] * 4)
    kpi_t.setStyle(TableStyle([
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT),
        ("BOX",           (0, 0), (-1, -1), 0.5, C_BORDER),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, max(4, round(8 * sc))))
    story.append(kpi_t)

    # Planqualität & Bewertung
    if plan.quality is not None and plan.pm_assessment is not None:
        pm = plan.pm_assessment
        story.append(Paragraph("Planqualität & Bewertung", s["section"]))

        delay = plan.overall_delay_days or 0
        if delay > 0:
            delay_text = f'<font color="#dc2626">+{delay} Tage</font>'
        elif delay < 0:
            delay_text = f'<font color="#16a34a">{delay} Tage</font>'
        else:
            delay_text = "± 0 Tage"
        forecast_text = (
            f"{pm.end_date_forecast_text}<br/>"
            f"Plan-Ende: {plan.overall_end.strftime('%d.%m.%Y')} · "
            f"Forecast: {(plan.overall_forecast_end or plan.overall_end).strftime('%d.%m.%Y')} · "
            f"Abweichung: {delay_text}"
        )
        score_text = (
            f"{_traffic_light_dot(plan.overall_traffic_light)} "
            f"<font size='{big}'><b>{plan.quality.score}</b></font>/100<br/>"
            f"<b>{plan.quality.rating}</b>"
        )
        qual_data = [[
            Paragraph(score_text, s["body"]),
            Paragraph(forecast_text, s["body"]),
        ]]
        qual_t = Table(qual_data, colWidths=[avail_w * 0.28, avail_w * 0.72])
        qual_t.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND",    (0, 0), (-1, -1), C_LIGHT),
            ("BOX",           (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("ALIGN",         (0, 0), (0, -1),  "CENTER"),
        ]))
        story.append(qual_t)

        if pm.notable_issues:
            story.append(Paragraph("Auffälligkeiten", s["section"]))
            for issue in pm.notable_issues[:5]:
                story.append(Paragraph(f"• {issue}", s["body"]))

        if pm.recommendations:
            story.append(Paragraph("Empfohlene Maßnahmen", s["section"]))
            for rec in pm.recommendations[:3]:
                story.append(Paragraph(f"• {rec}", s["body"]))

        if pm.critical_points or pm.open_decisions:
            cols = []
            for title, items in (("Kritische Punkte", pm.critical_points),
                                  ("Offene Entscheidungen", pm.open_decisions)):
                if items:
                    text = f"<b>{title}</b><br/>" + "<br/>".join(f"• {i}" for i in items[:3])
                else:
                    text = f"<b>{title}</b><br/>–"
                cols.append(Paragraph(text, s["small"]))
            cp_t = Table([cols], colWidths=[avail_w * 0.5] * 2)
            cp_t.setStyle(TableStyle([
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("BOX",           (0, 0), (-1, -1), 0.5, C_BORDER),
                ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_BORDER),
                ("TOPPADDING",    (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ]))
            story.append(Spacer(1, max(2, round(4 * sc))))
            story.append(cp_t)

        if plan.quality.data_basis_warnings:
            hint_lines = "<br/>".join(f"• {w.message}" for w in plan.quality.data_basis_warnings)
            story.append(Spacer(1, max(2, round(4 * sc))))
            story.append(Paragraph(f"<b>Hinweis zur Datenbasis:</b><br/>{hint_lines}", s["small"]))

    # Phasen
    phasen_block = [Paragraph("Projektphasen", s["section"])]
    rows = [["Ampel", "Phase", "Von", "Bis", "Aufg.", "Fort.", "Verantwortlich"]]
    for p in plan.phases:
        pct = get_phase_completion(p)
        rows.append([
            Paragraph(_traffic_light_dot(p.traffic_light), s["body"]),
            Paragraph(p.name, s["body"]),
            p.start_date.strftime("%d.%m.%y"),
            p.end_date.strftime("%d.%m.%y"),
            str(p.task_count),
            f"{pct}%",
            Paragraph(", ".join(p.owners[:3]), s["small"]),
        ])
    pt = Table(rows, colWidths=[
        avail_w * 0.05,  # Ampel
        avail_w * 0.29,  # Phase
        avail_w * 0.11,  # Von
        avail_w * 0.11,  # Bis
        avail_w * 0.08,  # Aufg.
        avail_w * 0.08,  # Fort.
        avail_w * 0.28,  # Verantwortlich
    ])
    pt.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), s["body"].fontSize),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_LIGHT]),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("ALIGN",         (1, 0), (1, -1),  "LEFT"),
        ("ALIGN",         (6, 0), (6, -1),  "LEFT"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (1, 0), (1, -1),  6),
        ("LEFTPADDING",   (6, 0), (6, -1),  6),
        ("WORDWRAP",      (0, 0), (-1, -1), True),
    ]))
    phasen_block.append(pt)
    story.append(KeepTogether(phasen_block))

    # Meilensteine
    if plan.next_milestones:
        ms_block = [Paragraph("Nächste Meilensteine", s["section"])]
        ms_rows = [["Nr.", "Meilenstein", "Termin", "Verantwortlich", "Status"]]
        for t in plan.next_milestones[:6]:
            flag = ' <font color="#d97706">▲</font>' if (t.end_date - date.today()).days < 14 else ""
            nr = f"M{t.milestone_number}" if t.milestone_number else ""
            ms_rows.append([
                Paragraph(f"<b>{nr}</b>", s["body"]),
                Paragraph(t.task_name + flag, s["body"]),
                t.end_date.strftime("%d.%m.%Y"),
                Paragraph(t.owner, s["small"]),
                Paragraph(_status_dot(t.status.value) + " " + t.status.value, s["body"]),
            ])
        ms_t = Table(ms_rows, colWidths=[
            avail_w * 0.08,  # Nr.
            avail_w * 0.36,  # Meilenstein
            avail_w * 0.15,  # Termin
            avail_w * 0.25,  # Verantwortlich
            avail_w * 0.16,  # Status
        ])
        ms_t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_BRAND),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), s["body"].fontSize),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_LIGHT]),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
            ("ALIGN",         (0, 0), (0, -1),  "CENTER"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("WORDWRAP",      (0, 0), (-1, -1), True),
        ]))
        ms_block.append(ms_t)
        story.append(KeepTogether(ms_block))

    # Risiken
    if plan.open_risks:
        risk_block = [Paragraph("Top-Risiken", s["section"])]
        risk_rows = [["Aufgabe", "Phase", "Risiko", "Termin", "Verantw."]]
        for t in plan.open_risks[:8]:
            risk_rows.append([
                Paragraph(t.task_name, s["body"]),
                Paragraph(t.phase, s["small"]),
                Paragraph(_risk_badge(t.risk_level.value if t.risk_level else None), s["body"]),
                t.end_date.strftime("%d.%m.%Y"),
                Paragraph(t.owner, s["small"]),
            ])
        rt = Table(risk_rows, colWidths=[
            avail_w * 0.38,  # Aufgabe
            avail_w * 0.22,  # Phase
            avail_w * 0.14,  # Risiko
            avail_w * 0.13,  # Termin
            avail_w * 0.13,  # Verantw.
        ])
        rt.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#7f1d1d")),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), s["body"].fontSize),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, colors.HexColor("#fef2f2")]),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("WORDWRAP",      (0, 0), (-1, -1), True),
        ]))
        risk_block.append(rt)
        story.append(KeepTogether(risk_block))

    # Blockierungen
    if plan.blocked_tasks:
        bl_block = [Paragraph("Blockierte Vorgänge – Handlungsbedarf", s["section"])]
        bl_rows = [["Aufgabe", "Phase", "Verantwortlich", "Fällig"]]
        for t in plan.blocked_tasks[:8]:
            bl_rows.append([
                Paragraph(t.task_name, s["body"]),
                Paragraph(t.phase, s["small"]),
                Paragraph(t.owner, s["small"]),
                t.end_date.strftime("%d.%m.%Y"),
            ])
        bl_t = Table(bl_rows, colWidths=[
            avail_w * 0.44,  # Aufgabe
            avail_w * 0.22,  # Phase
            avail_w * 0.22,  # Verantwortlich
            avail_w * 0.12,  # Fällig
        ])
        bl_t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_RED),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), s["body"].fontSize),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, colors.HexColor("#fef2f2")]),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("WORDWRAP",      (0, 0), (-1, -1), True),
        ]))
        bl_block.append(bl_t)
        story.append(KeepTogether(bl_block))

    story.append(Spacer(1, max(6, round(12 * sc))))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, max(2, round(4 * sc))))
    story.append(Paragraph(
        f"GANTT Print Assistant · ASIS · Quelle: {source_file} · Stand: {date.today().strftime('%d.%m.%Y')} · "
        f"Papier: {paper_size.upper()} · KI-generierte Verdichtung",
        s["footer"],
    ))

    doc.build(story)
    return buf.getvalue()


# ── Phasenplan / GANTT renderer ────────────────────────────────────────────────
def _month_label(m_start: date, col_width_pts: float) -> str:
    """Adaptive month label: shorter text for narrow columns."""
    if col_width_pts < 20 * mm:
        return m_start.strftime("%m/%y")   # "01/26"
    if col_width_pts < 35 * mm:
        return m_start.strftime("%b %y")   # "Jan 26"
    return m_start.strftime("%b %Y")       # "Jan 2026"


def _tint(c: colors.Color, factor: float) -> colors.Color:
    """Blend a colour toward white by `factor` (0 = unchanged, 1 = white)."""
    return colors.Color(
        c.red   + (1 - c.red)   * factor,
        c.green + (1 - c.green) * factor,
        c.blue  + (1 - c.blue)  * factor,
    )


def render_a3_gantt(plan: CondensedPlan, source_file: str,
                    paper_size: str = "a3", detail: bool = False) -> bytes:
    size     = PAPER_SIZES.get(paper_size, A3)
    pagesize = landscape(size)
    sc       = _scale(size, A3)          # scale relative to A3 base
    margin   = 15 * mm * max(sc, 0.7)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize,
                            leftMargin=margin, rightMargin=margin,
                            topMargin=12*mm*max(sc,0.7), bottomMargin=12*mm*max(sc,0.7))
    # Cap font scale at 2.0 for text on very large formats (A0/A1):
    # A0/A1 use A1-equivalent font sizes — still large and readable, not oversized.
    # Layout (bar sizes, column widths, padding) still uses the full sc.
    sc_s = min(sc, 2.0)
    s = _styles(sc_s)
    story = []

    detail_note = " · Detailansicht: alle Vorgänge" if detail else ""
    story.append(Paragraph(f"Phasenplan: {plan.project_name}", s["title"]))
    story.append(Paragraph(
        f"Erstellt: {date.today().strftime('%d.%m.%Y')} · Zeitraum: "
        f"{plan.overall_start.strftime('%d.%m.%Y')} – {plan.overall_end.strftime('%d.%m.%Y')} · "
        f"{len(plan.phases)} Phasen · {plan.total_tasks} Vorgänge · Papier: {paper_size.upper()} quer{detail_note}",
        s["subtitle"],
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=C_BORDER))
    story.append(Spacer(1, max(4, round(6 * sc))))

    months     = _get_months(plan.overall_start, plan.overall_end)
    page_w     = pagesize[0] - 2 * margin
    # Label column: 22% (28% in der Detailansicht, wegen der zusätzlichen
    # Steuerungsinfo-Zeile je Vorgang) der verfügbaren Breite (adaptiert sich
    # an alle Papiergrößen)
    label_col  = page_w * (0.28 if detail else 0.22)
    gantt_w    = page_w - label_col

    cpm_computable = plan.critical_path is not None and plan.critical_path.computable
    gates_sorted = sorted(plan.gates, key=lambda t: t.end_date)
    gate_numbers = {t.task_id: i for i, t in enumerate(gates_sorted, start=1)}
    # Diamanten erhalten nur dann eine Nummer, wenn diese auch in der
    # Meilenstein-Legende erklärt wird (Legende ist auf ~8 Einträge begrenzt).
    milestone_numbers = {m.number for m in plan.milestone_legend}
    month_w    = gantt_w / len(months) if months else gantt_w
    col_widths = [label_col] + [month_w] * len(months)
    phase_bar_h = max(9, round(11 * sc))
    task_bar_h  = max(6, round(8 * sc))
    # Cell padding scales with paper size
    cell_pad   = max(1, round(3 * min(sc, 1.5)))

    # Header row — Paragraphs need explicit textColor; TEXTCOLOR in TableStyle only affects plain strings
    hdr_style  = ParagraphStyle("hdr_label", fontName="Helvetica-Bold",
                                fontSize=s["body"].fontSize, leading=s["body"].leading,
                                textColor=C_WHITE)
    hdr_center = ParagraphStyle("hdr_month", fontName="Helvetica-Bold",
                                fontSize=s["body"].fontSize, leading=s["body"].leading,
                                alignment=TA_CENTER, textColor=C_WHITE)
    task_style = ParagraphStyle("task_label", fontName="Helvetica",
                                fontSize=s["small"].fontSize, leading=s["small"].leading,
                                textColor=C_DARK)

    header = [Paragraph("<b>Phase / Vorgang</b>", hdr_style)]
    for m_start, _ in months:
        label = _month_label(m_start, month_w)
        header.append(Paragraph(f"<b>{label}</b>", hdr_center))

    # Eine durchgehende Tabelle (Phasen + Einzelvorgänge), Monatsköpfe
    # wiederholen sich automatisch auf jeder Seite (repeatRows).
    data = [header]
    style_cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), cell_pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), cell_pad),
        ("LEFTPADDING",   (0, 0), (0, -1),  6),
        ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]

    r = 1  # laufender Zeilenindex in `data` (Zeile 0 = Monatskopf)
    for i, phase in enumerate(plan.phases):
        bar_color = PHASE_BAR_COLORS[i % len(PHASE_BAR_COLORS)]
        tint_head = _tint(bar_color, 0.82)
        tint_row  = _tint(bar_color, 0.93)
        pct       = get_phase_completion(phase)

        phase_crit = sum(1 for t in phase.tasks if t.critical_path_flag) if cpm_computable else 0
        crit_marker = (
            f' <font color="#dc2626">▲</font> '
            f"<font size='{s['small'].fontSize}' color='#6b7280'>({phase_crit} kritische Vorgänge)</font>"
        ) if phase_crit > 0 else ""

        phase_row = [Paragraph(
            f"{_traffic_light_dot(phase.traffic_light)} <b>{phase.name}</b> "
            f"<font size='{s['small'].fontSize}' color='#6b7280'>"
            f"({phase.task_count} Aufg. · {pct}%)</font>"
            f"{crit_marker}",
            s["body"],
        )]
        for m_start, _ in months:
            m_end = _month_end(m_start)
            phase_row.append(_gantt_cell(phase.tasks, phase.start_date, phase.end_date,
                                         m_start, m_end, bar_color, month_w, phase_bar_h,
                                         gate_numbers, milestone_numbers))
        data.append(phase_row)
        style_cmds.append(("BACKGROUND", (0, r), (-1, r), tint_head))
        r += 1

        if detail:
            # Jeder Einzelvorgang als eigene Zeile, chronologisch — mit zweiter,
            # kleiner grauer Zeile mit Steuerungsinfos (Verantwortlicher, Ampel,
            # Puffer, Vorgänger, Risiko, kritischer Pfad, Gate).
            for t in sorted(phase.tasks, key=lambda x: x.start_date):
                extra = []
                if t.owner and t.owner != "Nicht zugewiesen":
                    extra.append(t.owner)
                if t.traffic_light:
                    extra.append(_traffic_light_dot(t.traffic_light))
                if cpm_computable and t.buffer_days is not None:
                    extra.append(f"Puffer: {t.buffer_days} Tg.")
                if t.predecessor_links:
                    extra.append("Vorg.: " + ", ".join(l.task_id for l in t.predecessor_links))
                if t.risk_level:
                    extra.append(_risk_badge(t.risk_level.value))
                if cpm_computable and t.critical_path_flag:
                    extra.append('<font color="#dc2626"><b>KRITISCH</b></font>')
                if t.is_gate and t.gate_name:
                    extra.append(f"Gate: {t.gate_name}")

                extra_line = (
                    f"<br/><font size='{s['small'].fontSize}' color='#6b7280'>"
                    + " · ".join(extra) + "</font>"
                ) if extra else ""

                row = [Paragraph(f"{_status_dot(t.status.value)} {t.task_name}{extra_line}", task_style)]
                for m_start, _ in months:
                    m_end = _month_end(m_start)
                    if t.milestone or t.is_gate:
                        row.append(_marker_cell(t, gate_numbers, m_start, m_end, month_w, task_bar_h,
                                                 milestone_numbers))
                    else:
                        row.append(_gantt_bar(t.start_date, t.end_date,
                                              m_start, m_end,
                                              bar_color, month_w, task_bar_h))
                data.append(row)
                style_cmds.append(("BACKGROUND", (0, r), (-1, r), tint_row))
                r += 1
        else:
            # Unterphasen (eine Gliederungsebene unter der Phase) als Sammelzeilen
            # — hält den Plan auf 1 Seite, statt jeden Einzelvorgang aufzulisten.
            groups: dict[str, list[Task]] = {}
            for t in phase.tasks:
                groups.setdefault(t.sub_phase, []).append(t)

            if len(groups) > 1:
                group_items = []
                for g_name, g_tasks in groups.items():
                    g_start = min(t.start_date for t in g_tasks)
                    g_end   = max(t.end_date for t in g_tasks)
                    group_items.append((g_name, g_tasks, g_start, g_end))
                group_items.sort(key=lambda x: x[2])

                for g_name, g_tasks, g_start, g_end in group_items:
                    done  = sum(1 for t in g_tasks if t.status == TaskStatus.done)
                    g_pct = int(done / len(g_tasks) * 100) if g_tasks else 0
                    g_crit = sum(1 for t in g_tasks if t.critical_path_flag) if cpm_computable else 0
                    g_crit_marker = (
                        f' <font color="#dc2626">▲</font> '
                        f"<font size='{s['small'].fontSize}' color='#6b7280'>({g_crit} kritische Vorgänge)</font>"
                    ) if g_crit > 0 else ""
                    sub_row = [Paragraph(
                        f"{g_name} "
                        f"<font size='{s['small'].fontSize}' color='#6b7280'>"
                        f"({len(g_tasks)} Aufg. · {g_pct}%)</font>"
                        f"{g_crit_marker}",
                        task_style,
                    )]
                    for m_start, _ in months:
                        m_end = _month_end(m_start)
                        sub_row.append(_gantt_cell(g_tasks, g_start, g_end,
                                                   m_start, m_end, bar_color, month_w, task_bar_h,
                                                   gate_numbers, milestone_numbers))
                    data.append(sub_row)
                    style_cmds.append(("BACKGROUND", (0, r), (-1, r), tint_row))
                    r += 1

    gantt_table = Table(data, colWidths=col_widths, repeatRows=1)
    gantt_table.setStyle(TableStyle(style_cmds))
    story.append(gantt_table)

    # Legenden für Meilenstein-/Gate-Nummerierung (nur Phasenplan — in der
    # Detailansicht stehen die Namen bereits in der Vorgangszeile).
    if not detail:
        if plan.milestone_legend:
            legend = "  ·  ".join(
                f"<b>M{m.number}</b> = {m.name} ({m.date.strftime('%d.%m.%Y')})"
                for m in plan.milestone_legend
            )
            story.append(Spacer(1, max(2, round(3 * sc))))
            story.append(Paragraph(f"◆ Meilensteine:  {legend}", s["small"]))

        if gates_sorted:
            legend = "  ·  ".join(
                f"<b>G{i}</b> = {g.gate_name or g.task_name}"
                for i, g in enumerate(gates_sorted, start=1)
            )
            story.append(Spacer(1, max(1, round(2 * sc))))
            story.append(Paragraph(f"■ Gates:  {legend}", s["small"]))

        # Kompaktes Band: Top-Risiken / Offene Entscheidungen / Gesamtstatus
        risk_items = [t.task_name for t in plan.open_risks[:3]]
        decisions = plan.pm_assessment.open_decisions[:3] if plan.pm_assessment else []

        status_lines = []
        if plan.overall_traffic_light:
            status_lines.append(
                f"{_traffic_light_dot(plan.overall_traffic_light)} "
                f"Ampel: {plan.overall_traffic_light.value}"
            )
        if plan.quality:
            status_lines.append(f"Planqualität: {plan.quality.score}/100 ({plan.quality.rating})")
        if cpm_computable:
            status_lines.append(f"Kritischer Pfad: {len(plan.critical_path.task_ids)} Vorgänge")
        elif plan.critical_path:
            status_lines.append("Kritischer Pfad: nicht berechenbar")

        band_data = [[
            Paragraph("<b>Top-Risiken</b><br/>" +
                      ("<br/>".join(f"• {x}" for x in risk_items) if risk_items else "–"), s["small"]),
            Paragraph("<b>Offene Entscheidungen</b><br/>" +
                      ("<br/>".join(f"• {x}" for x in decisions) if decisions else "–"), s["small"]),
            Paragraph("<b>Gesamtstatus</b><br/>" + "<br/>".join(status_lines), s["small"]),
        ]]
        band_t = Table(band_data, colWidths=[page_w / 3] * 3)
        band_t.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("BOX",           (0, 0), (-1, -1), 0.5, C_BORDER),
            ("INNERGRID",     (0, 0), (-1, -1), 0.5, C_BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ]))
        story.append(Spacer(1, max(3, round(5 * sc))))
        story.append(band_t)
    else:
        # Detailansicht: kritischer Pfad + Qualitätswarnungen als eigene Abschnitte
        story.append(Spacer(1, max(4, round(8 * sc))))
        story.append(Paragraph("Kritischer Pfad", s["section"]))
        if cpm_computable:
            if plan.critical_path.task_names:
                story.append(Paragraph(" → ".join(plan.critical_path.task_names), s["body"]))
            else:
                story.append(Paragraph(
                    "Kein kritischer Pfad identifiziert (alle Vorgänge mit positivem Puffer).",
                    s["body"]))
        else:
            note = plan.critical_path.note if plan.critical_path else "Kritischer Pfad nicht berechenbar."
            story.append(Paragraph(note, s["body"]))

        if plan.quality and plan.quality.warnings:
            story.append(Paragraph("Qualitätswarnungen", s["section"]))
            shown = plan.quality.warnings[:20]
            for w in shown:
                story.append(Paragraph(f"• {w.message}", s["small"]))
            remaining = len(plan.quality.warnings) - len(shown)
            if remaining > 0:
                story.append(Paragraph(f"… + {remaining} weitere", s["small"]))

    story.append(Spacer(1, max(4, round(8 * sc))))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, max(2, round(3 * sc))))
    story.append(Paragraph(
        f"GANTT Print Assistant · ASIS · Quelle: {source_file} · Stand: {date.today().strftime('%d.%m.%Y')} · "
        f"Papier: {paper_size.upper()} quer",
        s["footer"],
    ))

    doc.build(story)
    return buf.getvalue()
