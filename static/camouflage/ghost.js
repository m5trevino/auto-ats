// GHOST CLIENT v8: THE LABORATORY
let globalJobs = [];
let currentView = 'FEED';
let currentJobId = null;

// PERSISTENCE LAYER
let selectedModel = "auto";
let selectedResume = "master.txt";
let selectedPrompt = "DEFAULT";
let selectedTemp = "0.3";

document.addEventListener("DOMContentLoaded", () => {
    switchView('FEED');
    setupSearch();
});

function switchView(view) {
    currentView = view;
    ['nav-feed', 'nav-tagging', 'nav-factory', 'nav-archive', 'nav-denied'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.className = (id === 'nav-' + view.toLowerCase()) ? 'nav-link active' : 'nav-link';
    });

    ['view-feed', 'view-tagging', 'view-factory', 'view-archive', 'view-denied'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.className = (id === 'view-' + view.toLowerCase()) ? 'view-section active' : 'view-section';
    });

    if (view === 'FEED') fetchFeed('NEW');
    if (view === 'TAGGING') renderRefineryView();
    if (view === 'FACTORY') fetchFeed('APPROVED');
    if (view === 'ARCHIVE') fetchArchive();
    if (view === 'DENIED') fetchFeed('DENIED');
}

async function loadResumes() {
    try {
        const res = await fetch('/api/resumes');
        let resumes = await res.json();

        const selects = document.querySelectorAll('.set-resume-select');
        if (resumes.length > 0 && selects.length > 0) {
            resumes.sort((a, b) => {
                if (a === 'master.txt') return -1;
                if (b === 'master.txt') return 1;
                return a.localeCompare(b);
            });
            const options = resumes.map(r => `<option value="${r}">${r}</option>`).join('');
            selects.forEach(sel => {
                sel.innerHTML = options;
                sel.value = selectedResume; // Use persistent value
                sel.onchange = (e) => { selectedResume = e.target.value; };
            });
        }
    } catch (e) { console.error("Could not load resumes.", e); }
}

async function loadProvider() {
    try {
        const res = await fetch('/api/provider');
        const cfg = await res.json();
        const urlInputs = document.querySelectorAll('#provider-url');
        const keyInputs = document.querySelectorAll('#provider-key');
        const modelInputs = document.querySelectorAll('#provider-model');
        urlInputs.forEach(i => i.value = cfg.url || '');
        keyInputs.forEach(i => i.value = cfg.api_key || '');
        modelInputs.forEach(i => i.value = cfg.model || '');
    } catch (e) { console.error("Could not load provider config.", e); }
}

async function saveProvider() {
    const headerId = (currentView === 'FACTORY') ? 'factory-detail-header' : 'detail-header';
    const header = document.getElementById(headerId);
    const url = header.querySelector('#provider-url').value.trim();
    const key = header.querySelector('#provider-key').value.trim();
    const model = header.querySelector('#provider-model').value.trim();
    const statusEl = header.querySelector('#provider-save-status');

    try {
        const res = await fetch('/api/provider', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url, api_key: key, model})
        });
        const data = await res.json();
        if (data.status === 'saved') {
            if (statusEl) { statusEl.textContent = 'Provider saved.'; statusEl.style.color = '#137333'; }
        } else {
            if (statusEl) { statusEl.textContent = `Error: ${data.reason}`; statusEl.style.color = '#b71b1b'; }
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = 'Network error.'; statusEl.style.color = '#b71b1b'; }
    }
}

async function loadModels() {
    try {
        const res = await fetch('/api/models');
        const models = await res.json();

        const selects = document.querySelectorAll('.set-model-select');
        if (models.length > 0 && selects.length > 0) {
            const options = models.map(m => `<option value="${m.id}">${m.note || m.id}</option>`).join('');

            selects.forEach(sel => {
                sel.innerHTML = options;
                if (models.find(m => m.id === selectedModel)) {
                    sel.value = selectedModel;
                } else if (models.find(m => m.id === "auto")) {
                    sel.value = "auto";
                    selectedModel = "auto";
                }
                sel.onchange = (e) => { selectedModel = e.target.value; };
            });
        }
    } catch (e) { console.error("Could not load models.", e); }
}

