from flask import Flask, jsonify, request, send_from_directory
import sqlite3
import json
import os
import random
import glob
import traceback
import subprocess
import re
from datetime import datetime
import httpx
from dotenv import load_dotenv

# Store original proxy environment variables to restore them later
_original_http_proxy = os.environ.get('HTTP_PROXY')
_original_https_proxy = os.environ.get('HTTPS_PROXY')
_original_all_proxy = os.environ.get('ALL_PROXY')
_original_no_proxy = os.environ.get('NO_PROXY')

# Clear proxy environment variables for this process to prevent external interference
if 'HTTP_PROXY' in os.environ: del os.environ['HTTP_PROXY']
if 'HTTPS_PROXY' in os.environ: del os.environ['HTTPS_PROXY']
if 'ALL_PROXY' in os.environ: del os.environ['ALL_PROXY']
# Also ensure NO_PROXY is cleared or set to bypass localhost if needed
if 'NO_PROXY' in os.environ: del os.environ['NO_PROXY']

# Make pipeline-scripts and project root importable
import sys
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_SCRIPTS = os.path.join(_PROJECT_ROOT, "pipeline-scripts")
for p in (_PROJECT_ROOT, _PIPELINE_SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import migration_engine
    import pdf_engine
    from strike_handler import execute_strike, get_db, sanitize_filename, get_target_dir
    from ats_scorer import ATSScorer
    from job_import import add_job, import_jobs
except ImportError as e:
    print(f"[!] CRITICAL ENGINE IMPORT ERROR: {e}")

load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')

# --- CONFIGURATION ---
DB_FILE = 'jobs.db'
HISTORY_FILE = 'job_history.json'
TAGS_FILE = 'categorized_tags.json'
BLACKLIST_FILE = 'blacklist.json'
RESUME_FILE = 'master.txt' # UPDATED
PROMPTS_FILE = 'user_prompts.json'
PROVIDER_FILE = 'provider_config.json'
PEACOCK_URL = os.getenv("PEACOCK_URL", "http://localhost:3099")
SESSION_STATS = {"scraped":0, "approved":0, "denied":0, "sent_to_groq":0}

# --- HELPERS ---
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def get_provider_config():
    """Return merged provider config: provider_config.json overrides .env."""
    cfg = {
        "url": os.getenv("FREELLM_URL", "http://localhost:3001/v1"),
        "api_key": os.getenv("FREELLM_API_KEY", ""),
        "model": os.getenv("FREELLM_MODEL", "auto"),
    }
    file_cfg = load_json(PROVIDER_FILE, {})
    for k in cfg:
        if file_cfg.get(k):
            cfg[k] = file_cfg[k]
    return cfg

def save_provider_config(cfg: dict):
    save_json(PROVIDER_FILE, cfg)

def load_json(filename, default=None):
    if default is None: default = {}
    if not os.path.exists(filename): return default
    try:
        with open(filename, 'r') as f: return json.load(f)
    except: return default

def save_json(filename, data):
    with open(filename, 'w') as f: json.dump(data, f, indent=2)

def update_history(key, amount=1):
    if key in SESSION_STATS: SESSION_STATS[key] += amount
    h = load_json(HISTORY_FILE, {"all_time":{}})
    if "all_time" not in h: h["all_time"] = {}
    if key not in h["all_time"]: h["all_time"][key] = 0
    h["all_time"][key] += amount
    save_json(HISTORY_FILE, h)

def sanitize_filename(text):
    return "".join(c for c in text if c.isalnum() or c in " -_").strip()[:50]

def get_target_dir(job_id, title, company):
    safe_title = sanitize_filename(title)
    safe_company = sanitize_filename(company)
    path = f"targets/{safe_title}_{safe_company}_{job_id}"
    if not os.path.exists(path): os.makedirs(path)
    return path

def extract_json(text):
    """
    Strips markdown noise and extracts the JSON block,
    handling various LLM output formats.
    """
    cleaned_text = text.strip()

    # Strategy 1: Look for ```json ... ``` or just ``` ... ``` blocks
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            print(f"[!] JSON Extraction Failed (Strategy 1 - Markdown block): {e}")
            pass # Fall through to other strategies

    # Strategy 2: Find the first { and last } that form a valid JSON object
    # This handles cases where JSON is embedded without markdown blocks
    try:
        json_start = cleaned_text.find('{')
        json_end = cleaned_text.rfind('}')
        if json_start != -1 and json_end != -1 and json_end > json_start:
            potential_json = cleaned_text[json_start : json_end + 1]
            return json.loads(potential_json)
        # If no valid {} block is found, or it's malformed, try direct parse
    except json.JSONDecodeError as e:
        print(f"[!] JSON Extraction Failed (Strategy 2 - Loose object): {e}")
        pass # Fall through to other strategies

    # Strategy 3: Attempt to parse the entire text directly as JSON
    # This catches cases where the LLM outputs pure JSON with no wrapping
    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        print(f"[!] JSON Extraction Failed (Strategy 3 - Direct parse): {e}")
        pass # Fall through, indicating failure

    print(f"[!] All JSON extraction strategies failed.")
    return None

# --- API ROUTES ---
@app.route('/api/status')
def status():
    h = load_json(HISTORY_FILE, {"all_time":{}})
    return jsonify({"session": SESSION_STATS, "all_time": h.get("all_time", {})})

def _list_resume_files():
    files = []
    if os.path.isdir("resumes"):
        files.extend(glob.glob("resumes/*.txt"))
    files.extend(glob.glob("master*.txt"))
    return sorted(set(files))


@app.route('/api/resumes')
def list_resumes():
    return jsonify(_list_resume_files())


@app.route('/api/upload_resume', methods=['POST'])
def upload_resume():
    """Accept a pasted resume text or uploaded file and save it to resumes/."""
    try:
        os.makedirs("resumes", exist_ok=True)
        name = request.form.get("name", "master.txt").strip()
        if not name.endswith(".txt"):
            name += ".txt"
        name = "".join(c for c in name if c.isalnum() or c in " ._-").strip()
        if not name or name == ".txt":
            name = "master.txt"
        path = os.path.join("resumes", name)

        text = None
        if "file" in request.files:
            file = request.files["file"]
            if file.filename:
                text = file.read().decode("utf-8", errors="ignore")
                if not name or name == "master.txt":
                    base = file.filename.rsplit(".", 1)[0]
                    path = os.path.join("resumes", f"{base}.txt")
        if text is None:
            text = request.form.get("text") or request.json.get("text", "")

        if not text or not text.strip():
            return jsonify({"status": "error", "reason": "No resume text provided"}), 400

        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return jsonify({"status": "saved", "path": path})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "reason": str(e)}), 500

