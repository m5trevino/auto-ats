"""
ATS Scorer — Score resumes against job descriptions using JD-derived keywords.

Reads the keyword reconnaissance produced by the TB-101 NEON_TERMINATOR agent so the
score is based on what the job description actually asks for, not a hardcoded
lexicon.

SCORE ARTIFACT SCHEMA CONTRACT:
================================

Location: targets/<job_id>/
Files:
  - baseline_score.json: master resume vs JD score (pre-strike)
  - final_score.json: generated resume vs JD score (post-strike)

Schema (both files):
{
  "score": float,              # 0-100 ATS match score
  "baseline_score": float|null, # Only in final_score.json (ref to baseline)
  "improvement": float|null,    # final - baseline
  "matched_keywords": [str],    # Keywords found in resume
  "missing_keywords": [str],    # Keywords NOT found in resume
  "missing_critical": [str],    # Required keywords missing
  "match_ratio": float,         # matched / total keywords
  "raw_points": float,          # Weighted points earned
  "max_points": float           # Maximum possible points
}
"""

import json
import os
import re
from typing import Dict, List, Optional, Set


class ATSScorer:
    """Scores resume text against job descriptions using TB-101 keyword recon."""

    DEFAULT_REQUIRED_WEIGHT = 3.0
    DEFAULT_PREFERRED_WEIGHT = 1.0

    def __init__(self, recon: Optional[Dict] = None):
        """Initialize scorer. Optional recon is the TB-101 output dict."""
        self.recon = recon or {}

    # ── Normalization ──────────────────────────────────────────────────────
    @staticmethod
    def _normalize(text: str) -> str:
        """Lowercase, strip punctuation, collapse whitespace."""
        if not text:
            return ""
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s/\-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

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

    def _keyword_present(self, keyword: str, corpus: Dict[str, Set[str]]) -> bool:
        """Check if a keyword (or close variant) appears in the corpus."""
        nv = self._normalize(keyword)
        if not nv:
            return False
        tokens = corpus.get("tokens", set())
        raw_text = corpus.get("raw_text", "")

        # Exact token / bigram / trigram match
        if nv in tokens:
            return True
        # Substring surrounded by spaces
        if f" {nv} " in f" {raw_text} ":
            return True
        # Word boundary regex
        if re.search(r"\b" + re.escape(nv) + r"\b", raw_text):
            return True

        # Stem heuristic: if keyword has 6+ chars, allow prefix stem match
        # e.g. "delivery schedules" → "delivery schedule" or "delivery scheduling"
        if len(nv) >= 6:
            stem = nv[:max(4, len(nv) - 3)]
            if re.search(r"\b" + re.escape(stem) + r"[a-z]*\b", raw_text):
                return True
        return False

    def _extract_recon_keywords(self) -> List[Dict]:
        """Build a unified weighted keyword list from TB-101 recon data."""
        keywords: List[Dict] = []

        for item in self.recon.get("required_priorities", []):
            kw = item.get("keyword", "") if isinstance(item, dict) else str(item)
            priority = item.get("priority", 8) if isinstance(item, dict) else 8
            keywords.append({
                "keyword": kw,
                "priority": priority,
                "type": "required",
                "weight": self.DEFAULT_REQUIRED_WEIGHT,
            })

        for item in self.recon.get("preferred_priorities", []):
            kw = item.get("keyword", "") if isinstance(item, dict) else str(item)
            priority = item.get("priority", 4) if isinstance(item, dict) else 4
            keywords.append({
                "keyword": kw,
                "priority": priority,
                "type": "preferred",
                "weight": self.DEFAULT_PREFERRED_WEIGHT,
            })

        # Add any hard_skills / soft_skills / domain_keywords not already covered
        seen = {self._normalize(k["keyword"]) for k in keywords}
        for category, ktype, weight in [
            ("hard_skills", "required", self.DEFAULT_REQUIRED_WEIGHT),
            ("domain_keywords", "required", self.DEFAULT_REQUIRED_WEIGHT),
            ("soft_skills", "preferred", self.DEFAULT_PREFERRED_WEIGHT),
        ]:
            for kw in self.recon.get(category, []):
                if isinstance(kw, dict):
                    kw = kw.get("keyword", "")
                kw = str(kw).strip()
                if not kw or self._normalize(kw) in seen:
                    continue
                keywords.append({
                    "keyword": kw,
                    "priority": 7 if ktype == "required" else 4,
                    "type": ktype,
                    "weight": weight,
                })
                seen.add(self._normalize(kw))

        # Sort by priority desc
        keywords.sort(key=lambda x: -x["priority"])
        return keywords

    def _empty_result(self) -> Dict:
        return {
            "score": 0.0,
            "match_ratio": 0.0,
            "matched_keywords": [],
            "missing_keywords": [],
            "missing_critical": [],
            "required_keywords": [],
            "preferred_keywords": [],
            "raw_points": 0.0,
            "max_points": 0.0,
            "source": "recon",
        }

    # ── Public API ─────────────────────────────────────────────────────────
    def score_resume_text_vs_jd(
        self,
        resume_text: str,
        job_desc: str,
        target_dir: Optional[str] = None,
    ) -> Dict:
        """Score resume text against the JD using the loaded recon data."""
        if not job_desc or not job_desc.strip():
            return self._empty_result()
        if not resume_text or not resume_text.strip():
            return self._empty_result()

        keywords = self._extract_recon_keywords()
        if not keywords:
            # Fallback: if no recon provided, score against raw JD tokens with equal weight
            keywords = [
                {"keyword": kw, "priority": 5, "type": "required", "weight": self.DEFAULT_REQUIRED_WEIGHT}
                for kw in self._build_corpus_from_text(job_desc)["tokens"]
                if len(kw) > 2
            ]

        resume_corpus = self._build_corpus_from_text(resume_text)

        matched: Set[str] = set()
        missing: Set[str] = set()
        raw_points = 0.0
        max_points = 0.0

        required_keywords: List[str] = []
        preferred_keywords: List[str] = []

        for kw_entry in keywords:
            kw = kw_entry["keyword"]
            weight = kw_entry["weight"]
            priority = kw_entry["priority"]
            ktype = kw_entry["type"]

            if ktype == "required":
                required_keywords.append(kw)
            else:
                preferred_keywords.append(kw)

            point_value = weight * priority
            max_points += point_value

            if self._keyword_present(kw, resume_corpus):
                matched.add(kw)
                raw_points += point_value
            else:
                missing.add(kw)

        score = 0.0
        match_ratio = 0.0
        if max_points > 0:
            score = round((raw_points / max_points) * 100, 2)
            total = len(keywords)
            if total > 0:
                match_ratio = round(len(matched) / total, 3)

        missing_critical = sorted({kw for kw in missing if kw in required_keywords})

        result = {
            "score": score,
            "match_ratio": match_ratio,
            "matched_keywords": sorted(matched),
            "missing_keywords": sorted(missing),
            "missing_critical": missing_critical,
            "required_keywords": sorted(required_keywords),
            "preferred_keywords": sorted(preferred_keywords),
            "raw_points": round(raw_points, 2),
            "max_points": round(max_points, 2),
            "source": "recon",
        }

        if target_dir:
            self.save_final_score(result, target_dir)

        return result

    def score_resume_vs_jd(self, resume_data: Dict, job_desc: str) -> Dict:
        """Flatten resume JSON to text, then score against JD."""
        parts: List[str] = []
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
        """Persist final ATS score results to target_dir/final_score.json."""
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
    scorer = ATSScorer({
        "required_priorities": [
            {"keyword": "Python", "priority": 10, "mention_count": 1},
            {"keyword": "AWS", "priority": 9, "mention_count": 1},
        ],
        "preferred_priorities": [
            {"keyword": "Docker", "priority": 4, "mention_count": 1},
        ],
        "hard_skills": ["Python", "AWS", "Docker"],
        "soft_skills": ["Communication"],
        "domain_keywords": ["SaaS"],
        "gatekeeper_credentials": [],
    })
    resume = """
    Senior Backend Engineer with 5 years experience.
    Tech Stack: Python, Django, PostgreSQL, Docker, Kubernetes, AWS, Git.
    Led team of 4 engineers. Improved API latency by 30%.
    """
    jd = """
    We are hiring a Senior Python Developer.
    Required: Python, AWS, strong communication.
    Preferred: Docker, Kubernetes.
    3+ years experience required.
    """
    result = scorer.score_resume_text_vs_jd(resume, jd)
    print(json.dumps(result, indent=2))
