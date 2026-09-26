"""
ATS Scorer — Score resumes against job descriptions using weighted keyword matching.

SCORE ARTIFACT SCHEMA CONTRACT (for OpenTUI/consumers):
=======================================================

Location: targets/<job_id>/
Files:
  - baseline_score.json: ExperienceDB vs JD score (pre-strike)
  - final_score.json: Generated resume vs JD score (post-strike)

Schema (both files):
{
  "score": float,              # 0-100 ATS match score
  "baseline_score": float|null, # Only in final_score.json (ref to baseline)
  "improvement": float|null,    # Only in final_score.json (final - baseline)
  "matched_keywords": [str],    # Keywords found in resume
  "missing_keywords": [str],    # Keywords NOT found in resume
  "missing_critical": [str],    # Required hard skills missing
  "match_ratio": float,         # matched / total keywords
  "raw_points": float,          # Weighted points earned
  "max_points": float           # Maximum possible points
}
"""

import json
import os
import re
from typing import Dict, List, Optional, Set, Tuple


# ── Weights ───────────────────────────────────────────────────────────────
HARD_WEIGHT = 3.0
SOFT_WEIGHT = 1.0
PREFERRED_WEIGHT = 0.6


# ── Skill Lexicon ──────────────────────────────────────────────────────────
HARD_SKILLS = {
    # Core Languages / Frameworks
    "python": 10,
    "javascript": 10,
    "typescript": 9,
    "react": 9,
    "node.js": 9,
    "sql": 9,
    "django": 8,
    "flask": 8,
    "fastapi": 8,
    "postgresql": 8,
    "mysql": 7,
    "mongodb": 7,
    "redis": 7,
    "html": 7,
    "css": 7,
    "sass": 6,
    "jquery": 6,
    # Cloud / DevOps / Infrastructure
    "aws": 9,
    "azure": 8,
    "gcp": 8,
    "docker": 8,
    "kubernetes": 8,
    "aws lambda": 7,
    "terraform": 7,
    "ansible": 7,
    "jenkins": 7,
    "github actions": 7,
    "cicd": 7,
    "ci/cd": 7,
    "nginx": 6,
    "apache": 6,
    "linux": 7,
    "ubuntu": 6,
    # AI / ML / Data
    "machine learning": 8,
    "langchain": 8,
    "llamaindex": 8,
    "openai": 8,
    "anthropic": 8,
    "huggingface": 7,
    "transformers": 7,
    "pytorch": 7,
    "tensorflow": 7,
    "vector database": 7,
    "embedding": 7,
    "rag": 7,
    "mcp": 6,
    "pandas": 7,
    "numpy": 7,
    "data analysis": 7,
    "etl": 7,
    # Modern Frontend / Backend
    "next.js": 8,
    "svelte": 7,
    "tailwind": 7,
    "vite": 6,
    "pydantic": 7,
    "prisma": 7,
    "supabase": 7,
    # Platform / Observability
    "argocd": 6,
    "pulumi": 6,
    "backstage": 6,
    "gitops": 6,
    "datadog": 6,
    "grafana": 6,
    "opentelemetry": 6,
    # General
    "git": 8,
    "rest api": 8,
    "graphql": 8,
    "microservices": 8,
    "oauth": 6,
    "jwt": 6,
}

SOFT_SKILLS = {
    "communication": 5,
    "leadership": 5,
    "teamwork": 5,
    "problem solving": 5,
    "critical thinking": 5,
    "time management": 4,
    "adaptability": 4,
    "collaboration": 4,
    "mentoring": 4,
    "project management": 4,
}

PREFERRED_SKILLS = {
    "aws lambda": 7,
    "cicd": 7,
    "ci/cd": 7,
    "kubernetes": 6,
    "docker": 6,
    "typescript": 5,
    "graphql": 5,
    "redis": 5,
    "kafka": 5,
    "rabbitmq": 5,
}