@app.route('/api/models')
def list_models():
    """Return FreeLLM-compatible model options."""
    return jsonify([
        {"id": "auto", "note": "FreeLLM Auto"},
        {"id": "auto:fast", "note": "FreeLLM Auto Fast"},
        {"id": "moonshotai/kimi-k2-instruct", "note": "Kimi K2"},
        {"id": "llama-3.3-70b-versatile", "note": "Llama 3.3 70B"},
        {"id": "qwen/qwen3-32b", "note": "Qwen3 32B"},
    ])

@app.route('/api/jobs')
def jobs():
    status_filter = request.args.get('status', 'NEW')
    view_mode = request.args.get('view_mode', 'pending') # 'pending' or 'generated' for GENERATION tab
    conn = get_db()
    query = "SELECT id, title, company, city, pay_fmt, date_posted, score, status, raw_json FROM jobs WHERE 1=1"

    if status_filter == 'NEW':
        query += " AND (status IS NULL OR status = 'NEW')"
    elif status_filter == 'APPROVED': # For Refinery Tab
        query += " AND (status = 'APPROVED' OR status = 'GENERATED' OR status = 'DELIVERED')"
    elif status_filter == 'DENIED':
        query += " AND (status = 'DENIED' OR status = 'AUTO_DENIED')"
    elif status_filter == 'DELIVERED':
        query += " AND status = 'DELIVERED'"
    elif status_filter == 'GENERATION': # Special handling for Generation tab with view_mode
        if view_mode == 'pending':
            query += " AND status = 'APPROVED'" # Show approved jobs awaiting generation
        elif view_mode == 'generated':
            query += " AND status = 'DELIVERED'" # Show delivered/generated jobs
        else:
            return jsonify([]), 400 # Bad request for unknown view_mode
    elif status_filter == 'BONEYARD':
        query += " AND status = 'BONEYARD'"

    if status_filter == 'GENERATION' and view_mode == 'generated':
        query += " ORDER BY date_posted DESC" # Newest on top for generated view
    else:
        query += " ORDER BY score DESC, date_posted ASC" # Default order for others

    try:
        rows = conn.execute(query).fetchall()
        out = []
        for r in rows:
            t_dir = get_target_dir(r['id'], r['title'], r['company'])
            safe_title = sanitize_filename(r['title'])
            safe_company = sanitize_filename(r['company'])
            dir_name = f"{safe_title}_{safe_company}_{r['id']}"
            pdf_web_path = f"/done/{dir_name}/resume.pdf"

            has_ai = os.path.exists(os.path.join(t_dir, "resume.json"))
            has_pdf = os.path.exists(os.path.join(t_dir, "resume.pdf"))

            # Only include in DELIVERED if it actually has AI/PDF, regardless of DB status for robustness
            if status_filter == 'DELIVERED' and not (has_ai or has_pdf):
                continue
            # For GENERATION tab, 'pending' view should only show APPROVED jobs
            if status_filter == 'GENERATION' and view_mode == 'pending' and r['status'] != 'APPROVED':
                continue
            # For GENERATION tab, 'generated' view should only show DELIVERED jobs
            if status_filter == 'GENERATION' and view_mode == 'generated' and r['status'] != 'DELIVERED':
                continue

            raw_data = json.loads(r['raw_json'])
            job_url = raw_data.get('url') or raw_data.get('link') or raw_data.get('jobUrl') or '#'

            out.append({
                "id": r['id'], "title": r['title'], "company": r['company'],
                "city": r['city'], "pay": r['pay_fmt'], "freshness": r['date_posted'],
                "score": r['score'], "status": r['status'],
                "has_ai": has_ai, "has_pdf": has_pdf,
                "pdf_link": pdf_web_path if has_pdf else None,
                "job_url": job_url,
                "safe_title": safe_title
            })
        return jsonify(out)
    except Exception as e:
        traceback.print_exc()
        return jsonify([])
    finally: conn.close()

