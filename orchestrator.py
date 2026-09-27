#!/usr/bin/env python3
"""
orchestrator.py — Resume Strike Team pipeline runner
SQLModel/SQLite + jsonschema validation loop. Zero LLM chain drift:
one API call per agent, one agent_runs row per strike, resume from any break.

Usage:
  python orchestrator.py init-db    --db resume.db
  python orchestrator.py seed       --db resume.db --agents-dir ./agents
  python orchestrator.py ingest-master --db resume.db --master-file master.txt
  python orchestrator.py add-job    --db resume.db --resume-id 1 --jd-file jd.txt
  python orchestrator.py run-approved --db resume.db
  python orchestrator.py status     --db resume.db
  python orchestrator.py smoke      --db resume.db
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import Column, JSON, select
from sqlmodel import Field, Session, SQLModel, create_engine

# Make pipeline-scripts importable so we can reuse the existing FreeLLM wiring.
_pipeline_scripts_dir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "pipeline-scripts")
)
if _pipeline_scripts_dir not in sys.path:
    sys.path.insert(0, _pipeline_scripts_dir)

try:
    from strike_handler import call_llm as _strike_call_llm
except Exception as _import_err:  # noqa: F841
    _strike_call_llm = None

try:
    import jsonschema  # type: ignore
    _HAVE_JSONSCHEMA = True
except ImportError:
    _HAVE_JSONSCHEMA = False

# Reuse existing PDF engine and ATS scorer.
try:
    import pdf_engine
    _PDF_ENGINE = pdf_engine
except Exception as _pdf_err:  # noqa: F841
    _PDF_ENGINE = None

try:
    import ats_scorer
except Exception as _ats_err:  # noqa: F841
    ats_scorer = None

EXECUTION_ORDER = ["TB-101", "TB-102", "TB-103", "TB-104", "TB-105"]

# ---------------------------------------------------------------------------
# SQLModel result helpers
# ---------------------------------------------------------------------------

def _first(result) -> Any:
    """Return the first row as a model instance.

    SQLModel/SQLAlchemy sometimes returns a Row tuple; unwrap it if needed."""
    row = result.first()
    if row is None:
        return None
    if hasattr(row, "__table__"):
        return row
    return row[0] if row else None


def _all(result) -> List[Any]:
    """Return all rows as model instances, unwrapping Row tuples if needed."""
    rows = result.all()
    if rows and not hasattr(rows[0], "__table__"):
        return [r[0] for r in rows]
    return rows


# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------

class Agent(SQLModel, table=True):
    """Agent spec loaded from tb-*.json files. The DB is the runtime source."""
    __tablename__ = "agents"
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True, unique=True)
    name: str
    version: str = "1.0.0"
    classification: str = ""
    identity: str = ""
    processing_protocol: list = Field(default_factory=list, sa_column=Column(JSON))
    input_contract: dict = Field(default_factory=dict, sa_column=Column(JSON))
    output_contract: dict = Field(default_factory=dict, sa_column=Column(JSON))
    hard_gates: list = Field(default_factory=list, sa_column=Column(JSON))
    error_states: list = Field(default_factory=list, sa_column=Column(JSON))
    validation: dict = Field(default_factory=dict, sa_column=Column(JSON))
    type_map: dict = Field(default_factory=dict, sa_column=Column(JSON))
    canonical_map: dict = Field(default_factory=dict, sa_column=Column(JSON))
    exemplar: dict = Field(default_factory=dict, sa_column=Column(JSON))
    input_payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    is_active: bool = True


class Resume(SQLModel, table=True):
    """Top-level resume container. The ingester parses master.txt into
    normalized tables and keeps the original raw_text for reference."""
    __tablename__ = "resumes"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    raw_text: str = ""
    parsed_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResumeHeader(SQLModel, table=True):
    __tablename__ = "resume_headers"
    id: Optional[int] = Field(default=None, primary_key=True)
    resume_id: int = Field(index=True)
    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    location: str = ""
    contact_info: str = ""
    summary: str = ""


class ResumeExperience(SQLModel, table=True):
    __tablename__ = "resume_experiences"
    id: Optional[int] = Field(default=None, primary_key=True)
    resume_id: int = Field(index=True)
    company: str = ""
    role: str = ""
    dates: str = ""
    location: str = ""
    bullets: list = Field(default_factory=list, sa_column=Column(JSON))
    sort_order: int = 0


class ResumeSkill(SQLModel, table=True):
    __tablename__ = "resume_skills"
    id: Optional[int] = Field(default=None, primary_key=True)
    resume_id: int = Field(index=True)
    skill: str = Field(index=True)
    category: str = ""
    sort_order: int = 0


class ResumeProject(SQLModel, table=True):
    __tablename__ = "resume_projects"
    id: Optional[int] = Field(default=None, primary_key=True)
    resume_id: int = Field(index=True)
    name: str = ""
    role: str = ""
    description: str = ""
    bullets: list = Field(default_factory=list, sa_column=Column(JSON))
    sort_order: int = 0


class ResumeEducation(SQLModel, table=True):
    __tablename__ = "resume_education"
    id: Optional[int] = Field(default=None, primary_key=True)
    resume_id: int = Field(index=True)
    degree: str = ""
    institution: str = ""
    location: str = ""
    date: str = ""
    sort_order: int = 0


class ResumeCertification(SQLModel, table=True):
    __tablename__ = "resume_certifications"
    id: Optional[int] = Field(default=None, primary_key=True)
    resume_id: int = Field(index=True)
    name: str = ""
    sort_order: int = 0


class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    id: Optional[int] = Field(default=None, primary_key=True)
    resume_id: int = Field(index=True)
    raw_jd_text: str = ""
    status: str = Field(default="APPROVED", index=True)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    agent_id: str = Field(index=True)
    seq: int
    input_json: str = ""
    input_hash: str = ""
    output_json: Optional[str] = None
    status: str = Field(default="QUEUED", index=True)
    error: Optional[str] = None
    warnings: list = Field(default_factory=list, sa_column=Column(JSON))
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# LLM SEAM — reuse existing FreeLLM/OpenAI-compatible wiring
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Very rough token estimate (≈ 4 chars/token for English + JSON)."""
    return max(1, len(text) // 4)


_RESUME_SCHEMA_KEYS = (
    "name", "email", "phone", "linkedin", "github", "contact_info",
    "summary", "skills", "experience", "projects", "education", "certifications",
)


def _mock_llm_for_agent(agent_id: str, payload: dict) -> str:
    """Deterministic mock responses per agent so smoke tests work offline.

    Mutates the input resume_json in-place so the full schema is preserved."""
    resume = payload.get("resume_json", {}) if isinstance(payload.get("resume_json"), dict) else {}
    for key in _RESUME_SCHEMA_KEYS:
        if key not in resume:
            resume[key] = [] if key in ("skills", "experience", "projects", "education", "certifications") else ""

    if agent_id == "TB-101":
        return json.dumps({
            "hard_skills": ["Python", "AWS", "Docker"],
            "soft_skills": ["Communication", "Leadership"],
            "domain_keywords": ["SaaS", "Logistics"],
            "gatekeeper_credentials": ["AWS Solutions Architect"],
            "required_priorities": [
                {"keyword": "Python", "priority": 10, "mention_count": 3},
                {"keyword": "AWS", "priority": 9, "mention_count": 2},
            ],
            "preferred_priorities": [
                {"keyword": "Docker", "priority": 4, "mention_count": 1},
            ],
        })

    if agent_id == "TB-102":
        resume["summary"] = "Systems architect with deep Python and AWS experience building resilient SaaS and logistics platforms."
    elif agent_id == "TB-103":
        resume["skills"] = ["Python", "AWS", "Docker", "Kubernetes", "PostgreSQL", "Communication"]
    elif agent_id == "TB-104" and resume.get("projects"):
        for p in resume["projects"]:
            desc = p.get("description", "")
            if "Python" not in desc:
                p["description"] = (desc + " Built with Python and AWS.").strip()
    elif agent_id == "TB-105":
        for exp in resume.get("experience", []):
            bullets = exp.get("bullets", [])
            if not bullets or not any(ch.isdigit() for ch in " ".join(bullets)):
                exp.setdefault("bullets", []).append("Improved throughput by 25% through system optimization.")
        resume.setdefault("polish_warnings", [])

    return json.dumps(resume)


def call_llm(agent_id: str, prompt: str, payload: dict) -> tuple[str, int, int]:
    """Single-agent API call. Returns (raw_text, tokens_in, tokens_out).

    Uses the existing strike_handler.call_llm wiring when available.
    Set MOCK_LLM=1 env var for deterministic offline smoke tests.
    One call per agent — never chain conversations across strikes."""
    if os.getenv("MOCK_LLM"):
        raw = _mock_llm_for_agent(agent_id, payload)
        return raw, _estimate_tokens(prompt), _estimate_tokens(raw)

    if _strike_call_llm is None:
        raise RuntimeError(
            "strike_handler.call_llm could not be imported. "
            "Set MOCK_LLM=1 for offline tests or fix the import path."
        )

    raw = _strike_call_llm(prompt, model="auto", temp=0.7)
    return raw, _estimate_tokens(prompt), _estimate_tokens(raw)


# ---------------------------------------------------------------------------
# PROMPT RENDERING
# ---------------------------------------------------------------------------

def render_prompt(agent: Agent, payload: dict) -> str:
    parts = [
        f"MACHINE TALK — {agent.agent_id}: {agent.name}",
        f"VERSION: {agent.version}",
        f"CLASSIFICATION: {agent.classification}",
        "",
        "IDENTITY",
        agent.identity,
        "",
        "PROCESSING PROTOCOL — execute in exact order. Do not skip. Do not reorder.",
        json.dumps(agent.processing_protocol, indent=1),
        "",
        "HARD GATES",
        json.dumps(agent.hard_gates, indent=1),
        "",
        "ERROR STATES",
        json.dumps(agent.error_states, indent=1),
        "",
        "TYPE_MAP",
        json.dumps(agent.type_map, indent=1),
        "",
        "VALIDATION",
        json.dumps(agent.validation, indent=1),
        "",
        "OUTPUT CONTRACT",
        json.dumps(agent.output_contract, indent=1),
        "",
        "EXEMPLAR",
        json.dumps(agent.exemplar, indent=1),
        "",
        "INPUT_PAYLOAD",
        json.dumps(payload, indent=1),
        "",
        "Respond with the OUTPUT CONTRACT object as raw JSON only. "
        "No markdown fences. No prose. No commentary before or after the JSON.",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# OUTPUT PARSING + VALIDATION
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_BANNED_HTML_RE = re.compile(r"<(table|tr|td|th|img|div|span|html|br)\b", re.IGNORECASE)


def parse_json_output(raw: str) -> dict:
    text = raw.strip()
    text = _FENCE_RE.sub("", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start:end + 1])


class _MiniValidator:
    """Fallback validator covering the subset of JSON Schema these contracts use."""

    def validate(self, data, schema, path="$") -> List[str]:
        errs: List[str] = []
        t = schema.get("type")
        py_t = {"object": dict, "array": list, "string": str,
                "integer": int, "number": (int, float), "boolean": bool}.get(t)
        if py_t and not isinstance(data, py_t):
            return [f"{path}: expected {t}, got {type(data).__name__}"]
        if t == "integer" and isinstance(data, bool):
            return [f"{path}: expected integer, got boolean"]
        if t == "object":
            for req in schema.get("required", []):
                if req not in data:
                    errs.append(f"{path}: missing required field '{req}'")
            props = schema.get("properties", {})
            for k, sub in props.items():
                if k in data:
                    errs += self.validate(data[k], sub, f"{path}.{k}")
            if schema.get("additionalProperties") is False:
                for k in data:
                    if k not in props:
                        errs.append(f"{path}: unexpected field '{k}'")
        if t == "array" and "items" in schema:
            for i, item in enumerate(data):
                errs += self.validate(item, schema["items"], f"{path}[{i}]")
        if t in ("integer", "number"):
            if "minimum" in schema and data < schema["minimum"]:
                errs.append(f"{path}: {data} < minimum {schema['minimum']}")
            if "maximum" in schema and data > schema["maximum"]:
                errs.append(f"{path}: {data} > maximum {schema['maximum']}")
        return errs


_MINI = _MiniValidator()


def validate_against_contract(agent: Agent, data: dict) -> List[str]:
    errors: List[str] = []
    contract = agent.output_contract or {}
    if _HAVE_JSONSCHEMA:
        try:
            jsonschema.validate(data, contract)
        except jsonschema.ValidationError as e:
            errors.append(str(e.message))
    else:
        errors += _MINI.validate(data, contract)
    v = agent.validation or {}
    if v.get("no_tables_or_html") and isinstance(data, dict):
        blob = json.dumps(data)
        if _BANNED_HTML_RE.search(blob):
            errors.append("validation: tables/HTML detected")
    return errors


def extract_warnings(data: dict) -> List[str]:
    """adaptation_warnings and polish_warnings are degradations, not failures."""
    out: List[str] = []
    for key in ("adaptation_warnings", "polish_warnings"):
        val = data.get(key)
        if isinstance(val, list):
            out += [str(x) for x in val]
    return out


# ---------------------------------------------------------------------------
# INPUT ASSEMBLERS
# ---------------------------------------------------------------------------

def get_last_output(session: Session, job_id: int, agent_id: str) -> dict:
    run = _first(
        session.exec(
            select(AgentRun)
            .where(AgentRun.job_id == job_id, AgentRun.agent_id == agent_id,
                   AgentRun.status == "SUCCEEDED")
            .order_by(AgentRun.seq.desc())
        )
    )
    if run is None or not run.output_json:
        raise RuntimeError(f"no SUCCEEDED output for {agent_id} on job {job_id}")
    return json.loads(run.output_json)


def assemble_resume_flat(session: Session, resume_id: int) -> dict:
    """Build the full flat resume JSON from normalized tables.

    Falls back to Resume.parsed_json if the normalized tables are empty.
    """
    resume = session.get(Resume, resume_id)
    if resume is None:
        raise RuntimeError(f"resume {resume_id} not found")

    header = _first(
        session.exec(select(ResumeHeader).where(ResumeHeader.resume_id == resume_id))
    )
    experiences = _all(
        session.exec(
            select(ResumeExperience)
            .where(ResumeExperience.resume_id == resume_id)
            .order_by(ResumeExperience.sort_order)
        )
    )
    skills = _all(
        session.exec(
            select(ResumeSkill)
            .where(ResumeSkill.resume_id == resume_id)
            .order_by(ResumeSkill.sort_order)
        )
    )
    projects = _all(
        session.exec(
            select(ResumeProject)
            .where(ResumeProject.resume_id == resume_id)
            .order_by(ResumeProject.sort_order)
        )
    )
    education = _all(
        session.exec(
            select(ResumeEducation)
            .where(ResumeEducation.resume_id == resume_id)
            .order_by(ResumeEducation.sort_order)
        )
    )
    certifications = _all(
        session.exec(
            select(ResumeCertification)
            .where(ResumeCertification.resume_id == resume_id)
            .order_by(ResumeCertification.sort_order)
        )
    )

    if not header and not experiences and not skills:
        return resume.parsed_json or {"raw_text": resume.raw_text}

    return {
        "name": header.name if header else "",
        "email": header.email if header else "",
        "phone": header.phone if header else "",
        "linkedin": header.linkedin if header else "",
        "github": header.github if header else "",
        "contact_info": header.contact_info if header else "",
        "summary": header.summary if header else "",
        "skills": [s.skill for s in skills],
        "experience": [
            {
                "company": e.company,
                "role": e.role,
                "dates": e.dates,
                "location": e.location,
                "bullets": e.bullets,
            }
            for e in experiences
        ],
        "projects": [
            {
                "name": p.name,
                "role": p.role,
                "description": p.description,
                "bullets": p.bullets,
            }
            for p in projects
        ],
        "education": [
            {
                "degree": e.degree,
                "institution": e.institution,
                "location": e.location,
                "date": e.date,
            }
            for e in education
        ],
        "certifications": [c.name for c in certifications],
    }


def _kw(item: Any) -> str:
    return item["keyword"] if isinstance(item, dict) else str(item)


def _prio(item: Any) -> int:
    return int(item.get("priority", 5)) if isinstance(item, dict) else 5


def render_weighted_block(recon: dict) -> str:
    """TB-101 recon → formatted priority block for TB-105."""
    all_items = list(recon.get("required_priorities", [])) + list(recon.get("preferred_priorities", []))

    def tier(lo: int, hi: int) -> List[str]:
        return [_kw(i) for i in all_items if lo <= _prio(i) <= hi]

    top5 = [_kw(i) for i in sorted(all_items, key=_prio, reverse=True)[:5]]
    return "\n".join([
        "REQUIRED (Priority 8-10): " + ", ".join(tier(8, 10)),
        "HIGH PRIORITY (Priority 5-7): " + ", ".join(tier(5, 7)),
        "PREFERRED (Priority 1-4): " + ", ".join(tier(1, 4)),
        "TOP 5 MUST-HAVES: " + ", ".join(top5),
    ])


def asm_tb101(session: Session, job: Job) -> dict:
    return {
        "resume_text": json.dumps(assemble_resume_flat(session, job.resume_id)),
        "job_description": job.raw_jd_text,
    }


def _jd_recon(session: Session, job: Job) -> dict:
    return get_last_output(session, job.id, "TB-101")


def asm_tb102(session: Session, job: Job) -> dict:
    return {
        "resume_json": assemble_resume_flat(session, job.resume_id),
        "job_description": job.raw_jd_text,
        "jd_recon": _jd_recon(session, job),
    }


def asm_tb103(session: Session, job: Job) -> dict:
    return {
        "resume_json": get_last_output(session, job.id, "TB-102"),
        "job_description": job.raw_jd_text,
        "jd_recon": _jd_recon(session, job),
        "max_skills": 24,
    }


def asm_tb104(session: Session, job: Job) -> dict:
    return {
        "resume_json": get_last_output(session, job.id, "TB-103"),
        "job_description": job.raw_jd_text,
        "jd_recon": _jd_recon(session, job),
    }


def asm_tb105(session: Session, job: Job) -> dict:
    recon = _jd_recon(session, job)
    return {
        "resume_md": json.dumps(get_last_output(session, job.id, "TB-104")),
        "job_description": job.raw_jd_text,
        "weighted_keyword_block": render_weighted_block(recon),
    }


ASSEMBLERS: Dict[str, Callable[[Session, Job], dict]] = {
    "TB-101": asm_tb101,
    "TB-102": asm_tb102,
    "TB-103": asm_tb103,
    "TB-104": asm_tb104,
    "TB-105": asm_tb105,
}


# ---------------------------------------------------------------------------
# PIPELINE RUNNER
# ---------------------------------------------------------------------------

def get_agent(session: Session, agent_id: str) -> Agent:
    agent = _first(
        session.exec(
            select(Agent).where(Agent.agent_id == agent_id, Agent.is_active == True)  # noqa: E712
        )
    )
    if agent is None:
        raise RuntimeError(f"agent {agent_id} not found or inactive — run seed first")
    return agent


def _target_dir(job: Job, resume: Resume) -> str:
    """Return a stable target directory path for job artifacts."""
    from pdf_engine import sanitize_filename
    title = sanitize_filename(job.raw_jd_text.split("\n", 1)[0].strip())
    if not title:
        title = f"job_{job.id}"
    name = sanitize_filename(resume.name) if resume else "resume"
    path = os.path.join("targets", f"{title}_{name}_{job.id}")
    os.makedirs(path, exist_ok=True)
    return path


def _flatten_resume_to_text(resume_data: dict) -> str:
    """Flatten a resume JSON dict into searchable text for ATS scoring."""
    parts: List[str] = []
    if resume_data.get("summary"):
        parts.append(str(resume_data["summary"]))
    skills = resume_data.get("skills", [])
    if isinstance(skills, list):
        parts.append(" ".join(str(s) for s in skills))
    for exp in resume_data.get("experience", []):
        parts.append(str(exp.get("company", "")))
        parts.append(str(exp.get("role", "")))
        for bullet in exp.get("bullets", []):
            parts.append(str(bullet))
    for proj in resume_data.get("projects", []):
        parts.append(str(proj.get("name", "")))
        parts.append(str(proj.get("description", "")))
        for bullet in proj.get("bullets", []):
            parts.append(str(bullet))
    for edu in resume_data.get("education", []):
        parts.append(str(edu.get("degree", "")))
        parts.append(str(edu.get("institution", "")))
    for cert in resume_data.get("certifications", []):
        parts.append(str(cert))
    return " ".join(p for p in parts if p)


def _run_ats_scores(job: Job, resume: Resume, final_data: dict, target_dir: str, session: Session) -> None:
    """Compute baseline and final ATS scores using TB-101 recon and save them."""
    recon = get_last_output(session, job.id, "TB-101")
    if not recon:
        print("[!] no TB-101 recon available; skipping ATS scoring")
        return
    if ats_scorer is None:
        print("[!] ats_scorer not available; skipping ATS scoring")
        return

    scorer = ats_scorer.ATSScorer(recon)

    baseline_result = scorer.score_resume_text_vs_jd(resume.raw_text or "", job.raw_jd_text)
    scorer.save_baseline_score(baseline_result, target_dir)
    baseline_score = baseline_result.get("score", 0.0)

    final_text = _flatten_resume_to_text(final_data)
    final_result = scorer.score_resume_text_vs_jd(final_text, job.raw_jd_text)
    scorer.save_final_score(final_result, target_dir, baseline_score=baseline_score)

    improvement = final_result.get("score", 0.0) - baseline_score
    print(f"[ATS] {baseline_score:.1f} → {final_result.get('score', 0.0):.1f} "
          f"({('+' if improvement >= 0 else '')}{improvement:.1f})")


def _generate_pdf_for_job(job: Job, final_data: dict, target_dir: str) -> Optional[str]:
    """Generate a PDF for the final resume and return the PDF path."""
    if _PDF_ENGINE is None:
        print("[!] pdf_engine not available; skipping PDF generation")
        return None

    result = _PDF_ENGINE.generate_pdf(
        job.id,
        resume_data=final_data,
        target_dir=target_dir,
    )
    if result.get("status") == "success":
        pdf_path = result.get("path") or os.path.join(target_dir, "resume.pdf")
        print(f"[PDF] generated {pdf_path}")
        return pdf_path
    print(f"[!] PDF generation failed: {result.get('message')}")
    return None


def _finalize_job(session: Session, job: Job, final_data: dict) -> None:
    """Write resume.json, compute ATS scores, and generate PDF."""
    resume = session.get(Resume, job.resume_id)
    target_dir = _target_dir(job, resume)

    json_path = os.path.join(target_dir, "resume.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, indent=2, ensure_ascii=False)
    print(f"[JSON] final resume written to {json_path}")

    _run_ats_scores(job, resume or Resume(), final_data, target_dir, session)
    _generate_pdf_for_job(job, final_data, target_dir)


def run_job(session: Session, job: Job) -> bool:
    job.status, job.error = "RUNNING", None
    session.add(job)
    session.commit()
    for seq, agent_id in enumerate(EXECUTION_ORDER, start=1):
        agent = get_agent(session, agent_id)
        payload = ASSEMBLERS[agent_id](session, job)
        run = AgentRun(
            job_id=job.id,
            agent_id=agent_id,
            seq=seq,
            input_json=json.dumps(payload),
            input_hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            status="RUNNING",
        )
        session.add(run)
        session.commit()
        try:
            t0 = time.time()
            raw, tin, tout = call_llm(agent_id, render_prompt(agent, payload), payload)
            latency = int((time.time() - t0) * 1000)
            data = parse_json_output(raw)
            errors = validate_against_contract(agent, data)
            if errors:
                raise ValueError("contract violation: " + "; ".join(errors[:5]))
            run.status, run.output_json = "SUCCEEDED", json.dumps(data)
            run.warnings, run.tokens_in, run.tokens_out, run.latency_ms = \
                extract_warnings(data), tin, tout, latency
        except Exception as e:
            run.status, run.error = "FAILED", str(e)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()
            job.status, job.error = "FAILED", f"{agent_id}: {e}"[:2000]
            session.add(job)
            session.commit()
            print(f"[FAIL] job {job.id} at {agent_id}: {e}")
            return False
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        session.commit()
        warn_note = f" warnings={run.warnings}" if run.warnings else ""
        print(f"[ OK ] job {job.id} {agent_id} ({run.latency_ms}ms, {run.tokens_in}+{run.tokens_out} tok){warn_note}")

    # Finalize: write resume.json, score, and generate PDF.
    final_data = get_last_output(session, job.id, "TB-105")
    _finalize_job(session, job, final_data)

    job.status = "SUCCEEDED"
    session.add(job)
    session.commit()
    print(f"[DONE] job {job.id} SUCCEEDED — final resume in agent_runs (TB-105)")
    return True


def run_approved(session: Session) -> None:
    jobs = _all(session.exec(select(Job).where(Job.status == "APPROVED")))
    if not jobs:
        print("no APPROVED jobs")
        return
    for job in jobs:
        run_job(session, job)


# ---------------------------------------------------------------------------
# MASTER RESUME INGESTER
# ---------------------------------------------------------------------------

def _extract_contact(line: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    emails = re.findall(r"[\w.-]+@[\w.-]+\.\w+", line)
    if emails:
        out["email"] = emails[0]
    phones = re.findall(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", line)
    if phones:
        out["phone"] = phones[0]
    linkedins = re.findall(r"linkedin\.com/[^\s|]+", line)
    if linkedins:
        out["linkedin"] = linkedins[0]
    githubs = re.findall(r"github\.com/[^\s|]+", line)
    if githubs:
        out["github"] = githubs[0]
    return out


def _section_header(line: str) -> Optional[str]:
    line = line.strip()
    if not line:
        return None
    if "PROFESSIONAL MANIFESTO" in line or "💀" in line:
        return "summary"
    if "CORE COMPETENCIES" in line or "🛠️" in line:
        return "skills"
    if "TECHNICAL PROJECTS" in line or ("🚀" in line and "PROJECT" in line.upper()):
        return "projects"
    if "PROFESSIONAL EXPERIENCE" in line or ("🏛️" in line and "EXPERIENCE" in line.upper()):
        return "experience"
    if "FOUNDATIONAL VENTURES" in line or "🧬" in line:
        return "ventures"
    if "EDUCATION" in line or "🎓" in line:
        return "education"
    if "CONTINUOUS CONSULTING" in line or "📡" in line:
        return "consulting"
    if "ONBOARDING DEBRIEF" in line or "📑" in line:
        return "footer"
    return None


def ingest_master(session: Session, master_path: str, resume_name: str = "master") -> Resume:
    """Parse master.txt into normalized resume tables and return the Resume row."""
    with open(master_path, "r", encoding="utf-8") as f:
        raw = f.read()
    lines = raw.splitlines()

    name = "Matthew Trevino"
    title = ""
    location = ""
    contact_info = ""
    email = ""
    phone = ""
    linkedin = ""
    github = ""

    for i, line in enumerate(lines[:10]):
        if i == 0 and "TREVINO DOCTRINE" not in line:
            name = line.strip().lstrip("# ").strip() or name
        if "Location:" in line:
            location = line.split("Location:", 1)[1].strip()
        if "Contact:" in line or "Digital:" in line or "•" in line:
            extracted = _extract_contact(line)
            email = email or extracted.get("email", "")
            phone = phone or extracted.get("phone", "")
            linkedin = linkedin or extracted.get("linkedin", "")
            github = github or extracted.get("github", "")
            contact_info = contact_info or line.strip()
        if i == 1 and not title:
            title = line.strip()

    resume = Resume(name=resume_name, raw_text=raw, parsed_json={})
    session.add(resume)
    session.commit()
    session.refresh(resume)

    current_section: Optional[str] = None
    section_buffer: List[str] = []

    def flush_section(section: Optional[str], buf: List[str]) -> None:
        if not section or not buf:
            return
        text = "\n".join(buf).strip()
        if section == "summary":
            header = _first(
                session.exec(select(ResumeHeader).where(ResumeHeader.resume_id == resume.id))
            )
            if header is None:
                header = ResumeHeader(resume_id=resume.id)
            header.name = name
            header.email = email
            header.phone = phone
            header.linkedin = linkedin
            header.github = github
            header.location = location
            header.contact_info = contact_info
            header.summary = text
            session.add(header)
        elif section == "skills":
            _parse_skills_block(session, resume.id, buf)
        elif section in ("experience", "ventures", "consulting"):
            _parse_experience_block(session, resume.id, buf)
        elif section == "projects":
            _parse_projects_block(session, resume.id, buf)
        elif section == "education":
            _parse_education_block(session, resume.id, buf)

    for line in lines[1:]:
        sec = _section_header(line)
        if sec:
            flush_section(current_section, section_buffer)
            current_section = sec
            section_buffer = []
        else:
            section_buffer.append(line)
    flush_section(current_section, section_buffer)

    session.commit()
    return resume


def _parse_skills_block(session: Session, resume_id: int, buf: List[str]) -> None:
    """Skills are tabular: first line = category headers, then tab-separated rows."""
    rows = [ln for ln in buf if ln.strip() and not ln.strip().startswith("-")]
    if not rows:
        return
    categories = [c.strip().rstrip(":") for c in rows[0].split("\t")]
    skill_order = 0
    for row in rows[1:]:
        cells = row.split("\t")
        for idx, cell in enumerate(cells):
            cell = cell.strip()
            if not cell:
                continue
            cat = categories[idx] if idx < len(categories) else ""
            session.add(ResumeSkill(resume_id=resume_id, skill=cell, category=cat, sort_order=skill_order))
            skill_order += 1


def _parse_experience_block(session: Session, resume_id: int, buf: List[str]) -> None:
    """Parse experience blocks. Bullets may be any non-empty line before the next header."""
    i = 0
    existing = _all(
        session.exec(select(ResumeExperience).where(ResumeExperience.resume_id == resume_id))
    )
    sort_order = len(existing)
    while i < len(buf):
        line = buf[i].strip()
        if not line:
            i += 1
            continue

        company = ""
        role = ""
        dates = ""
        location = ""
        bullets: List[str] = []

        if "•" in line or ("|" in line and not line.startswith("-")):
            if "•" in line:
                company, _, rest = line.partition("•")
                company = company.strip()
                location = rest.strip()
            else:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) == 2 and re.search(r"20\d{2}|Present|Seasonal", parts[1]):
                    role = parts[0]
                    dates = parts[1]
                else:
                    company = parts[0]
                    if len(parts) > 1:
                        location = parts[1]
            i += 1
            if i < len(buf):
                nxt = buf[i].strip()
                if nxt and "|" in nxt and not nxt.startswith("-"):
                    parts = [p.strip() for p in nxt.split("|")]
                    if not role:
                        role = parts[0]
                    if not dates and len(parts) > 1:
                        dates = parts[1]
                    if not location and len(parts) > 2:
                        location = parts[2]
                    i += 1
        else:
            role = line
            i += 1

        while i < len(buf):
            bl = buf[i].strip()
            if not bl:
                i += 1
                continue
            if _section_header(bl) is not None:
                break
            if "•" in bl and ("Inc" in bl or "LLC" in bl or "Corp" in bl or "CA" in bl or "Remote" in bl):
                break
            if "|" in bl and re.search(r"20\d{2}|Present|Seasonal", bl):
                break
            bullets.append(bl.lstrip("-• ").strip())
            i += 1

        if company or role:
            session.add(
                ResumeExperience(
                    resume_id=resume_id,
                    company=company,
                    role=role,
                    dates=dates,
                    location=location,
                    bullets=bullets,
                    sort_order=sort_order,
                )
            )
            sort_order += 1


def _parse_projects_block(session: Session, resume_id: int, buf: List[str]) -> None:
    """Parse project blocks:
    Name – Subtitle | Role
    blank
    Description line(s)
    - bullet(s)
    """
    existing = _all(
        session.exec(select(ResumeProject).where(ResumeProject.resume_id == resume_id))
    )
    sort_order = len(existing)
    i = 0
    while i < len(buf):
        line = buf[i].strip()
        if not line:
            i += 1
            continue

        name = ""
        role = ""
        description = ""
        bullets: List[str] = []

        if any(sep in line for sep in ["–", "—", "|"]) or " - " in line:
            for sep in ["–", "—", "|"]:
                if sep in line:
                    parts = [p.strip() for p in line.split(sep, 1)]
                    name = parts[0]
                    rest = parts[1] if len(parts) > 1 else ""
                    if "|" in rest:
                        role, _, desc = rest.partition("|")
                        role = role.strip()
                        description = desc.strip()
                    elif "–" in rest or "—" in rest:
                        sub, _, role_part = rest.partition("–")
                        if not role_part and "—" in rest:
                            sub, _, role_part = rest.partition("—")
                        description = sub.strip()
                        role = role_part.strip()
                    else:
                        description = rest
                    break
        else:
            name = line

        i += 1
        while i < len(buf) and not buf[i].strip():
            i += 1

        desc_lines: List[str] = []
        while i < len(buf):
            bl = buf[i].strip()
            if not bl:
                i += 1
                continue
            if bl.startswith("-") or bl.startswith("•"):
                break
            if any(sep in bl for sep in ["–", "—", "|"]) and (
                "Lead" in bl or "Developer" in bl or "Architect" in bl or "Engineer" in bl or "Manager" in bl
            ):
                break
            desc_lines.append(bl)
            i += 1
        description = (description + " " + " ".join(desc_lines)).strip()

        while i < len(buf):
            bl = buf[i].strip()
            if not bl:
                i += 1
                continue
            if bl.startswith("-") or bl.startswith("•"):
                bullets.append(bl.lstrip("-• ").strip())
                i += 1
            else:
                break

        if name:
            session.add(
                ResumeProject(
                    resume_id=resume_id,
                    name=name,
                    role=role,
                    description=description,
                    bullets=bullets,
                    sort_order=sort_order,
                )
            )
            sort_order += 1


def _parse_education_block(session: Session, resume_id: int, buf: List[str]) -> None:
    in_certs = False
    existing_edu = _all(
        session.exec(select(ResumeEducation).where(ResumeEducation.resume_id == resume_id))
    )
    edu_order = len(existing_edu)
    existing_certs = _all(
        session.exec(select(ResumeCertification).where(ResumeCertification.resume_id == resume_id))
    )
    cert_order = len(existing_certs)
    for line in buf:
        line = line.strip()
        if not line:
            continue
        if "Certifications:" in line or line.lower().startswith("certifications"):
            in_certs = True
            continue
        if in_certs:
            cert = line.lstrip("-• ").strip()
            if cert and cert not in ("Coursework:",):
                session.add(
                    ResumeCertification(resume_id=resume_id, name=cert, sort_order=cert_order)
                )
                cert_order += 1
            continue
        if "Coursework:" in line:
            continue
        if "Associate" in line or "Bachelor" in line or "GED" in line or "College" in line or "University" in line:
            degree = line
            institution = ""
            if ("College" in line or "University" in line) and "," in line:
                institution = line
                degree = ""
            session.add(
                ResumeEducation(
                    resume_id=resume_id,
                    degree=degree,
                    institution=institution,
                    location="",
                    date="",
                    sort_order=edu_order,
                )
            )
            edu_order += 1
        elif edu_order > 0 and ("College" in line or "University" in line or "School" in line):
            last = _first(
                session.exec(
                    select(ResumeEducation)
                    .where(ResumeEducation.resume_id == resume_id)
                    .order_by(ResumeEducation.sort_order.desc())
                )
            )
            if last and not last.institution:
                last.institution = line
                session.add(last)


# ---------------------------------------------------------------------------
# SMOKE TEST
# ---------------------------------------------------------------------------

def run_smoke(session: Session) -> None:
    """Run the full pipeline against a synthetic APPROVED job using MOCK_LLM."""
    agents = _all(session.exec(select(Agent).where(Agent.is_active == True)))  # noqa: E712
    if not agents:
        print("no agents seeded — run: python orchestrator.py seed --agents-dir ./agents")
        return

    resume = _first(session.exec(select(Resume)))
    if resume is None:
        resume = Resume(name="smoke-placeholder", raw_text="", parsed_json={
            "name": "Smoke Test",
            "email": "smoke@example.com",
            "phone": "(000) 000-0000",
            "linkedin": "",
            "github": "",
            "contact_info": "",
            "summary": "Backend engineer with Python and AWS experience.",
            "skills": ["Python", "AWS", "PostgreSQL", "Docker", "Communication"],
            "experience": [
                {
                    "company": "Example Corp",
                    "role": "Backend Engineer",
                    "dates": "2020-2024",
                    "location": "Remote",
                    "bullets": [
                        "Built Python APIs on AWS serving 1M+ daily requests.",
                        "Managed PostgreSQL and Docker CI/CD pipelines.",
                    ],
                }
            ],
            "projects": [
                {
                    "name": "Peacock",
                    "role": "Lead Architect",
                    "description": "Multi-agent AI ecosystem",
                    "bullets": ["Built Python orchestration layer."],
                }
            ],
            "education": [{"degree": "B.S. Computer Science", "institution": "Example University", "location": "Remote", "date": "2018"}],
            "certifications": ["AWS Solutions Architect"],
        })
        session.add(resume)
        session.commit()
        session.refresh(resume)

    job = Job(
        resume_id=resume.id,
        raw_jd_text=(
            "Senior Backend Engineer. Must have: Python, AWS, strong communication. "
            "Nice to have: Kubernetes, AWS Solutions Architect certification. "
            "5+ years building SaaS platforms."
        ),
        status="APPROVED",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    print(f"[SMOKE] job {job.id} approved — running with MOCK_LLM=1")
    run_job(session, job)
    cmd_status(session)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def seed(session: Session, agents_dir: str) -> None:
    import os
    for fname in sorted(os.listdir(agents_dir)):
        if not fname.endswith(".json") or not fname.startswith("tb-"):
            continue
        with open(os.path.join(agents_dir, fname)) as f:
            spec = json.load(f)
        existing = _first(
            session.exec(select(Agent).where(Agent.agent_id == spec["agent_id"]))
        )
        row = existing or Agent(agent_id=spec["agent_id"])
        for key in ("name", "version", "classification", "identity", "processing_protocol",
                    "input_contract", "output_contract", "hard_gates", "error_states",
                    "validation", "type_map", "canonical_map", "exemplar", "input_payload"):
            if key in spec:
                setattr(row, key, spec[key])
        row.is_active = True
        session.add(row)
        print(f"seeded {row.agent_id} ({fname})")
    session.commit()


def cmd_status(session: Session) -> None:
    jobs = _all(session.exec(select(Job).order_by(Job.id)))
    for job in jobs:
        print(f"job {job.id} [{job.status}] resume={job.resume_id} err={job.error or '-'}")
        runs = _all(
            session.exec(
                select(AgentRun).where(AgentRun.job_id == job.id).order_by(AgentRun.seq)
            )
        )
        for r in runs:
            print(
                f"  strike {r.seq} {r.agent_id:<7} [{r.status:<9}] {r.latency_ms}ms"
                + (f" err={r.error}" if r.error else "")
            )


def _add_db_arg(subparser):
    subparser.add_argument("--db", default=None, help="SQLite DB path (can also be global --db)")


def main() -> None:
    p = argparse.ArgumentParser(description="Resume Strike Team orchestrator")
    p.add_argument("--db", default="resume.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    s = sub.add_parser("seed")
    s.add_argument("--agents-dir", default="agents")
    _add_db_arg(s)
    i = sub.add_parser("ingest-master")
    i.add_argument("--master-file", default="master.txt")
    i.add_argument("--resume-name", default="master")
    _add_db_arg(i)
    a = sub.add_parser("add-job")
    a.add_argument("--resume-id", type=int, required=True)
    a.add_argument("--jd-file", required=True)
    _add_db_arg(a)
    r = sub.add_parser("run-approved")
    _add_db_arg(r)
    t = sub.add_parser("status")
    _add_db_arg(t)
    m = sub.add_parser("smoke")
    _add_db_arg(m)

    # Accept --db either before or after the subcommand.
    global_db = None
    if "--db" in sys.argv:
        try:
            idx = sys.argv.index("--db")
            global_db = sys.argv[idx + 1]
        except (ValueError, IndexError):
            pass

    args = p.parse_args()
    db_path = args.db or global_db or "resume.db"

    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if args.cmd == "init-db":
            print(f"schema created in {db_path}")
        elif args.cmd == "seed":
            seed(session, args.agents_dir)
        elif args.cmd == "ingest-master":
            resume = ingest_master(session, args.master_file, args.resume_name)
            print(f"ingested resume {resume.id} from {args.master_file}")
        elif args.cmd == "add-job":
            with open(args.jd_file) as f:
                jd = f.read()
            job = Job(resume_id=args.resume_id, raw_jd_text=jd, status="APPROVED")
            session.add(job)
            session.commit()
            print(f"job {job.id} APPROVED — trigger with run-approved")
        elif args.cmd == "run-approved":
            run_approved(session)
        elif args.cmd == "status":
            cmd_status(session)
        elif args.cmd == "smoke":
            run_smoke(session)


if __name__ == "__main__":
    main()
