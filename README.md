# Newspaper Antisemitism Risk Analyzer

An AI-powered web application for analyzing Spanish newspaper PDFs and generating antisemitism framing risk ratings using an IHRA-informed methodology.

## Features

- **PDF Upload**: Drag-and-drop one or more newspaper PDFs (El Mundo, La Razón, El País, ABC)
- **AI Analysis**: Claude AI scores each of 9 antisemitism framing categories
- **Dashboard**: Summary cards, score matrix, ranking table, and evidence browser
- **Historical Trends**: Line chart tracking all newspapers over time
- **Reports**: Download XLSX, CSV, and JSON exports for all analyses
- **Database**: Persistent SQLite storage with upsert (no duplicates)

## Quick Start

### Option 1: Local Python

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install system dependencies (macOS)
brew install tesseract tesseract-lang poppler

# 3. Install system dependencies (Ubuntu/Debian)
sudo apt-get install tesseract-ocr tesseract-ocr-spa poppler-utils

# 4. Start the server
python run.py

# 5. Open http://localhost:8000
```

### Option 2: Docker

```bash
docker-compose up --build
# Open http://localhost:8000
```

## PDF Naming Convention

Files **must** follow this exact format:
```
DD-MM-YY-Newspaper.pdf
```

Examples:
- `03-06-26-El Mundo.pdf`
- `03-06-26-La Razón.pdf`
- `03-06-26-El País.pdf`
- `03-06-26-ABC.pdf`

## Methodology

The system implements the **Replicable IHRA-Informed Methodology for Evaluating Antisemitic Framing Risk in Media** with 9 weighted categories:

| Category | Weight | Description |
|---|---|---|
| Collective Blame | 20% | Blaming Jews collectively for Israeli conduct |
| Conspiracy Tropes | 20% | Claims of hidden Jewish/Zionist control |
| Holocaust Inversion | 15% | Equating Israel/Jews with Nazis |
| Demonisation | 10% | Portraying Israel as uniquely evil |
| Denial of Jewish Self-Determination | 10% | Rejecting Jewish national legitimacy |
| Double Standards | 10% | Selective exceptionalism applied to Israel |
| Dehumanisation | 5% | Subhuman language for Israelis/Jews |
| Asymmetric Empathy | 5% | Erasing Israeli/Jewish civilian suffering |
| Contextual Omission | 5% | Omitting Hamas, hostages, security context |

**Overall Score Formula:**
```
FRS = (CB×0.20) + (CT×0.20) + (HI×0.15) + (D×0.10) + (DSD×0.10) + (DS×0.10) + (DH×0.05) + (AE×0.05) + (CO×0.05)
```

Calibrated against the 19 May 2026 reference editions:
- El País: 2.3/5 · El Mundo: 1.6/5 · La Razón: 1.1/5 · ABC: 0.8/5

## Generated Reports

| Report | Formats | Filename |
|---|---|---|
| Per-newspaper analysis | JSON | `{Newspaper}_{Date}.json` |
| Daily dataset | XLSX | `Daily_Antisemitism_Risk_Dataset_{Date}.xlsx` |
| Risk ranking | XLSX, CSV | `Spanish_Newspapers_Antisemitic_Framing_Risk_Ranking.*` |
| Evidence table | XLSX, CSV, JSON | `Expanded_Evidence_Table_With_Pages_and_Articles.*` |

## Architecture

```
antisemitism-app/
├── app/
│   ├── main.py          # FastAPI app + all API routes
│   ├── analyzer.py      # Claude AI analysis engine
│   ├── database.py      # SQLAlchemy models + CRUD
│   ├── pdf_extractor.py # Text extraction + OCR fallback
│   └── report_gen.py    # XLSX / CSV / JSON generation
├── static/
│   └── index.html       # Single-page frontend
├── data/                # SQLite database (auto-created)
├── .env                 # API key and settings
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── run.py               # Launch script
```

## Configuration

Edit `.env` to change settings:

```env
ANTHROPIC_API_KEY=your-key-here
DATABASE_URL=sqlite:///./data/antisemitism.db
CLAUDE_MODEL=claude-opus-4-8
```

## Security

- API key is stored server-side in `.env` only — never exposed to the browser
- All analysis runs server-side through the FastAPI backend
- CORS configured for local development (restrict in production)

## Traceability

Every analysis record stores:
- Source PDF filename
- Processing timestamp
- Methodology version (1.0)
- Framework references (IHRA, Entman 1993, van Dijk 1991)
- Per-category evidence with confidence levels

## Important Caveat

This tool evaluates **framing-risk indicators**, not proven antisemitic intent. Human review is essential. The framework distinguishes legitimate criticism of Israeli government policy from discriminatory framing or classic antisemitic tropes.