async function fetchFeed(status = 'NEW') {
    await loadModels();
    await loadProvider();
    await loadResumes();
    const res = await fetch(`/api/jobs?status=${status}`);
    globalJobs = await res.json();

    renderFeedList(globalJobs);
    if (globalJobs.length > 0) {
        loadDetail(globalJobs[0].id);
    } else {
        const headerId = (currentView === 'FACTORY') ? 'factory-detail-header' : 'detail-header';
        const bodyId = (currentView === 'FACTORY') ? 'factory-detail-body' : 'detail-body';
        const header = document.getElementById(headerId);
        if (header) header.innerHTML = '<div style="color:#666; padding:20px;">SECTOR CLEAR. NO TARGETS.</div>';
        const body = document.getElementById(bodyId);
        if (body) body.innerHTML = '';
    }
}

async function renderRefineryView() {
    const res = await fetch('/api/jobs?status=APPROVED');
    const jobs = await res.json();
    globalJobs = jobs; // Sync for detail loading
    const container = document.getElementById('refinery-container');

    container.innerHTML = `
        <div style="display:flex; gap:20px; height:600px;">
            <div style="flex:1; border-right:1px solid #eee; padding-right:20px; overflow-y:auto;">
                <h4 style="margin-bottom:10px; color:#2557a7;">APPROVED TARGETS</h4>
                ${jobs.map(j => `<div class="job-card" style="padding:10px; margin-bottom:10px; cursor:pointer;" onclick="loadDetail('${j.id}')">${j.title}</div>`).join('')}
                ${jobs.length === 0 ? '<div style="color:#999;">No approved jobs to refine.</div>' : ''}
            </div>
            <div id="tag-refinery-pane" style="flex:2; overflow-y:auto;">
                <div style="padding:20px; text-align:center; color:#999;">Select a target to refine tags.</div>
            </div>
        </div>
    `;
}

function openManualImport() {
    const modal = document.getElementById('manual-import-modal');
    if (modal) {
        modal.style.display = 'flex';
        switchImportTab('form');
    }
}

function closeManualImport() {
    const modal = document.getElementById('manual-import-modal');
    if (modal) modal.style.display = 'none';
    clearMiStatus();
}

function switchImportTab(tab) {
    document.querySelectorAll('.import-tab').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tab);
    });
    document.querySelectorAll('.import-panel').forEach(p => {
        p.style.display = p.id === `import-tab-${tab}` ? 'block' : 'none';
    });
    clearMiStatus();
}

function showMiStatus(msg, isError = false) {
    const box = document.getElementById('mi-status');
    if (!box) return;
    box.style.display = 'block';
    box.style.background = isError ? '#ffe6e6' : '#e6f4ea';
    box.style.color = isError ? '#b71b1b' : '#137333';
    box.textContent = msg;
}

function clearMiStatus() {
    const box = document.getElementById('mi-status');
    if (box) box.style.display = 'none';
}

async function submitManualJob() {
    const title = document.getElementById('mi-title').value.trim();
    const company = document.getElementById('mi-company').value.trim();
    const city = document.getElementById('mi-city').value.trim();
    const salary = document.getElementById('mi-salary').value.trim();
    const url = document.getElementById('mi-url').value.trim();
    const description = document.getElementById('mi-description').value.trim();

    if (!title || !company || !description) {
        return showMiStatus('Title, company, and description are required.', true);
    }

    const payload = {
        title, company, city,
        description: { text: description, html: description },
        url: url || '#',
        datePublished: new Date().toISOString()
    };
    if (salary) {
        payload.baseSalary = { min: parseFloat(salary), unitOfWork: 'YEAR' };
    }

    const res = await fetch('/api/add_job', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (data.status === 'added') {
        showMiStatus(`Added: ${title} (score ${data.score})`);
        ['mi-title', 'mi-company', 'mi-city', 'mi-salary', 'mi-url', 'mi-description'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        fetchFeed('NEW');
    } else if (data.status === 'skipped') {
        showMiStatus('Job already exists.', true);
    } else {
        showMiStatus(`Error: ${data.reason || 'unknown'}`, true);
    }
}

async function submitJsonJobs() {
    const raw = document.getElementById('mi-json').value.trim();
    if (!raw) return showMiStatus('Paste JSON first.', true);
    let records;
    try {
        records = JSON.parse(raw);
        if (!Array.isArray(records)) records = records.jobs || [];
    } catch (e) {
        return showMiStatus(`Invalid JSON: ${e.message}`, true);
    }

    const res = await fetch('/api/import_jobs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(records)
    });
    const data = await res.json();

    if (data.status === 'success') {
        showMiStatus(`Imported ${data.stats.added} jobs (${data.stats.skipped} skipped, ${data.stats.blacklisted} blacklisted).`);
        document.getElementById('mi-json').value = '';
        fetchFeed('NEW');
    } else {
        showMiStatus(`Error: ${data.reason}`, true);
    }
}

async function submitFileJobs() {
    const input = document.getElementById('mi-file');
    if (!input.files.length) return showMiStatus('Choose a .json file.', true);

    const file = input.files[0];
    let records;
    try {
        const text = await file.text();
        const parsed = JSON.parse(text);
        records = Array.isArray(parsed) ? parsed : (parsed.jobs || []);
    } catch (e) {
        return showMiStatus(`Could not read file: ${e.message}`, true);
    }

    const res = await fetch('/api/import_jobs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(records)
    });
    const data = await res.json();

    if (data.status === 'success') {
        showMiStatus(`Imported ${data.stats.added} jobs from ${file.name}.`);
        input.value = '';
        fetchFeed('NEW');
    } else {
        showMiStatus(`Error: ${data.reason}`, true);
    }
}

