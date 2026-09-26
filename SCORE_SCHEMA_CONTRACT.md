# Score Schema Contract

## Purpose
Stable JSON schema for ATS scoring artifacts consumed by OpenTUI and other tools.

## File Locations
- `targets/<job_id>/baseline_score.json`
- `targets/<job_id>/final_score.json`

## Schema

```json
{
  "score": 72.0,
  "baseline_score": 31.58,
  "improvement": 40.42,
  "matched_keywords": ["python", "aws", "git"],
  "missing_keywords": ["kubernetes"],
  "missing_critical": [],
  "match_ratio": 0.75,
  "raw_points": 45.0,
  "max_points": 62.5,
  "required_keywords": ["python", "aws"],
  "preferred_keywords": ["docker", "kubernetes"],
  "hard_matched": ["python", "aws"],
  "soft_matched": ["leadership"]
}
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `score` | float | 0-100 ATS match score |
| `baseline_score` | float \| null | Pre-strike score (only in final_score.json) |
| `improvement` | float \| null | final - baseline (only in final_score.json) |
| `matched_keywords` | [str] | Keywords found in both resume and JD |
| `missing_keywords` | [str] | JD keywords NOT found in resume |
| `missing_critical` | [str] | Required hard skills missing from resume |
| `match_ratio` | float | matched / total JD keywords |
| `raw_points` | float | Weighted points earned |
| `max_points` | float | Maximum possible weighted points |

## Example Usage

```python
import json

with open("targets/job_123/final_score.json") as f:
    final = json.load(f)

with open("targets/job_123/baseline_score.json") as f:
    baseline = json.load(f)

improvement = final.get("improvement") or (final["score"] - baseline["score"])
print(f"ATS SCORE: {baseline['score']} → {final['score']} ({improvement:+})")
```

## Stability Guarantee

These top-level keys are guaranteed to exist in both files:
- `score`
- `matched_keywords`
- `missing_keywords`
- `missing_critical`

These keys exist only in `final_score.json`:
- `baseline_score`
- `improvement`
