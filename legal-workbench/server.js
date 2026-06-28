const express = require('express');
const http = require('http');
const path = require('path');
const { WebSocketServer } = require('ws');
const ClaudeBridge = require('./lib/claude-bridge');
const createFileApi = require('./lib/file-api');

const PORT = process.env.PORT || 9000;

const STEPS = [
  { step: 1, label: '참조 파일 읽기' },
  { step: 2, label: '사건 문서 통독' },
  { step: 3, label: '판례 전문 통독' },
  { step: 4, label: '사안 파악' },
  { step: 5, label: '쟁점 정리' },
  { step: 6, label: '법리 그래프 설계' },
  { step: 7, label: '그래프 검증 루프' },
  { step: 8, label: '문서 생성' },
  { step: 9, label: '문서 검증 루프' },
  { step: 10, label: 'PDF 출력' },
];

const TOOL_PATTERNS = [
  { step: 1, tool: 'Read', pattern: /harness\/(data|style|quality|디렉토리_지도|관리_절차)/ },
  { step: 2, tool: 'Read', pattern: /cases\/.+\/(절차|작성|민원|증거|분석|외부소통|피청구인)/ },
  { step: 2, tool: 'Read', pattern: /법령_판례\// },
  { step: 3, tool: 'Bash', pattern: /extract_all\.py.*판례/ },
  { step: 6, tool: 'Write', pattern: /법리그래프_/ },
  { step: 6, tool: 'Edit', pattern: /법리그래프_/ },
  { step: 7, tool: 'Bash', pattern: /validate_graph/ },
  { step: 8, tool: 'Write', pattern: /작성_(최종|초안)\/.+\.txt/ },
  { step: 8, tool: 'Edit', pattern: /작성_(최종|초안)\/.+\.txt/ },
  { step: 9, tool: 'Bash', pattern: /validate_doc/ },
  { step: 10, tool: 'Bash', pattern: /generate_pdf\.py|\.pdf/ },
  { step: 10, tool: 'Write', pattern: /\.pdf$/ },
];

const TEXT_PATTERNS = [
  { step: 3, patterns: [/3단계/, /판례\s*전문\s*통독/] },
  { step: 4, patterns: [/4단계/, /사안\s*파악/, /문서\s*유형/, /피청구기관/, /청구\s*대상\s*정보/] },
  { step: 5, patterns: [/5단계/, /쟁점\s*정리/, /핵심\s*쟁점/, /문서\s*구성안/, /접근\s*방향/] },
  { step: 6, patterns: [/6단계/, /법리\s*연결\s*그래프/, /법리\s*그래프\s*설계/] },
  { step: 7, patterns: [/7단계/, /그래프\s*검증/, /Phase\s*[12].*그래프/] },
  { step: 8, patterns: [/8단계/, /문서\s*생성/, /그래프를\s*청사진/] },
  { step: 9, patterns: [/9단계/, /문서\s*검증/, /Phase\s*[12].*문서/] },
  { step: 10, patterns: [/10단계/, /PDF\s*출력/, /generate_pdf/] },
];

const STEP_COMPLETE_RE = /###\s*(\d+)단계\s*완료/;

class StepDetector {
  constructor() {
    this.currentStep = 0;
    this.completed = new Set();
  }

  detectFromTool(toolName, toolInput) {
    const fileArg =
      toolInput?.file_path ||
      toolInput?.path ||
      toolInput?.command ||
      '';

    for (const p of TOOL_PATTERNS) {
      if (p.tool === toolName && p.pattern.test(fileArg)) {
        return this._advance(p.step);
      }
    }
    return null;
  }

  detectFromText(text) {
    if (!text) return null;
    for (const p of TEXT_PATTERNS) {
      if (p.step > this.currentStep + 1) continue;
      if (p.patterns.some(re => re.test(text))) {
        return this._advance(p.step);
      }
    }

    const m = STEP_COMPLETE_RE.exec(text);
    if (m) {
      const step = parseInt(m[1], 10);
      this.completed.add(step);
      return {
        type: 'step_update',
        step: this.currentStep,
        completed: [...this.completed].sort((a, b) => a - b),
        completedStep: step,
      };
    }
    return null;
  }

  _advance(step) {
    if (step > this.currentStep) {
      this.currentStep = step;
      return {
        type: 'step_update',
        step,
        completed: [...this.completed].sort((a, b) => a - b),
      };
    }
    return null;
  }

  getState() {
    return {
      currentStep: this.currentStep,
      completed: [...this.completed].sort((a, b) => a - b),
    };
  }
}

const app = express();
app.use(express.json({ limit: '3mb' }));
app.use('/api', createFileApi());
app.use(express.static(path.join(__dirname, 'public')));
app.get('/api/steps', (_req, res) => res.json({ steps: STEPS }));

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

let activeSocket = null;

wss.on('connection', (ws) => {
  if (activeSocket && activeSocket.readyState <= 1) {
    activeSocket.close(1000, 'replaced');
  }
  activeSocket = ws;

  const bridge = new ClaudeBridge();
  const detector = new StepDetector();
  let aborted = false;

  function send(data) {
    if (ws.readyState === 1) {
      ws.send(JSON.stringify(data));
    }
  }

  async function streamSession() {
    aborted = false;
    send({ type: 'status', status: 'running' });

    let lastHadToolUse = false;
    let lastStopReason = null;
    let streamedText = '';
    let sentTextForTurn = false;

    for await (const event of bridge.readEvents()) {
      if (aborted) break;

      if (event.type === 'system' && event.subtype === 'init') {
        send({ type: 'session_info', sessionId: event.session_id });
        continue;
      }

      // Partial streaming: content_block_delta with text_delta
      if (event.type === 'stream_event') {
        const inner = event.event;
        if (!inner) continue;

        if (inner.type === 'content_block_delta' && inner.delta?.type === 'text_delta') {
          const text = inner.delta.text || '';
          if (text) {
            streamedText += text;
            send({ type: 'text_delta', content: text });
          }
        }

        if (inner.type === 'content_block_start' && inner.content_block?.type === 'tool_use') {
          lastHadToolUse = true;
        }

        if (inner.type === 'message_start') {
          streamedText = '';
          sentTextForTurn = false;
          lastHadToolUse = false;
        }

        if (inner.type === 'message_delta' && inner.delta?.stop_reason) {
          lastStopReason = inner.delta.stop_reason;
        }

        continue;
      }

      // Full assistant message: process tool_use blocks, detect steps from full text
      if (event.type === 'assistant') {
        const msg = event.message;
        if (!msg || !msg.content) continue;

        for (const block of msg.content) {
          if (block.type === 'text' && block.text) {
            // If we didn't stream any text (e.g. partial messages disabled), send full text
            if (!streamedText) {
              send({ type: 'text', content: block.text });
            }
            const stepUpdate = detector.detectFromText(block.text);
            if (stepUpdate) send(stepUpdate);
            sentTextForTurn = true;
          }
          if (block.type === 'tool_use') {
            lastHadToolUse = true;
            const toolData = {
              type: 'tool_call',
              toolUseId: block.id,
              tool: block.name,
              input: block.input || {},
            };
            const stepUpdate = detector.detectFromTool(block.name, block.input);
            if (stepUpdate) {
              toolData.stepUpdate = stepUpdate;
              send(stepUpdate);
            }
            send(toolData);
          }
        }

        if (msg.stop_reason) {
          lastStopReason = msg.stop_reason;
        }

        // Reset streamed text for next turn
        streamedText = '';
        continue;
      }

      if (event.type === 'user') {
        if (!event.message || !event.message.content) continue;
        for (const block of event.message.content) {
          if (block.type === 'tool_result') {
            const preview = typeof block.content === 'string'
              ? block.content.substring(0, 500)
              : JSON.stringify(block.content).substring(0, 500);

            send({
              type: 'tool_result',
              toolUseId: block.tool_use_id,
              preview,
              isError: !!block.is_error,
            });
          }
        }
        continue;
      }

      if (event.type === 'result') {
        const resultData = {
          sessionId: event.session_id || bridge.sessionId,
          costUsd: event.total_cost_usd || 0,
          durationMs: event.duration_ms || 0,
          numTurns: event.num_turns || 0,
        };

        const needsInput = event.subtype === 'success'
          && lastStopReason === 'end_turn'
          && !lastHadToolUse;

        if (needsInput) {
          send({ type: 'needs_input', ...resultData });
        } else {
          send({ type: 'complete', ...resultData });
        }
        continue;
      }
    }

    if (aborted) {
      send({ type: 'status', status: 'aborted' });
    } else if (!bridge.isRunning) {
      const code = bridge.proc?.exitCode;
      if (code && code !== 0) {
        send({ type: 'error', message: `프로세스가 종료되었습니다 (exit code ${code})` });
      } else {
        send({ type: 'complete', sessionId: bridge.sessionId, costUsd: 0, durationMs: 0, numTurns: 0 });
      }
    }
  }

  ws.on('message', async (raw) => {
    let data;
    try {
      data = JSON.parse(raw.toString());
    } catch {
      send({ type: 'error', message: 'Invalid JSON' });
      return;
    }

    if (data.type === 'prompt') {
      aborted = true;
      bridge.stop();
      detector.currentStep = 0;
      detector.completed.clear();

      let prompt = (data.content || '').trim();
      if (!prompt) {
        send({ type: 'error', message: 'Empty prompt' });
        return;
      }
      if (!prompt.startsWith('/')) {
        prompt = '/legal-harness ' + prompt;
      }

      send({ type: 'status', status: 'starting' });
      bridge.start(prompt);
      streamSession().catch(err => {
        send({ type: 'error', message: err.message });
      });
      return;
    }

    if (data.type === 'response') {
      const content = (data.content || '').trim();
      if (!content) {
        send({ type: 'error', message: 'Empty response' });
        return;
      }
      if (!bridge.sessionId) {
        send({ type: 'error', message: 'No active session to resume' });
        return;
      }

      aborted = true;
      bridge.stop();

      send({ type: 'status', status: 'resuming' });
      bridge.start(content, bridge.sessionId);
      streamSession().catch(err => {
        send({ type: 'error', message: err.message });
      });
      return;
    }

    if (data.type === 'abort') {
      aborted = true;
      bridge.stop();
      return;
    }
  });

  ws.on('close', () => {
    aborted = true;
    bridge.stop();
    if (activeSocket === ws) activeSocket = null;
  });
});

process.on('SIGTERM', () => { process.exit(0); });
process.on('SIGINT', () => { process.exit(0); });

server.listen(PORT, '0.0.0.0', () => {
  console.log(`Legal Workbench running at http://localhost:${PORT}`);
});
