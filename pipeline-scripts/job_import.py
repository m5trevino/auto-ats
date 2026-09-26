"""
Job Import Engine — Manual, semi-auto, and automated job ingestion.

Normalizes job records from many sources (Indeed, LinkedIn, Apify, JSON feeds,
manual forms, browser userscripts) into the canonical jobs.db schema.
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    import dateutil.parser
except ImportError:
    dateutil = None

DB_FILE = os.getenv("AUTOATS_DB", "jobs.db")
BLACKLIST_FILE = "blacklist.json"
TAGS_FILE = "categorized_tags.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_str(val) -> str:
    return str(val).strip() if val is not None else ""


def _normalize_salary(job: Dict):
    """Return (annual_pay_int, pay_fmt_string) from many possible salary shapes."""
    bs = job.get("baseSalary") or job.get("salary") or job.get("compensation") or {}
    if isinstance(bs, dict):
        min_p = bs.get("min") or bs.get("value") or bs.get("minValue") or bs.get("minAmount")
        unit = bs.get("unitOfWork") or bs.get("unit") or bs.get("period") or "YEAR"
        if min_p is None and "minValue" in bs:
            min_p = bs["minValue"]
        try:
            val = float(min_p)
            unit = str(unit).upper()
            annual = 0
            if unit in ("HOUR", "HR", "HOURLY"):
                annual = val * 2080
            elif unit in ("MONTH", "MONTHLY"):
                annual = val * 12
            elif unit in ("WEEK", "WEEKLY"):
                annual = val * 52
            elif unit in ("BIWEEKLY", "BI-WEEKLY"):
                annual = val * 26
            else:
                annual = val
            return int(annual), f"${int(annual / 1000)}k" if annual > 10000 else f"${int(val)}/hr"
        except Exception:
            pass

    # Plain numeric fields
    for k in ("salary_min", "salaryMin", "min_salary", "minSalary", "annual_pay", "pay"):
        try:
            val = float(job.get(k))
            return int(val), f"${int(val / 1000)}k"
        except Exception:
            continue
    return 0, "-"


def _calc_freshness(job: Dict) -> int:
    raw = job.get("datePublished") or job.get("date_posted") or job.get("postedAt") or job.get("published") or job.get("date")
    if not raw:
        return 0
    try:
        if dateutil:
            dt = dateutil.parser.isoparse(str(raw))
        else:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max(0, (now - dt).days)
    except Exception:
        return 0


def _load_tags() -> Dict:
    if not os.path.exists(TAGS_FILE):
        return {}
    try:
        with open(TAGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_blacklist() -> List[str]:
    if not os.path.exists(BLACKLIST_FILE):
        return []
    try:
        with open(BLACKLIST_FILE, "r") as f:
            return [x.lower() for x in json.load(f)]
    except Exception:
        return []


def _calculate_score(job: Dict, tags_db: Dict) -> int:
    score = 50
    signals = []
    for k, v in job.get("attributes", {}).items():
        if isinstance(v, str):
            signals.append(v.lower())

    title = str(job.get("title", "")).lower()
    company = str(job.get("company", job.get("employer", ""))).lower()

    quals = [x.lower() for x in tags_db.get("qualifications", [])]
    skills = [x.lower() for x in tags_db.get("skills", [])]
    benefits = [x.lower() for x in tags_db.get("benefits", [])]
    denied = [x.lower() for x in tags_db.get("denied", [])]

    for sig in signals:
        if sig in quals:
            score += 10
        if sig in skills:
            score += 10
        if sig in benefits:
            score += 5
        if sig in denied:
            score -= 30

    if any(w in title for w in ("architect", "automation", "logistics", "senior", "lead", "principal")):
        score += 15
    if any(w in title for w in ("junior", "entry", "intern", "associate")):
        score -= 10

    return max(0, min(100, score))


def normalize_job(record: Dict) -> Optional[Dict]:
    """Convert a job record from any source into the canonical DB shape."""
    if not isinstance(record, dict):
        return None

    # ID
    jid = record.get("key") or record.get("id") or record.get("job_id") or record.get("jobId")
    if not jid:
        jid = f"manual_{int(datetime.now().timestamp() * 1000)}"

    # Title
    title = record.get("title") or record.get("jobTitle") or "Unknown"

    # Company / employer
    employer = record.get("employer")
    if isinstance(employer, dict):
        company = employer.get("name") or employer.get("companyName") or "Unknown"
    else:
        company = record.get("company") or record.get("companyName") or record.get("organization") or "Unknown"

    # Location
    loc = record.get("location") or {}
    if isinstance(loc, dict):
        city = loc.get("city") or loc.get("name") or loc.get("location") or ""
        state = loc.get("state") or loc.get("admin1Code") or loc.get("region") or ""
    else:
        city = str(loc)
        state = ""

    # Description (canonicalize to Apify-ish shape)
    desc = record.get("description")
    if isinstance(desc, dict):
        desc_text = desc.get("text") or desc.get("html") or desc.get("content") or ""
    else:
        desc_text = str(desc or "")

    # URL
    url = (
        record.get("url")
        or record.get("link")
        or record.get("jobUrl")
        or record.get("job_url")
        or record.get("applicationUrl")
        or "#"
    )

    # Salary
    annual_pay, pay_fmt = _normalize_salary(record)

    # Date posted
    freshness = _calc_freshness(record)

    # Build canonical raw_json compatible with the rest of the app
    canonical = {
        "key": jid,
        "title": title,
        "employer": {"name": company},
        "location": {"city": city, "admin1Code": state},
        "datePublished": record.get("datePublished") or record.get("date_posted") or record.get("postedAt") or _now_iso(),
        "baseSalary": {"min": annual_pay, "unitOfWork": "YEAR"},
        "attributes": record.get("attributes") or {},
        "description": {"text": desc_text, "html": desc_text},
        "url": url,
    }

    tags_db = _load_tags()
    blacklist = _load_blacklist()
    score = _calculate_score(canonical, tags_db)

    lower_title = title.lower()
    lower_company = company.lower()
    status = "NEW"
    for term in blacklist:
        if term and (term in lower_title or term in lower_company):
            status = "AUTO_DENIED"
            break

    return {
        "id": jid,
        "title": title,
        "company": company,
        "city": city,
        "state": state,
        "date_posted": freshness,
        "annual_pay": annual_pay,
        "pay_fmt": pay_fmt,
        "score": score,
        "raw_json": json.dumps(canonical),
        "status": status,
    }


def add_job(record: Dict) -> Dict:
    """Insert a single normalized job record, skipping duplicates."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS jobs
           (id TEXT PRIMARY KEY, title TEXT, company TEXT, city TEXT,
            state TEXT, date_posted INTEGER, annual_pay INTEGER,
            pay_fmt TEXT, score INTEGER, raw_json TEXT, status TEXT)"""
    )

    job = normalize_job(record)
    if not job:
        conn.close()
        return {"status": "error", "reason": "invalid record"}

    c.execute("SELECT id FROM jobs WHERE id=?", (job["id"],))
    if c.fetchone():
        conn.close()
        return {"status": "skipped", "id": job["id"], "reason": "duplicate"}

    c.execute(
        "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            job["id"], job["title"], job["company"], job["city"], job["state"],
            job["date_posted"], job["annual_pay"], job["pay_fmt"], job["score"],
            job["raw_json"], job["status"],
        ),
    )
    conn.commit()
    conn.close()
    return {"status": "added", "id": job["id"], "score": job["score"], "job_status": job["status"]}


def import_jobs(records: List[Dict]) -> Dict:
    """Bulk import a list of job records. Returns stats."""
    added = skipped = blacklisted = 0
    ids = []
    for record in records:
        res = add_job(record)
        if res["status"] == "added":
            added += 1
            ids.append(res["id"])
            if res.get("job_status") == "AUTO_DENIED":
                blacklisted += 1
        elif res["status"] == "skipped":
            skipped += 1
    return {"added": added, "skipped": skipped, "blacklisted": blacklisted, "ids": ids}
