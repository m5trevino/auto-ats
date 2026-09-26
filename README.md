# Auto-ATS

Open-source, self-hosted resume tailoring system. Clone it, drop in your master resume, add job postings from any source, and generate targeted resumes + PDFs with ATS scoring.

## What it does

- Scores your master resume against a job description before generation.
- Runs a 6-strike LLM pipeline to tailor your resume to the job.
- Scores the generated resume and shows improvement.
- Generates a PDF automatically.
- Supports manual entry, bulk JSON import, file upload, and browser userscripts (e.g. Indeed).

## Quick start

```bash
git clone <repo-url> auto-ats
cd auto-ats
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your LLM provider URL and API key, OR set it in the web UI.

cp /path/to/your/resume.txt master.txt
# OR upload/paste your resume in the web UI after starting the app.

python3 server.py
```

Open `http://localhost:5000`. The UI looks like a job-search site.

## LLM Provider Setup

Auto-ATS uses any OpenAI-compatible chat-completions endpoint.

### Option 1: Environment file

```bash
cp .env.example .env
# edit .env
FREELLM_URL=https://api.openai.com/v1
FREELLM_API_KEY=sk-...
FREELLM_MODEL=gpt-4o
```

### Option 2: Web UI

1. Click any job.
2. Click the **gear** icon.
3. Under **LLM PROVIDER**, enter:
   - **URL**: your provider's `/v1` endpoint
   - **API Key**: your key
   - **Default Model**: e.g. `gpt-4o`, `llama-3.3-70b-versatile`, `auto`
4. Click **Save Provider**.

### Supported providers

Any provider with an OpenAI-compatible `/chat/completions` endpoint:

- OpenAI
- Groq
- Together
- LocalAI / LM Studio / llama.cpp server
- Any custom FreeLLM-style proxy

The per-job **MODEL** dropdown still lets you override the model for each generation.

## Add your master resume

The app uses your master resume for two things:

1. Baseline ATS scoring against each job.
2. Source material for the AI tailoring pipeline.

### Option 1: Replace `master.txt`

```bash
cp your_resume.txt master.txt
```

### Option 2: Upload in the app

1. Click any job.
2. Click the **gear** icon.
3. Under **UPLOAD / PASTE RESUME**, choose a `.txt` file or paste text.
4. Click **Save Resume**.
5. Select it from the **RESUME** dropdown.

You can store multiple resumes in `resumes/` and switch between them per job.

## Add jobs

| Method | How |
|--------|-----|
| **Manual form** | Click **+ Add Job** in the top nav |
| **JSON paste** | Same modal → JSON Array tab |
| **JSON file** | Same modal → JSON File tab |
| **Indeed userscript** | Install `src/userscripts/indeed-saver.js` in Tampermonkey/Violentmonkey, click **Save to ATS** on an Indeed job page |
| **Scraper files** | Drop Apify/Indeed/LinkedIn `.json` files in the project root and click **+ Import Scrapes** |

## Workflow

1. Jobs arrive in **Job Feed** (status `NEW`).
2. Click a job and **Approve** the ones you want to pursue.
3. Switch to **Generation**, select the job, choose your resume + model, and click **Create Tailored**.
4. If the ATS gate blocks a low-match job, click **Force** to generate anyway.
5. Generated resumes move to **The Vault**. Click **VIEW PDF** or open the `targets/<job>/` folder.

## Environment variables

Copy `.env.example` to `.env` and set:

```env
FREELLM_URL=http://localhost:3001/v1
FREELLM_API_KEY=optional
ATS_GATE_THRESHOLD=35.0
ATS_GATE_MIN_SIGNAL=5.0
```

## Project layout

```
auto-ats/
├── server.py                 # Flask API
├── pipeline-scripts/         # Scorer, strike orchestrator, import engine
│   ├── ats_scorer.py
│   ├── strike_handler.py
│   └── job_import.py
├── static/camouflage/        # Indeed-clone web UI
│   ├── index.html
│   ├── ghost.js
│   └── style.css
├── payloads/prompts/         # 6-strike prompts
├── targets/                  # Generated resumes + PDFs (gitignored)
├── jobs.db                   # SQLite metadata (gitignored)
├── master.txt                # Your master resume (gitignored, template provided)
└── resumes/                  # Optional additional resumes (gitignored)
```

## Notes

- `master.txt`, `jobs.db`, `targets/`, `.env`, and other runtime data are excluded from git so the repo is safe to share.
- The default `master.txt` is a template. Do not commit your real resume.

## License

MIT
