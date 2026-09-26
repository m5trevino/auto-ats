---
id: "018"
status: complete
priority: p2
dependencies: ["016"]
created: "2026-06-08"
---

# Expand ATS Keyword Lexicon

## Description
Add modern technology keywords to the ATS scorer so recent JDs are matched accurately.

## New Keywords Added
| Category | Examples |
|----------|----------|
| AI/ML | LangChain, LlamaIndex, OpenAI, Anthropic, HuggingFace, vector DB, RAG, MCP |
| Modern Frontend | Next.js, Svelte, Tailwind, Vite |
| Modern Backend | Pydantic, Prisma, Supabase |
| Platform Eng | ArgoCD, Pulumi, Backstage, GitOps |
| Observability | Datadog, Grafana, OpenTelemetry |

## Files Modified
- `pipeline-scripts/ats_scorer.py` — `HARD_SKILLS`, `SKILL_SYNONYMS`
