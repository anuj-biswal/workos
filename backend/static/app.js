// =====================================================================
// STATE MANAGEMENT & CONFIG
// =====================================================================
const state = {
    user: null, // { name: '', role: '' }
    currentView: 'chat',
    pendingAttachments: [],
    workspaceId: 'default-workspace',
    files: [],
    activities: [],
    tasks: [],
    tokens: {
        limit: 500000,
        used: 0,
        prompt: 0,
        completion: 0
    },
    chatMessages: []
};

// =====================================================================
// INITIALIZATION & ROUTING
// =====================================================================
document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

function initApp() {
    // Check if user is logged in (mock)
    const storedUser = sessionStorage.getItem('agentos_user');
    if (storedUser) {
        state.user = JSON.parse(storedUser);
        showApp();
    } else {
        document.getElementById('login-screen').style.display = 'flex';
    }

    // Login Form Submit
    document.getElementById('login-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const name = document.getElementById('login-name').value;
        const role = document.getElementById('login-role').value;
        state.user = { name, role };
        sessionStorage.setItem('agentos_user', JSON.stringify(state.user));
        
        // Setup user info in UI
        setupUserInfo();
        
        // Hide login, show app
        document.getElementById('login-screen').style.animation = 'slideUp 0.5s reverse forwards';
        setTimeout(() => {
            document.getElementById('login-screen').style.display = 'none';
            showApp();
        }, 500);
    });

    // Hash Router
    window.addEventListener('hashchange', handleRoute);
    
    // Setup event listeners
    setupEventListeners();
}

function setupUserInfo() {
    if (!state.user) return;
    document.getElementById('user-name-sidebar').textContent = state.user.name;
    document.getElementById('user-role-sidebar').textContent = state.user.role;
    document.getElementById('user-avatar-sidebar').textContent = state.user.name.charAt(0).toUpperCase();
}

function showApp() {
    document.getElementById('app-container').style.display = 'flex';
    setupUserInfo();
    handleRoute();
    
    // Initial data fetch
    fetchAllData();
    
    // Setup interval for polling activity/tasks
    setInterval(() => {
        fetchActivity();
        fetchTasks();
        fetchTokens();
    }, 10000);
}

function handleRoute() {
    let hash = window.location.hash.replace('#', '');
    if (!['dashboard', 'files', 'chat', 'settings'].includes(hash)) {
        hash = 'chat';
        window.location.hash = 'chat';
    }
    
    state.currentView = hash;
    
    // Update sidebar nav
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(el => el.classList.remove('active'));
    document.getElementById(`nav-${hash}`).classList.add('active');
    
    // Update views
    document.querySelectorAll('.view').forEach(el => el.style.display = 'none');
    document.getElementById(`view-${hash}`).style.display = 'flex';
    
    // Update Title
    const titles = {
        'dashboard': 'Dashboard',
        'files': 'Workspace Files',
        'chat': 'Agent Chat',
        'settings': 'Settings & Configuration'
    };
    const subtitles = {
        'dashboard': 'Overview of your workspace activity and metrics',
        'files': 'Manage and preview uploaded documents',
        'chat': 'Interact with the LangGraph agent',
        'settings': 'Manage token guardrails and model info'
    };
    
    document.getElementById('page-title').textContent = titles[hash];
    document.getElementById('page-subtitle').textContent = subtitles[hash];
    
    // Trigger view-specific logic
    if (hash === 'dashboard') {
        renderCalendar();
        renderTasks();
    } else if (hash === 'files') {
        renderFileGrid();
    } else if (hash === 'chat') {
        scrollToBottom();
    } else if (hash === 'settings') {
        updateTokenRing();
        loadRagEvalDashboard();
    }
}

window.navigateTo = function(view) {
    window.location.hash = view;
};

// =====================================================================
// DATA FETCHING (API Integration)
// =====================================================================
async function fetchAllData() {
    await Promise.all([
        fetchWorkspaceFiles(),
        fetchActivity(),
        fetchTasks(),
        fetchTokens()
    ]);
    updateQuickStats();
}

async function fetchWorkspaceFiles() {
    try {
        const res = await fetch(`/api/workspace/${state.workspaceId}/files`);
        if (!res.ok) throw new Error('Failed to fetch files');
        const data = await res.json();
        state.files = (data.files || []).map(f => typeof f === 'object' ? f.name : f);
        if (state.currentView === 'files') renderFileGrid();
        if (state.currentView === 'dashboard') renderRecentFiles();
        updateSidebarFileList();
        updateQuickStats();
    } catch (e) {
        console.error(e);
        showToast('Error fetching files', 'error');
    }
}

async function fetchActivity() {
    try {
        const res = await fetch('/api/activity');
        if (!res.ok) throw new Error('Failed to fetch activity');
        const data = await res.json();
        state.activities = data.activities || data.activity || [];
        if (state.currentView === 'dashboard') renderActivityFeed();
        updateQuickStats();
    } catch (e) {
        console.error(e);
    }
}

async function fetchTasks() {
    try {
        const res = await fetch('/api/tasks');
        if (!res.ok) throw new Error('Failed to fetch tasks');
        const data = await res.json();
        state.tasks = data.tasks || [];
        if (state.currentView === 'dashboard') {
            renderTasks();
            renderCalendar(); // Tasks show as dots on calendar
        }
        updateQuickStats();
    } catch (e) {
        console.error(e);
    }
}

async function fetchTokens() {
    try {
        const res = await fetch('/api/tokens');
        if (!res.ok) throw new Error('Failed to fetch tokens');
        const data = await res.json();
        state.tokens.used = data.total_tokens || 0;
        state.tokens.prompt = data.prompt_tokens || 0;
        state.tokens.completion = data.completion_tokens || 0;
        state.tokens.limit = data.limit || 500000;
        
        updateTokenSidebar();
        if (state.currentView === 'settings') updateTokenRing();
        updateChatTokenHint();
        updateQuickStats();
    } catch (e) {
        console.error(e);
    }
}

// =====================================================================
// EVENT LISTENERS & UI SETUP
// =====================================================================
function setupEventListeners() {
    // Global File Upload
    const uploadBtn = document.getElementById('top-bar-upload-btn');
    const globalUploadInput = document.getElementById('global-file-upload');
    const zoneUploadBtn = document.getElementById('upload-zone-btn');
    const zoneUploadInput = document.getElementById('upload-zone-input');
    const uploadZone = document.getElementById('upload-zone');
    
    uploadBtn.addEventListener('click', () => globalUploadInput.click());
    zoneUploadBtn.addEventListener('click', () => zoneUploadInput.click());
    
    globalUploadInput.addEventListener('change', handleFileUpload);
    zoneUploadInput.addEventListener('change', handleFileUpload);
    
    uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); });
    uploadZone.addEventListener('dragleave', (e) => { e.preventDefault(); uploadZone.classList.remove('dragover'); });
    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault(); uploadZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            zoneUploadInput.files = e.dataTransfer.files;
            handleFileUpload({ target: zoneUploadInput });
        }
    });

    // Chat Inputs
    document.getElementById('chat-send-btn').addEventListener('click', sendChatMessage);
    const chatInput = document.getElementById('chat-input');
    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });
    chatInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
    });

    // Chat Attachment — stage files as chips instead of uploading immediately
    document.getElementById('chat-attach-btn').addEventListener('click', () => {
        document.getElementById('chat-file-input').click();
    });
    document.getElementById('chat-file-input').addEventListener('change', handleChatFileSelect);

    // Image Modal Close
    document.getElementById('modal-close').addEventListener('click', () => {
        document.getElementById('image-modal').classList.remove('show');
    });

    // Task Modal Setup
    const taskModal = document.getElementById('task-modal');
    document.getElementById('add-task-btn').addEventListener('click', () => {
        document.getElementById('task-form').reset();
        document.getElementById('task-date').valueAsDate = new Date();
        taskModal.classList.add('show');
    });
    document.getElementById('close-task-modal').addEventListener('click', () => {
        taskModal.classList.remove('show');
    });
    document.getElementById('task-form').addEventListener('submit', handleTaskCreate);
    
    // File filtering tabs
    document.querySelectorAll('.file-tabs .tab[data-file-filter]').forEach(tab => {
        tab.addEventListener('click', (e) => {
            document.querySelectorAll('.file-tabs .tab[data-file-filter]').forEach(t => t.classList.remove('active'));
            e.target.classList.add('active');
            renderFileGrid(e.target.dataset.fileFilter);
        });
    });

    // Settings Token Updates
    document.getElementById('update-limit-btn').addEventListener('click', async () => {
        const val = parseInt(document.getElementById('token-limit-input').value);
        await updateTokenLimit(val);
    });

    // Calendar Nav
    document.getElementById('cal-prev').addEventListener('click', () => shiftMonth(-1));
    document.getElementById('cal-next').addEventListener('click', () => shiftMonth(1));

    // File Preview Close
    document.getElementById('close-preview').addEventListener('click', () => {
        const panel = document.getElementById('file-preview-panel');
        panel.classList.remove('show');
        setTimeout(() => panel.style.display = 'none', 300);
    });
}