async function syncIntel() {
    const filesRes = await fetch('/api/scrapes');
    const files = await filesRes.json();
    if (files.length === 0) return alert("No new intel files found.");

    if (!confirm(`Found ${files.length} intel files. Sync now?`)) return;

    const res = await fetch('/api/migrate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({files: files})
    });
    const data = await res.json();
    alert(`SYNC COMPLETE: ${data.stats.new} New Targets Acquired.`);
    fetchFeed('NEW');
}

async function dismissJob(id) {
    if(!confirm("Deny this target?")) return;
    await fetch('/api/deny', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})});
    fetchFeed(currentView === 'FEED' ? 'NEW' : 'APPROVED');
}

async function approveJob(id) {
    await fetch('/api/approve', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})});
    fetchFeed('NEW');
}

function renderFeedList(jobs) {
    const containerId = (currentView === 'FACTORY') ? 'factory-container' : 'feed-container';
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    if (!jobs || jobs.length === 0) {
        container.innerHTML = `<div style="padding:20px; color:#666;">No targets found in ${currentView}.</div>`;
        return;
    }

    // If we're in factory view, we might want a different layout,
    // but for now let's just make sure it renders the cards.
    if (currentView === 'FACTORY') {
        container.style.display = 'grid';
        container.style.gridTemplateColumns = 'repeat(auto-fill, minmax(300px, 1fr))';
        container.style.gap = '20px';
    } else {
        container.style.display = 'block';
    }

    jobs.forEach(job => {
        const card = document.createElement('div');
        card.className = 'job-card';
        card.id = `card-${job.id}`;
        card.innerHTML = `
            <div style="display:flex; justify-content:space-between;">
                <div class="badge-new">${job.status}</div>
                <div style="font-weight:bold; color:#2557a7; font-size:12px;">SCORE: ${job.score}</div>
            </div>
            <div class="job-title">${job.title}</div>
            <div class="job-company">${job.company}</div>
            <div class="job-loc">${job.city}</div>
            <div style="margin-top:10px;"><span class="pill-gray">${job.pay}</span></div>
            <div id="smuggler-${job.id}" style="margin-top:12px;"></div>
        `;
        card.addEventListener('click', () => loadDetail(job.id));
        container.appendChild(card);
        smuggleTags(job.id);
    });
}

