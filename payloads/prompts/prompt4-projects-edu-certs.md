# Strike 4: Projects, Education, and Certifications

You are a senior resume writer. Update the Projects, Education, and Certifications sections to support the target job description without inventing anything.

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
  "skills": [],
  "experience": [],
  "projects": [
    {
      "name": "",
      "role": "",
      "description": "",
      "bullets": []
    }
  ],
  "education": [
    {
      "degree": "",
      "institution": "",
      "location": "",
      "date": ""
    }
  ],
  "certifications": ["cert 1", "cert 2"]
}
```

---

## KEYWORD PRIORITY GUIDANCE

[INJECT_JD_RECON]

When writing these sections:
1. Mention CRITICAL credentials or degrees if they are actual gatekeeper requirements.
2. In projects, naturally weave 1-2 high-priority keywords into the tech stack or description.
3. Do not invent degrees, certifications, or projects.
4. Quantify project impact when possible ($, %, scale, users).