// =====================================================================
// FILE HANDLING
// =====================================================================
async function handleFileUpload(e) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    
    showToast(`Uploading ${files.length} file(s)...`, 'info');
    
    for (let i = 0; i < files.length; i++) {
        const formData = new FormData();
        formData.append("file", files[i]);
        formData.append("workspace_id", state.workspaceId);
        
        try {
            const res = await fetch('/api/upload', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (res.ok) {
                showToast(`Uploaded ${data.filename}`, 'success');
                appendSystemMessage(`File attached: ${data.filename}`);
            } else {
                showToast(`Failed to upload ${files[i].name}: ${data.detail}`, 'error');
            }
        } catch (err) {
            console.error(err);
            showToast(`Upload error: ${files[i].name}`, 'error');
        }
    }
    
    e.target.value = ''; // Reset input
    fetchAllData(); // Refresh UI
}

function renderFileGrid(filter = 'all') {
    const grid = document.getElementById('file-grid');
    grid.innerHTML = '';
    
    let filtered = state.files;
    if (filter !== 'all') {
        filtered = state.files.filter(f => {
            const ext = f.split('.').pop().toLowerCase();
            if (filter === 'excel') return ['xls', 'xlsx'].includes(ext);
            if (filter === 'csv') return ext === 'csv';
            if (filter === 'pdf') return ext === 'pdf';
            if (filter === 'text') return ['txt', 'doc', 'docx'].includes(ext);
            if (filter === 'image') return ['png', 'jpg', 'jpeg', 'gif'].includes(ext);
            return true;
        });
    }
    
    if (filtered.length === 0) {
        grid.innerHTML = `
            <div class="empty-state" style="grid-column:1/-1;">
                <i class="ph ph-folder-dashed" style="font-size:3rem;opacity:0.3;"></i>
                <p>No ${filter !== 'all' ? filter : ''} files found.</p>
            </div>
        `;
        return;
    }
    
    filtered.forEach(file => {
        const ext = file.split('.').pop().toLowerCase();
        let icon = 'ph-file';
        if (['xls','xlsx'].includes(ext)) icon = 'ph-file-xls';
        else if (ext === 'csv') icon = 'ph-file-csv';
        else if (ext === 'pdf') icon = 'ph-file-pdf';
        else if (['png','jpg','jpeg','gif'].includes(ext)) icon = 'ph-image';
        
        const card = document.createElement('div');
        card.className = 'file-card';
        card.onclick = () => openFilePreview(file);
        
        let previewHtml = `<i class="ph ${icon} file-type-icon"></i>`;
        if (['png','jpg','jpeg','gif'].includes(ext)) {
            previewHtml = `<img src="/api/workspace/${state.workspaceId}/download/${file}" alt="${file}">`;
        }
        
        card.innerHTML = `
            <div class="file-preview-box">
                ${previewHtml}
            </div>
            <div class="file-info">
                <div class="file-name" title="${file}">${file}</div>
                <div class="file-meta">
                    <span>${ext.toUpperCase()}</span>
                </div>
            </div>
        `;
        grid.appendChild(card);
    });
}

function renderRecentFiles() {
    const list = document.getElementById('recent-files-list');
    list.innerHTML = '';
    
    if (state.files.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <i class="ph ph-folder-dashed" style="font-size:2rem;opacity:0.3;"></i>
                <p>No files yet</p>
            </div>
        `;
        return;
    }
    
    // Just show last 5
    const recent = state.files.slice(-5).reverse();
    recent.forEach(file => {
        const item = document.createElement('div');
        item.style = 'background: var(--bg-primary); padding: 0.75rem; border-radius: var(--radius-sm); border: 1px solid var(--border-color); display: flex; align-items: center; gap: 0.5rem; cursor: pointer;';
        item.onclick = () => { navigateTo('files'); setTimeout(() => openFilePreview(file), 100); };
        item.innerHTML = `<i class="ph ph-file" style="color: var(--accent);"></i> <span style="font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${file}</span>`;
        list.appendChild(item);
    });
}

function updateSidebarFileList() {
    const list = document.getElementById('chat-file-list');
    if (!list) return;
    list.innerHTML = '';
    if (state.files.length === 0) {
        list.innerHTML = '<li class="empty-item">No files</li>';
        return;
    }
    state.files.forEach(f => {
        const li = document.createElement('li');
        li.innerHTML = `<i class="ph ph-file-text"></i> <span style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${f}</span>`;
        li.onclick = () => { navigateTo('files'); setTimeout(() => openFilePreview(f), 150); };
        list.appendChild(li);
    });
}

async function openFilePreview(filename) {
    const panel = document.getElementById('file-preview-panel');
    const content = document.getElementById('preview-content');
    document.getElementById('preview-filename').textContent = filename;
    document.getElementById('preview-download').href = `/api/workspace/${state.workspaceId}/download/${filename}`;
    
    content.innerHTML = '<div style="text-align:center; padding: 2rem;"><i class="ph ph-spinner ph-spin" style="font-size: 2rem;"></i> Loading preview...</div>';
    panel.style.display = 'flex';
    void panel.offsetWidth; // Force reflow
    panel.classList.add('show');
    
    try {
        const res = await fetch(`/api/workspace/${state.workspaceId}/preview/${filename}`);
        if (!res.ok) {
            content.innerHTML = '<div style="text-align:center; padding: 2rem; color: var(--danger);">Preview not available for this file type.</div>';
            return;
        }
        const data = await res.json();
        
        if (data.type === 'text') {
            content.innerHTML = `<pre style="font-size: 0.8rem; white-space: pre-wrap; word-break: break-all;">${escapeHtml(data.content)}</pre>`;
        } else if (data.type === 'table') {
            // Simple HTML table generator from array of dicts
            if (!data.content.length) {
                content.innerHTML = 'Empty table';
                return;
            }
            const headers = Object.keys(data.content[0]);
            let html = '<table style="width: 100%; border-collapse: collapse; font-size: 0.8rem;">';
            html += '<tr style="background: var(--bg-secondary); border-bottom: 2px solid var(--border-color);">';
            headers.forEach(h => html += `<th style="text-align: left; padding: 8px;">${h}</th>`);
            html += '</tr>';
            data.content.forEach(row => {
                html += '<tr style="border-bottom: 1px solid var(--border-color);">';
                headers.forEach(h => html += `<td style="padding: 8px;">${row[h]}</td>`);
                html += '</tr>';
            });
            html += '</table>';
            content.innerHTML = `<div style="overflow-x: auto;">${html}</div>`;
            
        } else if (data.type === 'image') {
            content.innerHTML = `<img src="${data.content}" style="max-width: 100%; border-radius: var(--radius-sm);">`;
        } else {
             content.innerHTML = `<div style="text-align:center; padding: 2rem;">Preview not available.</div>`;
        }
    } catch (e) {
        content.innerHTML = '<div style="text-align:center; padding: 2rem; color: var(--danger);">Error loading preview.</div>';
    }
}

// =====================================================================
// CHAT & AGENT INTERACTION
// =====================================================================
window.insertPrompt = function(text) {
    document.getElementById('chat-input').value = text;
    document.getElementById('chat-input').focus();
};