async function loadDetail(id) {
    currentJobId = id;
    const job = globalJobs.find(j => j.id === id);
    if (!job) return;

    if (currentView === 'TAGGING') {
        const res = await fetch(`/api/get_job_details?id=${id}`);
        const details = await res.json();
        renderTagRefinery(details);
        return;
    }

    const headerId = (currentView === 'FACTORY') ? 'factory-detail-header' : 'detail-header';
    const bodyId = (currentView === 'FACTORY') ? 'factory-detail-body' : 'detail-body';

    // RENDER HEADER
    document.getElementById(headerId).innerHTML = `
        <div class="job-title" style="font-size:24px; margin-bottom:10px;">${job.title}</div>
        <div style="margin-bottom:12px; font-size:15px;">${job.company} • ${job.city}</div>

        <div style="margin-top:20px; display:flex; gap:10px;">
            ${job.status === 'NEW' ? `
                <button id="btn-approve" class="btn-action" style="flex:1; background:#2a8547; color:#fff; border:none;" onclick="approveJob('${id}')">
                    <i class="bi bi-check-circle"></i> Approve
                </button>
            ` : `
                <button id="btn-tailor" class="btn-action btn-tailor" style="flex:1; margin-bottom:0;" onclick="createTailored('${id}')">
                    <i class="bi bi-magic"></i> Create Tailored
                </button>
                <button class="btn-sm-ghost" style="border-color:#b71b1b; color:#b71b1b;" onclick="if(confirm('Force generate despite low ATS match?')) processJob('${id}', true)">
                    Force
                </button>
            `}
            <button class="btn-gear" onclick="toggleSettings(this)">
                <i class="bi bi-gear-fill"></i>
            </button>
        </div>

        <div class="settings-panel">
            <div class="setting-row"><div class="setting-label">MODEL</div><select class="groq-select set-model-select"></select></div>
            <div class="setting-row"><div class="setting-label">RESUME</div><select class="groq-select set-resume-select"></select></div>
            <div class="setting-row">
                <div class="setting-label"><span>LLM PROVIDER</span></div>
                <input id="provider-url" type="text" style="width:100%; padding:6px; border:1px solid #ccc; border-radius:4px; font-size:12px; margin-bottom:6px;" placeholder="https://api.openai.com/v1" value="">
                <input id="provider-key" type="password" style="width:100%; padding:6px; border:1px solid #ccc; border-radius:4px; font-size:12px; margin-bottom:6px;" placeholder="API key" value="">
                <input id="provider-model" type="text" style="width:100%; padding:6px; border:1px solid #ccc; border-radius:4px; font-size:12px; margin-bottom:6px;" placeholder="Default model (e.g. gpt-4o, auto)" value="">
                <button class="btn-sm-ghost" style="border-color:#2557a7; color:#2557a7;" onclick="saveProvider()">Save Provider</button>
                <div id="provider-save-status" style="font-size:12px; margin-top:4px;"></div>
            </div>
            <div class="setting-row">
                <div class="setting-label"><span>UPLOAD / PASTE RESUME</span></div>
                <input type="file" id="resume-upload" accept=".txt,.md,.docx" style="margin-bottom:8px; font-size:12px;">
                <textarea id="resume-paste" style="width:100%; height:80px; padding:6px; border:1px solid #ccc; border-radius:4px; font-size:12px;" placeholder="Or paste resume text here..."></textarea>
                <button class="btn-sm-ghost" style="margin-top:6px; border-color:#2a8547; color:#2a8547;" onclick="saveResume()">Save Resume</button>
                <div id="resume-save-status" style="font-size:12px; margin-top:4px;"></div>
            </div>
            <div class="setting-row">
                <div class="setting-label"><span>PROTOCOL</span><a href="#" onclick="savePrompt()" style="color:#2557a7; text-decoration:none;">Save As...</a></div>
                <select class="groq-select prompt-select" onchange="loadPromptContent(this.value)"><option value="DEFAULT">PARKER LEWIS</option></select>
            </div>
            <div class="setting-row"><textarea class="groq-textarea set-prompt-area"></textarea></div>
            <div class="setting-row">
                <div class="setting-label"><span>TEMP</span> <span class="setting-val val-temp">0.3</span></div>
                <input type="range" class="set-temp-slider" min="0" max="2" step="0.1" value="0.3" oninput="this.parentElement.querySelector('.val-temp').innerText = this.value">
            </div>
            <hr style="border:0; border-top:1px solid #eee; margin:15px 0;">
            <button class="btn-action" style="background:#b71b1b; color:#fff; border:none;" onclick="resetJob('${id}')">DELETE ARTIFACTS</button>
        </div>

        <div style="margin-top:10px;">
             ${job.status === 'DELIVERED' ? `<button id="btn-pdf" class="btn-action btn-pdf" onclick="createPDF('${id}')">VIEW PDF</button>` : ''}
            <div style="display:flex; gap:10px; margin-top:10px;">
                <button class="btn-target" onclick="window.open('${job.job_url}', '_blank')">Target Link</button>
                <button class="btn-move" style="margin-left:auto;" onclick="dismissJob('${id}')">Dismiss</button>
            </div>
        </div>
    `;

    const res = await fetch(`/api/get_job_details?id=${id}`);
    const details = await res.json();
    renderDescription(details);
    loadModels();
    loadProvider();
    loadResumes();
    loadPromptList();
}

