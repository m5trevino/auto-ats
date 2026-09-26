#!/usr/bin/env python3
"""
Strike Handler — 6-strike AI pipeline for resume generation.
Integrates ATS scoring throughout the pipeline.
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Optional

# Ensure pipeline-scripts is importable when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from ats_scorer import ATSScorer
except ImportError:
    ATSScorer = None

# pdf_engine lives at project root; make it importable from pipeline-scripts as well
try:
    import pdf_engine
except ImportError:
    pdf_engine = None


# ── Config ─────────────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
DB_FILE = os.getenv("AUTOATS_DB", os.path.join(_PROJECT_ROOT, "jobs.db"))
OUTPUT_DIR = os.path.normpath(os.getenv("AUTOATS_TARGETS", os.path.join(_PROJECT_ROOT, "targets")))
PROMPTS_DIR = os.path.join(_PROJECT_ROOT, "payloads", "prompts")


# ── Database Helpers ───────────────────────────────────────────────────────
def get_db(db_path: Optional[str] = None):
    """Return a sqlite3 connection with row factory."""
    path = db_path or DB_FILE
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def sanitize_filename(text: str) -> str:
    """Make a filesystem-safe string from a job title/company."""
    return "".join(c for c in text if c.isalnum() or c in " -_").strip()[:50]


def get_target_dir(job_id: str, title: str, company: str) -> str:
    """Return (and create) the output directory for a job."""
    safe_title = sanitize_filename(title)
    safe_company = sanitize_filename(company)
    path = os.path.join(OUTPUT_DIR, f"{safe_title}_{safe_company}_{job_id}")
    os.makedirs(path, exist_ok=True)
    return path


# ── JSON Extraction ────────────────────────────────────────────────────────
def extract_json(text: str) -> Optional[Dict]:
    """Strips markdown noise and extracts a JSON object from LLM output."""
    cleaned = text.strip()

    # Strategy 1: fenced ```json block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 2: first { ... last }
    try:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        pass

    # Strategy 3: direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    return None


# ── Contact overlay from master resume ─────────────────────────────────────
def _extract_contact_from_master(master_text: str) -> Dict:
    """Pull name, email, phone, linkedin, github from the master resume."""
    result = {"name": "", "email": "", "phone": "", "linkedin": "", "github": "", "contact_info": ""}
    lines = master_text.splitlines()

    # Scan first 15 lines for contact info (we need email/linkedin before deriving name)
    for line in lines[:15]:
        emails = re.findall(r"[\w.-]+@[\w.-]+\.\w+", line)
        if emails and not result["email"]:
            result["email"] = emails[0]
        phones = re.findall(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", line)
        if phones and not result["phone"]:
            result["phone"] = phones[0]
        linkedins = re.findall(r"linkedin\.com/[^\s|]+", line)
        if linkedins and not result["linkedin"]:
            result["linkedin"] = linkedins[0]
        githubs = re.findall(r"github\.com/[^\s|]+", line)
        if githubs and not result["github"]:
            result["github"] = githubs[0]
        if "Location:" in line and not result["contact_info"]:
            result["contact_info"] = line.replace("Location:", "").strip()
        elif "Contact:" in line and not result["contact_info"]:
            result["contact_info"] = line.replace("Contact:", "").strip()

    # Derive name from email handle if not found in text
    if not result["name"]:
        if result["email"]:
            local = result["email"].split("@")[0]
            # Convert mtrevino1983 -> Matthew Trevino using known mapping
            known_map = {
                "mtrevino1983": "Matthew Trevino",
            }
            if local in known_map:
                result["name"] = known_map[local]
            else:
                # Generic: split camel-case / dotted names
                parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", local)
                parts = re.sub(r"[0-9._-]+", " ", parts).strip()
                result["name"] = parts.title()
        elif result["linkedin"]:
            m = re.search(r"linkedin\.com/in/([\w-]+)", result["linkedin"])
            if m:
                handle = m.group(1)
                handle = re.sub(r"[0-9._-]+", " ", handle).strip()
                handle = re.sub(r"([a-z])([A-Z])", r"\1 \2", handle)
                result["name"] = handle.title()

    # Try to find an explicit full name anywhere in the document only if we still don't have one.
    # The known email map is trusted more than regex guessing on section headers.
    if not result["name"]:
        name_match = re.search(r"(?:^|[\s:])([A-Z][a-z]+\s+[A-Z][a-z]+)(?:\s+(?:is|has|with|'s|\|\s))?(?!\s*[A-Z][a-z]+\s+is)", master_text, re.M)
        if name_match:
            candidate = name_match.group(1).strip()
            # Reject common section headers that look like names
            section_headers = {"Professional Summary", "Core Competencies", "Professional Experience", "Technical Projects", "Education", "Certifications", "Job Summary", "Responsibilities", "Experience"}
            if candidate not in section_headers:
                result["name"] = candidate

    return result


def _clean_resume_json(data: Dict, master_text: str) -> Dict:
    """Strip markdown markers and overlay real contact info from master resume."""
    contact = _extract_contact_from_master(master_text)

    def clean_str(s):
        if not isinstance(s, str):
            return s
        s = re.sub(r"\*\*\s*", "", s)
        s = re.sub(r"\s*\*\*", "", s)
        return s.strip()

    def clean_list(items):
        out = []
        for item in items:
            if isinstance(item, str):
                c = clean_str(item)
                if c:
                    out.append(c)
            else:
                out.append(item)
        return out

    placeholder_names = {"your name", "yourname", "full name", "fullname", "[full name]", "{{full_name}}", "{{full name}}", "name here"}
    for key in ["name", "email", "phone", "linkedin", "github", "contact_info", "summary"]:
        val = data.get(key, "")
        if val and not re.sub(r"[^A-Za-z0-9]", "", str(val)):
            val = ""
        is_placeholder = False
        if isinstance(val, str):
            low = val.lower().strip()
            is_placeholder = (
                not val or "{{" in val or "FULL_NAME" in val or "[Full Name]" in val
                or low in placeholder_names
                or low == "name" or low.startswith("your ") or low.startswith("example")
                or low in {"professional summary", "core competencies", "education", "experience"}
            )
        if is_placeholder:
            data[key] = contact.get(key, "")
        else:
            data[key] = clean_str(val)

    data["skills"] = clean_list(data.get("skills", []))
    data["certifications"] = clean_list(data.get("certifications", []))

    for exp in data.get("experience", []):
        for k in ["role", "company", "dates", "location"]:
            exp[k] = clean_str(exp.get(k, ""))
        exp["bullets"] = clean_list(exp.get("bullets", []))

    for proj in data.get("projects", []):
        for k in ["name", "role", "description"]:
            proj[k] = clean_str(proj.get(k, ""))
        proj["bullets"] = clean_list(proj.get("bullets", []))

    for edu in data.get("education", []):
        for k in ["degree", "institution", "location", "date"]:
            edu[k] = clean_str(edu.get(k, ""))

    return data


def _parse_markdown_resume(markdown_text: str, master_text: str = "") -> Dict:
    """
    Convert a markdown resume into the JSON schema expected by pdf_engine.
    Used as fallback when the LLM ignores JSON instructions.
    """
    data: Dict = {
        "name": "",
        "email": "",
        "phone": "",
        "linkedin": "",
        "github": "",
        "contact_info": "",
        "summary": "",
        "skills": [],
        "experience": [],
        "projects": [],
        "education": [],
        "certifications": [],
    }

    lines = markdown_text.splitlines()

    # Header line usually has name, contact
    if lines:
        first = lines[0].strip().lstrip("# ").strip()
        if first and "{{" not in first:
            data["name"] = first.strip()
        else:
            # Try next non-empty line
            for line in lines[1:8]:
                candidate = line.strip().lstrip("# ").strip()
                if candidate and "{{" not in candidate and "@" not in candidate:
                    data["name"] = candidate
                    break

    # Contact line
    for line in lines[:10]:
        if "@" in line or re.search(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", line):
            data["contact_info"] = line.strip().lstrip("| ").strip()
            emails = re.findall(r"[\w.-]+@[\w.-]+\.\w+", line)
            if emails:
                data["email"] = emails[0]
            phones = re.findall(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", line)
            if phones:
                data["phone"] = phones[0]
            linkedins = re.findall(r"linkedin\.com/[^\s|)\"]+", line)
            if linkedins:
                data["linkedin"] = linkedins[0]
            githubs = re.findall(r"github\.com/[^\s|)\"]+", line)
            if githubs:
                data["github"] = githubs[0]
            break

    # Summary
    summary_text = _extract_markdown_section(markdown_text, "Professional Summary")
    if not summary_text:
        summary_text = _extract_markdown_section(markdown_text, "Summary")
    if summary_text:
        data["summary"] = " ".join(line.strip() for line in summary_text.splitlines() if line.strip()).strip()

    # Skills - collect bullet/list lines and bold categories
    skills_text = _extract_markdown_section(markdown_text, "Core Competencies")
    if not skills_text:
        skills_text = _extract_markdown_section(markdown_text, "Skills")
    if skills_text:
        skill_lines = [line.strip().lstrip("- * ").strip() for line in skills_text.splitlines() if line.strip()]
        flat = []
        for line in skill_lines:
            if ":" in line:
                flat.extend(part.strip() for part in line.split(":", 1)[1].split(",") if part.strip())
            else:
                flat.extend(part.strip() for part in line.split(",") if part.strip())
        data["skills"] = [s for s in flat if s]

    # Experience
    exp_text = _extract_markdown_section(markdown_text, "Professional Experience")
    if exp_text:
        entries = re.split(r"\n###\s+", exp_text)
        for entry in entries:
            if not entry.strip():
                continue
            lines = entry.splitlines()
            header = lines[0].strip().lstrip("### ") if lines else ""
            role_company = re.split(r"\s*\|\s*", header)
            role = role_company[0].strip() if role_company else ""
            company = role_company[1].strip() if len(role_company) > 1 else ""
            dates = role_company[2].strip() if len(role_company) > 2 else ""
            bullets = []
            for line in lines[1:]:
                line = line.strip()
                if line.startswith("-") or line.startswith("*"):
                    bullets.append(line.lstrip("- *").strip())
            if role or company:
                data["experience"].append({
                    "role": role,
                    "company": company,
                    "dates": dates,
                    "location": "",
                    "bullets": bullets,
                })

    # Education
    edu_text = _extract_markdown_section(markdown_text, "Education")
    if edu_text:
        for line in edu_text.splitlines():
            line = line.strip().lstrip("- *")
            if not line:
                continue
            parts = re.split(r"\s*\|\s*", line)
            degree = parts[0].strip() if parts else ""
            institution = parts[1].strip() if len(parts) > 1 else ""
            date = parts[2].strip() if len(parts) > 2 else ""
            data["education"].append({
                "degree": degree,
                "institution": institution,
                "location": "",
                "date": date,
            })

    # Certifications
    cert_text = _extract_markdown_section(markdown_text, "Certifications")
    if cert_text:
        for line in cert_text.splitlines():
            line = line.strip().lstrip("- *")
            if line:
                data["certifications"].append(line)

    return data


# ── Resume Helpers ─────────────────────────────────────────────────────────
def flatten_resume_to_text(resume_data: Dict) -> str:
    """Flatten resume.json structure to searchable text for ATS scoring."""
    parts: List[str] = []

    # Summary
    if resume_data.get("summary"):
        parts.append(str(resume_data["summary"]))

    # Skills
    skills = resume_data.get("skills")
    if isinstance(skills, list):
        parts.append(" ".join(str(s) for s in skills))
    elif isinstance(skills, dict):
        for category in skills.get("categories", []):
            for item in category.get("items", []):
                parts.append(str(item))
        # Also handle flat dict of lists
        for key, value in skills.items():
            if isinstance(value, list):
                parts.append(" ".join(str(v) for v in value))
            elif isinstance(value, str):
                parts.append(value)

    # Experience
    for exp in resume_data.get("experience", []):
        parts.append(str(exp.get("company", "")))
        parts.append(str(exp.get("role", "")))
        for bullet in exp.get("bullets", []):
            parts.append(str(bullet))

    # Projects
    for proj in resume_data.get("projects", []):
        parts.append(str(proj.get("name", "")))
        parts.append(str(proj.get("description", "")))

    # Education
    for edu in resume_data.get("education", []):
        parts.append(str(edu.get("degree", "")))
        parts.append(str(edu.get("field", "")))
        parts.append(str(edu.get("institution", "")))

    # Certifications
    for cert in resume_data.get("certifications", []):
        if isinstance(cert, str):
            parts.append(cert)
        elif isinstance(cert, dict):
            parts.append(str(cert.get("name", "")))

    return " ".join(p for p in parts if p)


# ── Keyword Extraction (NEON TERMINATOR output) ──────────────────────────────
def parse_neon_output(raw_text: str) -> Dict:
    """
    Parse NEON TERMINATOR keyword extraction output.

    First tries JSON (new format), then falls back to legacy markdown bullet parsing.
    Returns dict with categories, weighted list, and top_5.
    """
    categories = {
        "hard_skills": [],
        "soft_skills": [],
        "domain_keywords": [],
        "gatekeeper_credentials": [],
    }
    weighted: List[Dict] = []

    # Try JSON first
    parsed_json = extract_json(raw_text)
    if isinstance(parsed_json, dict):
        for cat in categories.keys():
            categories[cat] = parsed_json.get(cat, []) if isinstance(parsed_json.get(cat), list) else []
        for cat, items in categories.items():
            for item in items:
                if isinstance(item, dict):
                    weighted.append({
                        "keyword": str(item.get("keyword", item.get("skill", ""))),
                        "priority": int(item.get("priority", 5)),
                        "category": cat,
                        "type": str(item.get("type", "unknown")),
                    })
                elif isinstance(item, str):
                    weighted.append({"keyword": item, "priority": 5, "category": cat, "type": "unknown"})
        # Handle new required_priorities / preferred_priorities arrays
        for arr, t in [(parsed_json.get("required_priorities", []), "required"), (parsed_json.get("preferred_priorities", []), "preferred")]:
            for item in arr:
                if isinstance(item, dict):
                    weighted.append({
                        "keyword": str(item.get("keyword", "")),
                        "priority": int(item.get("priority", 5)),
                        "category": "hard_skills",
                        "type": t,
                    })
        weighted.sort(key=lambda x: x["priority"], reverse=True)
        return {
            **categories,
            "weighted": weighted,
            "top_5": [w["keyword"] for w in weighted[:5]],
        }

    # Legacy markdown bullet parser
    current_cat: Optional[str] = None
    priority_pattern = re.compile(r"\[PRIORITY:\s*(\d+)\]", re.IGNORECASE)
    required_pattern = re.compile(r"\[REQUIRED\]", re.IGNORECASE)
    preferred_pattern = re.compile(r"\[PREFERRED\]", re.IGNORECASE)

    for line in raw_text.split("\n"):
        line = line.strip()

        upper = line.upper()
        if "HARD SKILL" in upper or "HARD_SKILL" in upper:
            current_cat = "hard_skills"
            continue
        if "SOFT SKILL" in upper or "SOFT_SKILL" in upper:
            current_cat = "soft_skills"
            continue
        if "DOMAIN" in upper:
            current_cat = "domain_keywords"
            continue
        if "GATEKEEPER" in upper or "CREDENTIAL" in upper:
            current_cat = "gatekeeper_credentials"
            continue

        if line.startswith(("*", "-")):
            priority_match = priority_pattern.search(line)
            priority = int(priority_match.group(1)) if priority_match else 5
            is_required = bool(required_pattern.search(line))
            is_preferred = bool(preferred_pattern.search(line))
            keyword = line.lstrip("*-").strip()
            keyword = priority_pattern.sub("", keyword).strip()
            keyword = required_pattern.sub("", keyword).strip()
            keyword = preferred_pattern.sub("", keyword).strip()
            keyword = re.sub(r"\s*[-:]\s*mentioned.*$", "", keyword, flags=re.IGNORECASE).strip()

            if keyword and current_cat:
                categories[current_cat].append(keyword)
                weighted.append({
                    "keyword": keyword,
                    "priority": priority,
                    "category": current_cat,
                    "type": "required" if is_required else ("preferred" if is_preferred else "unknown"),
                })

    weighted.sort(key=lambda x: x["priority"], reverse=True)
    return {
        **categories,
        "weighted": weighted,
        "top_5": [w["keyword"] for w in weighted[:5]],
    }


def _build_weighted_keyword_block(extracted_kws: Dict) -> str:
    """
    Build a rich, tiered keyword injection block for strike prompts.

    Organizes keywords by priority and type, with explicit placement
    instructions for the AI to follow when generating resume sections.
    """
    weighted = extracted_kws.get("weighted", [])

    required_high = [w for w in weighted if w.get("type") == "required" and w.get("priority", 5) >= 8]
    required_med = [w for w in weighted if w.get("type") == "required" and 5 <= w.get("priority", 5) < 8]
    preferred = [w for w in weighted if w.get("type") == "preferred" or w.get("priority", 5) < 5]

    block = f"""## TARGET KEYWORD INJECTION PROTOCOL