async function sendChatMessage() {
    const inputEl = document.getElementById('chat-input');
    let text = inputEl.value.trim();
    const hasAttachments = state.pendingAttachments.length > 0;
    
    if (!text && !hasAttachments) return;

    // If user attached files but typed no message, auto-generate one
    if (!text && hasAttachments) {
        const names = state.pendingAttachments.map(f => f.name);
        text = `I've uploaded ${names.join(', ')}. Please analyze ${names.length === 1 ? 'this file' : 'these files'}.`;
    }

    // Build combined user message with attachment list
    let displayText = text;
    if (hasAttachments) {
        const attachList = state.pendingAttachments.map(f => `📎 ${f.name}`).join('\n');
        displayText = attachList + '\n\n' + text;
    }
    appendUserMessage(displayText);
    inputEl.value = '';
    inputEl.style.height = 'auto';

    // Upload any pending attachments first
    if (hasAttachments) {
        const loadingUploadId = appendAgentMessage('📤 Uploading your files...', true);
        for (const file of state.pendingAttachments) {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('workspace_id', state.workspaceId);
            try {
                const upRes = await fetch('/api/upload', { method: 'POST', body: formData });
                const upData = await upRes.json();
                if (!upRes.ok) showToast(`Failed to upload ${file.name}: ${upData.detail}`, 'error');
            } catch (err) {
                showToast(`Upload error: ${file.name}`, 'error');
            }
        }
        removeMessage(loadingUploadId);
        clearAttachments();
        await fetchWorkspaceFiles(); // Refresh file lists
    }

    const loadingId = appendAgentMessage("Thinking...", true);

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, workspace_id: state.workspaceId })
        });
        const data = await res.json();
        
        removeMessage(loadingId);

        if (!res.ok) {
            appendAgentMessage(`⚠️ ${data.detail || 'Server error'}`);
            return;
        }

        appendAgentMessage(data.reply || "Done.");

        if (data.plan && data.plan.length > 0) {
            renderPlan(data.plan);
        }
        
        fetchTokens();

    } catch (e) {
        removeMessage(loadingId);
        appendAgentMessage("⚠️ Error connecting to the agent backend.");
    }
}

function appendUserMessage(text) {
    const history = document.getElementById('chat-history');
    const div = document.createElement('div');
    div.className = 'message user';
    div.innerHTML = `
        <div class="message-avatar">${state.user ? state.user.name.charAt(0) : 'U'}</div>
        <div class="message-content">${escapeHtml(text)}</div>
    `;
    history.appendChild(div);
    scrollToBottom();
}

function renderMarkdown(text) {
    // Replace citations like [Source: filename.pdf, page 5] or [Source: filename.pdf] with clickable badges
    const citationRegex = /\[Source:\s*([^,\]]+)(?:,\s*page\s*(\d+))?\]/gi;
    text = text.replace(citationRegex, (match, filename, page) => {
        if (page) {
            return `<span class="citation-badge" onclick="openPdfCitation('${filename}', ${page})"><i class="ph ph-file-pdf"></i> ${filename} (p.${page})</span>`;
        } else {
            return `<span class="citation-badge" onclick="navigateTo('files'); setTimeout(() => openFilePreview('${filename}'), 150)"><i class="ph ph-file"></i> ${filename}</span>`;
        }
    });

    // Use marked.js to parse markdown into HTML
    if (typeof marked !== 'undefined') {
        try {
            return marked.parse(text);
        } catch(e) {
            return escapeHtml(text);
        }
    }
    return escapeHtml(text);
}

function appendAgentMessage(text, isLoading = false, isRawHtml = false) {
    const history = document.getElementById('chat-history');
    const id = 'msg-' + Date.now();
    const div = document.createElement('div');
    div.className = 'message agent';
    div.id = id;
    
    let rendered;
    if (isLoading || isRawHtml) {
        rendered = text;
    } else {
        rendered = renderMarkdown(text);
    }
    
    div.innerHTML = `
        <div class="message-avatar"><i class="ph ph-robot" style="${isLoading ? 'animation: pulse 1s infinite;' : ''}"></i></div>
        <div class="message-content markdown-body" style="${isLoading ? 'opacity: 0.7;' : ''}">${rendered}</div>
    `;
    history.appendChild(div);
    scrollToBottom();
    return id;
}

function appendSystemMessage(text) {
    const history = document.getElementById('chat-history');
    const div = document.createElement('div');
    div.style = 'text-align: center; color: var(--text-secondary); font-size: 0.8rem; margin: 0.5rem 0;';
    div.textContent = text;
    history.appendChild(div);
    scrollToBottom();
}

function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function scrollToBottom() {
    const h = document.getElementById('chat-history');
    h.scrollTop = h.scrollHeight;
}

function _getToolCategory(toolName) {
    if (toolName === '__synthesize__') return { label: 'Synthesize & Respond', icon: 'ph-brain', cls: 'synthesize' };
    if (toolName === 'search_documents') return { label: 'RAG Search', icon: 'ph-magnifying-glass', cls: 'rag' };
    if (toolName.includes('chart') || toolName.includes('histogram') || toolName.includes('visualize')) return { label: 'Chart', icon: 'ph-chart-bar', cls: 'chart' };
    if (toolName.includes('analyze_dataset') || toolName.includes('clean') || toolName.includes('transform')) return { label: 'Data', icon: 'ph-database', cls: 'data' };
    if (toolName.includes('image') || toolName.includes('vision')) return { label: 'Vision', icon: 'ph-eye', cls: 'vision' };
    return { label: 'File', icon: 'ph-file-text', cls: 'file' };
}

function renderPlan(planSteps) {
    const planId = 'plan-' + Date.now();
    const history = document.getElementById('chat-history');
    const div = document.createElement('div');
    div.className = 'message agent';

    // Separate tool steps from the synthesize step
    const toolSteps = planSteps.filter(s => s.tool !== '__synthesize__');
    const synthStep = planSteps.find(s => s.tool === '__synthesize__');
    
    let stepsHtml = toolSteps.map((step, idx) => {
        const cat = _getToolCategory(step.tool);
        return `
        <div class="plan-step">
            <div class="step-number">${idx + 1}</div>
            <div style="flex: 1;">
                <span class="step-category-badge ${cat.cls}"><i class="ph ${cat.icon}"></i> ${cat.label}</span>
                <div class="step-desc" contenteditable="true" data-step-idx="${idx}">${escapeHtml(step.description)}</div>
            </div>
        </div>
    `;
    }).join('');

    // Add the synthesize step with a distinct style
    if (synthStep) {
        const synthIdx = toolSteps.length;
        stepsHtml += `
        <div class="plan-step synthesize-step">
            <div class="step-number" style="background: linear-gradient(135deg, var(--accent), #a855f7);">${synthIdx + 1}</div>
            <div style="flex: 1;">
                <span class="step-category-badge synthesize"><i class="ph ph-brain"></i> Synthesize & Respond</span>
                <div class="step-desc">${escapeHtml(synthStep.description)}</div>
            </div>
        </div>
    `;
    }

    const encodedSteps = btoa(unescape(encodeURIComponent(JSON.stringify(planSteps))));

    div.innerHTML = `
        <div class="message-avatar"><i class="ph ph-target"></i></div>
        <div style="flex:1;">
            <div class="plan-container">
                <div class="plan-header">
                    <span style="font-weight: 600; font-size: 0.9rem;"><i class="ph ph-list-numbers"></i> Here's my plan</span>
                    <button class="btn-accent btn-sm approve-btn" onclick="executePlan('${planId}')">Approve & Execute</button>
                </div>
                <div id="${planId}" data-steps="${encodedSteps}">
                    ${stepsHtml}
                </div>
            </div>
        </div>
    `;
    history.appendChild(div);
    scrollToBottom();
}