function renderDescription(details) {
    const bodyId = (currentView === 'FACTORY') ? 'factory-detail-body' : 'detail-body';
    const bodyBox = document.getElementById(bodyId);
    if (!bodyBox) return;
    const skillHTML = details.skills.map(s => `<span class="skill-tag">${s.name}</span>`).join('');
    bodyBox.innerHTML = `
        <div class="insight-box">
            <div class="insight-title">Profile insights</div>
            <div>${skillHTML || 'No tags.'}</div>
        </div>
        <div style="font-weight:700; font-size:18px; margin-bottom:12px;">Job details</div>
        <div style="font-size:15px; line-height:1.6; color:#1a1a1a;">${details.description}</div>
    `;
}

function renderTagRefinery(details) {
    const pane = document.getElementById('tag-refinery-pane');
    if (!pane) return;
    const unsorted = details.skills.filter(s => s.category === 'new');
    const sorted = details.skills.filter(s => s.category !== 'new');

    pane.innerHTML = `
        <h4 style="color:#595959; font-size:14px;">UNSORTED SIGNALS</h4>
        <div style="padding:10px; border:1px solid #eee; border-radius:8px; margin-bottom:20px;">
            ${unsorted.map(s => `<span class="skill-tag" style="background:#fff3e0; border-color:#ffb74d; color:#e65100; cursor:pointer;" onclick="harvestTagUI('${s.name}', 'skills')">${s.name}</span>`).join('')}
        </div>
        <h4 style="color:#595959; font-size:14px;">SORTED ASSETS</h4>
        <div style="padding:10px; border:1px solid #eee; border-radius:8px;">
            ${sorted.map(s => `<span class="skill-tag">${s.name} (${s.category})</span>`).join('')}
        </div>
    `;
}

async function harvestTagUI(tag, category) {
    await fetch('/api/harvest_tag', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tag:tag, category:category})});
    const res = await fetch(`/api/get_job_details?id=${currentJobId}`);
    renderTagRefinery(await res.json());
}

function toggleSettings(btn) {
    const header = btn.closest('.detail-header');
    const panel = header.querySelector('.settings-panel');
    if (panel) {
        const isOpen = panel.classList.toggle('open');
        btn.classList.toggle('active', isOpen);
    }
}

async function loadPromptList() {
    const res = await fetch('/api/prompts');
    const keys = await res.json();
    const selects = document.querySelectorAll('.prompt-select');
    if (selects.length > 0) {
        const options = `<option value="DEFAULT">PARKER LEWIS</option>` + keys.map(k => `<option value="${k}">${k.toUpperCase()}</option>`).join('');
        selects.forEach(sel => {
            sel.innerHTML = options;
            sel.value = selectedPrompt;
            sel.onchange = (e) => {
                selectedPrompt = e.target.value;
                loadPromptContent(e.target.value);
            };
        });

        // AUTO-LOAD CONTENT IF EMPTY
        const area = document.querySelector('.set-prompt-area');
        if (area && !area.value) loadPromptContent(selectedPrompt);
    }
}

async function loadPromptContent(name) {
    const res = await fetch('/api/get_prompt_content', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name})});
    const data = await res.json();
    document.querySelectorAll('.set-prompt-area').forEach(area => area.value = data.content);
}

