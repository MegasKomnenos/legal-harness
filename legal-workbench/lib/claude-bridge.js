const { spawn } = require('child_process');
const readline = require('readline');
const path = require('path');

// PATH에서 claude 실행 파일을 찾는다. 환경별 경로는 CLAUDE_BIN 환경변수로 덮어쓸 수 있다.
// (Windows에서 claude.cmd 해석이 필요하면 CLAUDE_BIN에 절대 경로를 지정하거나 spawn 옵션에 shell:true를 둔다.)
const CLAUDE_BIN = process.env.CLAUDE_BIN || 'claude';
const PROJECT_DIR = path.resolve(__dirname, '..', '..');

class ClaudeBridge {
  constructor() {
    this.proc = null;
    this.sessionId = null;
    this.running = false;
    this._rl = null;
  }

  start(prompt, resumeSessionId) {
    this.stop();

    const args = [
      '-p',
      '--output-format', 'stream-json',
      '--verbose',
      '--include-partial-messages',
    ];

    if (resumeSessionId) {
      args.push('--resume', resumeSessionId);
    }

    args.push(prompt);

    this.proc = spawn(CLAUDE_BIN, args, {
      cwd: PROJECT_DIR,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, FORCE_COLOR: '0' },
    });

    this.running = true;

    this._rl = readline.createInterface({
      input: this.proc.stdout,
      crlfDelay: Infinity,
    });

    this.proc.stderr.on('data', (chunk) => {
      const msg = chunk.toString().trim();
      if (msg) console.error('[claude stderr]', msg);
    });

    this.proc.on('exit', (code, signal) => {
      this.running = false;
      console.log(`[claude] exited code=${code} signal=${signal}`);
    });

    this.proc.on('error', (err) => {
      this.running = false;
      console.error('[claude] spawn error:', err.message);
    });
  }

  async *readEvents() {
    if (!this._rl) return;

    const lines = [];
    let done = false;
    let resolve;
    let pending = new Promise(r => { resolve = r; });

    this._rl.on('line', (line) => {
      lines.push(line);
      resolve();
      pending = new Promise(r => { resolve = r; });
    });

    this._rl.on('close', () => {
      done = true;
      resolve();
    });

    while (true) {
      if (lines.length === 0) {
        if (done) break;
        await pending;
        if (lines.length === 0 && done) break;
      }

      while (lines.length > 0) {
        const line = lines.shift();
        if (!line.trim()) continue;
        try {
          const event = JSON.parse(line);
          if (event.type === 'system' && event.subtype === 'init' && event.session_id) {
            this.sessionId = event.session_id;
          }
          if (event.type === 'result' && event.session_id) {
            this.sessionId = event.session_id;
          }
          yield event;
        } catch {
          console.warn('[claude] unparseable line:', line.substring(0, 200));
        }
      }
    }
  }

  stop() {
    this.running = false;
    if (this._rl) {
      this._rl.close();
      this._rl = null;
    }
    if (this.proc && this.proc.exitCode === null) {
      this.proc.kill('SIGTERM');
      const proc = this.proc;
      setTimeout(() => {
        if (proc.exitCode === null) proc.kill('SIGKILL');
      }, 5000);
    }
    this.proc = null;
  }

  get isRunning() {
    return this.running && this.proc !== null && this.proc.exitCode === null;
  }
}

module.exports = ClaudeBridge;