# ── Synonym Mappings ───────────────────────────────────────────────────────
SKILL_SYNONYMS: Dict[str, List[str]] = {
    "python": ["python", "py", "python3", "python scripting"],
    "javascript": ["javascript", "js", "ecmascript", "node.js", "nodejs", "node"],
    "typescript": ["typescript", "ts"],
    "react": ["react", "reactjs", "react.js"],
    "node.js": ["node.js", "nodejs", "node"],
    "sql": ["sql", "postgresql", "mysql", "mssql", "sqlite"],
    "docker": ["docker", "docker container"],
    "kubernetes": ["kubernetes", "k8s"],
    "aws": ["aws", "amazon web services", "amazonaws"],
    "git": ["git"],
    "machine learning": ["machine learning", "ml", "ai"],
    "next.js": ["next.js", "nextjs"],
    "svelte": ["svelte"],
    "tailwind": ["tailwind", "tailwindcss"],
    "pydantic": ["pydantic"],
    "prisma": ["prisma"],
    "supabase": ["supabase"],
    "argocd": ["argocd", "argo cd"],
    "pulumi": ["pulumi"],
    "backstage": ["backstage"],
    "gitops": ["gitops"],
    "datadog": ["datadog"],
    "grafana": ["grafana"],
    "opentelemetry": ["opentelemetry", "otel"],
    "langchain": ["langchain"],
    "llamaindex": ["llamaindex"],
    "openai": ["openai", "gpt", "chatgpt"],
    "anthropic": ["anthropic", "claude"],
    "huggingface": ["huggingface", "transformers"],
    "vector database": ["vector database", "vectordb", "pinecone", "weaviate", "chroma"],
    "embedding": ["embedding", "embeddings", "vector search"],
    "rag": ["rag", "retrieval augmented generation"],
    "mcp": ["mcp"],
    "communication": ["communication", "communicate", "verbal", "written"],
    "leadership": ["leadership", "lead", "manager", "management"],
    "teamwork": ["teamwork", "team", "collaborate", "collaboration"],
    "problem solving": ["problem solving", "problem-solve", "analytical"],
    "time management": ["time management", "deadline"],
    "cicd": ["cicd", "ci/cd", "continuous integration", "continuous deployment"],
    "ci/cd": ["cicd", "ci/cd", "continuous integration", "continuous deployment"],
    "aws lambda": ["aws lambda", "lambda"],
    "rest api": ["rest api", "restful api", "rest"],
    "graphql": ["graphql"],
}