async function saveResume() {
    const headerId = (currentView === 'FACTORY') ? 'factory-detail-header' : 'detail-header';
    const header = document.getElementById(headerId);
    const fileInput = header.querySelector('#resume-upload');
    const pasteArea = header.querySelector('#resume-paste');
    const statusEl = header.querySelector('#resume-save-status');

    let text = '';
    let filename = '';
    if (fileInput && fileInput.files.length > 0) {
        filename = fileInput.files[0].name.replace(/\.[^/.]+$/, '') + '.txt';
        text = await fileInput.files[0].text();
    }
    if (!text && pasteArea) {
        text = pasteArea.value.trim();
    }
    if (!text) {
        if (statusEl) { statusEl.textContent = 'Provide a file or paste text.'; statusEl.style.color = '#b71b1b'; }
        return;
    }

    const name = prompt('Save resume as:', filename || 'master.txt');
    if (!name) return;

    try {
        const form = new FormData();
        form.append('text', text);
        form.append('name', name);

        const res = await fetch('/api/upload_resume', {method: 'POST', body: form});
        const data = await res.json();
        if (data.status === 'saved') {
            if (statusEl) { statusEl.textContent = `Saved as ${data.path}`; statusEl.style.color = '#137333'; }
            await loadResumes();
            const sel = header.querySelector('.set-resume-select');
            if (sel) sel.value = data.path;
            selectedResume = data.path;
            if (pasteArea) pasteArea.value = '';
            if (fileInput) fileInput.value = '';
        } else {
            if (statusEl) { statusEl.textContent = `Error: ${data.reason}`; statusEl.style.color = '#b71b1b'; }
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = 'Network error.'; statusEl.style.color = '#b71b1b'; }
    }
}

async function savePrompt() {
    const headerId = (currentView === 'FACTORY') ? 'factory-detail-header' : 'detail-header';
    const header = document.getElementById(headerId);
    const content = header.querySelector('.set-prompt-area').value;

    if (!content) return alert("No content to save.");

    const name = prompt("Enter Protocol Name (e.g. PARKER_LEWIS_V2):");
    if (!name) return;

    try {
        const res = await fetch('/api/prompts', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name, content: content})
        });
        const data = await res.json();
        if (data.status === 'saved') {
            alert("PROTOCOL ARCHIVED.");
            await loadPromptList();
            // Select the newly saved prompt in all dropdowns
            document.querySelectorAll('.prompt-select').forEach(sel => sel.value = name);
        }
    } catch (e) { alert("Save Error."); }
}

async function processJob(id, force = false) {
    const headerId = (currentView === 'FACTORY') ? 'factory-detail-header' : 'detail-header';
    const bodyId = (currentView === 'FACTORY') ? 'factory-detail-body' : 'detail-body';
    const header = document.getElementById(headerId);
    const body = document.getElementById(bodyId);

    const btn = header.querySelector('#btn-tailor');
    if (btn) btn.innerText = force ? "Forcing..." : "Processing...";

    const model = header.querySelector('.set-model-select').value || "auto";
    const temp = header.querySelector('.set-temp-slider').value;
    const resume = header.querySelector('.set-resume-select').value;
    const prompt = header.querySelector('.set-prompt-area').value;

    try {
        const res = await fetch('/api/process_job', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: id, model: model, temp: temp, prompt_override: prompt, resume_file: resume, force: force})
        });
        const data = await res.json();
        if (data.status === 'success') {
            const baseline = data.baseline_score !== undefined ? data.baseline_score : 'N/A';
            const final = data.final_score !== undefined ? data.final_score : 'N/A';
            const improvement = data.improvement !== undefined ? data.improvement : 'N/A';
            if (body) {
                body.innerHTML = `
                    <div class="insight-box" style="border-left:4px solid #2a8547;">
                        <div class="insight-title">ATS SCORE</div>
                        <div style="font-size:24px; font-weight:700; color:#2a8547;">
                            ${baseline} → ${final} <span style="font-size:14px; color:#666;">(${improvement >= 0 ? '+' : ''}${improvement})</span>
                        </div>
                        <div style="font-size:12px; color:#666; margin-top:5px;">
                            ${force ? 'Forced generation: ATS gate was bypassed.' : 'Baseline vs Final ATS match score.'}
                        </div>
                    </div>
                    <div style="padding:15px;">
                        <strong>Resume generated.</strong> View it in The Vault.
                    </div>
                `;
            }
            switchView('ARCHIVE');
        } else if (data.status === 'rejected') {
            if (body) {
                body.innerHTML = `
                    <div class="insight-box" style="border-left:4px solid #b71b1b;">
                        <div class="insight-title">ATS GATE BLOCKED</div>
                        <div style="font-size:16px; font-weight:600; color:#b71b1b;">${data.reason}</div>
                        <div style="font-size:12px; color:#666; margin-top:8px;">
                            The baseline match is low. You can still force the system to tailor a resume.
                        </div>
                    </div>
                    <div style="padding:15px;">
                        <button class="btn-action" style="background:#b71b1b; color:#fff; border:none;" onclick="processJob('${id}', true)">
                            <i class="bi bi-exclamation-triangle"></i> Force Generate Anyway
                        </button>
                    </div>
                `;
            }
        } else {
            alert("Error: " + (data.error || JSON.stringify(data)));
        }
    } catch (e) { alert("Network Error"); }
    if (btn) btn.innerText = "Create Tailored";
}