### 🔴 CRITICAL - REQUIRED (Priority 8-10):
These MUST appear prominently in your resume:
{chr(10).join(f"- {w['keyword']} (Priority: {w['priority']}/10)" for w in required_high[:5]) if required_high else "- None detected"}

### 🟡 HIGH - REQUIRED (Priority 5-7):
Include these in relevant sections:
{chr(10).join(f"- {w['keyword']} (Priority: {w['priority']}/10)" for w in required_med[:5]) if required_med else "- None detected"}

### 🟢 PREFERRED (Priority 1-4):
Include if space permits:
{chr(10).join(f"- {w['keyword']} (Priority: {w['priority']}/10)" for w in preferred[:5]) if preferred else "- None detected"}

### TOP 5 MUST-HAVES:
The 5 most important keywords (prioritize these):
{chr(10).join(f"{i+1}. {kw}" for i, kw in enumerate(extracted_kws.get("top_5", [])[:5])) if extracted_kws.get('top_5') else "- None detected"}

### DOMAIN CONTEXT:
{chr(10).join(f"- {kw}" for kw in extracted_kws.get("domain_keywords", [])[:5]) if extracted_kws.get('domain_keywords') else "- None detected"}

### GATEKEEPER CREDENTIALS:
{chr(10).join(f"- {cred}" for cred in extracted_kws.get("gatekeeper_credentials", [])[:3]) if extracted_kws.get('gatekeeper_credentials') else "- None detected"}