# ── ATSScorer ────────────────────────────────────────────────────────────
class ATSScorer:
    """Scores resume text against job descriptions using weighted keyword matching."""

    def __init__(self):
        self.hard_skills = HARD_SKILLS
        self.soft_skills = SOFT_SKILLS
        self.preferred_skills = PREFERRED_SKILLS
        self.synonyms = SKILL_SYNONYMS

    # ── Normalization ──────────────────────────────────────────────────────
    def _normalize(self, text: str) -> str:
        """Lowercase, strip punctuation, collapse whitespace."""
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s/\-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _keyword_in_corpus(self, keyword: str, corpus: Dict[str, Set[str]]) -> bool:
        """Check if a keyword (or any of its synonyms) appears in the corpus."""
        normalized_kw = self._normalize(keyword)
        variants = self.synonyms.get(normalized_kw, [normalized_kw])
        tokens = corpus.get("tokens", set())
        raw_text = corpus.get("raw_text", "")

        for variant in variants:
            nv = self._normalize(variant)
            if not nv:
                continue
            # Exact token match
            if nv in tokens:
                return True
            # Substring match against raw text (catches variants not tokenized)
            if f" {nv} " in f" {raw_text} ":
                return True
            # Word-boundary regex for multi-word skills
            if re.search(r"\b" + re.escape(nv) + r"\b", raw_text):
                return True
        return False

    def _extract_skills_from_text(self, text: str) -> Dict[str, List[Dict]]:
        """Extract hard/soft/preferred skills from text, returning weighted list."""
        corpus = self._build_corpus_from_text(text)
        weighted: List[Dict] = []

        for category, skill_dict, weight in [
            ("hard", self.hard_skills, HARD_WEIGHT),
            ("soft", self.soft_skills, SOFT_WEIGHT),
            ("preferred", self.preferred_skills, PREFERRED_WEIGHT),
        ]:
            for canonical, priority in skill_dict.items():
                if self._keyword_in_corpus(canonical, corpus):
                    weighted.append({
                        "keyword": canonical,
                        "priority": priority,
                        "category": category,
                        "weight": weight,
                    })

        weighted.sort(key=lambda x: (-x["priority"], x["keyword"]))
        return {"weighted": weighted}

    def _build_corpus_from_text(self, text: str) -> Dict[str, Set[str]]:
        """Build search corpus from resume/job description text."""
        normalized = self._normalize(text)
        words = normalized.split()
        tokens: Set[str] = set(words)

        # Add bigrams and trigrams for multi-word skill matching
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                tokens.add(" ".join(words[i:i + n]))

        return {
            "tokens": tokens,
            "raw_text": text.lower(),
        }

    def _compute_weighted_score(
        self,
        required: Set[str],
        preferred: Set[str],
        matched: Set[str],
    ) -> Tuple[float, float]:
        """Compute weighted raw_points and max_points."""
        raw_points = 0.0
        max_points = 0.0

        for kw in required:
            weight = self.hard_skills.get(kw, 0) * HARD_WEIGHT
            if not weight:
                weight = self.soft_skills.get(kw, 0) * SOFT_WEIGHT
            if not weight:
                weight = 5 * HARD_WEIGHT
            max_points += weight
            if kw in matched:
                raw_points += weight

        for kw in preferred:
            weight = self.preferred_skills.get(kw, 0) * PREFERRED_WEIGHT
            if not weight:
                weight = self.hard_skills.get(kw, 0) * PREFERRED_WEIGHT
            if not weight:
                weight = 3 * PREFERRED_WEIGHT
            max_points += weight
            if kw in matched:
                raw_points += weight

        return raw_points, max_points

    def _extract_jd_keywords(self, jd_text: str) -> Dict[str, List[str]]:
        """Extract required and preferred keywords from a job description."""
        extracted = self._extract_skills_from_text(jd_text)
        weighted = extracted.get("weighted", [])

        required = [
            w["keyword"] for w in weighted
            if w.get("priority", 5) >= 7
        ]
        preferred = [
            w["keyword"] for w in weighted
            if 4 <= w.get("priority", 5) < 7
        ]
        return {"required": required, "preferred": preferred}

    def _empty_result(self) -> Dict:
        return {
            "score": 0.0,
            "match_ratio": 0.0,
            "matched_keywords": [],
            "missing_keywords": [],
            "missing_critical": [],
            "required_keywords": [],
            "preferred_keywords": [],
            "hard_matched": [],
            "soft_matched": [],
            "raw_points": 0.0,
            "max_points": 0.0,
        }

    # ── Public API ─────────────────────────────────────────────────────────
    def score_resume_text_vs_jd(
        self,
        resume_text: str,
        job_desc: str,
        target_dir: Optional[str] = None,
    ) -> Dict:
        """
        Score flattened resume text against job description text.

        Similar to score_resume_vs_jd but takes text instead of ExperienceDB.

        Args:
            resume_text: Flattened resume content (from flatten_resume_to_text).
            job_desc: Raw job description text.
            target_dir: Optional directory to write final_score.json.

        Returns:
            Dict with score, match_ratio, matched/missing keywords, etc.
        """
        if not job_desc or not job_desc.strip():
            return self._empty_result()
        if not resume_text or not resume_text.strip():
            return self._empty_result()

        jd_analysis = self._extract_jd_keywords(job_desc)
        resume_corpus = self._build_corpus_from_text(resume_text)

        required = set(jd_analysis["required"])
        preferred = set(jd_analysis["preferred"])
        all_jd_keywords = required | preferred

        matched: Set[str] = set()
        hard_matched: Set[str] = set()
        soft_matched: Set[str] = set()

        for kw in required:
            if self._keyword_in_corpus(kw, resume_corpus):
                matched.add(kw)
                if kw in self.hard_skills:
                    hard_matched.add(kw)
                elif kw in self.soft_skills:
                    soft_matched.add(kw)

        for kw in preferred:
            if self._keyword_in_corpus(kw, resume_corpus):
                matched.add(kw)
                if kw in self.hard_skills:
                    hard_matched.add(kw)
                elif kw in self.soft_skills:
                    soft_matched.add(kw)

        missing = sorted(all_jd_keywords - matched)
        missing_critical = sorted((required - matched) & set(self.hard_skills.keys()))

        raw_points, max_points = self._compute_weighted_score(required, preferred, matched)

        score = 0.0
        match_ratio = 0.0
        if max_points > 0:
            score = round((raw_points / max_points) * 100, 2)
            match_ratio = round(len(matched) / len(all_jd_keywords), 3)

        result = {
            "score": score,
            "match_ratio": match_ratio,
            "matched_keywords": sorted(matched),
            "missing_keywords": missing,
            "missing_critical": missing_critical,
            "required_keywords": sorted(required),
            "preferred_keywords": sorted(preferred),
            "hard_matched": sorted(hard_matched),
            "soft_matched": sorted(soft_matched),
            "raw_points": round(raw_points, 2),
            "max_points": round(max_points, 2),
        }

        if target_dir:
            self.save_final_score(result, target_dir)

        return result

    def score_resume_vs_jd(self, resume_data: Dict, job_desc: str) -> Dict:
        """
        Legacy ExperienceDB-based scorer.
        Flattens resume_data to text then delegates to score_resume_text_vs_jd.
        """
        parts = []
        if isinstance(resume_data, dict):
            for section in ["summary", "skills", "experience", "projects", "education", "certifications"]:
                value = resume_data.get(section)
                if isinstance(value, str):
                    parts.append(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict):
                            parts.extend(str(v) for v in item.values() if isinstance(v, str))
                elif isinstance(value, dict):
                    parts.extend(str(v) for v in value.values() if isinstance(v, str))
        resume_text = " ".join(parts)
        return self.score_resume_text_vs_jd(resume_text, job_desc)

    def save_final_score(
        self,
        result: Dict,
        target_dir: str,
        baseline_score: Optional[float] = None,
    ) -> str:
        """
        Persist final ATS score results to target_dir/final_score.json.

        Args:
            result: Dict from score_resume_text_vs_jd with score, matched_keywords, etc.
            target_dir: Directory to write final_score.json (usually targets/<job>/).
            baseline_score: Optional baseline score for improvement calculation.

        Returns:
            Path to written file.
        """
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, "final_score.json")

        output = dict(result)
        output["baseline_score"] = baseline_score
        if baseline_score is not None and "score" in output:
            output["improvement"] = round(output["score"] - baseline_score, 2)
        else:
            output["improvement"] = None

        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        return path

    def save_baseline_score(self, result: Dict, target_dir: str) -> str:
        """Persist baseline ATS score to target_dir/baseline_score.json."""
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, "baseline_score.json")

        output = {
            "score": result.get("score", 0.0),
            "matched_keywords": result.get("matched_keywords", []),
            "missing_keywords": result.get("missing_keywords", []),
            "missing_critical": result.get("missing_critical", []),
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        return path


# ── CLI Smoke Test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    scorer = ATSScorer()
    resume = """
    Senior Backend Engineer with 5 years experience.
    Tech Stack: Python, Django, PostgreSQL, Docker, Kubernetes, AWS, Git.
    Led team of 4 engineers. Improved API latency by 30%.
    """
    jd = """
    We are hiring a Senior Python Developer.
    Required: Python, Django, PostgreSQL, AWS, Git.
    Preferred: Docker, Kubernetes, CI/CD.
    3+ years experience required. Leadership experience a plus.
    """
    result = scorer.score_resume_text_vs_jd(resume, jd)
    print(json.dumps(result, indent=2))