@app.route('/api/get_job_details')
def job_details():
    id = request.args.get('id')
    conn = get_db()
    row = conn.execute("SELECT raw_json FROM jobs WHERE id=?", (id,)).fetchone()
    conn.close()
    if not row: return jsonify({"description":"Not Found", "skills":[]})

    data = json.loads(row['raw_json'])
    desc = data.get('description', {}).get('html') or data.get('description', {}).get('text') or "No Desc"
    url = data.get('url') or data.get('link') or data.get('jobUrl') or '#'

    tags_db = load_json(TAGS_FILE, {"qualifications": [], "skills": [], "benefits": [], "ignored": [], "denied": []})
    category_map = {}
    for cat, items in tags_db.items():
        for item in items: category_map[item.lower()] = cat
    job_skills = []
    for k, v in data.get('attributes', {}).items():
        cat = category_map.get(v.lower(), "new")
        job_skills.append({"name": v, "category": cat})

    return jsonify({"description": desc, "skills": job_skills, "url": url})

@app.route('/api/harvest_tag', methods=['POST'])
def harvest_tag():
    tag = request.json.get('tag')
    category = request.json.get('category', 'skills')
    tags_db = load_json(TAGS_FILE, {"qualifications": [], "skills": [], "benefits": [], "ignored": [], "denied": []})
    for cat in tags_db:
        if tag in tags_db[cat]: tags_db[cat].remove(tag)
    if tag not in tags_db[category]:
        tags_db[category].append(tag)
        save_json(TAGS_FILE, tags_db)
    return jsonify({"status": "harvested"})

@app.route('/api/open_folder', methods=['POST'])
def open_folder():
    id = request.json.get('id')
    conn = get_db()
    row = conn.execute("SELECT title, company FROM jobs WHERE id=?", (id,)).fetchone()
    conn.close()
    if row:
        t_dir = get_target_dir(id, row['title'], row['company'])
        subprocess.Popen(['xdg-open', t_dir])
        return jsonify({"status": "opened"})
    return jsonify({"status": "error"})

@app.route('/api/approve', methods=['POST'])
def approve():
    conn = get_db()
    conn.execute("UPDATE jobs SET status='APPROVED' WHERE id=?", (request.json['id'],))
    conn.commit()
    conn.close()
    update_history('approved')
    return jsonify({"status":"approved"})

@app.route('/api/deny', methods=['POST'])
def deny():
    conn = get_db()
    conn.execute("UPDATE jobs SET status='DENIED' WHERE id=?", (request.json['id'],))
    conn.commit()
    conn.close()
    update_history('denied')
    return jsonify({"status":"denied"})

