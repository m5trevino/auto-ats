import os
import json
import sqlite3
import jinja2
import sys
import subprocess

# --- CONFIG ---
DB_FILE = 'jobs.db'
TEMPLATE_FILE = 'template.tex'
LUALATEX_BIN = "/usr/local/texlive/2025/bin/x86_64-linux/lualatex"


def get_db(db_file=None):
    conn = sqlite3.connect(db_file or DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def sanitize_filename(text):
    return "".join(c for c in text if c.isalnum() or c in " -_").strip()[:50]


def get_job_data(job_id, db_file=None):
    conn = get_db(db_file)
    row = conn.execute("SELECT title, company FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()

    if not row:
        return None, None, None, None

    safe_title = sanitize_filename(row['title'])
    safe_company = sanitize_filename(row['company'])
    path = f"targets/{safe_title}_{safe_company}_{job_id}"

    return path, f"{safe_title}_{safe_company}_{job_id}", row['title'], row['company']


def tex_escape(text):
    """Sanitizes text for LaTeX compatibility."""
    if not isinstance(text, str):
        return text
    tex_map = {
        '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
        '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}', '\\': r'\textbackslash{}',
    }
    return "".join(tex_map.get(c, c) for c in text)


def escape_dict(d):
    """Recursively escapes a dictionary/list for LaTeX."""
    if isinstance(d, dict):
        return {k: escape_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [escape_dict(v) for v in d]
    return tex_escape(d)


def generate_pdf(job_id, db_file=None, resume_data=None, target_dir=None):
    """Generate a PDF from resume.json in the target directory.

    Args:
        job_id: job identifier
        db_file: optional SQLite path (defaults to jobs.db)
        resume_data: optional dict to use instead of reading resume.json
        target_dir: optional explicit directory; if None, derive from DB
    """
    print(f"[*] PDF ENGINE: Engaging Apex Protocol for Job ID {job_id}...")

    # 1. Locate Artifacts
    if target_dir:
        t_dir = target_dir
        dir_name = os.path.basename(t_dir)
    else:
        t_dir, dir_name, real_title, real_company = get_job_data(job_id, db_file)
        if not t_dir:
            return {"status": "error", "message": "Job not found in database."}
    os.makedirs(t_dir, exist_ok=True)

    json_path = os.path.join(t_dir, "resume.json")
    tex_path = os.path.join(t_dir, "resume.tex")
    pdf_path = os.path.join(t_dir, "resume.pdf")

    # 2. Load and Escape JSON Data
    try:
        if resume_data is not None:
            raw_data = resume_data
        else:
            if not os.path.exists(json_path):
                return {"status": "error", "message": "resume.json artifact missing. Run strike first."}
            with open(json_path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
        # Persist resume.json so the artifact exists even when resume_data is passed in
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(raw_data, f, indent=2, ensure_ascii=False)
        data = escape_dict(raw_data)
    except Exception as e:
        return {"status": "error", "message": f"JSON Corrupt: {str(e)}"}

    # 3. Render Template
    try:
        env = jinja2.Environment(
            block_start_string='[*',
            block_end_string='*]',
            variable_start_string='((',
            variable_end_string='))',
            comment_start_string='{##',
            comment_end_string='##}',
            loader=jinja2.FileSystemLoader('.')
        )
        template = env.get_template(TEMPLATE_FILE)
        rendered_tex = template.render(**data)

        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(rendered_tex)

    except Exception as e:
        return {"status": "error", "message": f"LaTeX Rendering Error: {str(e)}"}

    # 4. Compile PDF
    try:
        cmd = [LUALATEX_BIN, "-interaction=nonstopmode", "resume.tex"]
        # Run inside the target directory so log/aux files stay there
        result = subprocess.run(cmd, cwd=t_dir, capture_output=True, text=True)

        if result.returncode == 0 or os.path.exists(pdf_path):
            if result.returncode != 0:
                print(f"\033[93m[!] LuaLaTeX exited with code {result.returncode}, but PDF was produced.\033[0m")
            print(f"[+] PDF GENERATED: {pdf_path}")
            return {"status": "success", "path": pdf_path, "public_path": f"/done/{dir_name}/resume.pdf"}
        else:
            print(f"[!] Compilation Failed: {result.stdout}")
            return {"status": "error", "message": "LaTeX Compilation Failed. Check logs."}

    except Exception as e:
        return {"status": "error", "message": f"System Error: {str(e)}"}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(generate_pdf(sys.argv[1]))
    else:
        print("Usage: python pdf_engine.py <job_id>")
