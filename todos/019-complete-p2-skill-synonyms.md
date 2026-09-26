---
id: "019"
status: complete
priority: p2
dependencies: ["016"]
created: "2026-06-08"
---

# Skill Synonym Resolution

## Description
Add synonym resolution so variants map to canonical forms during ATS matching.

## Synonym Examples
| Variant | Canonical |
|---------|-----------|
| JS, ECMAScript, Node | javascript |
| React.js, ReactJS, JSX | react |
| CI/CD, CICD, CI-CD | ci/cd |
| AWS, EC2, S3, Lambda | aws |
| Py, Python3 | python |
| K8s | kubernetes |

## Files Modified
- `pipeline-scripts/ats_scorer.py` — `SKILL_SYNONYMS`, `_keyword_in_corpus()`
