# GANTT Print Assistant

Webtool zum Importieren von Projektplänen und Erstellen druckfertiger PDF-Auswertungen.

## Features

- **Import** von MS Project XML, Excel (.xlsx/.xls) und CSV
- **Executive Summary (A4)** — Projektstatus, Planqualität, Meilensteine, Risiken und Blockierungen auf einer Seite
- **Phasenplan / GANTT (A3)** — Übersicht aller Phasen mit Balkendiagramm und optionaler Vorgangs-Detailansicht
- **Critical Path Method (CPM)** — automatische Berechnung von Puffer, kritischem Pfad und Verzugsprognose
- **Planqualitäts-Score** — Ampelbewertung mit automatischer PM-Einschätzung
- **Export** nach JSON, Excel und CSV

## Live-Demo

Frontend: [floriangloebl.github.io/gantt-print-assistant](https://floriangloebl.github.io/gantt-print-assistant/)  
Backend API: [gantt-print-assistant-api.onrender.com](https://gantt-print-assistant-api.onrender.com/docs)

## Lokale Installation

**Voraussetzungen:** Python 3.11+

```bash
git clone https://github.com/FlorianGloebl/gantt-print-assistant.git
cd gantt-print-assistant
bash start.sh
```

Das Backend startet auf `http://localhost:8000`, das Frontend öffnest du als Datei im Browser:

```
frontend/index.html
```

## Projektstruktur

```
├── backend/
│   ├── main.py          # FastAPI-Endpunkte
│   ├── parser.py        # Import (XML, Excel, CSV)
│   ├── condensator.py   # Daten-Aggregation
│   ├── cpm.py           # Critical Path Method
│   ├── pdf_renderer.py  # PDF-Generierung (ReportLab)
│   ├── quality.py       # Planqualitäts-Score & Ampel
│   ├── exporters.py     # JSON/Excel/CSV-Export
│   └── dateutils.py     # Arbeitstag-Arithmetik
├── frontend/
│   └── index.html       # Single-Page-App (Alpine.js)
├── requirements.txt
└── start.sh
```

## Tech Stack

| Komponente | Technologie |
|------------|-------------|
| Backend | Python, FastAPI, ReportLab, pandas |
| Frontend | HTML, Alpine.js (kein Build-Schritt) |
| Hosting Backend | Render (Free Tier) |
| Hosting Frontend | GitHub Pages |

## API

Die REST-API ist unter `/docs` vollständig dokumentiert (Swagger UI).

Wichtigste Endpunkte:

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `POST` | `/upload` | Datei hochladen, Session anlegen |
| `GET` | `/condense/{session_id}` | Plan verdichten & auswerten |
| `GET` | `/pdf/a4/{session_id}` | Executive Summary als PDF |
| `GET` | `/pdf/a3/{session_id}` | Phasenplan/GANTT als PDF |
| `GET` | `/export/excel/{session_id}` | Angereicherte Daten als Excel |

## Lizenz

MIT