window.executePlan = async function(planId) {
    const container = document.getElementById(planId);
    if (!container) return;
    
    const btn = container.parentElement.querySelector('.approve-btn');
    btn.textContent = "Executing...";
    btn.disabled = true;
    btn.style.background = 'var(--text-secondary)';

    // Parse the encoded plan back
    let originalSteps;
    try {
        originalSteps = JSON.parse(decodeURIComponent(escape(atob(container.getAttribute('data-steps')))));
    } catch(e) {
        showToast('Failed to parse plan data', 'error');
        return;
    }
    
    // Read any edits from contenteditable
    const stepsDivs = container.querySelectorAll('.step-desc');
    stepsDivs.forEach((div) => {
        const idx = parseInt(div.getAttribute('data-step-idx'));
        if (idx < originalSteps.length) {
            originalSteps[idx].description = div.innerText; // Get edited text
        }
    });

    // Filter out the synthesize display-only step before sending to backend
    const executableSteps = originalSteps.filter(s => s.tool !== '__synthesize__');

    try {
        const res = await fetch('/api/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plan_steps: executableSteps, workspace_id: state.workspaceId })
        });
        const result = await res.json();

        if (!res.ok) {
            btn.textContent = "Failed";
            btn.style.background = 'var(--warning)';
            appendAgentMessage(`⚠️ Execution error: ${result.detail || res.status}`);
            return;
        }
        
        btn.textContent = "✅ Completed";
        btn.style.background = 'var(--success)';
        
        // Collect generated files and errors from step results
        const generatedFiles = [];
        const errors = [];
        if (result.results && result.results.length > 0) {
            result.results.forEach(r => {
                // Detect generated filenames only for tools that create/modify/generate files
                // or if the output specifically states "Successfully"
                if (r.tool.startsWith('create_') || r.tool.startsWith('generate_') || r.tool.startsWith('modify_') || r.output.includes('Successfully')) {
                    const filePattern = /([a-zA-Z0-9_\-]+\.(?:png|jpg|jpeg|gif|svg|csv|xlsx|xls|pdf|txt))/gi;
                    let match;
                    while ((match = filePattern.exec(r.output)) !== null) {
                        if (!generatedFiles.includes(match[1])) generatedFiles.push(match[1]);
                    }
                }
                if (r.status === 'error') {
                    errors.push(r);
                }
            });
        }

        // Show the LLM's final reply as the primary output
        if (result.reply) {
            appendAgentMessage(result.reply);
        } else if (errors.length > 0) {
            // If no reply but there were errors, show them
            let errorHtml = '<strong>⚠️ Some steps encountered errors:</strong><br><br>';
            errors.forEach(r => {
                errorHtml += `<div style="margin-bottom:0.5rem;"><span style="color:var(--danger)">❌</span> <strong>${escapeHtml(r.tool)}</strong>: ${escapeHtml(r.output)}</div>`;
            });
            appendAgentMessage(errorHtml, false, true);
        } else {
            appendAgentMessage('✅ Execution completed successfully.');
        }

        // Show inline preview for any generated images
        if (generatedFiles.length > 0) {
            const imageFiles = generatedFiles.filter(f => /\.(png|jpg|jpeg|gif|svg)$/i.test(f));
            if (imageFiles.length > 0) {
                let imgHtml = '';
                imageFiles.forEach(img => {
                    const fileUrl = `/api/workspace/${state.workspaceId}/download/${img}`;
                    imgHtml += `<div style="margin-top:10px;"><img src="${fileUrl}" style="max-width:100%; border-radius:8px; border:1px solid var(--border-color); cursor:zoom-in;" onclick="openImageModal('${fileUrl}')"><div style="font-size:0.8rem; color:var(--text-secondary); margin-top:4px;"><span class="file-link-chip" onclick="navigateTo('files'); setTimeout(() => openFilePreview('${img}'), 150);"><i class="ph ph-file-text"></i> ${img}</span></div></div>`;
                });
                appendAgentMessage(imgHtml, false, true);
            }
            addOutputFilesToSidebar(generatedFiles);
        }

        // ── Debug Panel (RAG diagnostics + LLM evaluation) ──────────
        if (result.debug) {
            renderDebugPanel(result.debug);
        }
        
        fetchAllData(); // Refresh UI

    } catch(e) {
        btn.textContent = "Failed";
        btn.style.background = 'var(--danger)';
        appendAgentMessage(`⚠️ Execution failed: ${e.message}`);
    }
}

function addOutputFilesToSidebar(filenames) {
    const container = document.getElementById('chat-outputs');
    if (!container) return;
    if (container.querySelector('.empty-item')) container.innerHTML = '';
    
    filenames.forEach(filename => {
        const ext = filename.split('.').pop().toLowerCase();
        const isImage = ['png', 'jpg', 'jpeg', 'gif', 'svg'].includes(ext);
        
        let icon = 'ph-file';
        if (['xls','xlsx'].includes(ext)) icon = 'ph-file-xls';
        else if (ext === 'csv') icon = 'ph-file-csv';
        else if (ext === 'pdf') icon = 'ph-file-pdf';
        else if (isImage) icon = 'ph-image';
        
        const item = document.createElement('div');
        item.className = 'chat-output-item';
        item.onclick = () => { navigateTo('files'); setTimeout(() => openFilePreview(filename), 150); };
        
        if (isImage) {
            const fileUrl = `/api/workspace/${state.workspaceId}/download/${filename}`;
            item.innerHTML = `<img src="${fileUrl}" alt="${filename}"> <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${filename}</span>`;
        } else {
            item.innerHTML = `<i class="ph ${icon}"></i> <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${filename}</span>`;
        }
        container.appendChild(item);
    });
}

// Legacy compat
function addOutputImageToSidebar(url) {
    // Extract filename from URL
    const parts = url.split('/');
    const filename = parts[parts.length - 1];
    addOutputFilesToSidebar([filename]);
}

// =====================================================================
// CALENDAR & TASKS
// =====================================================================
let currentCalDate = new Date(); // Using 2026 as per prompt environment, but logic scales

function renderCalendar() {
    const grid = document.getElementById('calendar-grid');
    if (!grid) return;
    
    grid.innerHTML = '';
    
    const year = currentCalDate.getFullYear();
    const month = currentCalDate.getMonth();
    
    const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    document.getElementById('cal-month-year').textContent = `${monthNames[month]} ${year}`;
    
    const firstDay = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const prevDaysInMonth = new Date(year, month, 0).getDate();
    
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    days.forEach(d => {
        const el = document.createElement('div');
        el.className = 'cal-day-header';
        el.textContent = d;
        grid.appendChild(el);
    });
    
    // Previous month filler
    for (let i = 0; i < firstDay; i++) {
        grid.appendChild(createCalCell(prevDaysInMonth - firstDay + i + 1, true));
    }
    
    // Current month
    const today = new Date();
    for (let i = 1; i <= daysInMonth; i++) {
        const isToday = today.getDate() === i && today.getMonth() === month && today.getFullYear() === year;
        grid.appendChild(createCalCell(i, false, isToday, year, month));
    }
    
    // Next month filler
    const totalCells = firstDay + daysInMonth;
    const remaining = Math.ceil(totalCells / 7) * 7 - totalCells;
    for (let i = 1; i <= remaining; i++) {
        grid.appendChild(createCalCell(i, true));
    }
}

function createCalCell(day, isOther, isToday = false, year, month) {
    const el = document.createElement('div');
    el.className = 'cal-cell' + (isOther ? ' other-month' : '') + (isToday ? ' today' : '');
    
    const dateStr = !isOther ? `${year}-${String(month+1).padStart(2, '0')}-${String(day).padStart(2, '0')}` : null;
    
    let eventsHtml = '';
    if (dateStr) {
        // Find tasks for this date
        const dayTasks = state.tasks.filter(t => t.due_date && t.due_date.startsWith(dateStr));
        dayTasks.forEach(t => {
            let color = 'var(--info)';
            if (t.priority === 'high') color = 'var(--danger)';
            if (t.priority === 'medium') color = 'var(--warning)';
            if (t.status === 'completed') color = 'var(--success)';
            eventsHtml += `<div class="cal-dot" style="background:${color};" title="${escapeHtml(t.title)}"></div>`;
        });
    }

    el.innerHTML = `
        <div class="cal-date">${day}</div>
        <div class="cal-events">${eventsHtml}</div>
    `;
    return el;
}

function shiftMonth(dir) {
    currentCalDate.setMonth(currentCalDate.getMonth() + dir);
    renderCalendar();
}

