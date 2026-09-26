# TECHNICAL MANIFEST: TREVINO WAR ROOM (v5.0 PARKER LEWIS)

## 1. ARCHITECTURAL OVERVIEW

### 1.1 Stack Definition
*   **Runtime:** Python 3.x
*   **Core Framework:** Flask (Synchronous, Threaded)
*   **Persistence:** SQLite3 (Metadata), Local Filesystem (Artifacts/Logs)
*   **Frontend:** Vanilla JavaScript (ES6+), HTML5, CSS3 (No frameworks)
*   **AI Integration:** Groq API (via `groq` SDK and `httpx`)

### 1.2 Design Pattern
The application utilizes a **Local Monolithic MVC** pattern with a **Split-Brain Persistence Strategy**.
*   **Model:** Hybrid. Metadata (Status, Pay, Location) is relational (SQLite). Content (Resumes, Logs, JSON) is unstructured (Filesystem).
*   **View:** A Single Page Application (SPA) served statically (`index.html`), relying on DOM manipulation rather than reactive binding.
*   **Controller:** `server.py` acts as the API Gateway, handling routing, OS-level subprocessing, and third-party API orchestration.

### 1.3 Data Flow
1.  **Ingestion:** Raw JSON scrapes $\rightarrow$ `migration_engine.py` $\rightarrow$ Normalization $\rightarrow$ `jobs.db`.
2.  **Visualization:** `jobs.db` $\rightarrow$ `server.py` (JSON API) $\rightarrow$ `main.js` (Grid Render).
3.  **Execution (The "Strike"):** User Intent $\rightarrow$ Context Assembly (`master_resume` + `tags` + `job_desc`) $\rightarrow$ Groq API $\rightarrow$ JSON Artifact $\rightarrow$ Filesystem.

---

## 2. COMPONENT DECONSTRUCTION

### 2.1 Backend Controller (`server.py`)
**Role:** Central command for state mutations and API orchestration.

*   **`KeyDeck` Class:**
    *   *Logic:* Implements a Round-Robin rotation strategy for API keys loaded from environment variables.
    *   *Why:* Mitigates rate-limiting risks during "Gauntlet" (high-volume) operations by distributing load across available credentials.

*   **`execute_strike(job_id, model, temp, session_id)`:**
    *   *Logic:*
        1.  Retrieves raw job JSON from DB.
        2.  Loads `master_resume.txt` and `categorized_tags.json` from disk (Hot-read, allowing runtime edits).
        3.  Constructs the "Parker Lewis" system prompt.
        4.  Dispatches to Groq.
        5.  Writes full transaction logs (`full_transmission_log.txt`) and artifacts (`resume.json`) to the target directory.
        6.  Triggers OS-level editor via `subprocess.Popen` (Linux/XDG dependency).

### 2.2 Ingestion Engine (`migration_engine.py`)
**Role:** ETL (Extract, Transform, Load) pipeline for raw scrape data.

*   **`process_files(file_list)`:**
    *   *Logic:* Iterates through selected JSON files.
    *   *Deduplication:* Checks `jobs.db` for existing `id` (derived from scraper `key`). If found, the record is skipped to preserve existing `status` flags (e.g., DENIED/APPROVED).
    *   *Blacklisting:* Performs substring matching against `blacklist.json` on Job Title and Employer. Matches are inserted with `status='AUTO_DENIED'`.
    *   *Normalization:* Converts complex nested JSON salary objects into integer values (`annual_pay`) for sorting.

### 2.3 Frontend Controller (`static/js/main.js`)
**Role:** Interface state management and asynchronous communication.

*   **State Management:**
    *   Uses global variables (`jobList`, `currentJobId`) to track the viewport state.
    *   **Tab System:** Switches rendering logic between `NEW` (Grid), `REFINERY` (Tag Harvesting), and `FACTORY` (AI Controls).

*   **`batchExecute(singleMode)`:**
    *   *Logic:* Iterates through selected DOM elements (checkboxes).
    *   *Async Queue:* Awaits `fetch` calls sequentially to prevent local server or API rate-limit saturation.
    *   *Terminal Emulation:* Appends HTML strings to a `div` (`#factory-terminal`) to visualize the backend process in real-time.

---

## 3. ALGORITHMIC DEEP DIVE

### 3.1 The "Parker Lewis" Protocol (Prompt Engineering)
The system relies on a specific Context Injection strategy found in `server.py`:
1.  **Persona Injection:** Defines the AI as an "Apex Predator of Logistics Efficiency."
2.  **Constraint Enforcement:** Demands strict JSON schema compliance.
3.  **Hallucination Guidance:** Explicitly instructs the model to use provided "Strategic Assets" (Tags) to "bridge" the gap between the Master Resume and the Job Description.

### 3.2 The Gauntlet (Multi-Model Consensus)
**Located in:** `static/js/main.js` (`runGauntlet`) $\rightarrow$ `server.py` (`api_strike`).
*   **Logic:** Instead of a single generation, the frontend iterates through an array of defined models (Llama-3, Qwen, Moonshot, etc.).
*   **Storage:** Creates a timestamped directory (e.g., `gauntlet/2025-12-09.../`).
*   **Utility:** Allows for A/B testing of model efficacy on specific job descriptions without overwriting the primary production artifact.

### 3.3 Dynamic Tag Harvesting
**Located in:** `api/harvest_tag` endpoint.
*   **Logic:** Moves a specific skill string from one JSON category (or raw text) into `categorized_tags.json`.
*   **Why:** Creates a "Feedback Loop." As the user processes more jobs, the "Strategic Assets" pool grows, improving the context for future AI generations.

---

## 4. STATE MANAGEMENT & PERSISTENCE

### 4.1 Database Schema (`jobs.db`)
Single-table architecture optimized for rapid filtering.
*   `id`: Primary Key (Scraper Key or Timestamp).
*   `status`: State Machine (`NEW`, `APPROVED`, `DENIED`, `AUTO_DENIED`, `DELIVERED`).
*   `raw_json`: Stores the full blob to avoid schema migrations when scraper output changes.

### 4.2 File System Hierarchy
The application enforces a rigid directory structure for artifact management:
*   `targets/{Safe_Title}_{Safe_Company}_{ID}/`: The container for a single job pursuit.
    *   `resume.json`: The AI output (editable).
    *   `resume.pdf`: The final compiled asset (via `pdf_engine.py` - *Note: Currently flagged as Pending in UI*).
    *   `full_transmission_log.txt`: Audit trail of the prompt and raw response.

### 4.3 Technical Debt / Known Limitations
1.  **Editor Dependency:** The `trigger_editor` function relies on `xdg-open` or specific env vars (`EDITOR_CMD`). May fail on non-Linux environments.
2.  **PDF Engine:** The route `openPDF` alerts "Pending", indicating the PDF generation logic (WeasyPrint or similar) is not fully integrated in the current deployment.
3.  **Concurrency:** `server.py` runs in default Flask mode. Heavy concurrent requests during Batch operations may block if not deployed with Gunicorn/Waitress (acceptable for single-user local tool).
