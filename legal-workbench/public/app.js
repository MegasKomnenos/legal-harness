(function () {
  'use strict';

  // ── State ──────────────────────────────
  let ws = null;
  let sessionRunning = false;
  let waitingForInput = false;
  let elapsedTimer = null;
  let startTime = null;
  let mermaidCounter = 0;
  let streamingTextEl = null;
  let streamingTextBuffer = '';
  let renderPending = false;

  // Artifact dock state
  let dockOpen = false;
  let dockHeight = 260;
  let activeArtifactTab = null;
  const artifactPaths = {};
  const pendingArtifacts = {};

  const CASE_INFO = {
    '01_공사소음': { label: '공사소음', desc: '건설 공사 소음 관련 정보공개·행정심판' },
    '02_대동제': { label: '대동제', desc: '행사 관련 정보공개·행정심판' },
    '03_성희롱': { label: '성희롱', desc: '성희롱 사건 정보공개·행정심판·민원' },
  };

  const DOC_TYPES = ['보충서면', '청구이유서', '별지', '법리보충', '요약본', '국민신문고'];

  let selectedCase = null;

  const ARTIFACT_PATTERNS = [
    { type: 'log', label: '진행로그', pattern: /진행로그_/ },
    { type: 'graph', label: '법리 그래프', pattern: /법리그래프_/ },
    { type: 'doc', label: '문서', pattern: /작성_(최종|초안)\/.+\.txt$/ },
    { type: 'validation', label: '검증', pattern: /검증이력_/ },
    { type: 'pdf', label: 'PDF', pattern: /\.pdf$/ },
  ];

  const STEPS = [
    { step: 1, label: '참조 파일 읽기', hint: '법리DB, 판례 사전, 구조 템플릿 등 8종 참조 파일 로드' },
    { step: 2, label: '사건 문서 통독', hint: '청구서, 결정통지서, 답변서 등 사건 문서 전문 읽기' },
    { step: 3, label: '판례 전문 통독', hint: '인용할 판례 PDF 추출 및 전문 통독' },
    { step: 4, label: '사안 파악', hint: '문서 유형, 청구 대상, 예상 비공개 사유 정리' },
    { step: 5, label: '쟁점 정리', hint: '핵심 쟁점, 접근 방향, 문서 구성안 확인' },
    { step: 6, label: '법리 그래프 설계', hint: '쟁점-법리-판례-포섭 연결 그래프 (JSON + Mermaid)' },
    { step: 7, label: '그래프 검증 루프', hint: '스크립트 + 독립 평가로 그래프 타당성 반복 검증' },
    { step: 8, label: '문서 생성', hint: '확정된 그래프를 기반으로 법률 문서 작성' },
    { step: 9, label: '문서 검증 루프', hint: '스크립트 + 독립 평가 2-Phase 반복 검증' },
    { step: 10, label: 'PDF 출력', hint: 'A4 서식에 맞춰 PDF 생성' },
    { step: 11, label: '정리 및 보관', hint: '산출물 배치 확인, 로그 파일 archive 이동' },
  ];

  // ── DOM refs ───────────────────────────
  const $ = (id) => document.getElementById(id);
  const connDot = $('connDot');
  const sessionInfo = $('sessionInfo');
  const elapsedEl = $('elapsed');
  const stepTimeline = $('stepTimeline');
  const fileTree = $('fileTree');
  const outputStream = $('outputStream');
  const welcome = $('welcome');
  const caseCards = $('caseCards');
  const promptInput = $('promptInput');
  const promptHint = $('promptHint');
  const btnSend = $('btnSend');
  const btnAbort = $('btnAbort');
  const filePanel = $('filePanel');
  const filePanelPath = $('filePanelPath');
  const filePanelBody = $('filePanelBody');
  const btnSave = $('btnSave');
  const btnDownload = $('btnDownload');
  const btnClosePanel = $('btnClosePanel');
  const overlay = $('overlay');
  const docTypePicker = $('docTypePicker');
  const pickerLabel = $('pickerLabel');
  const pickerTypes = $('pickerTypes');
  const pickerBack = $('pickerBack');
  const promptArea = $('promptArea');
  const artifactDock = $('artifactDock');
  const dockTabs = $('dockTabs');
  const dockPanel = $('dockPanel');
  const dockResize = $('dockResize');

  // ── Init ───────────────────────────────
  marked.setOptions({
    highlight: function (code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return code;
    },
    breaks: true,
  });

  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose',
  });

  buildStepTimeline();
  loadFileTree('');
  loadCaseCards();
  connectWS();

  promptInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  promptInput.addEventListener('input', autoResize);
  btnSend.addEventListener('click', handleSend);
  btnAbort.addEventListener('click', handleAbort);
  btnClosePanel.addEventListener('click', closeFilePanel);
  overlay.addEventListener('click', closeFilePanel);
  btnSave.addEventListener('click', handleFileSave);
  btnDownload.addEventListener('click', handleFileDownload);

  initDockResize();
  pickerBack.addEventListener('click', showCaseCards);

  // 파일 섹션은 초기에 접힘
  const filesToggle = document.querySelector('[data-target="filesBody"]');
  if (filesToggle) {
    filesToggle.classList.add('collapsed');
    $('filesBody').classList.add('hidden');
  }

  document.querySelectorAll('.sidebar-toggle').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = $(btn.dataset.target);
      btn.classList.toggle('collapsed');
      target.classList.toggle('hidden');
    });
  });

  // ── WebSocket ──────────────────────────

  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = () => {
      connDot.className = 'connection-dot connected';
    };

    ws.onclose = () => {
      connDot.className = 'connection-dot error';
      if (sessionRunning) {
        appendStatus('연결이 끊어졌습니다 (다시 시도하려면 프롬프트를 전송하세요)', 'error');
        setIdle();
      }
      setTimeout(connectWS, 3000);
    };

    ws.onerror = () => {
      connDot.className = 'connection-dot error';
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        handleEvent(data);
      } catch {}
    };
  }

  function wsSend(data) {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify(data));
    }
  }

  // ── Event Handling ─────────────────────

  function handleEvent(data) {
    switch (data.type) {
      case 'status':
        handleStatus(data);
        break;
      case 'session_info':
        sessionInfo.textContent = data.sessionId ? data.sessionId.substring(0, 8) : '';
        break;
      case 'text':
        handleText(data);
        break;
      case 'text_delta':
        handleTextDelta(data);
        break;
      case 'tool_call':
        handleToolCall(data);
        break;
      case 'tool_result':
        handleToolResult(data);
        break;
      case 'step_update':
        handleStepUpdate(data);
        break;
      case 'needs_input':
        handleNeedsInput(data);
        break;
      case 'complete':
        handleComplete(data);
        break;
      case 'error':
        appendStatus((data.message || 'Error') + ' (다시 시도하려면 프롬프트를 전송하세요)', 'error');
        setIdle();
        break;
    }
  }

  function handleStatus(data) {
    if (data.status === 'starting' || data.status === 'resuming') {
      sessionRunning = true;
      waitingForInput = false;
      btnSend.style.display = 'none';
      btnAbort.style.display = 'flex';
      promptInput.disabled = true;
      promptInput.classList.remove('waiting');
      promptHint.textContent = '';
      promptHint.className = 'prompt-hint';
      startTimer();

      const ft = document.querySelector('[data-target="filesBody"]');
      if (ft && ft.classList.contains('collapsed')) {
        ft.classList.remove('collapsed');
        $('filesBody').classList.remove('hidden');
      }
    }
    if (data.status === 'running') {
      appendStatus(data.status === 'running' ? '처리 중...' : data.status, 'info');
    }
    if (data.status === 'aborted') {
      appendStatus('중단됨', 'warning');
      setIdle();
    }
  }

  function handleTextDelta(data) {
    if (welcome.style.display !== 'none') {
      welcome.style.display = 'none';
    }
    if (!streamingTextEl) {
      streamingTextEl = document.createElement('div');
      streamingTextEl.className = 'msg-block msg-text streaming';
      outputStream.appendChild(streamingTextEl);
      streamingTextBuffer = '';
    }
    streamingTextBuffer += data.content;
    if (!renderPending) {
      renderPending = true;
      requestAnimationFrame(() => {
        renderPending = false;
        if (streamingTextEl) {
          streamingTextEl.innerHTML = marked.parse(streamingTextBuffer);
          scrollToBottom();
        }
      });
    }
  }

  function flushStreamingText() {
    if (streamingTextEl) {
      streamingTextEl.classList.remove('streaming');
      streamingTextEl = null;
      streamingTextBuffer = '';
    }
  }

  function handleText(data) {
    if (welcome.style.display !== 'none') {
      welcome.style.display = 'none';
    }
    flushStreamingText();
    appendMarkdown(data.content);
  }

  function handleToolCall(data) {
    flushStreamingText();
    const card = document.createElement('div');
    card.className = 'tool-card';
    card.id = 'tc-' + data.toolUseId;

    const arg = getToolArg(data.tool, data.input);
    card.innerHTML = `
      <div class="tool-card-header">
        <span class="tool-icon">&#9654;</span>
        <span class="tool-name">${esc(data.tool)}</span>
        <span class="tool-arg">${esc(arg)}</span>
      </div>
      <div class="tool-card-body"></div>
    `;

    card.querySelector('.tool-card-header').addEventListener('click', () => {
      card.classList.toggle('expanded');
    });

    outputStream.appendChild(card);
    scrollToBottom();
    highlightFileInTree(arg);

    const artifact = detectArtifact(data.tool, data.input);
    if (artifact) {
      pendingArtifacts[data.toolUseId] = artifact;
    }
  }

  function handleToolResult(data) {
    const card = document.getElementById('tc-' + data.toolUseId);
    if (card) {
      const body = card.querySelector('.tool-card-body');
      body.textContent = data.preview || '';
      if (data.isError) card.classList.add('error');
    }

    const artifact = pendingArtifacts[data.toolUseId];
    if (artifact && !data.isError) {
      delete pendingArtifacts[data.toolUseId];
      triggerArtifactUpdate(artifact.type, artifact.path);
    }
  }

  function detectArtifact(tool, input) {
    if (tool !== 'Write' && tool !== 'Edit') return null;
    const filePath = input.file_path || input.path || '';
    for (const ap of ARTIFACT_PATTERNS) {
      if (ap.pattern.test(filePath)) {
        return { type: ap.type, label: ap.label, path: filePath };
      }
    }
    return null;
  }

  function handleStepUpdate(data) {
    updateStepTimeline(data.step, data.completed || [], data.completedStep);
  }

  function handleNeedsInput(data) {
    waitingForInput = true;
    sessionRunning = false;
    stopTimer();
    btnAbort.style.display = 'none';
    btnSend.style.display = 'flex';
    promptInput.disabled = false;
    promptInput.classList.add('waiting');
    promptInput.placeholder = '응답을 입력하세요...';
    promptHint.textContent = '응답 대기 중: 확인 또는 수정 사항을 입력하세요';
    promptHint.className = 'prompt-hint waiting';
    promptInput.focus();

    const activeStep = document.querySelector('.step-item.active');
    if (activeStep) {
      const stepNum = parseInt(activeStep.id.replace('step-', ''), 10);
      setStepWaiting(stepNum);
    }

    if (data.costUsd !== undefined) {
      updateSessionMetrics(data);
    }
  }

  function handleComplete(data) {
    appendCompleteCard(data);
    setIdle();
  }

  function setIdle() {
    sessionRunning = false;
    waitingForInput = false;
    stopTimer();
    btnAbort.style.display = 'none';
    btnSend.style.display = 'flex';
    promptInput.disabled = false;
    promptInput.classList.remove('waiting');
    promptInput.placeholder = '사건명 문서유형 (예: 01_공사소음 보충서면)';
    promptHint.textContent = '';
    promptHint.className = 'prompt-hint';
  }

  // ── Send / Abort ───────────────────────

  function handleSend() {
    const text = promptInput.value.trim();
    if (!text) return;

    appendUserMessage(text);
    promptInput.value = '';
    autoResize();

    if (waitingForInput) {
      wsSend({ type: 'response', content: text });
    } else {
      welcome.style.display = 'none';
      resetSteps();
      resetDock();
      wsSend({ type: 'prompt', content: text });
    }
  }

  function handleAbort() {
    wsSend({ type: 'abort' });
  }

  // ── Output Rendering ──────────────────

  function appendMarkdown(text) {
    const mermaidBlocks = [];
    const processed = text.replace(/```mermaid\n([\s\S]*?)```/g, (_, code) => {
      const id = 'mermaid-' + (++mermaidCounter);
      mermaidBlocks.push({ id, code: code.trim() });
      return `<div class="mermaid-container" id="${id}"></div>`;
    });

    const div = document.createElement('div');
    div.className = 'msg-block msg-text';
    div.innerHTML = marked.parse(processed);
    outputStream.appendChild(div);

    for (const { id, code } of mermaidBlocks) {
      renderMermaid(id, code);
    }

    scrollToBottom();
  }

  async function renderMermaid(elementId, code) {
    try {
      const { svg } = await mermaid.render('mmd-' + elementId, code);
      const el = document.getElementById(elementId);
      if (el) el.innerHTML = svg;
    } catch (err) {
      const el = document.getElementById(elementId);
      if (el) {
        el.innerHTML = `<pre style="text-align:left;font-size:12px;color:var(--vermilion)">${esc(err.message)}\n\n${esc(code)}</pre>`;
      }
    }
  }

  function appendUserMessage(text) {
    const div = document.createElement('div');
    div.className = 'user-msg';
    div.innerHTML = `<div class="user-msg-label">사용자</div><div>${esc(text)}</div>`;
    outputStream.appendChild(div);
    scrollToBottom();
  }

  function appendStatus(message, level) {
    const div = document.createElement('div');
    div.className = `status-msg ${level}`;
    const icon = level === 'info' ? '<div class="status-spinner"></div>' : '';
    div.innerHTML = `${icon}<span>${esc(message)}</span>`;
    outputStream.appendChild(div);
    scrollToBottom();
  }

  function appendCompleteCard(data) {
    const div = document.createElement('div');
    div.className = 'complete-card';
    div.innerHTML = `
      <div class="metric">
        <span class="metric-value">${formatDuration(data.durationMs)}</span>
        <span class="metric-label">소요 시간</span>
      </div>
      <div class="metric">
        <span class="metric-value">$${(data.costUsd || 0).toFixed(3)}</span>
        <span class="metric-label">비용</span>
      </div>
      <div class="metric">
        <span class="metric-value">${data.numTurns || '-'}</span>
        <span class="metric-label">턴</span>
      </div>
    `;
    outputStream.appendChild(div);
    scrollToBottom();
  }

  // ── Step Timeline ──────────────────────

  function buildStepTimeline() {
    stepTimeline.innerHTML = '';
    for (const s of STEPS) {
      const item = document.createElement('div');
      item.className = 'step-item';
      item.id = 'step-' + s.step;
      item.title = s.hint || '';
      item.innerHTML = `
        <div class="step-indicator">
          <div class="step-dot">${s.step}</div>
          <div class="step-line"></div>
        </div>
        <div class="step-label">${esc(s.label)}</div>
      `;
      stepTimeline.appendChild(item);
    }
  }

  function updateStepTimeline(currentStep, completed, justCompleted) {
    for (const s of STEPS) {
      const el = document.getElementById('step-' + s.step);
      if (!el) continue;
      el.classList.remove('active', 'done', 'waiting');

      if (completed.includes(s.step)) {
        el.classList.add('done');
      } else if (s.step === currentStep) {
        el.classList.add('active');
      }
    }

    if (justCompleted) {
      const el = document.getElementById('step-' + justCompleted);
      if (el) {
        el.classList.remove('active');
        el.classList.add('done');
      }
    }
  }

  function resetSteps() {
    for (const s of STEPS) {
      const el = document.getElementById('step-' + s.step);
      if (el) el.classList.remove('active', 'done', 'waiting');
    }
  }

  function setStepWaiting(step) {
    const el = document.getElementById('step-' + step);
    if (el) {
      el.classList.remove('active');
      el.classList.add('waiting');
    }
  }

  // ── File Tree ──────────────────────────

  async function loadFileTree(dir) {
    try {
      const res = await fetch(`/api/files/list?dir=${encodeURIComponent(dir)}`);
      const data = await res.json();
      if (!res.ok) return;

      const container = dir ? document.querySelector(`[data-dir="${dir}"] .ft-children`) : fileTree;
      if (!container) return;

      container.innerHTML = '';

      for (const entry of data.entries) {
        if (entry.name === 'legal-workbench' || entry.name === 'webapp' || entry.name === '__pycache__' || entry.name === 'node_modules') continue;

        const item = document.createElement('div');

        if (entry.type === 'dir') {
          item.className = 'ft-dir';
          item.dataset.dir = entry.path;
          item.innerHTML = `
            <div class="ft-item">
              <span class="ft-icon">&#128193;</span>
              <span class="ft-name">${esc(entry.name)}</span>
              ${isProtected(entry.path) ? '<span class="ft-badge">읽기 전용</span>' : ''}
            </div>
            <div class="ft-children collapsed"></div>
          `;

          let loaded = false;
          item.querySelector('.ft-item').addEventListener('click', async () => {
            const children = item.querySelector('.ft-children');
            if (children.classList.contains('collapsed')) {
              if (!loaded) {
                await loadFileTree(entry.path);
                loaded = true;
              }
              children.classList.remove('collapsed');
              item.querySelector('.ft-icon').innerHTML = '&#128194;';
            } else {
              children.classList.add('collapsed');
              item.querySelector('.ft-icon').innerHTML = '&#128193;';
            }
          });
        } else {
          item.className = 'ft-file';
          item.dataset.path = entry.path;
          const icon = getFileIcon(entry.name);
          item.innerHTML = `
            <div class="ft-item" data-filepath="${esc(entry.path)}">
              <span class="ft-icon">${icon}</span>
              <span class="ft-name">${esc(entry.name)}</span>
            </div>
          `;

          item.querySelector('.ft-item').addEventListener('click', () => {
            openFile(entry.path, entry.name);
          });
        }

        container.appendChild(item);
      }
    } catch (err) {
      console.error('File tree error:', err);
    }
  }

  async function loadCaseCards() {
    try {
      const res = await fetch('/api/files/list?dir=cases');
      const data = await res.json();
      if (!res.ok) return;

      caseCards.innerHTML = '';
      for (const entry of data.entries) {
        if (entry.type !== 'dir') continue;
        const info = CASE_INFO[entry.name];
        const card = document.createElement('div');
        card.className = 'case-card';
        if (info) {
          card.innerHTML = `<div class="case-card-label">${esc(info.label)}</div><div class="case-card-desc">${esc(info.desc)}</div>`;
        } else {
          card.innerHTML = `<div class="case-card-label">${esc(entry.name)}</div>`;
        }
        card.addEventListener('click', () => selectCase(entry.name));
        caseCards.appendChild(card);
      }
    } catch {}
  }

  function selectCase(caseName) {
    selectedCase = caseName;
    const info = CASE_INFO[caseName];
    const label = info ? info.label : caseName;

    caseCards.style.display = 'none';
    pickerLabel.textContent = `${label}: 어떤 문서를 생성할까요?`;
    pickerTypes.innerHTML = '';

    for (const dt of DOC_TYPES) {
      const btn = document.createElement('button');
      btn.className = 'picker-type';
      btn.textContent = dt;
      btn.addEventListener('click', () => {
        promptInput.value = `${caseName} ${dt}`;
        promptInput.focus();
        autoResize();
      });
      pickerTypes.appendChild(btn);
    }

    docTypePicker.style.display = '';
  }

  function showCaseCards() {
    selectedCase = null;
    caseCards.style.display = '';
    docTypePicker.style.display = 'none';
  }

  // ── File Panel ─────────────────────────

  let currentFilePath = null;
  let currentFileReadOnly = false;

  async function openFile(filePath, fileName) {
    const ext = fileName.split('.').pop().toLowerCase();

    if (ext === 'pdf' || ext === 'hwp' || ext === 'docx') {
      currentFilePath = filePath;
      currentFileReadOnly = true;
      filePanelPath.textContent = filePath;
      filePanelBody.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-secondary)">
        <p>바이너리 파일은 직접 열 수 없습니다.</p>
        <p style="margin-top:8px;font-size:13px">다운로드 버튼을 사용하세요.</p>
      </div>`;
      btnSave.style.display = 'none';
      btnDownload.style.display = 'block';
      openFilePanel();
      return;
    }

    try {
      const res = await fetch(`/api/files/read?path=${encodeURIComponent(filePath)}`);
      const data = await res.json();
      if (!res.ok) {
        alert(data.error || 'Failed to read file');
        return;
      }

      currentFilePath = filePath;
      currentFileReadOnly = isProtected(filePath);
      filePanelPath.textContent = filePath;

      if (ext === 'md' && currentFileReadOnly) {
        filePanelBody.innerHTML = '';
        const preview = document.createElement('div');
        preview.className = 'md-preview';
        preview.innerHTML = marked.parse(data.content);
        filePanelBody.appendChild(preview);
      } else if (currentFileReadOnly) {
        filePanelBody.innerHTML = '';
        const pre = document.createElement('pre');
        pre.textContent = data.content;
        filePanelBody.appendChild(pre);
      } else {
        filePanelBody.innerHTML = '';
        const textarea = document.createElement('textarea');
        textarea.value = data.content;
        textarea.spellcheck = false;
        filePanelBody.appendChild(textarea);
      }

      btnSave.style.display = currentFileReadOnly ? 'none' : 'block';
      btnDownload.style.display = 'block';
      openFilePanel();
    } catch (err) {
      alert('Failed to open file: ' + err.message);
    }
  }

  function openFilePanel() {
    filePanel.classList.add('open');
    overlay.classList.add('visible');
  }

  function closeFilePanel() {
    filePanel.classList.remove('open');
    overlay.classList.remove('visible');
  }

  async function handleFileSave() {
    if (!currentFilePath || currentFileReadOnly) return;
    const textarea = filePanelBody.querySelector('textarea');
    if (!textarea) return;

    try {
      const res = await fetch('/api/files/write', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: currentFilePath, content: textarea.value }),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.error || 'Save failed');
        return;
      }
      btnSave.textContent = '저장됨';
      setTimeout(() => { btnSave.textContent = '저장'; }, 1500);
    } catch (err) {
      alert('Save error: ' + err.message);
    }
  }

  function handleFileDownload() {
    if (!currentFilePath) return;
    window.open(`/api/files/download?path=${encodeURIComponent(currentFilePath)}`, '_blank');
  }

  // ── File tree helpers ──────────────────

  function highlightFileInTree(pathFragment) {
    document.querySelectorAll('.ft-item.highlighted').forEach((el) => {
      el.classList.remove('highlighted');
    });

    if (!pathFragment) return;

    const items = document.querySelectorAll('.ft-item[data-filepath]');
    for (const item of items) {
      if (item.dataset.filepath && pathFragment.includes(item.dataset.filepath)) {
        item.classList.add('highlighted');
        break;
      }
    }
  }

  function getFileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    const icons = {
      md: '&#128196;',
      txt: '&#128196;',
      py: '&#128225;',
      pdf: '&#128211;',
      hwp: '&#128211;',
      docx: '&#128211;',
      json: '&#128218;',
      sh: '&#9881;',
    };
    return icons[ext] || '&#128196;';
  }

  function isProtected(filePath) {
    return filePath.startsWith('harness') || filePath.startsWith('법령_판례');
  }

  function getToolArg(tool, input) {
    if (tool === 'Read' || tool === 'Write' || tool === 'Edit') {
      return input.file_path || input.path || '';
    }
    if (tool === 'Bash') {
      return input.command || '';
    }
    if (tool === 'Agent') {
      return input.prompt ? input.prompt.substring(0, 100) : '';
    }
    return JSON.stringify(input).substring(0, 100);
  }

  // ── Timer ──────────────────────────────

  function startTimer() {
    startTime = Date.now();
    stopTimer();
    elapsedTimer = setInterval(() => {
      elapsedEl.textContent = formatDuration(Date.now() - startTime);
    }, 1000);
  }

  function stopTimer() {
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  function updateSessionMetrics(data) {
    if (data.costUsd !== undefined) {
      sessionInfo.textContent = `$${data.costUsd.toFixed(3)}`;
    }
  }

  // ── Utilities ──────────────────────────

  function esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      outputStream.scrollTop = outputStream.scrollHeight;
    });
  }

  function autoResize() {
    promptInput.style.height = 'auto';
    promptInput.style.height = Math.min(promptInput.scrollHeight, 150) + 'px';
  }

  function formatDuration(ms) {
    if (!ms || ms < 0) return '0:00';
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    const sec = s % 60;
    if (m >= 60) {
      const h = Math.floor(m / 60);
      return `${h}:${String(m % 60).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
    }
    return `${m}:${String(sec).padStart(2, '0')}`;
  }

  // ── Artifact Dock ──────────────────────

  function resetDock() {
    dockOpen = false;
    activeArtifactTab = null;
    Object.keys(artifactPaths).forEach(k => delete artifactPaths[k]);
    Object.keys(pendingArtifacts).forEach(k => delete pendingArtifacts[k]);
    dockTabs.innerHTML = '';
    dockPanel.innerHTML = '';
    artifactDock.classList.remove('open');
    dockResize.classList.remove('visible');
    document.documentElement.style.setProperty('--dock-h', '0px');
  }

  function openDock() {
    if (dockOpen) return;
    dockOpen = true;
    artifactDock.classList.add('open');
    dockResize.classList.add('visible');
    document.documentElement.style.setProperty('--dock-h', dockHeight + 'px');
  }

  function ensureTab(type) {
    if (dockTabs.querySelector(`[data-artifact="${type}"]`)) return;
    const ap = ARTIFACT_PATTERNS.find(a => a.type === type);
    if (!ap) return;

    const btn = document.createElement('button');
    btn.className = 'dock-tab';
    btn.dataset.artifact = type;
    btn.innerHTML = `${esc(ap.label)}<span class="tab-dot"></span>`;
    btn.addEventListener('click', () => switchArtifactTab(type));

    const spacer = dockTabs.querySelector('.dock-spacer');
    if (spacer) {
      dockTabs.insertBefore(btn, spacer);
    } else {
      dockTabs.appendChild(btn);
    }

    if (!dockTabs.querySelector('.dock-spacer')) {
      const sp = document.createElement('div');
      sp.className = 'dock-spacer';
      dockTabs.appendChild(sp);

      const collapse = document.createElement('button');
      collapse.className = 'dock-collapse';
      collapse.innerHTML = '&#9660;';
      collapse.title = '접기';
      collapse.addEventListener('click', toggleDock);
      dockTabs.appendChild(collapse);
    }
  }

  function toggleDock() {
    if (dockOpen) {
      dockOpen = false;
      artifactDock.classList.remove('open');
      dockResize.classList.remove('visible');
      document.documentElement.style.setProperty('--dock-h', '0px');
    } else {
      openDock();
      if (activeArtifactTab) switchArtifactTab(activeArtifactTab);
    }
  }

  function switchArtifactTab(type) {
    activeArtifactTab = type;
    dockTabs.querySelectorAll('.dock-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.artifact === type);
      if (t.dataset.artifact === type) t.classList.remove('updated');
    });
    loadArtifactContent(type);
  }

  let artifactFetchTimer = null;

  function triggerArtifactUpdate(type, path) {
    artifactPaths[type] = path;
    ensureTab(type);
    openDock();

    const tab = dockTabs.querySelector(`[data-artifact="${type}"]`);
    if (activeArtifactTab !== type && tab) {
      tab.classList.add('updated');
    }

    if (activeArtifactTab === type || !activeArtifactTab) {
      clearTimeout(artifactFetchTimer);
      artifactFetchTimer = setTimeout(() => {
        switchArtifactTab(type);
      }, 500);
    }
  }

  async function loadArtifactContent(type) {
    const path = artifactPaths[type];
    if (!path) {
      dockPanel.innerHTML = '<div style="padding:20px;color:var(--text-muted);text-align:center">아직 생성되지 않았습니다</div>';
      return;
    }

    if (type === 'pdf') {
      dockPanel.innerHTML = renderPdfCard(path);
      dockPanel.querySelector('.art-pdf-btn')?.addEventListener('click', () => {
        window.open(`/api/files/download?path=${encodeURIComponent(path)}`, '_blank');
      });
      return;
    }

    try {
      const res = await fetch(`/api/files/read?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (!res.ok) {
        dockPanel.innerHTML = `<div style="padding:20px;color:var(--vermilion)">${esc(data.error)}</div>`;
        return;
      }

      switch (type) {
        case 'log':
          dockPanel.innerHTML = renderProgressLog(data.content);
          break;
        case 'graph':
          dockPanel.innerHTML = renderGraphArtifact(data.content);
          initGraphTabs();
          break;
        case 'doc':
          dockPanel.innerHTML = renderLegalDoc(data.content);
          break;
        case 'validation':
          dockPanel.innerHTML = renderValidation(data.content);
          initValidationAccordion();
          break;
      }
    } catch (err) {
      dockPanel.innerHTML = `<div style="padding:20px;color:var(--vermilion)">로드 실패: ${esc(err.message)}</div>`;
    }
  }

  // ── Dock Resize ────────────────────────

  function initDockResize() {
    let dragging = false;
    let startY = 0;
    let startH = 0;

    dockResize.addEventListener('mousedown', (e) => {
      dragging = true;
      startY = e.clientY;
      startH = dockHeight;
      dockResize.classList.add('dragging');
      e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      const delta = startY - e.clientY;
      dockHeight = Math.max(120, Math.min(startH + delta, window.innerHeight * 0.6));
      document.documentElement.style.setProperty('--dock-h', dockHeight + 'px');
    });

    document.addEventListener('mouseup', () => {
      if (dragging) {
        dragging = false;
        dockResize.classList.remove('dragging');
      }
    });
  }

  // ── Renderers ──────────────────────────

  function renderProgressLog(content) {
    const lines = content.split('\n');
    let html = '<div class="art-log">';
    let currentStep = null;
    let files = [];

    for (const line of lines) {
      const stepMatch = line.match(/###\s*(\d+)단계\s*완료/);
      if (stepMatch) {
        if (currentStep) {
          html += renderLogStep(currentStep, files, true);
        }
        currentStep = parseInt(stepMatch[1], 10);
        files = [];
        continue;
      }
      const fileMatch = line.match(/^[-*]\s+(.+)/);
      if (fileMatch && currentStep !== null) {
        files.push(fileMatch[1].trim());
      }
    }
    if (currentStep) {
      html += renderLogStep(currentStep, files, true);
    }

    for (const s of STEPS) {
      if (!content.includes(`${s.step}단계 완료`)) {
        html += renderLogStep(s.step, [], false);
      }
    }

    html += '</div>';
    return html;
  }

  function renderLogStep(step, files, done) {
    const s = STEPS.find(st => st.step === step);
    const label = s ? s.label : `${step}단계`;
    let html = '<div class="log-step">';
    html += `<div class="log-check ${done ? 'done' : 'pending'}">${done ? '&#10003;' : step}</div>`;
    html += '<div class="log-body">';
    html += `<div class="log-title">${esc(label)}</div>`;
    if (files.length > 0) {
      html += `<div class="log-files">${files.map(f => esc(f)).join('<br>')}</div>`;
    }
    html += '</div></div>';
    return html;
  }

  function renderGraphArtifact(content) {
    const mermaidMatch = content.match(/```mermaid\n([\s\S]*?)```/);
    const jsonMatch = content.match(/```json\n([\s\S]*?)```/);

    let html = '<div class="art-graph">';
    html += '<div class="graph-subtabs">';
    html += '<button class="graph-subtab active" data-view="diagram">다이어그램</button>';
    html += '<button class="graph-subtab" data-view="structure">구조</button>';
    html += '</div>';

    const mermaidId = 'dock-mmd-' + (++mermaidCounter);
    html += `<div class="graph-view active" data-view="diagram"><div class="graph-diagram" id="${mermaidId}"></div></div>`;

    html += '<div class="graph-view" data-view="structure">';
    if (jsonMatch) {
      try {
        const obj = JSON.parse(jsonMatch[1]);
        html += renderJsonTree(obj, 0);
      } catch {
        html += `<pre style="font-family:var(--mono);font-size:12px;padding:8px;white-space:pre-wrap">${esc(jsonMatch[1])}</pre>`;
      }
    } else {
      html += '<div style="padding:16px;color:var(--text-muted)">JSON 구조를 찾을 수 없습니다</div>';
    }
    html += '</div>';
    html += '</div>';

    if (mermaidMatch) {
      setTimeout(() => renderMermaid(mermaidId, mermaidMatch[1].trim()), 150);
    }

    return html;
  }

  function initGraphTabs() {
    dockPanel.querySelectorAll('.graph-subtab').forEach(btn => {
      btn.addEventListener('click', () => {
        const view = btn.dataset.view;
        dockPanel.querySelectorAll('.graph-subtab').forEach(b => b.classList.toggle('active', b.dataset.view === view));
        dockPanel.querySelectorAll('.graph-view').forEach(v => v.classList.toggle('active', v.dataset.view === view));
      });
    });
  }

  function renderJsonTree(obj, depth) {
    if (obj === null) return '<span class="jt-value jt-value-null">null</span>';
    if (typeof obj !== 'object') {
      const cls = typeof obj === 'string' ? 'jt-value-str'
        : typeof obj === 'number' ? 'jt-value-num'
        : typeof obj === 'boolean' ? 'jt-value-bool' : 'jt-value';
      return `<span class="jt-value ${cls}">${esc(JSON.stringify(obj))}</span>`;
    }

    const isArr = Array.isArray(obj);
    const entries = isArr ? obj.map((v, i) => [i, v]) : Object.entries(obj);
    let html = '';

    for (const [key, value] of entries) {
      if (typeof value === 'object' && value !== null) {
        const label = isArr ? `[${key}]` : key;
        html += `<details class="jt-branch"${depth < 2 ? ' open' : ''}>`;
        html += `<summary class="jt-key">${esc(String(label))}</summary>`;
        html += renderJsonTree(value, depth + 1);
        html += '</details>';
      } else {
        const label = isArr ? `[${key}]` : key;
        html += `<div class="jt-leaf"><span class="jt-key">${esc(String(label))}</span>: ${renderJsonTree(value, depth + 1)}</div>`;
      }
    }
    return html;
  }

  function renderLegalDoc(text) {
    const lines = text.split('\n');
    let html = '';
    let foundTitle = false;
    let prevWasTitle = false;
    let inTail = false;

    for (const line of lines) {
      const trimmed = line.trim();

      if (!trimmed) {
        html += '<div class="ld-gap"></div>';
        prevWasTitle = false;
        continue;
      }

      if (!foundTitle) {
        html += `<div class="ld-title">${esc(trimmed)}</div>`;
        foundTitle = true;
        prevWasTitle = true;
        continue;
      }

      if (prevWasTitle && trimmed.startsWith('(')) {
        html += `<div class="ld-subtitle">${esc(trimmed)}</div>`;
        continue;
      }
      prevWasTitle = false;

      if (/^(청\s*구\s*취\s*지|청\s*구\s*이\s*유|증\s*거\s*서\s*류)/.test(trimmed)) {
        html += `<div class="ld-section-title">${esc(trimmed)}</div>`;
        continue;
      }

      if (/^(사\s*건|청\s*구\s*인|피\s*청\s*구\s*인)/.test(trimmed)) {
        html += `<div class="ld-field">${esc(trimmed)}</div>`;
        continue;
      }

      if (/^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\./.test(trimmed)) {
        html += `<div class="ld-h2">${esc(trimmed)}</div>`;
        continue;
      }

      if (/^\d+\./.test(trimmed)) {
        html += `<div class="ld-h3">${esc(trimmed)}</div>`;
        continue;
      }

      if (/^[가나다라마바사아자차카타파하]\./.test(trimmed)) {
        html += `<div class="ld-h4">${esc(trimmed)}</div>`;
        continue;
      }

      if (/^\([0-9가나다라마바사아자차카타파하]+\)/.test(trimmed)) {
        html += `<div class="ld-indent1">${esc(trimmed)}</div>`;
        continue;
      }

      if (/^[①②③④⑤⑥⑦⑧⑨⑩]/.test(trimmed)) {
        html += `<div class="ld-indent2">${esc(trimmed)}</div>`;
        continue;
      }

      if (/^\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*$/.test(trimmed)) {
        inTail = true;
      }

      if (inTail) {
        if (/귀중|귀하/.test(trimmed)) {
          html += `<div class="ld-tail-center">${esc(trimmed)}</div>`;
        } else {
          html += `<div class="ld-tail">${esc(trimmed)}</div>`;
        }
        continue;
      }

      html += `<p class="ld-body">${esc(trimmed)}</p>`;
    }

    return `<div class="art-doc"><div class="legal-doc-frame">${html}</div></div>`;
  }

  function renderValidation(content) {
    const rounds = content.split(/(?=### 회차 \d+)/);
    let html = '<div class="art-validation">';

    for (const round of rounds) {
      const headerMatch = round.match(/### 회차 (\d+)/);
      if (!headerMatch) continue;
      const num = headerMatch[1];
      const lines = round.split('\n').slice(1);

      let items = [];
      for (const line of lines) {
        const m = line.match(/^[-*]\s*(Phase \d+|Layer \d+|.+?)[\s:：]+\s*(pass|fail)\s*(.*)/i);
        if (m) {
          items.push({ label: m[1].trim(), pass: m[2].toLowerCase() === 'pass', detail: m[3].replace(/^[→\->]+\s*/, '').trim() });
        }
      }

      const allPass = items.length > 0 && items.every(i => i.pass);
      const anyFail = items.some(i => !i.pass);
      const badgeClass = allPass ? 'pass' : anyFail ? 'fail' : 'partial';
      const badgeText = allPass ? 'pass' : anyFail ? 'fail' : 'partial';

      html += `<div class="val-round${allPass ? '' : ' open'}">`;
      html += `<div class="val-round-header"><span>회차 ${esc(num)}</span><span class="val-round-badge ${badgeClass}">${badgeText}</span></div>`;
      html += '<div class="val-round-body">';
      for (const item of items) {
        html += `<div class="val-item"><span class="val-mark ${item.pass ? 'pass' : 'fail'}">${item.pass ? '&#10003;' : '&#10007;'}</span><span class="val-desc">${esc(item.label)}</span></div>`;
        if (item.detail) {
          html += `<div class="val-fix">${esc(item.detail)}</div>`;
        }
      }
      if (items.length === 0) {
        html += marked.parse(lines.join('\n'));
      }
      html += '</div></div>';
    }

    if (!content.includes('### 회차')) {
      html += '<div class="art-validation">' + marked.parse(content) + '</div>';
    }

    html += '</div>';
    return html;
  }

  function initValidationAccordion() {
    dockPanel.querySelectorAll('.val-round-header').forEach(header => {
      header.addEventListener('click', () => {
        header.parentElement.classList.toggle('open');
      });
    });
  }

  function renderPdfCard(path) {
    const name = path.split('/').pop();
    return `<div class="art-pdf">
      <div class="art-pdf-icon">&#128211;</div>
      <div class="art-pdf-name">${esc(name)}</div>
      <button class="art-pdf-btn">다운로드</button>
    </div>`;
  }
})();
