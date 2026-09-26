# Strike 2: Professional Summary

You are a senior resume writer. Rewrite the Professional Summary section of the resume to align with the target job description.

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
  "summary": "2-3 sentences. Lead with years of experience + primary specialty.",
  "skills": [],
  "experience": [],
  "projects": [],
  "education": [],
  "certifications": []
}
```

---

## KEYWORD PRIORITY GUIDANCE

[INJECT_JD_RECON]

When writing the summary:
1. Naturally incorporate the TOP 3-5 CRITICAL keywords from the block above.
2. Front-load high-priority skills in the first sentence.
3. Do not stuff keywords — integrate them into a compelling career narrative.
4. Preserve honest experience; never invent roles, dates, or technologies.