@app.route('/api/restore', methods=['POST'])
def restore():
    conn = get_db()
    conn.execute("UPDATE jobs SET status='NEW' WHERE id=?", (request.json['id'],))
    conn.commit()
    conn.close()
    return jsonify({"status":"restored"})

@app.route('/api/boneyard', methods=['POST'])
def boneyard():
    conn = get_db()
    conn.execute("UPDATE jobs SET status='BONEYARD' WHERE id=?", (request.json['id'],))
    conn.commit()
    conn.close()
    return jsonify({"status":"boneyard"})

@app.route('/api/blacklist', methods=['POST'])
def blacklist():
    term = request.json.get('term', '').lower()
    bl = load_json(BLACKLIST_FILE, [])
    if term not in bl:
        bl.append(term)
        save_json(BLACKLIST_FILE, bl)
    conn = get_db()
    conn.execute(f"UPDATE jobs SET status='AUTO_DENIED' WHERE (lower(title) LIKE ? OR lower(company) LIKE ?) AND status != 'APPROVED'", (f'%{term}%', f'%{term}%'))
    conn.commit()
    conn.close()
    return jsonify({"status": "blacklisted"})

@app.route('/api/get_artifact', methods=['POST'])
def get_artifact():
    id = request.json.get('id')
    save_content = request.json.get('save_content')
    conn = get_db()
    row = conn.execute("SELECT title, company FROM jobs WHERE id=?", (id,)).fetchone()
    conn.close()
    t_dir = get_target_dir(id, row['title'], row['company'])
    json_path = os.path.join(t_dir, "resume.json")
    if save_content:
        with open(json_path, 'w') as f: f.write(save_content)
        return jsonify({"status": "saved"})
    if os.path.exists(json_path):
        with open(json_path, 'r') as f: return jsonify({"content": f.read()})
    return jsonify({"content": "No Artifact"})

@app.route('/api/scrapes', methods=['GET'])
def list_scrapes():
    all_files = glob.glob("*.json")
    exclude = [HISTORY_FILE, TAGS_FILE, BLACKLIST_FILE, "package.json", "tsconfig.json", PROMPTS_FILE, "master_resume.json"]
    valid = [f for f in all_files if f not in exclude and "input_json" not in f]
    return jsonify(valid)

@app.route('/api/migrate', methods=['POST'])
def run_migration():
    files = request.json.get('files', [])
    try:
        stats = migration_engine.process_files(files)
        SESSION_STATS['scraped'] += stats["new"]
        update_history('scraped', stats["new"])
        return jsonify({"status": "success", "stats": stats})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/add_job', methods=['POST'])
def api_add_job():
    """Add a single job manually from Indeed or any source."""
    try:
        data = request.json
        result = add_job(data)
        if result["status"] == "added":
            SESSION_STATS['scraped'] += 1
            update_history('scraped')
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route('/api/import_jobs', methods=['POST'])
def api_import_jobs():
    """Bulk import jobs from any JSON array (Apify, LinkedIn, manual list, etc.)."""
    try:
        payload = request.json
        records = payload if isinstance(payload, list) else payload.get('jobs', [])
        stats = import_jobs(records)
        SESSION_STATS['scraped'] += stats["added"]
        update_history('scraped', stats["added"])
        return jsonify({"status": "success", "stats": stats})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "reason": str(e)}), 500