async function createTailored(id) {
    await processJob(id, false);
}

async function createPDF(id) {
    const btn = document.getElementById('btn-pdf');
    btn.innerText = "Generating...";
    try {
        const res = await fetch("/api/generate_pdf", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id: id}) });
        const data = await res.json();
        if (data.status === "success") {
            window.open(data.path, '_blank');
        } else { alert("PDF Error: " + data.message); }
    } catch(e) { alert("Error"); }
    btn.innerText = "VIEW PDF";
}

async function fetchArchive() {
    const res = await fetch('/api/jobs?status=DELIVERED');
    globalJobs = await res.json();
    const container = document.getElementById('archive-container');

    const rows = await Promise.all(globalJobs.map(async job => {
        let scoreBadge = '';
        try {
            const scoreRes = await fetch(`/api/scores?id=${job.id}`);
            const scores = await scoreRes.json();
            const baseline = scores.baseline ? scores.baseline.score : 'N/A';
            const final = scores.final ? scores.final.score : 'N/A';
            const improvement = scores.final && scores.final.improvement !== null ? scores.final.improvement : 'N/A';
            scoreBadge = `<div style="font-size:12px; color:#2a8547; font-weight:600;">ATS ${baseline} → ${final} (${improvement >= 0 ? '+' : ''}${improvement})</div>`;
        } catch (e) { scoreBadge = '<div style="font-size:12px; color:#666;">No ATS data</div>'; }

        return `
        <div class="archive-row">
            <input type="checkbox" class="archive-check" value="${job.id}">
            <div class="archive-info">
                <div style="font-weight:700;">${job.title}</div>
                <div>${job.company}</div>
                ${scoreBadge}
            </div>
            <div class="archive-actions">
                ${job.has_pdf ? `<button class="btn-sm-ghost" onclick="window.open('${job.pdf_link}', '_blank')">PDF</button>` : ''}
                <button class="btn-sm-ghost" onclick="window.open('${job.job_url}', '_blank')">Link</button>
            </div>
        </div>
    `;
    }));

    container.innerHTML = rows.join('');
}

async function smuggleTags(id) {
    try {
        const res = await fetch(`/api/get_job_details?id=${id}`);
        const data = await res.json();
        const target = document.getElementById(`smuggler-${id}`);
        if (target && data.skills) target.innerHTML = `<ul style="margin:0; padding-left:18px; font-size:12px; color:#666;">${data.skills.slice(0,2).map(s => `<li>${s.name}</li>`).join('')}</ul>`;
    } catch(e){}
}

function setupSearch() {
    const btn = document.querySelector('.btn-find');
    const input = document.querySelector('.search-input');
    if (btn) btn.addEventListener('click', (e) => {
        e.preventDefault();
        const term = input.value.toLowerCase();
        const filtered = globalJobs.filter(j => j.title.toLowerCase().includes(term) || j.company.toLowerCase().includes(term));
        renderFeedList(filtered);
    });
}

// KEYBOARD PROTOCOL
document.addEventListener('keydown', e => {
    if(e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    // GLOBAL HOTKEYS: SUPER + ALT + A (Approve), SUPER + ALT + D (Deny)
    if (e.metaKey && e.altKey) {
        if (e.key.toLowerCase() === 'a') {
            e.preventDefault();
            if (currentJobId) approveJob(currentJobId);
            return;
        }
        if (e.key.toLowerCase() === 'd') {
            e.preventDefault();
            if (currentJobId) dismissJob(currentJobId);
            return;
        }
    }

    if(e.key==='ArrowDown' || e.key==='ArrowRight') {
        e.preventDefault();
        const idx = globalJobs.findIndex(j=>j.id===currentJobId);
        if(globalJobs[idx+1]) loadDetail(globalJobs[idx+1].id);
        return;
    }

    if(e.key==='ArrowUp' || e.key==='ArrowLeft') {
        e.preventDefault();
        const idx = globalJobs.findIndex(j=>j.id===currentJobId);
        if(globalJobs[idx-1]) loadDetail(globalJobs[idx-1].id);
        return;
    }
});