function renderTasks() {
    const list = document.getElementById('task-list');
    if (!list) return;
    list.innerHTML = '';
    
    if (state.tasks.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <i class="ph ph-clipboard-text" style="font-size:2rem;opacity:0.3;"></i>
                <p>No tasks yet</p>
            </div>
        `;
        return;
    }
    
    state.tasks.forEach(task => {
        const div = document.createElement('div');
        div.className = `task-item ${task.status === 'completed' ? 'done' : ''}`;
        
        let pClass = 'priority-low';
        if (task.priority === 'high') pClass = 'priority-high';
        if (task.priority === 'medium') pClass = 'priority-medium';

        div.innerHTML = `
            <input type="checkbox" class="task-checkbox" ${task.status === 'completed' ? 'checked' : ''} onchange="toggleTaskStatus(${task.id}, this.checked)">
            <div class="task-content">
                <div class="task-title">${escapeHtml(task.title)}</div>
                <div class="task-meta">
                    <span class="priority-dot ${pClass}"></span>
                    ${task.due_date ? `<span>${task.due_date}</span>` : ''}
                </div>
            </div>
            <div class="task-actions">
                <button class="icon-btn" onclick="deleteTask(${task.id})" title="Delete"><i class="ph ph-trash"></i></button>
            </div>
        `;
        list.appendChild(div);
    });
}

async function handleTaskCreate(e) {
    e.preventDefault();
    const title = document.getElementById('task-title').value;
    const desc = document.getElementById('task-desc').value;
    const date = document.getElementById('task-date').value;
    const priority = document.getElementById('task-priority').value;
    
    try {
        const res = await fetch('/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description: desc, due_date: date, priority })
        });
        if (res.ok) {
            document.getElementById('task-modal').classList.remove('show');
            showToast('Task created', 'success');
            fetchTasks();
        }
    } catch (e) { showToast('Error creating task', 'error'); }
}

window.toggleTaskStatus = async function(id, isChecked) {
    const task = state.tasks.find(t => t.id === id);
    if (!task) return;
    task.status = isChecked ? 'completed' : 'pending';
    
    try {
        await fetch(`/api/tasks/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(task)
        });
        fetchTasks();
    } catch (e) { showToast('Error updating task', 'error'); }
}

window.deleteTask = async function(id) {
    try {
        await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
        showToast('Task deleted', 'info');
        fetchTasks();
    } catch (e) { showToast('Error deleting task', 'error'); }
}

