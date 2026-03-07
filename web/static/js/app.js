/**
 * Agent Factory Web Interface — Main Application
 * SPA routing, dashboard, chat UI, settings, fallback modal
 */
document.addEventListener('DOMContentLoaded', async () => {
    // ─── Init i18n ─────────────────────────────────────────────────────
    await I18n.load(I18n.getLang());

    // ─── State ─────────────────────────────────────────────────────────
    let agents = [];
    let agentCatalog = [];
    let currentEventSource = null;
    let pendingFallback = null;
    const currentProjectId = (() => {
        const params = new URLSearchParams(window.location.search);
        return (params.get('project_id') || params.get('project') || '').trim();
    })();

    function withProject(path) {
        if (!currentProjectId) return path;
        const url = new URL(path, window.location.origin);
        url.searchParams.set('project_id', currentProjectId);
        return `${url.pathname}${url.search}`;
    }

    // ─── DOM refs ──────────────────────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    // ─── Navigation ────────────────────────────────────────────────────
    $$('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            $$('.nav-item').forEach(n => n.classList.remove('active'));
            item.classList.add('active');
            $$('.page').forEach(p => p.classList.remove('active'));
            $(`#page-${item.dataset.page}`).classList.add('active');
        });
    });

    // ─── Language Switch ───────────────────────────────────────────────
    $$('.lang-btn').forEach(btn => {
        btn.addEventListener('click', () => I18n.load(btn.dataset.lang));
    });
    $$('input[name="lang"]').forEach(radio => {
        radio.addEventListener('change', () => I18n.load(radio.value));
    });

    // ─── Toast ─────────────────────────────────────────────────────────
    function showToast(msg, duration = 3000) {
        const toast = $('#toast');
        toast.textContent = msg;
        toast.classList.add('visible');
        setTimeout(() => toast.classList.remove('visible'), duration);
    }

    // ─── Load Engine Status ────────────────────────────────────────────
    async function loadEngines() {
        try {
            const res = await fetch('/api/settings/engines');
            const data = await res.json();
            const container = $('#engineCards');
            container.innerHTML = data.engines.map(e => `
                <div class="engine-card ${e.registered ? 'active' : 'inactive'}">
                    <div class="engine-name">${e.name}</div>
                    <div class="engine-status ${e.registered ? 'active' : 'inactive'}">
                        <span class="status-dot"></span>
                        ${e.registered ? I18n.t('engine.active') : I18n.t('engine.no_key')}
                    </div>
                    ${e.masked_key ? `<div style="margin-top:8px;font-size:0.8rem;color:var(--text-muted);font-family:var(--font-mono)">${e.masked_key}</div>` : ''}
                </div>
            `).join('');
        } catch (e) {
            console.error('Failed to load engines:', e);
        }
    }

    // ─── Load Agents ───────────────────────────────────────────────────
    async function loadAgents() {
        try {
            const res = await fetch(withProject('/api/agents'));
            const data = await res.json();
            agents = data.agents || [];

            // Dashboard grid
            const grid = $('#agentsGrid');
            grid.innerHTML = agents.map(a => `
                <div class="agent-card" data-id="${a.id}">
                    <div class="agent-name">${a.name}</div>
                    <div class="agent-role">${a.role || a.tagline || a.type}</div>
                    <span class="agent-badge">Lv.${a.level}</span>
                </div>
            `).join('');

            // Run page select
            const select = $('#agentSelect');
            select.innerHTML = agents.map(a =>
                `<option value="${a.id}">${a.name} (${a.type})</option>`
            ).join('');
            updateAgentInfo();

            // Card click → go to run page
            $$('.agent-card').forEach(card => {
                card.addEventListener('click', () => {
                    select.value = card.dataset.id;
                    updateAgentInfo();
                    $$('.nav-item').forEach(n => n.classList.remove('active'));
                    $$('.nav-item')[1].classList.add('active');
                    $$('.page').forEach(p => p.classList.remove('active'));
                    $('#page-run').classList.add('active');
                });
            });
        } catch (e) {
            console.error('Failed to load agents:', e);
        }
    }

    function renderEditableCategories(categories) {
        const box = $('#editableCategories');
        if (!box) return;
        const labels = (categories || []).map(c => `\`${c.key}\``).join(', ');
        box.textContent = `${I18n.t('settings.editable_categories')}: ${labels}`;
    }

    function bindEditor(agent) {
        if (!agent) return;
        $('#editAgentName').value = agent.name || '';
        $('#editAgentRole').value = agent.role || '';
        $('#editAgentTone').value = agent.tone || '';
        $('#editAgentTraits').value = (agent.traits || []).join('\n');
        $('#editAgentSignatures').value = (agent.signature_lines || []).join('\n');
        $('#editAgentSystem').value = agent.system_ko || '';
        $('#editPreferredModel').value = agent.runtime_rules?.preferred_model || '';
        $('#editCodexDirective').value = agent.runtime_rules?.codex_directive || '';
    }

    async function loadAgentCatalog() {
        try {
            const res = await fetch(withProject('/api/agents/catalog'));
            const data = await res.json();
            agentCatalog = data.agents || [];
            renderEditableCategories(data.editable_categories || []);

            const grid = $('#agentCatalog');
            if (grid) {
                grid.innerHTML = agentCatalog.map(a => `
                    <div class="agent-card">
                        <div class="agent-name">${a.name}</div>
                        <div class="agent-role">${a.role || ''}</div>
                        <span class="agent-badge">skills: ${a.summary?.skills_count ?? 0}</span>
                    </div>
                `).join('');
            }

            const editSelect = $('#editAgentSelect');
            if (editSelect) {
                editSelect.innerHTML = agentCatalog.map(a => `<option value="${a.id}">${a.name} (${a.id})</option>`).join('');
                const first = agentCatalog[0];
                if (first) bindEditor(first);
                editSelect.onchange = () => bindEditor(agentCatalog.find(a => a.id === editSelect.value));
            }
        } catch (e) {
            console.error('Failed to load agent catalog:', e);
        }
    }

    function updateAgentInfo() {
        const select = $('#agentSelect');
        const agent = agents.find(a => a.id === select.value);
        if (agent) {
            $('#agentInfo').innerHTML = `
                <strong>${agent.name}</strong><br>
                ${agent.role || ''}<br>
                <span style="color:var(--text-muted)">${agent.tagline || ''}</span>
                ${agent.preferred_model ? `<br><br><code style="color:var(--accent-blue);font-size:0.8rem">${agent.preferred_model}</code>` : ''}
            `;
        }
    }
    $('#agentSelect')?.addEventListener('change', updateAgentInfo);

    // ─── Load Settings (Key Cards) ─────────────────────────────────────
    async function loadSettings() {
        try {
            const res = await fetch('/api/settings/engines');
            const data = await res.json();
            const container = $('#keyCards');
            container.innerHTML = data.engines.map(e => `
                <div class="key-card">
                    <div class="key-card-name">${e.name}</div>
                    <input class="key-card-input" type="password"
                        data-key="${e.env_key}" value="${e.registered ? '••••••••' : ''}"
                        placeholder="${I18n.t('settings.key_placeholder')}">
                    <span class="key-card-status ${e.registered ? 'registered' : 'not-registered'}">
                        ${e.registered ? I18n.t('settings.key_registered') : I18n.t('settings.key_not_registered')}
                    </span>
                    <button class="key-save-btn" data-key="${e.env_key}">${I18n.t('settings.save')}</button>
                </div>
            `).join('');

            // Save button handlers
            $$('.key-save-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const envKey = btn.dataset.key;
                    const input = $(`input[data-key="${envKey}"]`);
                    const value = input.value.trim();
                    if (!value || value === '••••••••') return;

                    const res = await fetch('/api/settings/keys', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ env_key: envKey, value }),
                    });
                    if (res.ok) {
                        showToast(I18n.t('settings.saved'));
                        loadEngines();
                        loadSettings();
                    }
                });
            });
        } catch (e) {
            console.error('Failed to load settings:', e);
        }
    }

    function splitLines(value) {
        return String(value || '')
            .split(/\r?\n/)
            .map(s => s.trim())
            .filter(Boolean);
    }

    // ─── Chat / Run Agent ──────────────────────────────────────────────
    function addMessage(type, content) {
        const chatMessages = $('#chatMessages');
        // Remove welcome message
        const welcome = chatMessages.querySelector('.welcome-msg');
        if (welcome) welcome.remove();

        const div = document.createElement('div');
        div.className = `msg msg-${type}`;
        if (type === 'status') {
            div.innerHTML = `<span class="spinner"></span> ${content}`;
        } else if (type === 'error') {
            div.innerHTML = `<pre>${content}</pre>`;
        } else {
            div.innerHTML = content;
        }
        chatMessages.appendChild(div);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return div;
    }

    async function executeAgent() {
        const agentId = $('#agentSelect').value;
        const task = $('#chatInput').value.trim();
        if (!task) return;

        const autoApprove = $('#autoApprove')?.checked || false;
        const fsaMode = $('#fsaMode')?.checked || false;

        // Add user message
        addMessage('user', task);
        $('#chatInput').value = '';
        $('#btnExecute').disabled = true;

        const statusMsg = addMessage('status', I18n.t('run.streaming'));

        try {
            const res = await fetch('/api/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    agent_id: agentId,
                    task,
                    project_id: currentProjectId || undefined,
                    auto_approve: autoApprove,
                    execution_mode: fsaMode ? 'fsa' : 'approval',
                    fsa: fsaMode,
                }),
            });

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (line.startsWith('data:')) {
                        try {
                            const data = JSON.parse(line.slice(5).trim());
                            handleSSEEvent(data, statusMsg);
                        } catch (e) { /* skip non-json lines */ }
                    }
                }
            }
        } catch (e) {
            statusMsg.remove();
            addMessage('error', `${I18n.t('common.error')}\n${e.message}`);
        }

        $('#btnExecute').disabled = false;
    }

    function handleSSEEvent(data, statusMsg) {
        switch (data.type) {
            case 'start':
                statusMsg.innerHTML = `<span class="spinner"></span> ${data.message}`;
                break;
            case 'engine_selected':
                statusMsg.innerHTML = `<span class="spinner"></span> Model: <code>${data.model}</code> (${data.tier})`;
                break;
            case 'running':
                statusMsg.innerHTML = `<span class="spinner"></span> ${data.message}`;
                break;
            case 'complete':
                statusMsg.remove();
                addMessage('agent', `<pre>${data.output}</pre>`);
                break;
            case 'error':
                statusMsg.remove();
                addMessage('error', data.message);
                break;
            case 'uncallable':
                statusMsg.remove();
                showFallbackModal(data, true);
                break;
            case 'cross_fallback':
            case 'free_fallback':
                if (data.needs_approval) {
                    statusMsg.remove();
                    showFallbackModal(data, false);
                }
                break;
        }
    }

    // ─── Fallback Modal ────────────────────────────────────────────────
    function showFallbackModal(data, isUncallable) {
        pendingFallback = data;
        $('#fallbackReason').textContent = data.reason;
        $('#fallbackModel').textContent = data.model;

        if (isUncallable) {
            $('#fallbackModal .modal-header h3').textContent = I18n.t('fallback.uncallable_title');
            $('#btnApprove').style.display = 'none';
        } else {
            $('#fallbackModal .modal-header h3').textContent = I18n.t('fallback.title');
            $('#btnApprove').style.display = '';
        }

        $('#fallbackModal').classList.add('visible');
    }

    $('#btnApprove')?.addEventListener('click', () => {
        $('#fallbackModal').classList.remove('visible');
        if (pendingFallback) {
            addMessage('status', `${I18n.t('fallback.approve')}: ${pendingFallback.model}`);
            // Re-run with auto_approve
            const task = $('#chatInput').value || '(이전 작업 재실행)';
            fetch('/api/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    agent_id: $('#agentSelect').value,
                    task,
                    project_id: currentProjectId || undefined,
                    auto_approve: true,
                    execution_mode: ($('#fsaMode')?.checked || false) ? 'fsa' : 'approval',
                    fsa: $('#fsaMode')?.checked || false,
                }),
            });
        }
    });

    $('#btnReject')?.addEventListener('click', () => {
        $('#fallbackModal').classList.remove('visible');
        addMessage('error', I18n.t('fallback.rejected_msg'));
    });

    // ─── 에이전트 모델 요약 카드 (생성 직후 + 전체 목록) ─────────────────────

    async function fetchModelInfo(agentId) {
        try {
            const res = await fetch(withProject(`/api/agents/${agentId}/model-info`));
            return res.ok ? await res.json() : null;
        } catch { return null; }
    }

    function renderModelCard(info, container) {
        if (!info) return;
        const autoTag = info.auto_assigned
            ? `<span style="font-size:0.75rem;color:var(--accent-amber);margin-left:8px">★ 자동 배정</span>`
            : '';
        container.innerHTML = `
            <div class="engine-card" style="margin-top:12px">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
                    <div class="engine-name">${info.name}</div>
                    ${autoTag}
                </div>
                <div style="font-size:0.85rem;color:var(--text-secondary);margin-bottom:4px">역할: ${info.role || '(없음)'}</div>
                <div style="font-size:0.85rem;color:var(--text-secondary);margin-bottom:12px">
                    선호 엔진: <span style="color:var(--accent-purple)">${info.engine_label}</span>
                </div>
                <div style="display:flex;gap:8px;align-items:center">
                    <input class="key-card-input" id="modelInput_${info.agent_id}"
                        value="${info.preferred_model}" placeholder="모델 ID"
                        style="flex:1;font-family:var(--font-mono);font-size:0.82rem">
                    <button class="key-save-btn" onclick="saveModelInline('${info.agent_id}')">저장</button>
                </div>
            </div>`;
    }

    // 전역 핸들러 (onclick에서 호출)
    window.saveModelInline = async (agentId) => {
        const input = document.getElementById(`modelInput_${agentId}`);
        if (!input) return;
        const newModel = input.value.trim();
        if (!newModel) return;
        const res = await fetch(withProject(`/api/agents/${agentId}/model`), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preferred_model: newModel }),
        });
        if (res.ok) {
            showToast(`✅ ${agentId} → ${newModel} 저장됨`);
            await loadAgentModelList();
        } else {
            showToast(`${I18n.t('common.error')}: 저장 실패`);
        }
    };

    async function loadAgentModelList() {
        const container = $('#agentModelList');
        if (!container) return;
        try {
            const res = await fetch(withProject('/api/agents'));
            if (!res.ok) return;
            const data = await res.json();
            const agentList = data.agents || [];
            if (!agentList.length) {
                container.innerHTML = '<p style="color:var(--text-muted);font-size:0.9rem">등록된 에이전트가 없습니다.</p>';
                return;
            }
            // 각 에이전트의 model-info를 병렬로 가져옴
            const infoList = await Promise.all(agentList.map(a => fetchModelInfo(a.id)));
            container.innerHTML = infoList.filter(Boolean).map(info => `
                <div class="engine-card" style="margin-bottom:12px">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
                        <div class="engine-name">${info.name}</div>
                        <span style="font-size:0.8rem;color:var(--accent-purple);padding:2px 8px;border:1px solid var(--border-focus);border-radius:var(--radius-full)">${info.engine_label}</span>
                    </div>
                    <div style="font-size:0.82rem;color:var(--text-secondary);margin-bottom:10px">
                        역할: ${info.role || '(없음)'}
                    </div>
                    <div style="display:flex;gap:8px;align-items:center">
                        <input class="key-card-input" id="modelInput_${info.agent_id}"
                            value="${info.preferred_model}" placeholder="모델 ID"
                            style="flex:1;font-family:var(--font-mono);font-size:0.82rem">
                        <button class="key-save-btn" onclick="saveModelInline('${info.agent_id}')">저장</button>
                    </div>
                </div>`).join('');
        } catch (e) {
            console.error('loadAgentModelList error:', e);
        }
    }

    $('#btnCreateAgent')?.addEventListener('click', async () => {
        const payload = {
            agent_id: ($('#createAgentId')?.value || '').trim(),
            name: ($('#createAgentName')?.value || '').trim(),
            role: ($('#createAgentRole')?.value || '').trim(),
        };
        if (!payload.agent_id || !payload.name || !payload.role) return;
        const res = await fetch(withProject('/api/agents'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (res.ok) {
            const data = await res.json();
            showToast(I18n.t('settings.created'));
            await loadAgents();
            await loadAgentCatalog();
            await loadAgentModelList();
            // 생성된 에이전트의 모델 요약 카드를 결과 패널에 표시
            const resultBox = $('#createAgentResult');
            if (resultBox) {
                const info = await fetchModelInfo(data.agent_id);
                renderModelCard(info, resultBox);
                resultBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        } else {
            const err = await res.text();
            showToast(`${I18n.t('common.error')}: ${err}`);
        }
    });

    $('#btnSaveAgent')?.addEventListener('click', async () => {
        const agentId = $('#editAgentSelect')?.value;
        if (!agentId) return;
        const updates = {
            name: ($('#editAgentName')?.value || '').trim(),
            role: ($('#editAgentRole')?.value || '').trim(),
            tone: ($('#editAgentTone')?.value || '').trim(),
            traits: splitLines($('#editAgentTraits')?.value),
            signature_lines: splitLines($('#editAgentSignatures')?.value),
            system_ko: ($('#editAgentSystem')?.value || '').trim(),
            preferred_model: ($('#editPreferredModel')?.value || '').trim(),
            codex_directive: ($('#editCodexDirective')?.value || '').trim(),
        };
        const res = await fetch(withProject(`/api/agents/${agentId}`), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ updates }),
        });
        if (res.ok) {
            showToast(I18n.t('settings.updated'));
            await loadAgents();
            await loadAgentCatalog();
            await loadAgentModelList();
        } else {
            const err = await res.text();
            showToast(`${I18n.t('common.error')}: ${err}`);
        }
    });


    // Execute button & Enter key
    $('#btnExecute')?.addEventListener('click', executeAgent);
    $('#chatInput')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            executeAgent();
        }
    });

    // ─── Initial Load ──────────────────────────────────────────────────
    await Promise.all([loadEngines(), loadAgents(), loadSettings(), loadAgentCatalog()]);

    // ─── 언어 변경 시 동적 컴포넌트 재렌더링 ───────────────────────────────
    document.addEventListener('langchange', async () => {
        // 엔진 상태 카드: 활성/비활성 텍스트 재적용
        await loadEngines();
        // 설정 카드: 저장 버튼, 등록여부 라벨 재적용
        await loadSettings();
        await loadAgentCatalog();
        // Run 페이지 버튼 텍스트 재적용
        const btnExec = $('#btnExecute');
        if (btnExec && !btnExec.disabled) btnExec.textContent = I18n.t('run.execute');
        // chat placeholder 재적용
        const chatInput = $('#chatInput');
        if (chatInput) chatInput.placeholder = I18n.t('run.input_placeholder');
    });

    // ─── Tic-Tac-Toe ───────────────────────────────────────────────────────
    function initTicTacToe() {
        const WINNING_LINES = [
            [0, 1, 2], [3, 4, 5], [6, 7, 8], // 가로
            [0, 3, 6], [1, 4, 7], [2, 5, 8], // 세로
            [0, 4, 8], [2, 4, 6],         // 대각선
        ];

        let board = Array(9).fill(null); // null | 'X' | 'O'
        let currentPlayer = 'X';
        let gameOver = false;

        const statusEl = $('#tttStatus');
        const cells = $$('.ttt-cell');
        const resetBtn = $('#tttReset');

        function setStatus(text, cls) {
            statusEl.textContent = text;
            statusEl.className = 'ttt-status' + (cls ? ' ' + cls : '');
        }

        function checkWinner() {
            for (const [a, b, c] of WINNING_LINES) {
                if (board[a] && board[a] === board[b] && board[a] === board[c]) {
                    return { winner: board[a], line: [a, b, c] };
                }
            }
            if (board.every(v => v !== null)) return { winner: null, draw: true };
            return null;
        }

        function render() {
            cells.forEach((cell, i) => {
                cell.textContent = board[i] || '';
                cell.className = 'ttt-cell' + (board[i] ? ' ' + board[i].toLowerCase() + ' taken' : '');
            });
        }

        function handleClick(e) {
            const cell = e.currentTarget;
            const idx = parseInt(cell.dataset.index, 10);
            if (gameOver || board[idx]) return;

            board[idx] = currentPlayer;
            render();

            const result = checkWinner();
            if (result) {
                gameOver = true;
                if (result.draw) {
                    setStatus("🤝 It's a Draw!", 'draw');
                } else {
                    setStatus(`🎉 Player ${result.winner} Wins!`, result.winner === 'X' ? 'x-win' : 'o-win');
                    result.line.forEach(i => cells[i].classList.add('winning'));
                }
                return;
            }
            currentPlayer = currentPlayer === 'X' ? 'O' : 'X';
            setStatus(`Player ${currentPlayer}'s turn`, currentPlayer === 'X' ? 'x-turn' : 'o-turn');
        }

        function resetGame() {
            board = Array(9).fill(null);
            currentPlayer = 'X';
            gameOver = false;
            setStatus("Player X's turn", 'x-turn');
            render();
        }

        cells.forEach(cell => cell.addEventListener('click', handleClick));
        resetBtn.addEventListener('click', resetGame);

        // 초기 상태
        setStatus("Player X's turn", 'x-turn');
    }

    // 내비게이션으로 Tic-Tac-Toe 페이지 진입 시 최초 1회 초기화
    let tttInitialized = false;
    $$('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            if (item.dataset.page === 'tictactoe' && !tttInitialized) {
                initTicTacToe();
                tttInitialized = true;
            }
        });
    });
});
