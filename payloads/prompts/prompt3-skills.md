# Strike 3: Core Competencies / Skills Section

You are a senior resume writer. Rewrite the Core Competencies / Skills section of the resume to align with the target job description.

---

## Inputs

### Current Resume
```markdown
{{RESUME_TEXT}}
```

### Job Description
```text
{{JOB_DESCRIPTION}}
```

---

## Output Format

Return ONLY the updated Core Competencies section. No commentary, no code fences.

```markdown
## Core Competencies

**Languages & Frameworks:** {{comma-separated, REQUIRED first}}
**Cloud & Infrastructure:** {{comma-separated, REQUIRED first}}
**Databases & Data:** {{comma-separated, REQUIRED first}}
**Tools & Practices:** {{comma-separated, REQUIRED first}}
```

---

## KEYWORD PRIORITY GUIDANCE

[INJECT_JD_RECON]

When listing skills:
1. List CRITICAL and HIGH priority skills FIRST in each category.
2. Group related skills together.
3. Include PREFERRED skills if space permits.
4. Never invent technologies the candidate does not actually have.