function renderActivityFeed() {
    const list = document.getElementById('activity-list');
    if (!list) return;
    list.innerHTML = '';
    
    if (state.activities.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <i class="ph ph-activity" style="font-size:2rem;opacity:0.3;"></i>
                <p>No activity yet</p>
            </div>
        `;
        return;
    }
    
    // Sort descending by timestamp (simplistic fallback if backend didn't sort)
    const sorted = [...state.activities].reverse();
    
    sorted.forEach(act => {
        const div = document.createElement('div');
        div.className = 'activity-item';
        
        let icon = 'ph-info';
        if (act.action === 'upload') icon = 'ph-upload-simple';
        if (act.action === 'chat') icon = 'ph-chat-circle-dots';
        if (act.action === 'execute') icon = 'ph-lightning';

        const date = new Date(act.timestamp);
        const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        
        div.innerHTML = `
            <div class="activity-icon"><i class="ph ${icon}"></i></div>
            <div class="activity-content">
                <div class="activity-text"><strong>${escapeHtml(act.user)}</strong> ${escapeHtml(act.details)}</div>
                <div class="activity-time">${timeStr}</div>
            </div>
        `;
        list.appendChild(div);
    });
}

function updateQuickStats() {
    const eFiles = document.getElementById('stat-files');
    const eTasks = document.getElementById('stat-tasks-done');
    const eExecs = document.getElementById('stat-executions');
    const eTokens = document.getElementById('stat-tokens');
    
    if (eFiles) eFiles.textContent = state.files.length;
    if (eTasks) eTasks.textContent = state.tasks.filter(t => t.status === 'completed').length;
    if (eExecs) eExecs.textContent = state.activities.filter(a => a.action === 'execute').length;
    if (eTokens) eTokens.textContent = formatNumber(state.tokens.used);
}

// =====================================================================
// SETTINGS & TOKENS
// =====================================================================
function updateTokenSidebar() {
    const fill = document.getElementById('sidebar-token-fill');
    const label = document.getElementById('sidebar-token-label');
    if (!fill || !label) return;
    
    let pct = (state.tokens.used / state.tokens.limit) * 100;
    if (pct > 100) pct = 100;
    
    fill.style.width = `${pct}%`;
    if (pct > 90) fill.style.background = 'var(--danger)';
    else if (pct > 75) fill.style.background = 'var(--warning)';
    else fill.style.background = 'var(--accent)';
    
    label.textContent = `${formatNumber(state.tokens.used)} / ${formatNumber(state.tokens.limit)}`;
}

function updateTokenRing() {
    const fill = document.getElementById('token-ring-fill');
    const val = document.getElementById('token-ring-value');
    if (!fill || !val) return;
    
    let pct = (state.tokens.used / state.tokens.limit) * 100;
    if (pct > 100) pct = 100;
    
    // SVG circle math: circumference = 2 * pi * r (r=50) = ~314
    const offset = 314 - (pct / 100) * 314;
    fill.style.strokeDashoffset = offset;
    
    if (pct > 90) fill.style.stroke = 'var(--danger)';
    else if (pct > 75) fill.style.stroke = 'var(--warning)';
    else fill.style.stroke = 'var(--accent)';
    
    val.textContent = `${Math.round(pct)}%`;
    
    // Stats text
    document.getElementById('settings-prompt-tokens').textContent = formatNumber(state.tokens.prompt);
    document.getElementById('settings-completion-tokens').textContent = formatNumber(state.tokens.completion);
    document.getElementById('settings-total-tokens').textContent = formatNumber(state.tokens.used);
    
    const rem = state.tokens.limit - state.tokens.used;
    const eRem = document.getElementById('settings-remaining-tokens');
    eRem.textContent = formatNumber(rem);
    if (rem < 10000) eRem.style.color = 'var(--danger)';
    else eRem.style.color = 'var(--success)';
}

function updateChatTokenHint() {
    const u = document.getElementById('chat-tokens-used');
    const l = document.getElementById('chat-tokens-limit');
    if (u) u.textContent = formatNumber(state.tokens.used);
    if (l) l.textContent = formatNumber(state.tokens.limit);
}

window.setLimitPreset = function(val) {
    document.getElementById('token-limit-input').value = val;
    document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
    event.target.classList.add('active');
};

async function updateTokenLimit(val) {
    try {
        const res = await fetch(`/api/tokens/limit?limit=${val}`, { method: 'POST' });
        if (res.ok) {
            showToast(`Token limit updated to ${formatNumber(val)}`, 'success');
            fetchTokens();
        }
    } catch (e) {
        showToast('Error updating token limit', 'error');
    }
}

// =====================================================================
// UTILS
// =====================================================================
window.openImageModal = function(src) {
    const modal = document.getElementById('image-modal');
    const img = document.getElementById('modal-img');
    if (modal && img) {
        img.src = src;
        modal.classList.add('show');
    }
};

function showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    let icon = 'ph-info';
    if (type === 'success') icon = 'ph-check-circle';
    if (type === 'error') icon = 'ph-warning-circle';
    if (type === 'warning') icon = 'ph-warning';
    
    toast.innerHTML = `<i class="ph ${icon}" style="font-size: 1.25rem;"></i> <span>${escapeHtml(msg)}</span>`;
    container.appendChild(toast);
    
    // Trigger animation
    setTimeout(() => toast.classList.add('show'), 10);
    
    // Remove after 3s
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}

// =====================================================================
// ATTACHMENT CHIPS (Chat file staging)
// =====================================================================
function handleChatFileSelect(e) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    
    const maxFiles = 5;
    const totalWillBe = state.pendingAttachments.length + files.length;
    
    if (totalWillBe > maxFiles) {
        showToast(`Maximum ${maxFiles} files allowed. You can attach ${maxFiles - state.pendingAttachments.length} more.`, 'warning');
    }
    
    for (let i = 0; i < files.length && state.pendingAttachments.length < maxFiles; i++) {
        state.pendingAttachments.push(files[i]);
    }
    
    e.target.value = ''; // Reset input
    renderAttachmentChips();
}

function renderAttachmentChips() {
    const container = document.getElementById('chat-attachments');
    if (!container) return;
    
    if (state.pendingAttachments.length === 0) {
        container.style.display = 'none';
        container.innerHTML = '';
        return;
    }
    
    container.style.display = 'flex';
    container.innerHTML = state.pendingAttachments.map((file, idx) => {
        const ext = file.name.split('.').pop().toLowerCase();
        let icon = 'ph-file';
        if (['xls','xlsx'].includes(ext)) icon = 'ph-file-xls';
        else if (ext === 'csv') icon = 'ph-file-csv';
        else if (ext === 'pdf') icon = 'ph-file-pdf';
        else if (['png','jpg','jpeg','gif'].includes(ext)) icon = 'ph-image';
        
        return `<div class="attachment-chip">
            <i class="ph ${icon}"></i>
            <span>${escapeHtml(file.name)}</span>
            <button class="remove-chip" onclick="removeAttachment(${idx})" title="Remove">✕</button>
        </div>`;
    }).join('');
}

window.removeAttachment = function(idx) {
    state.pendingAttachments.splice(idx, 1);
    renderAttachmentChips();
};

function clearAttachments() {
    state.pendingAttachments = [];
    renderAttachmentChips();
}

// ═══════════════════ RAG DEBUG PANEL ═══════════════════
function renderDebugPanel(debug) {
    const history = document.getElementById('chat-history');
    const div = document.createElement('div');
    div.className = 'message agent debug-panel-container';
    
    const diag = debug.rag_diagnostics || {};
    const llmEval = debug.llm_evaluation || {};
    const errors = debug.errors_encountered || [];
    const timing = debug.per_step_timing || [];
    const latency = diag.latency_ms || {};
    const perResult = diag.per_result_debug || [];

    // Score color helper
    function scoreColor(score) {
        if (score === null || score === undefined) return '#6b7280';
        if (score >= 0.8) return '#10b981';
        if (score >= 0.5) return '#f59e0b';
        return '#ef4444';
    }
    function scoreBar(score, max, label, color) {
        const pct = max > 0 ? Math.min((score / max) * 100, 100) : 0;
        return `<div class="dbg-bar-row"><span class="dbg-bar-label">${label}</span><div class="dbg-bar-track"><div class="dbg-bar-fill" style="width:${pct}%; background:${color || '#06b6d4'}"></div></div><span class="dbg-bar-val">${score !== null && score !== undefined ? score.toFixed(4) : '—'}</span></div>`;
    }

    // ── Retrieval Metrics Section ────────────────────────────
    let metricsHtml = `<div class="dbg-section">
        <div class="dbg-section-title"><i class="ph ph-magnifying-glass"></i> Retrieval Metrics</div>
        <div class="dbg-grid">
            <div class="dbg-stat"><span class="dbg-stat-val">${diag.results_returned ?? '—'}</span><span class="dbg-stat-label">Results</span></div>
            <div class="dbg-stat"><span class="dbg-stat-val">${diag.source_diversity ?? '—'}</span><span class="dbg-stat-label">Sources</span></div>
            <div class="dbg-stat"><span class="dbg-stat-val">${diag.vector_results_count ?? '—'}</span><span class="dbg-stat-label">Vector Hits</span></div>
            <div class="dbg-stat"><span class="dbg-stat-val">${diag.bm25_results_count ?? '—'}</span><span class="dbg-stat-label">BM25 Hits</span></div>
            <div class="dbg-stat"><span class="dbg-stat-val">${diag.vector_bm25_overlap ?? '—'}</span><span class="dbg-stat-label">Overlap</span></div>
            <div class="dbg-stat"><span class="dbg-stat-val" style="color:${scoreColor(diag.agreement_rate)}">${diag.agreement_rate !== null ? (diag.agreement_rate * 100).toFixed(1) + '%' : '—'}</span><span class="dbg-stat-label">Agreement</span></div>
        </div>
        ${scoreBar(diag.avg_cosine_similarity, 1, 'Avg Cosine Sim', '#06b6d4')}
        ${scoreBar(diag.max_cosine_similarity, 1, 'Max Cosine Sim', '#0ea5e9')}
        ${scoreBar(diag.avg_bm25_score, diag.max_bm25_score || 20, 'Avg BM25', '#8b5cf6')}
    </div>`;

    // ── Latency Waterfall ─────────────────────────────────────
    const totalMs = latency.total || 1;
    let waterfallHtml = `<div class="dbg-section">
        <div class="dbg-section-title"><i class="ph ph-timer"></i> Latency Waterfall <span class="dbg-tag">${totalMs.toFixed(0)}ms total</span></div>
        <div class="dbg-waterfall">`;
    const stages = [
        {label: 'Embedding', ms: latency.embedding, color: '#06b6d4'},
        {label: 'Vector Search', ms: latency.vector_search, color: '#3b82f6'},
        {label: 'BM25 Search', ms: latency.bm25_search, color: '#8b5cf6'},
        {label: 'RRF Fusion', ms: latency.fusion, color: '#10b981'},
    ];
    stages.forEach(s => {
        const pct = (s.ms / totalMs * 100).toFixed(1);
        waterfallHtml += `<div class="dbg-wf-row"><span class="dbg-wf-label">${s.label}</span><div class="dbg-wf-track"><div class="dbg-wf-bar" style="width:${pct}%; background:${s.color}"></div></div><span class="dbg-wf-ms">${(s.ms || 0).toFixed(0)}ms</span></div>`;
    });
    waterfallHtml += '</div></div>';

    // ── Per-Result Debug ─────────────────────────────────────
    let chunksHtml = '';
    if (perResult.length > 0) {
        chunksHtml = `<div class="dbg-section"><div class="dbg-section-title"><i class="ph ph-stack"></i> Retrieved Chunks</div><div class="dbg-chunks">`;
        perResult.forEach((r, i) => {
            const methods = (r.found_by || []).map(m => `<span class="dbg-method-tag dbg-method-${m}">${m}</span>`).join('');
            chunksHtml += `<div class="dbg-chunk-card">
                <div class="dbg-chunk-header"><span class="dbg-chunk-rank">#${i+1}</span> <span class="dbg-chunk-file">${r.filename || '?'}</span> <span class="dbg-chunk-page">p${r.page || '?'}</span> ${methods}</div>
                ${scoreBar(r.cosine_similarity, 1, 'Cosine', '#06b6d4')}
                ${scoreBar(r.bm25_score, diag.max_bm25_score || 20, 'BM25', '#8b5cf6')}
                ${scoreBar(r.rrf_score, 0.035, 'RRF', '#10b981')}
            </div>`;
        });
        chunksHtml += '</div></div>';
    }

    // ── LLM-as-Judge Evaluation ──────────────────────────────
    let evalHtml = '';
    if (llmEval && !llmEval.error) {
        const faith = llmEval.faithfulness || {};
        const rel = llmEval.relevancy || {};
        const cp = llmEval.context_precision || {};
        evalHtml = `<div class="dbg-section dbg-eval-section">
            <div class="dbg-section-title"><i class="ph ph-brain"></i> LLM-as-Judge Evaluation <span class="dbg-tag">${(llmEval.eval_latency_ms || 0).toFixed(0)}ms</span></div>
            <div class="dbg-eval-grid">
                <div class="dbg-eval-card">
                    <div class="dbg-eval-score" style="color:${scoreColor(faith.score)}">${faith.score !== null && faith.score !== undefined ? (faith.score * 100).toFixed(0) + '%' : '—'}</div>
                    <div class="dbg-eval-label">Faithfulness</div>
                    <div class="dbg-eval-desc">${faith.reasoning || ''}</div>
                </div>
                <div class="dbg-eval-card">
                    <div class="dbg-eval-score" style="color:${scoreColor(rel.score)}">${rel.score !== null && rel.score !== undefined ? (rel.score * 100).toFixed(0) + '%' : '—'}</div>
                    <div class="dbg-eval-label">Relevancy</div>
                    <div class="dbg-eval-desc">${rel.reasoning || ''}</div>
                </div>
                <div class="dbg-eval-card">
                    <div class="dbg-eval-score" style="color:${scoreColor(cp.score)}">${cp.score !== null && cp.score !== undefined ? (cp.score * 100).toFixed(0) + '%' : '—'}</div>
                    <div class="dbg-eval-label">Context Precision</div>
                    <div class="dbg-eval-desc">${cp.reasoning || ''}</div>
                </div>
            </div>
            ${llmEval.overall_score !== null ? `<div class="dbg-overall">Overall: <strong style="color:${scoreColor(llmEval.overall_score)}">${(llmEval.overall_score * 100).toFixed(0)}%</strong></div>` : ''}
        </div>`;
    } else if (llmEval && llmEval.error) {
        evalHtml = `<div class="dbg-section"><div class="dbg-section-title"><i class="ph ph-brain"></i> LLM Evaluation</div><div class="dbg-error">⚠️ ${llmEval.error}</div></div>`;
    }

    // ── Errors ────────────────────────────────────────────────
    let errorsHtml = '';
    if (errors.length > 0) {
        errorsHtml = `<div class="dbg-section"><div class="dbg-section-title"><i class="ph ph-warning"></i> Errors Encountered</div>`;
        errors.forEach(e => {
            const typeClass = `dbg-err-${e.error_type || 'unknown'}`;
            errorsHtml += `<div class="dbg-error-row"><span class="dbg-err-badge ${typeClass}">${e.error_type || 'unknown'}</span> <strong>${e.tool}</strong> ${e.retries > 0 ? `<span class="dbg-retry-badge">${e.retries} retries</span>` : ''}</div>`;
        });
        errorsHtml += '</div>';
    }

    // ── Step Timing ──────────────────────────────────────────
    let timingHtml = '';
    if (timing.length > 0) {
        timingHtml = `<div class="dbg-section"><div class="dbg-section-title"><i class="ph ph-clock"></i> Step Timing</div>`;
        timing.forEach(t => {
            timingHtml += `<div class="dbg-timing-row"><span class="dbg-timing-tool">${t.tool}</span><span class="dbg-timing-ms">${(t.duration_ms || 0).toFixed(0)}ms</span></div>`;
        });
        timingHtml += '</div>';
    }

    div.innerHTML = `
        <div class="debug-panel">
            <div class="debug-panel-header" onclick="this.parentElement.classList.toggle('collapsed')">
                <span><i class="ph ph-bug"></i> RAG Debug Panel</span>
                <span class="debug-panel-toggle"><i class="ph ph-caret-down"></i></span>
            </div>
            <div class="debug-panel-body">
                ${metricsHtml}
                ${waterfallHtml}
                ${evalHtml}
                ${chunksHtml}
                ${errorsHtml}
                ${timingHtml}
            </div>
        </div>`;
    
    history.appendChild(div);
    scrollToBottom();
}

// ═══════════════════ RAG EVAL DASHBOARD (Settings) ═══════════════════
async function loadRagEvalDashboard() {
    const container = document.getElementById('rag-eval-dashboard');
    if (!container) return;

    try {
        const res = await fetch(`/api/workspace/${state.workspaceId}/rag/eval`);
        const data = await res.json();
        
        const health = data.index_health || {};
        const perf = data.search_performance || {};
        const evalData = data.llm_evaluation || {};
        const summary = evalData.summary || {};
        const history = perf.history || [];

        container.innerHTML = `
            <div class="eval-grid">
                <div class="eval-card">
                    <div class="eval-card-title"><i class="ph ph-database"></i> Index Health</div>
                    <div class="eval-card-stat">${health.indexed_files || 0}</div>
                    <div class="eval-card-label">Files Indexed</div>
                    <div class="eval-card-sub">${health.total_chunks || 0} total chunks</div>
                </div>
                <div class="eval-card">
                    <div class="eval-card-title"><i class="ph ph-magnifying-glass"></i> Searches</div>
                    <div class="eval-card-stat">${perf.total_searches || 0}</div>
                    <div class="eval-card-label">Total Queries</div>
                </div>
                <div class="eval-card">
                    <div class="eval-card-title"><i class="ph ph-brain"></i> Avg Faithfulness</div>
                    <div class="eval-card-stat" style="color:${summary.avg_faithfulness >= 0.8 ? '#10b981' : summary.avg_faithfulness >= 0.5 ? '#f59e0b' : '#ef4444'}">${summary.avg_faithfulness !== null && summary.avg_faithfulness !== undefined ? (summary.avg_faithfulness * 100).toFixed(0) + '%' : '—'}</div>
                    <div class="eval-card-label">${summary.total_evals || 0} evaluations</div>
                </div>
                <div class="eval-card">
                    <div class="eval-card-title"><i class="ph ph-target"></i> Avg Relevancy</div>
                    <div class="eval-card-stat" style="color:${summary.avg_relevancy >= 0.8 ? '#10b981' : summary.avg_relevancy >= 0.5 ? '#f59e0b' : '#ef4444'}">${summary.avg_relevancy !== null && summary.avg_relevancy !== undefined ? (summary.avg_relevancy * 100).toFixed(0) + '%' : '—'}</div>
                    <div class="eval-card-label">Answer Quality</div>
                </div>
            </div>
            ${history.length > 0 ? `
            <div class="eval-table-wrapper">
                <div class="eval-table-title">Recent Search History</div>
                <table class="eval-table">
                    <thead><tr><th>Query</th><th>Results</th><th>Confidence</th><th>Cosine Sim</th><th>Agreement</th><th>Latency</th></tr></thead>
                    <tbody>
                        ${history.slice(-10).reverse().map(h => `<tr>
                            <td class="eval-query-cell">${escapeHtml((h.query || '').substring(0, 50))}${(h.query || '').length > 50 ? '…' : ''}</td>
                            <td>${h.results_count || 0}</td>
                            <td>${h.avg_confidence ? h.avg_confidence.toFixed(4) : '—'}</td>
                            <td>${h.avg_cosine_sim ? h.avg_cosine_sim.toFixed(4) : '—'}</td>
                            <td>${h.agreement_rate ? (h.agreement_rate * 100).toFixed(1) + '%' : '—'}</td>
                            <td>${h.latency_ms ? h.latency_ms.toFixed(0) + 'ms' : '—'}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>
            </div>` : '<div class="eval-empty">No searches yet. Try asking the agent a question about your documents.</div>'}
        `;
    } catch(e) {
        container.innerHTML = `<div class="eval-empty">Failed to load RAG evaluation data: ${e.message}</div>`;
    }
}

// =====================================================================
// RAG FRONTEND EXTENSIONS
// =====================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Folder Upload Setup
    const folderUploadBtn = document.getElementById('upload-folder-btn');
    const folderUploadInput = document.getElementById('upload-folder-input');
    if (folderUploadBtn && folderUploadInput) {
        folderUploadBtn.addEventListener('click', () => folderUploadInput.click());
        folderUploadInput.addEventListener('change', handleFolderUpload);
    }

    // Modal Closes
    document.getElementById('close-pdf-page-modal').addEventListener('click', () => {
        document.getElementById('pdf-page-modal').classList.remove('show');
    });
    document.getElementById('close-chunk-modal').addEventListener('click', () => {
        document.getElementById('chunk-modal').classList.remove('show');
    });

    // Zoom PDF
    document.getElementById('pdf-zoom-in').addEventListener('click', () => zoomPdf(1.2));
    document.getElementById('pdf-zoom-out').addEventListener('click', () => zoomPdf(0.8));

    // Settings Sync
    document.getElementById('rag-alpha-slider')?.addEventListener('input', (e) => {
        document.getElementById('alpha-value-display').textContent = e.target.value;
    });

    // Tab for RAG Index
    const ragIndexTab = document.getElementById('tab-rag-index');
    if (ragIndexTab) {
        ragIndexTab.addEventListener('click', () => {
            document.getElementById('file-grid').style.display = 'none';
            document.getElementById('rag-doc-management').style.display = 'block';
            loadRagIndex();
        });
    }

    // Tabs logic override for RAG Index
    document.querySelectorAll('.file-tabs .tab[data-file-filter]').forEach(tab => {
        tab.addEventListener('click', (e) => {
            if (e.target.dataset.fileFilter !== 'rag_index') {
                document.getElementById('rag-doc-management').style.display = 'none';
                document.getElementById('file-grid').style.display = 'grid';
            }
        });
    });

    // Re-index All
    document.getElementById('rag-reindex-all-btn')?.addEventListener('click', async () => {
        showToast('Re-indexing all files...', 'info');
        try {
            const res = await fetch(`/api/workspace/${state.workspaceId}/rag/re-embed-all`, { method: 'POST' });
            if (res.ok) {
                showToast('Re-indexing triggered successfully', 'success');
                setTimeout(loadRagIndex, 1000);
            } else {
                showToast('Failed to trigger re-indexing', 'error');
            }
        } catch (e) {
            showToast('Error triggering re-indexing', 'error');
        }
    });
});

