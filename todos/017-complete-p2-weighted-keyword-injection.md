---
id: "017"
status: complete
priority: p1
dependencies: ["016"]
created: "2026-06-08"
---

# Feed Weighted Keywords to Strike Prompts

## Description
Currently Strike 1 extracts keywords but doesn't pass priority weights to Strike 2-6. The AI doesn't know which keywords are most important.

## Success Criteria
- [x] Keywords assigned priority 1-10 based on JD frequency and context
- [x] REQUIRED vs PREFERRED distinction in prompts
- [x] Top 5 keywords emphasized in Strike 2 (Summary) and Strike 3 (Skills)
- [x] Strike 5 instructed to mention high-priority keywords in experience bullets

## Files Modified
- `pipeline-scripts/strike_handler.py` — `parse_neon_output()`, `_build_weighted_keyword_block()`
- `payloads/prompts/prompt1-hard-soft-domain-gatekeeper.md` — priority output format
- `payloads/prompts/prompt2-summary.md` — consume weights
- `payloads/prompts/prompt3-skills.md` — consume weights
- `payloads/prompts/prompt5-experience.md` — consume weights
