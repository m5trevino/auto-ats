---
id: "016"
status: complete
priority: p1
dependencies: ["005"]
created: "2026-06-08"
---

# Add Post-Generation ATS Scoring

## Description
Implement scoring of the FINAL generated resume against the JD. Currently we only score the ExperienceDB before strikes run. We need to verify the 6-strike pipeline actually improved the ATS match score.

## Success Criteria
- [x] Every generated resume gets a final ATS score
- [x] Score improvement (final - baseline) is logged
- [x] Warnings shown if improvement < 10 points
- [x] Final score persisted to target directory

## Files Modified
- `pipeline-scripts/ats_scorer.py` — `score_resume_text_vs_jd()`, `_build_corpus_from_text()`, `save_final_score()`
- `pipeline-scripts/strike_handler.py` — post-generation scoring call