def _extract_indeed_job(html: str, url: str, key: str) -> dict:
    """Parse Indeed job-detail HTML into a canonical job record."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')

    # Title
    title_el = (
        soup.find(attrs={"data-testid": "jobsearch-JobInfoHeader-title"})
        or soup.find(class_="jobsearch-JobInfoHeader-title")
        or soup.find('h1')
    )
    title = title_el.get_text(strip=True) if title_el else "Unknown"

    # Company — usually in a link or div near the title
    company = "Unknown"
    company_el = soup.find(attrs={"data-testid": "jobsearch-JobInfoHeader-companyName"}) \
        or soup.find(class_="jobsearch-JobInfoHeader-companyName") \
        or soup.find('a', href=re.compile(r'/cmp/'))
    if company_el:
        company = company_el.get_text(strip=True)
    else:
        # Try the subtitle line
        sub = soup.find(class_="jobsearch-JobInfoHeader-subtitle")
        if sub:
            parts = [p.get_text(strip=True) for p in sub.find_all(class_="css-")]
            if parts:
                company = parts[0]

    # Location
    location = ""
    loc_el = soup.find(attrs={"data-testid": "jobsearch-JobInfoHeader-location"}) \
        or soup.find(class_="jobsearch-JobInfoHeader-location")
    if loc_el:
        location = loc_el.get_text(strip=True)

    # Description
    desc_el = (
        soup.find(id="jobDescriptionText")
        or soup.find(attrs={"data-testid": "text-job-description"})
        or soup.find(class_="jobsearch-JobComponent-description")
    )
    description = desc_el.get_text(separator="\n", strip=True) if desc_el else ""

    # Salary — look for common salary text in the header or description
    salary_min = 0
    salary_text = ""
    for el in soup.find_all(string=re.compile(r'\$\d[\d,]*\s*(?:-\s*\$?\d[\d,]*)?\s*(?:a year|per year|yearly|/year|yr|annual|hour|hr)', re.I)):
        salary_text = el.strip()
        break
    if not salary_text:
        for el in soup.find_all(string=re.compile(r'\$\d[\d,]*', re.I)):
            salary_text = el.strip()
            break
    m = re.search(r'\$?([\d,]+(?:\.\d+)?)\s*(?:-\s*\$?([\d,]+(?:\.\d+)?))?\s*(a year|per year|yearly|/year|yr|annual|hour|hr)', salary_text, re.I)
    if m:
        low = float(m.group(1).replace(',', ''))
        unit = m.group(3).lower()
        if unit.startswith('hour'):
            salary_min = int(low * 2080)
        else:
            salary_min = int(low)

    return {
        "key": key,
        "title": title,
        "employer": {"name": company},
        "location": {"city": location},
        "description": {"text": description, "html": str(desc_el) if desc_el else ""},
        "url": url,
        "datePublished": datetime.now().isoformat(),
        "baseSalary": {"min": salary_min, "unitOfWork": "YEAR"} if salary_min else {},
    }


@app.route('/api/tm-save', methods=['POST'])
def tm_save():
    """Receive raw HTML job captures from the Indeed browser userscript."""
    try:
        payload = request.json
        records = payload if isinstance(payload, list) else [payload]
        imported = 0
        errors = []
        for rec in records:
            try:
                html = rec.get("html", "")
                url = rec.get("url", "")
                key = rec.get("key") or rec.get("id") or f"tm_{int(datetime.now().timestamp() * 1000)}"
                source = rec.get("source", "unknown")
                if source.lower() == "indeed" or "indeed.com" in url:
                    normalized = _extract_indeed_job(html, url, key)
                else:
                    # Generic fallback: try to find any text block and h1
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, 'html.parser')
                    desc = soup.get_text(separator="\n", strip=True)[:8000]
                    normalized = {
                        "key": key,
                        "title": (soup.find('h1') or soup.find('title') or {}).get_text(strip=True) if (soup.find('h1') or soup.find('title')) else "Unknown",
                        "employer": {"name": "Unknown"},
                        "location": {"city": ""},
                        "description": {"text": desc, "html": html},
                        "url": url,
                        "datePublished": datetime.now().isoformat(),
                    }
                result = add_job(normalized)
                if result["status"] == "added":
                    imported += 1
            except Exception as e:
                traceback.print_exc()
                errors.append(str(e))
        return jsonify({"status": "saved", "imported": imported, "errors": errors})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route('/api/provider', methods=['GET', 'POST'])
def provider_config():
    """Read or write the LLM provider configuration."""
    try:
        if request.method == 'GET':
            cfg = get_provider_config()
            # Return masked key to avoid leaking it over the wire unnecessarily
            masked = cfg.copy()
            if masked.get("api_key"):
                key = masked["api_key"]
                masked["api_key"] = key[:4] + "*" * (len(key) - 8) + key[-4:] if len(key) > 8 else "*" * len(key)
            return jsonify(masked)

        data = request.json
        cfg = {
            "url": data.get("url", "").strip(),
            "api_key": data.get("api_key", "").strip(),
            "model": data.get("model", "").strip(),
        }
        # Merge with existing so a masked value doesn't overwrite the real key
        existing = get_provider_config()
        if cfg["api_key"].startswith("****") or cfg["api_key"].count("*") > len(cfg["api_key"]) // 2:
            cfg["api_key"] = existing.get("api_key", "")
        for k in list(cfg):
            if not cfg[k]:
                cfg[k] = existing.get(k, "")
        save_provider_config(cfg)
        return jsonify({"status": "saved"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "reason": str(e)}), 500

@app.route('/api/generate_pdf', methods=['POST'])
def trigger_pdf():
    try:
        job_id = request.json['id']
        result = pdf_engine.generate_pdf(job_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/prompts', methods=['GET', 'POST'])
def manage_prompts():
    if request.method == 'POST':
        name, content = request.json.get('name'), request.json.get('content')
        data = load_json(PROMPTS_FILE)
        data[name] = content
        save_json(PROMPTS_FILE, data)
        return jsonify({"status": "saved"})
    return jsonify(list(load_json(PROMPTS_FILE).keys()))

@app.route('/api/get_prompt_content', methods=['POST'])
def get_prompt_content():
    name = request.json.get('name')
    if name == 'DEFAULT':
        return jsonify({"content": "SYSTEM IDENTITY:\nYou are Parker Lewis..."})
    return jsonify({"content": load_json(PROMPTS_FILE).get(name, "")})

@app.route('/api/scores', methods=['GET'])
def get_scores():
    """Return baseline and final ATS scores for a job target."""
    job_id = request.args.get('id')
    if not job_id:
        return jsonify({"error": "Missing id"}), 400

    conn = get_db()
    row = conn.execute("SELECT title, company FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Job not found"}), 404

    t_dir = get_target_dir(job_id, row['title'], row['company'])
    result = {"baseline": None, "final": None}

    baseline_path = os.path.join(t_dir, "baseline_score.json")
    if os.path.exists(baseline_path):
        with open(baseline_path, 'r', encoding='utf-8') as f:
            result["baseline"] = json.load(f)

    final_path = os.path.join(t_dir, "final_score.json")
    if os.path.exists(final_path):
        with open(final_path, 'r', encoding='utf-8') as f:
            result["final"] = json.load(f)

    return jsonify(result)

@app.route('/api/process_job', methods=['POST'])
def process_job():
    """Run the hardened 6-strike pipeline for a job and return scores."""
    session_id = f"STRIKE_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
    res = execute_strike(
        job_id=request.json['id'],
        model=request.json.get('model'),
        temp=request.json.get('temp', 0.3),
        session_id=session_id,
        prompt_override=request.json.get('prompt_override'),
        resume_file=request.json.get('resume_file'),
        force=request.json.get('force', False)
    )

    # Enrich response with score data when available
    if res.get("status") == "success" and res.get("target_dir"):
        try:
            baseline_path = os.path.join(res["target_dir"], "baseline_score.json")
            final_path = os.path.join(res["target_dir"], "final_score.json")
            if os.path.exists(baseline_path):
                with open(baseline_path, 'r', encoding='utf-8') as f:
                    res["baseline_score"] = json.load(f).get("score")
            if os.path.exists(final_path):
                with open(final_path, 'r', encoding='utf-8') as f:
                    final_data = json.load(f)
                    res["final_score"] = final_data.get("score")
                    res["improvement"] = final_data.get("improvement")
        except Exception as e:
            print(f"[!] Could not read score artifacts: {e}")

    if res.get("status") == "success":
        update_history('sent_to_groq')
    return jsonify(res)

@app.route('/')
def index(): return send_from_directory('static/camouflage', 'index.html')
@app.route('/static/<path:path>')
def send_static(path): return send_from_directory('static', path)
@app.route('/done/<path:filename>')
def send_done(filename): return send_from_directory('targets', filename)

if __name__ == "__main__":
    print(f"\n--- WAR ROOM ONLINE (v6.0 APEX PROTOCOL) ---")
    try:
        app.run(port=5000, debug=False)
    finally:
        # Restore original proxy environment variables on shutdown
        if _original_http_proxy: os.environ['HTTP_PROXY'] = _original_http_proxy
        elif 'HTTP_PROXY' in os.environ: del os.environ['HTTP_PROXY']

        if _original_https_proxy: os.environ['HTTPS_PROXY'] = _original_https_proxy
        elif 'HTTPS_PROXY' in os.environ: del os.environ['HTTPS_PROXY']

        if _original_all_proxy: os.environ['ALL_PROXY'] = _original_all_proxy
        elif 'ALL_PROXY' in os.environ: del os.environ['ALL_PROXY']

        if _original_no_proxy: os.environ['NO_PROXY'] = _original_no_proxy
        elif 'NO_PROXY' in os.environ: del os.environ['NO_PROXY']
