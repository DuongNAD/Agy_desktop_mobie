/**
 * frontend/js/components/terminal.js
 * Mobile Touch Terminal with ANSI Renderer & Fixed Virtual Utility Keys Toolbar.
 */

import { AnsiUp } from '../lib/ansi_up.js';

export class TerminalComponent {
  constructor(state, ws, ansiUpInstance = null) {
    this.state = state;
    this.ws = ws;
    this.ansiUp = ansiUpInstance || new AnsiUp();
    this.isCtrlSticky = false;
    this.maxLines = 1000;
    this.isAutoScroll = true;
    this.history = [];
    this.historyIndex = -1;

    this.init();
  }

  init() {
    this.bindDOMEvents();
    this.subscribeState();
  }

  bindDOMEvents() {
    // Virtual Keys Toolbar
    const toolbar = document.getElementById('terminal-accessory-bar') || document.getElementById('terminal-touch-toolbar');
    if (toolbar) {
      toolbar.addEventListener('click', (e) => {
        const target = e.target.closest('button');
        if (!target) return;

        const key = target.dataset.key;
        const char = target.dataset.char;
        const action = target.dataset.action;

        if (key === 'Control' || target.id === 'vkey-ctrl' || target.id === 'btn-term-ctrl') {
          this.isCtrlSticky = !this.isCtrlSticky;
          target.classList.toggle('active', this.isCtrlSticky);
          return;
        }

        if (action === 'interrupt' || key === 'CtrlC') {
          this.sendInterrupt();
          return;
        }

        if (key === 'Escape') {
          this.sendRawKey('\x1b');
          return;
        }

        if (key === 'Tab') {
          this.sendRawKey('\t');
          return;
        }

        if (key === 'ArrowUp') {
          this.sendRawKey('\x1b[A');
          return;
        }

        if (key === 'ArrowDown') {
          this.sendRawKey('\x1b[B');
          return;
        }

        if (key === 'ArrowLeft') {
          this.sendRawKey('\x1b[D');
          return;
        }

        if (key === 'ArrowRight') {
          this.sendRawKey('\x1b[C');
          return;
        }

        if (char) {
          this.sendRawKey(char);
        }
      });
    }

    // Top action buttons (Clear, Restart, Kill)
    const btnClear = document.getElementById('btn-terminal-clear') || document.getElementById('btn-term-clear');
    if (btnClear) {
      btnClear.addEventListener('click', () => this.clear());
    }

    const btnRestart = document.getElementById('btn-terminal-restart') || document.getElementById('btn-term-restart');
    if (btnRestart) {
      btnRestart.addEventListener('click', () => this.restart());
    }

    const btnKill = document.getElementById('btn-terminal-kill');
    if (btnKill) {
      btnKill.addEventListener('click', () => this.sendInterrupt());
    }

    // Command Input Form
    const termForm = document.getElementById('form-terminal-input');
    const termInput = document.getElementById('input-terminal-cmd') || document.getElementById('terminal-input');

    if (termForm) {
      termForm.addEventListener('submit', (e) => {
        e.preventDefault();
        this.submitCommand();
      });
    }

    if (termInput) {
      termInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          this.submitCommand();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (this.history.length > 0) {
            if (this.historyIndex === -1) {
              this.historyIndex = this.history.length - 1;
            } else if (this.historyIndex > 0) {
              this.historyIndex--;
            }
            termInput.value = this.history[this.historyIndex] || '';
          }
        } else if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (this.historyIndex !== -1) {
            if (this.historyIndex < this.history.length - 1) {
              this.historyIndex++;
              termInput.value = this.history[this.historyIndex] || '';
            } else {
              this.historyIndex = -1;
              termInput.value = '';
            }
          }
        }
      });
    }
  }

  subscribeState() {
    this.ws.on('terminal_output', (chunk) => {
      this.appendOutput(chunk);
    });

    this.state.on('terminal:output', (chunk) => {
      this.appendOutput(chunk);
    });

    this.state.subscribe('activeView', (view) => {
      if (view === 'terminal') {
        this.ws.connectTerminal();
      }
    });
  }

  appendOutput(chunk) {
    const buffer = document.getElementById('terminal-buffer') || document.getElementById('terminal-screen');
    if (!buffer) return;

    const html = this.ansiUp.ansiToHtml(chunk);
    const span = document.createElement('span');
    span.innerHTML = html;
    buffer.appendChild(span);

    // Prune excessive scrollback lines
    while (buffer.childNodes.length > this.maxLines) {
      buffer.removeChild(buffer.firstChild);
    }

    if (this.isAutoScroll) {
      const screen = document.getElementById('terminal-viewport') || document.getElementById('terminal-screen');
      if (screen) {
        screen.scrollTop = screen.scrollHeight;
      }
    }
  }

  submitCommand() {
    const input = document.getElementById('input-terminal-cmd') || document.getElementById('terminal-input');
    if (!input) return;

    const cmd = input.value;
    input.value = '';

    if (cmd) {
      this.history.push(cmd);
      this.historyIndex = -1;
    }

    this.sendRawKey((cmd || '') + '\r\n');
  }

  sendRawKey(data) {
    let payload = data;
    if (this.isCtrlSticky && payload.length === 1) {
      const code = payload.toUpperCase().charCodeAt(0) - 64;
      if (code >= 1 && code <= 26) {
        payload = String.fromCharCode(code);
      }
      this.isCtrlSticky = false;
      const ctrlBtn = document.getElementById('vkey-ctrl') || document.getElementById('btn-term-ctrl');
      if (ctrlBtn) ctrlBtn.classList.remove('active');
    }

    this.ws.sendTerminalInput(payload);
  }

  sendInterrupt() {
    this.ws.sendTerminalInterrupt();
    this.ws.sendTerminalInput('\x03');
  }

  clear() {
    this.ws.sendTerminalClear();
    const buffer = document.getElementById('terminal-buffer') || document.getElementById('terminal-screen');
    if (buffer) buffer.innerHTML = '';
  }

  restart() {
    this.ws.disconnectTerminal();
    const buffer = document.getElementById('terminal-buffer') || document.getElementById('terminal-screen');
    if (buffer) buffer.innerHTML = '<div style="color:var(--amber-alert);">Restarting terminal session...</div>';
    setTimeout(() => {
      this.ws.connectTerminal();
    }, 500);
  }
}