### KEYWORD PLACEMENT STRATEGY:
1. Put TOP 5 MUST-HAVES in the Professional Summary (first 1-2 sentences).
2. Put CRITICAL keywords at the TOP of the Core Competencies / Skills section.
3. Weave CRITICAL and HIGH keywords into the first 3 bullets of your most recent role.
4. Mention each keyword naturally — no lists, no stuffing.
5. Pair every keyword with a metric ($, %, scale, time) when possible.
"""
    return block


# ── Prompt Loader ───────────────────────────────────────────────────────────
def load_prompt(name: str) -> str:
    """Load a prompt template from payloads/prompts/."""
    path = os.path.join(PROMPTS_DIR, name)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def inject_keyword_block(prompt_text: str, keyword_block: str) -> str:
    """Replace [INJECT_JD_RECON] placeholder with the keyword block."""
    if "[INJECT_JD_RECON]" in prompt_text:
        return prompt_text.replace("[INJECT_JD_RECON]", keyword_block)
    # Fallback: append after OUTPUT FORMAT if placeholder missing
    return prompt_text + "\n\n" + keyword_block


# ── LLM Strike Call ────────────────────────────────────────────────────────
def _load_provider_config() -> Dict:
    """Load provider config from provider_config.json, fall back to .env."""
    cfg = {
        "url": os.getenv("FREELLM_URL", "http://localhost:3001/v1"),
        "api_key": os.getenv("FREELLM_API_KEY", ""),
        "model": os.getenv("FREELLM_MODEL", "auto"),
    }
    cfg_path = os.path.normpath(os.path.join(_PROJECT_ROOT, "provider_config.json"))
    try:
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
            for k in cfg:
                if file_cfg.get(k):
                    cfg[k] = file_cfg[k]
    except Exception as e:
        print(f"\033[93m[!] Could not read provider_config.json: {e}\033[0m")
    return cfg


def call_llm(prompt: str, model: str = "auto", temp: float = 0.7) -> str:
    """
    Call any OpenAI-compatible LLM endpoint.

    Falls back to a deterministic mock JSON resume if:
      - the API is unreachable,
      - the response cannot be parsed, or
      - the MOCK_LLM environment variable is set.
    """
    if os.getenv("MOCK_LLM"):
        return _mock_llm_response()

    cfg = _load_provider_config()
    freellm_url = cfg.get("url") or "http://localhost:3001/v1"
    chat_url = f"{freellm_url.rstrip('/')}/chat/completions"
    api_key = cfg.get("api_key", "")
    default_model = cfg.get("model") or "auto"

    payload = {
        "model": model or default_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temp,
        "format": "json",
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    import urllib.request
    import urllib.error
    import time

    max_retries = int(os.getenv("STRIKE_MAX_RETRIES", "3"))
    backoff = float(os.getenv("STRIKE_RETRY_BACKOFF", "1.5"))
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(
                chat_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # OpenAI-compatible response shape
            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            content = message.get("content", "") if isinstance(message, dict) else str(choice)

            # If the model returns a valid JSON resume, return it directly.
            parsed = extract_json(content)
            if isinstance(parsed, dict):
                return json.dumps(parsed, indent=2)
            # Otherwise return the raw text and let downstream parsers handle it.
            return content

        except urllib.error.HTTPError as e:
            last_exception = e
            status = e.code
            if status in (429, 502, 503) and attempt < max_retries:
                wait = backoff * (2 ** attempt)
                print(f"\033[93m[!] FreeLLM API returned {status}. Retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\033[0m")
                time.sleep(wait)
                continue
            break
        except Exception as e:
            last_exception = e
            break

    print(f"\033[93m[!] FreeLLM API call failed: {last_exception}. Using mock response.\033[0m")
    return _mock_llm_response()


def _mock_llm_response() -> str:
    """Deterministic fallback resume used when the LLM API is unavailable.

    Replace this with your own generic placeholder if you want the mock
    mode to produce something closer to your profile. Do not put real PII here.
    """
    return json.dumps({
        "name": "Your Name",
        "email": "you@example.com",
        "phone": "(000) 000-0000",
        "linkedin": "linkedin.com/in/yourprofile",
        "github": "github.com/yourusername",
        "contact_info": "Your City, ST",
        "summary": "Senior Backend Engineer with 5 years experience in Python, Django, PostgreSQL, AWS, Git, Docker, and Kubernetes.",
        "skills": ["Python", "Django", "PostgreSQL", "AWS", "Git", "Docker", "Kubernetes", "CI/CD"],
        "experience": [
            {
                "company": "Example Corp",
                "role": "Senior Backend Engineer",
                "dates": "2021-2026",
                "bullets": [
                    "Built Python/Django APIs serving 1M+ daily requests on AWS.",
                    "Deployed Docker/Kubernetes CI/CD pipelines cutting release time by 50%.",
                    "Led a team of 4 engineers using Git workflows and code reviews.",
                ],
            }
        ],
        "projects": [],
        "education": [{"degree": "B.S. Computer Science", "institution": "Example University", "location": "Remote", "date": "2018"}],
        "certifications": ["AWS Solutions Architect"],
    }, indent=2)


# ── Pipeline Orchestrator ───────────────────────────────────────────────────
def run_baseline_ats_score(job_desc: str, master_resume_text: str, target_dir: str) -> Optional[Dict]:
    """Score the master resume against the JD before strikes, save baseline_score.json."""
    if ATSScorer is None:
        return None
    scorer = ATSScorer()
    result = scorer.score_resume_text_vs_jd(master_resume_text, job_desc)
    scorer.save_baseline_score(result, target_dir)
    return result


def run_post_strike_ats_score(
    resume_data: Dict,
    job_desc: str,
    target_dir: str,
    job_id: str,
    database_name: Optional[str] = None,
) -> Optional[Dict]:
    """Score the final generated resume against the JD, save final_score.json, update DB."""
    if ATSScorer is None:
        return None

    try:
        # Load baseline score for comparison
        baseline_path = os.path.join(target_dir, "baseline_score.json")
        baseline_score = None
        if os.path.exists(baseline_path):
            with open(baseline_path, "r", encoding="utf-8") as f:
                baseline_data = json.load(f)
                baseline_score = baseline_data.get("score")

        # Score the final resume
        scorer = ATSScorer()
        resume_text = flatten_resume_to_text(resume_data)
        final_result = scorer.score_resume_text_vs_jd(resume_text, job_desc, target_dir=target_dir)
        final_score = final_result.get("score", 0)

        # Add baseline/improvement into the saved file
        scorer.save_final_score(final_result, target_dir, baseline_score=baseline_score)

        # Calculate improvement
        improvement = None
        if baseline_score is not None:
            improvement = round(final_score - baseline_score, 2)
            print(f"\033[96m[*] ATS SCORE: {baseline_score} → {final_score} ({'+' if improvement >= 0 else ''}{improvement})\033[0m")
        else:
            print(f"\033[96m[*] ATS FINAL SCORE: {final_score}/100\033[0m")

        # Validation warnings
        if improvement is not None and improvement < 10:
            print(f"\033[93m[!] WARNING: Low improvement ({improvement} points). Strikes may not have bridged key gaps.\033[0m")
        if final_score < 50:
            print(f"\033[91m[!] WARNING: Final score {final_score} below 50. Resume may not pass ATS filters.\033[0m")

        # Update job record with final score if the column exists
        try:
            conn = get_db(database_name)
            cur = conn.execute("PRAGMA table_info(jobs)")
            columns = {row["name"] for row in cur.fetchall()}
            if "score" in columns:
                conn.execute("UPDATE jobs SET score=? WHERE id=?", (int(final_score), job_id))
                conn.commit()
            conn.close()
        except Exception as e:
            print(f"\033[93m[!] Could not store final ATS score: {e}\033[0m")

        return final_result
    except Exception as e:
        print(f"\033[93m[!] Post-generation ATS scoring failed (non-critical): {e}\033[0m")
        return None


def execute_strike(
    job_id: str,
    model: str = "default",
    temp: float = 0.3,
    session_id: Optional[str] = None,
    prompt_override: Optional[str] = None,
    resume_file: Optional[str] = None,
    database_name: Optional[str] = None,
    force: bool = False,
) -> Dict:
    """
    Execute the full 6-strike pipeline for a single job.

    Loads the job from the database, runs Strike 1 keyword extraction,
    runs Strikes 2-6, and performs post-generation ATS scoring.
    """
    conn = get_db(database_name)
    row = conn.execute(
        "SELECT raw_json, title, company FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"status": "failed", "error": "Job Not Found"}

    title, company = row["title"], row["company"]
    target_dir = get_target_dir(job_id, title, company)
    log_path = os.path.join(target_dir, "full_transmission_log.txt")

    job_data = json.loads(row["raw_json"])
    job_desc = job_data.get("description", {}).get("text", "")

    # Load master resume
    master_resume_file = resume_file
    if not master_resume_file:
        project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
        candidates = [os.path.join(project_root, "master.txt")]
        resumes_dir = os.path.join(project_root, "resumes")
        if os.path.isdir(resumes_dir):
            candidates.extend(sorted(os.path.join(resumes_dir, f) for f in os.listdir(resumes_dir) if f.endswith(".txt")))
        for c in candidates:
            if os.path.exists(c):
                master_resume_file = c
                break
    if not master_resume_file:
        master_resume_file = os.path.join(os.path.dirname(__file__), "..", "master.txt")
    try:
        with open(master_resume_file, "r", encoding="utf-8") as f:
            master_resume_text = f.read()
    except Exception:
        master_resume_text = ""

    # ── Baseline ATS score ───────────────────────────────────────────────
    baseline_result = run_baseline_ats_score(job_desc, master_resume_text, target_dir)
    if baseline_result:
        baseline_score = baseline_result.get("score", 0)
        max_points = baseline_result.get("max_points", 0)
        threshold = float(os.getenv("ATS_GATE_THRESHOLD", "35.0"))
        min_signal = float(os.getenv("ATS_GATE_MIN_SIGNAL", "5.0"))
        gate_passed = baseline_score >= threshold and max_points >= min_signal
        print(f"\033[96m[*] ATS GATE: Score {baseline_score}/100 (threshold: {threshold}, signal: {max_points}) - {'PASSED' if gate_passed else 'LOW'}\033[0m")
        # The gate is advisory only. We always continue so dispatcher/logistics/soft-skill
        # jobs don't get blocked just because the lexicon is tech-heavy.
        if not gate_passed:
            print(f"\033[93m[!] ATS GATE ADVISORY: continuing strike despite low signal.\033[0m")

    # ── Strike 1: NEON TERMINATOR keyword extraction ─────────────────────
    prompt1 = prompt_override or load_prompt("prompt1-hard-soft-domain-gatekeeper.md")
    prompt1 = prompt1.replace("{{RESUME_TEXT}}", master_resume_text).replace("{{JOB_DESCRIPTION}}", job_desc)
    prompt1 = prompt1.replace("{resume_text}", master_resume_text).replace("{job_desc}", job_desc)

    neon_output = call_llm(prompt1, model=model, temp=temp)
    extracted_kws = parse_neon_output(neon_output)
    keyword_block = _build_weighted_keyword_block(extracted_kws)

    # Save strike 1 log
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"=== STRIKE 1: NEON TERMINATOR ===\n{neon_output}\n\n")

    # ── Strikes 2-6: Resume generation ────────────────────────────────────
    prompts = [
        ("prompt2-summary.md", "STRIKE 2: SUMMARY"),
        ("prompt3-skills.md", "STRIKE 3: SKILLS"),
        ("prompt4-projects-edu-certs.md", "STRIKE 4: PROJECTS / EDU / CERTS"),
        ("prompt5-experience.md", "STRIKE 5: EXPERIENCE"),
        ("prompt5-experience.md", "STRIKE 6: FINAL POLISH"),
    ]

    last_output = ""
    json_payload: Optional[Dict] = None

    for idx, (prompt_name, label) in enumerate(prompts, start=2):
        prompt_template = load_prompt(prompt_name)
        if not prompt_template:
            continue

        prompt = inject_keyword_block(prompt_template, keyword_block)
        prompt = prompt.replace("{{RESUME_TEXT}}", master_resume_text).replace("{{JOB_DESCRIPTION}}", job_desc)
        prompt = prompt.replace("{resume_text}", master_resume_text).replace("{job_desc}", job_desc)
        if last_output:
            prompt = prompt.replace("{{RESUME_MD}}", last_output)
            prompt = prompt.replace("{previous_resume}", last_output)

        last_output = call_llm(prompt, model=model, temp=temp)
        json_payload = extract_json(last_output)

        # Clean/overlay contact info on extracted JSON
        if json_payload:
            json_payload = _clean_resume_json(json_payload, master_resume_text)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"=== {label} ===\n{last_output}\n\n")

    # ── Save final resume.json ────────────────────────────────────────────
    if json_payload:
        resume_json_path = os.path.join(target_dir, "resume.json")
        with open(resume_json_path, "w", encoding="utf-8") as f:
            json.dump(json_payload, f, indent=2, ensure_ascii=False)

        # ── Post-generation ATS scoring ────────────────────────────────
        run_post_strike_ats_score(json_payload, job_desc, target_dir, job_id, database_name=database_name)

        # ── Auto-generate PDF ──────────────────────────────────────────
        pdf_path_out = ""
        if pdf_engine is not None:
            try:
                print(f"[*] AUTO-ENGAGING PDF ENGINE FOR {job_id}...")
                pdf_res = pdf_engine.generate_pdf(job_id)
                if pdf_res.get('status') == 'success':
                    pdf_path_out = pdf_res.get('path', '')
            except Exception as e:
                print(f"\033[93m[!] AUTO-PDF FAIL: {e}\033[0m")

        # Update job status to DELIVERED (resume + PDF artifacts complete)
        try:
            conn = get_db(database_name)
            conn.execute("UPDATE jobs SET status='DELIVERED' WHERE id=?", (job_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"\033[93m[!] Could not update job status: {e}\033[0m")

        return {
            "status": "success",
            "job_id": job_id,
            "target_dir": target_dir,
            "resume_json": resume_json_path,
            "log": log_path,
            "pdf_url": pdf_path_out,
        }

    return {"status": "failed", "error": "Could not extract final resume JSON"}


# ── CLI entrypoint ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Auto-ATS Strike Handler")
    parser.add_argument("job_id", help="Job ID to process")
    parser.add_argument("--model", default="default")
    parser.add_argument("--temp", type=float, default=0.3)
    parser.add_argument("--resume-file", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    result = execute_strike(
        job_id=args.job_id,
        model=args.model,
        temp=args.temp,
        resume_file=args.resume_file,
        database_name=args.db,
    )
    print(json.dumps(result, indent=2))
