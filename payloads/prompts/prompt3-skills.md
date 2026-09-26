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

Return ONLY a valid JSON object with this exact schema. No markdown, no code fences, no commentary.

```json
{
  "name": "",
  "email": "",
  "phone": "",
  "linkedin": "",
  "github": "",
  "contact_info": "",
  "summary": "",
  "skills": ["skill 1", "skill 2", "REQUIRED skill first"],
  "experience": [],
  "projects": [],
  "education": [],
  "certifications": []
}
```

---

## KEYWORD PRIORITY GUIDANCE

[INJECT_JD_RECON]

When listing skills:
1. List CRITICAL and HIGH priority skills FIRST in each category.
2. Group related skills together.
3. Include PREFERRED skills if space permits.
4. Never invent technologies the candidate does not actually have.