async function handleFolderUpload(e) {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const progressContainer = document.getElementById('upload-progress-container');
    const progressBar = document.getElementById('upload-progress-bar');
    const progressText = document.getElementById('upload-progress-text');
    
    progressContainer.style.display = 'block';
    progressBar.style.width = '0%';
    progressText.textContent = `Uploading folder... (0/${files.length})`;

    const formData = new FormData();
    formData.append("workspace_id", state.workspaceId);
    for (let i = 0; i < files.length; i++) {
        formData.append("files", files[i]);
    }

    try {
        // Mock progress for UI
        let progress = 0;
        const interval = setInterval(() => {
            progress += 5;
            if (progress > 90) clearInterval(interval);
            progressBar.style.width = `${progress}%`;
        }, 500);

        const res = await fetch('/api/upload-folder', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        
        clearInterval(interval);
        progressBar.style.width = '100%';
        progressText.textContent = `Upload complete. Processing files...`;
        
        if (res.ok) {
            showToast(`Uploaded ${files.length} files from folder`, 'success');
            appendSystemMessage(`Folder uploaded with ${files.length} files. Indexing in background...`);
        } else {
            showToast(`Failed to upload folder: ${data.detail}`, 'error');
        }
        
        setTimeout(() => {
            progressContainer.style.display = 'none';
        }, 3000);
        
    } catch (err) {
        console.error(err);
        showToast(`Upload folder error`, 'error');
        progressContainer.style.display = 'none';
    }
    
    e.target.value = ''; // Reset input
    fetchAllData(); // Refresh UI
}

// ── Citation PDF Viewer ───────────────────────────────────────────────
let currentPdfZoom = 1;
window.openPdfCitation = function(filename, pageNumber) {
    const modal = document.getElementById('pdf-page-modal');
    const img = document.getElementById('pdf-page-img');
    const loading = document.getElementById('pdf-page-loading');
    const title = document.getElementById('pdf-page-modal-title');
    
    title.textContent = `${filename} - Page ${pageNumber}`;
    img.style.display = 'none';
    loading.style.display = 'block';
    modal.classList.add('show');
    
    // Reset zoom
    currentPdfZoom = 1;
    img.style.transform = `scale(${currentPdfZoom})`;
    
    const imgUrl = `/api/workspace/${state.workspaceId}/rag/pdf-page/${encodeURIComponent(filename)}/${pageNumber}`;
    
    // Preload image
    const tempImg = new Image();
    tempImg.onload = () => {
        img.src = imgUrl;
        loading.style.display = 'none';
        img.style.display = 'block';
    };
    tempImg.onerror = () => {
        loading.style.display = 'none';
        showToast('Could not load PDF page image.', 'error');
        modal.classList.remove('show');
    };
    tempImg.src = imgUrl;
};

function zoomPdf(factor) {
    currentPdfZoom *= factor;
    currentPdfZoom = Math.max(0.5, Math.min(currentPdfZoom, 3));
    document.getElementById('pdf-page-img').style.transform = `scale(${currentPdfZoom})`;
}

// ── RAG Document Index Management ──────────────────────────────────────
async function loadRagIndex() {
    const tbody = document.getElementById('rag-doc-list');
    tbody.innerHTML = '<tr><td colspan="3" style="padding:10px; text-align:center;">Loading index...</td></tr>';
    
    try {
        const res = await fetch(`/api/workspace/${state.workspaceId}/rag/chunks?per_page=1000`);
        if (!res.ok) throw new Error('Failed to fetch chunks');
        const data = await res.json();
        const chunks = data.chunks || [];
        
        // Group by filename
        const docs = {};
        chunks.forEach(c => {
            const fname = c.metadata.filename || 'Unknown';
            if (!docs[fname]) docs[fname] = [];
            docs[fname].push(c);
        });
        
        tbody.innerHTML = '';
        if (Object.keys(docs).length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" style="padding:10px; text-align:center; opacity:0.5;">No indexed documents found.</td></tr>';
            return;
        }
        
        for (const [filename, docChunks] of Object.entries(docs)) {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid rgba(0,0,0,0.05)';
            tr.innerHTML = `
                <td style="padding:10px; font-weight:500;">
                    <i class="ph ph-file-text" style="color:var(--accent); margin-right:5px;"></i>
                    ${escapeHtml(filename)}
                </td>
                <td style="padding:10px;">${docChunks.length} chunks</td>
                <td style="padding:10px;">
                    <button class="btn-accent btn-sm" onclick="viewDocumentChunks('${escapeHtml(filename)}')">View/Edit Chunks</button>
                </td>
            `;
            tbody.appendChild(tr);
        }
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="3" style="padding:10px; color:var(--danger);">Error loading index data.</td></tr>';
    }
}

window.viewDocumentChunks = async function(filename) {
    const modal = document.getElementById('chunk-modal');
    const container = document.getElementById('chunk-list-container');
    const title = document.getElementById('chunk-modal-title');
    const countDisplay = document.getElementById('chunk-count-display');
    
    title.textContent = `Chunks for: ${filename}`;
    container.innerHTML = '<div style="text-align:center; padding: 2rem;"><i class="ph ph-spinner ph-spin" style="font-size: 2rem; color:var(--accent);"></i></div>';
    countDisplay.textContent = 'Loading...';
    modal.classList.add('show');
    
    try {
        const res = await fetch(`/api/workspace/${state.workspaceId}/rag/chunks?document=${encodeURIComponent(filename)}&per_page=100`);
        if (!res.ok) throw new Error('Failed to fetch chunks');
        const data = await res.json();
        const chunks = data.chunks || [];
        
        countDisplay.textContent = `${chunks.length} chunks`;
        container.innerHTML = '';
        
        if (chunks.length === 0) {
            container.innerHTML = '<div style="text-align:center; opacity:0.5; padding: 2rem;">No chunks found.</div>';
            return;
        }
        
        chunks.forEach((chunk, idx) => {
            const card = document.createElement('div');
            card.className = 'chunk-card';
            
            let badgeHtml = '';
            if (chunk.metadata.is_table === 'true' || chunk.metadata.is_table === true) {
                badgeHtml += `<span class="badge badge-table"><i class="ph ph-table"></i> Table</span>`;
            }
            if (chunk.metadata.page) {
                badgeHtml += `<span class="badge"><i class="ph ph-file-dashed"></i> Page ${chunk.metadata.page}</span>`;
            }
            
            card.innerHTML = `
                <div class="chunk-header">
                    <div><strong>#${idx + 1}</strong> | ID: <span style="font-family:monospace; opacity:0.7;">${chunk.id.substring(0,8)}...</span></div>
                    <div class="chunk-badges">${badgeHtml}</div>
                </div>
                <div class="chunk-text" id="chunk-text-${chunk.id}">${escapeHtml(chunk.text)}</div>
                <div class="chunk-actions">
                    <button class="btn-accent btn-sm outline" onclick="document.getElementById('chunk-text-${chunk.id}').classList.toggle('expanded')">Expand/Collapse</button>
                    <button class="btn-accent btn-sm" onclick="editChunk('${chunk.id}', this)">Edit Text</button>
                </div>
            `;
            container.appendChild(card);
        });
        
    } catch (e) {
        container.innerHTML = '<div style="text-align:center; color:var(--danger); padding: 2rem;">Error loading chunks.</div>';
    }
};

window.editChunk = function(chunkId, btnEl) {
    const textEl = document.getElementById(`chunk-text-${chunkId}`);
    const currentText = textEl.innerText;
    
    // Replace text div with textarea
    const textarea = document.createElement('textarea');
    textarea.className = 'chunk-editor-textarea';
    textarea.value = currentText;
    textarea.id = `chunk-edit-input-${chunkId}`;
    
    textEl.replaceWith(textarea);
    
    // Change button to Save
    btnEl.textContent = 'Save Changes';
    btnEl.onclick = async () => {
        btnEl.textContent = 'Saving...';
        btnEl.disabled = true;
        
        const newText = textarea.value;
        try {
            const res = await fetch(`/api/workspace/${state.workspaceId}/rag/chunks/${chunkId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: newText })
            });
            
            if (res.ok) {
                showToast('Chunk updated successfully', 'success');
                // Replace textarea with text div
                const newTextEl = document.createElement('div');
                newTextEl.className = 'chunk-text';
                newTextEl.id = `chunk-text-${chunkId}`;
                newTextEl.textContent = newText;
                textarea.replaceWith(newTextEl);
                
                btnEl.textContent = 'Edit Text';
                btnEl.disabled = false;
                btnEl.onclick = () => editChunk(chunkId, btnEl);
            } else {
                throw new Error('Failed to update');
            }
        } catch (e) {
            showToast('Error updating chunk', 'error');
            btnEl.textContent = 'Save Changes';
            btnEl.disabled = false;
        }
    };
};
