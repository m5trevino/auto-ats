import sqlite3
import json
import os
from datetime import datetime, timezone
import dateutil.parser

DB_FILE = "jobs.db"
BLACKLIST_FILE = "blacklist.json"

def calc_freshness(job):
    try:
        raw = job.get('datePublished')
        if not raw: return 999
        dt = dateutil.parser.isoparse(raw)
        now = datetime.now(timezone.utc)
        return (now - dt).days
    except: return 999

def safe_str(val):
    return str(val) if val is not None else ""

def normalize_pay(job):
    try:
        bs = job.get('baseSalary', {})
        if not bs: return 0, "-"
        min_p = bs.get('min')
        if not min_p: return 0, "-"
        unit = bs.get('unitOfWork', 'YEAR')
        val = float(min_p)
        annual = 0
        if unit == 'HOUR': annual = val * 2080
        elif unit == 'MONTH': annual = val * 12
        elif unit == 'WEEK': annual = val * 52
        else: annual = val
        return int(annual), f"${int(annual/1000)}k" if annual > 10000 else f"${int(val)}/hr"
    except: return 0, "-"

TAGS_FILE = "categorized_tags.json"

def calculate_score(job, tags_db):
    score = 50 # Base score

    # Extract all possible signal strings from the job data
    signals = []
    # 1. Attributes/Tags
    for k, v in job.get('attributes', {}).items():
        if isinstance(v, str): signals.append(v.lower())

    # 2. Title keywords
    title = job.get('title', '').lower()

    # Cross-reference with our vault
    quals = [x.lower() for x in tags_db.get('qualifications', [])]
    skills = [x.lower() for x in tags_db.get('skills', [])]
    benefits = [x.lower() for x in tags_db.get('benefits', [])]
    denied = [x.lower() for x in tags_db.get('denied', [])]

    for sig in signals:
        if sig in quals: score += 10
        if sig in skills: score += 10
        if sig in benefits: score += 5
        if sig in denied: score -= 30

    # Bonus for title keywords (Basic check)
    if "architect" in title or "automation" in title or "logistics" in title:
        score += 15

    return max(0, min(100, score))

def process_files(file_list):
    print(f"--- STARTING MIGRATION ON {len(file_list)} FILES ---")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Ensure table exists
    c.execute('''CREATE TABLE IF NOT EXISTS jobs
                 (id TEXT PRIMARY KEY, title TEXT, company TEXT, city TEXT,
                  state TEXT, date_posted INTEGER, annual_pay INTEGER,
                  pay_fmt TEXT, score INTEGER, raw_json TEXT, status TEXT)''')

    # Load Intelligence Vault
    tags_db = {}
    if os.path.exists(TAGS_FILE):
        with open(TAGS_FILE, 'r') as f: tags_db = json.load(f)

    # Load Blacklist
    blacklist = []
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, 'r') as f:
            blacklist = [x.lower() for x in json.load(f)]

    stats = {"new": 0, "skipped": 0, "blacklisted": 0, "files": 0}

    for filename in file_list:
        if not os.path.exists(filename): continue
        print(f"Processing {filename}...")
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = json.load(f)
                jobs = content if isinstance(content, list) else content.get('jobs', [])
        except Exception as e:
            print(f"Error reading {filename}: {e}")
            continue

        stats["files"] += 1

        for job in jobs:
            jid = job.get('key')
            if not jid: jid = f"job_{stats['new']}_{int(datetime.now().timestamp())}"

            c.execute("SELECT status FROM jobs WHERE id=?", (jid,))
            row = c.fetchone()
            if row:
                stats["skipped"] += 1
                continue

            title = safe_str(job.get('title')).lower()
            employer = job.get('employer', {}).get('name', '').lower()
            status = "NEW"

            for term in blacklist:
                if term in title or term in employer:
                    status = "AUTO_DENIED"
                    stats["blacklisted"] += 1
                    break

            # Calculate Intelligence Score
            score = calculate_score(job, tags_db)

            freshness = calc_freshness(job)
            pay_val, pay_fmt = normalize_pay(job)
            loc = job.get('location') or {}

            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                jid,
                job.get('title', 'Unknown'),
                job.get('employer', {}).get('name', 'Unknown'),
                safe_str(loc.get('city')),
                safe_str(loc.get('admin1Code')),
                freshness,
                pay_val,
                pay_fmt,
                score,
                json.dumps(job),
                status
            ))
            if status == "NEW": stats["new"] += 1

    conn.commit()
    conn.close()
    return stats
