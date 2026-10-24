/**
 * frontend/js/components/ssh.js
 * SSH Connection Manager — handles SSH modal, connect form, and session switching.
 */

export class SSHComponent {
  constructor(state, ws, api) {
    this.state = state;
    this.ws = ws;
    this.api = api;
    this.currentSSHSessionId = null;
    this.sshWebSocket = null;

    this.init();
  }

  init() {
    this.bindDOMEvents();
  }

  bindDOMEvents() {
    // Terminal type selector
    const typeSelector = document.getElementById('terminal-type-selector');
    if (typeSelector) {
      typeSelector.addEventListener('change', (e) => {
        const sshBtn = document.getElementById('btn-ssh-connect');
        if (e.target.value === 'ssh') {
          if (sshBtn) sshBtn.style.display = '';
          // If no active SSH session, show modal
          if (!this.currentSSHSessionId) {
            this.showModal();
          } else {
            // Switch to existing SSH session
            this.switchToSSH(this.currentSSHSessionId);
          }
        } else {
          if (sshBtn) sshBtn.style.display = 'none';
          this.switchToLocal();
        }
      });
    }

    // SSH connect button
    const sshBtn = document.getElementById('btn-ssh-connect');
    if (sshBtn) {
      sshBtn.addEventListener('click', () => this.showModal());
    }

    // SSH modal close
    const closeBtn = document.getElementById('btn-ssh-modal-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => this.hideModal());
    }

    const cancelBtn = document.getElementById('btn-ssh-cancel');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => this.hideModal());
    }

    // SSH connect form
    const form = document.getElementById('form-ssh-connect');
    if (form) {
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        this.handleConnect();
      });
    }

    // Close modal on backdrop click
    const modal = document.getElementById('ssh-connect-modal');
    if (modal) {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) this.hideModal();
      });
    }
  }

  showModal() {
    const modal = document.getElementById('ssh-connect-modal');
    if (modal) {
      modal.classList.remove('hidden');
      this.loadActiveSessions();
    }
  }

  hideModal() {
    const modal = document.getElementById('ssh-connect-modal');
    if (modal) modal.classList.add('hidden');
  }

  setStatus(msg, isError = false) {
    const el = document.getElementById('ssh-connect-status');
    if (el) {
      el.textContent = msg;
      el.className = `ssh-status ${isError ? 'ssh-status-error' : 'ssh-status-ok'}`;
    }
  }

  async handleConnect() {
    const host = document.getElementById('ssh-host')?.value?.trim();
    const port = parseInt(document.getElementById('ssh-port')?.value || '22');
    const username = document.getElementById('ssh-username')?.value?.trim() || 'root';
    const password = document.getElementById('ssh-password')?.value || '';
    const name = document.getElementById('ssh-name')?.value?.trim() || '';

    if (!host) {
      this.setStatus('Please enter a host address', true);
      return;
    }

    this.setStatus('Connecting...');

    try {
      const token = this.state.get('authToken') || localStorage.getItem('agy_token');
      const resp = await fetch('/api/ssh/connect', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ host, port, username, password, name: name || undefined }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        this.setStatus(`Failed: ${err.detail || 'Connection error'}`, true);
        return;
      }

      const data = await resp.json();
      this.currentSSHSessionId = data.session_id;
      this.setStatus(`Connected: ${data.username}@${data.host}`);

      // Clear terminal buffer
      const buffer = document.getElementById('terminal-buffer') || document.getElementById('terminal-screen');
      if (buffer) {
        buffer.innerHTML = `<div style="color:#4fc3f7;">Connected to ${data.username}@${data.host}:${data.port}</div>`;
      }

      // Switch WebSocket to SSH
      this.switchToSSH(data.session_id);
      this.hideModal();

    } catch (err) {
      this.setStatus(`Error: ${err.message}`, true);
    }
  }

  switchToSSH(sessionId) {
    // Disconnect local terminal WebSocket
    if (this.ws && this.ws.disconnectTerminal) {
      this.ws.disconnectTerminal();
    }

    // Connect SSH WebSocket
    const token = this.state.get('authToken') || localStorage.getItem('agy_token');
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/ssh?token=${token}&session_id=${sessionId}`;

    if (this.sshWebSocket) {
      try { this.sshWebSocket.close(); } catch (_) {}
    }

    this.sshWebSocket = new WebSocket(wsUrl);

    this.sshWebSocket.onmessage = (event) => {
      try {
        const frame = JSON.parse(event.data);
        if (frame.type === 'output') {
          this.state.emit('terminal:output', frame.data);
        } else if (frame.type === 'connected') {
          this.state.emit('terminal:output', `\r\n[SSH Connected: ${frame.host}]\r\n`);
        } else if (frame.type === 'error') {
          this.state.emit('terminal:output', `\r\n[SSH Error: ${frame.message}]\r\n`);
        }
      } catch (_) {}
    };

    this.sshWebSocket.onclose = () => {
      this.state.emit('terminal:output', '\r\n[SSH Disconnected]\r\n');
    };

    // Override terminal input to use SSH WebSocket
    this.state.set('terminalMode', 'ssh');
    this.state.set('sshWebSocket', this.sshWebSocket);
  }

  switchToLocal() {
    // Close SSH WebSocket
    if (this.sshWebSocket) {
      try { this.sshWebSocket.close(); } catch (_) {}
      this.sshWebSocket = null;
    }
    this.state.set('terminalMode', 'local');
    this.state.set('sshWebSocket', null);

    // Reconnect local terminal
    if (this.ws && this.ws.connectTerminal) {
      this.ws.connectTerminal();
    }
  }

  async loadActiveSessions() {
    const container = document.getElementById('ssh-sessions-container');
    if (!container) return;

    try {
      const token = this.state.get('authToken') || localStorage.getItem('agy_token');
      const resp = await fetch('/api/ssh/sessions', {
        headers: { 'Authorization': `Bearer ${token}` },
      });

      if (!resp.ok) {
        container.innerHTML = '<div class="ssh-no-sessions">No active sessions</div>';
        return;
      }

      const sessions = await resp.json();
      if (!sessions.length) {
        container.innerHTML = '<div class="ssh-no-sessions">No active sessions</div>';
        return;
      }

      container.innerHTML = sessions.map(s => `
        <div class="ssh-session-item">
          <div class="ssh-session-info">
            <span class="ssh-session-name">${s.name || s.username + '@' + s.host}</span>
            <span class="ssh-session-status ${s.is_alive ? 'alive' : 'dead'}">${s.is_alive ? '●' : '○'}</span>
          </div>
          <div class="ssh-session-actions">
            <button class="btn-ssh-reconnect" data-session-id="${s.session_id}" ${!s.is_alive ? 'disabled' : ''}>Connect</button>
            <button class="btn-ssh-disconnect" data-session-id="${s.session_id}">✕</button>
          </div>
        </div>
      `).join('');

      // Bind reconnect/disconnect buttons
      container.querySelectorAll('.btn-ssh-reconnect').forEach(btn => {
        btn.addEventListener('click', () => {
          const sid = btn.dataset.sessionId;
          this.currentSSHSessionId = sid;
          this.switchToSSH(sid);
          this.hideModal();
        });
      });

      container.querySelectorAll('.btn-ssh-disconnect').forEach(btn => {
        btn.addEventListener('click', async () => {
          const sid = btn.dataset.sessionId;
          try {
            await fetch(`/api/ssh/sessions/${sid}`, {
              method: 'DELETE',
              headers: { 'Authorization': `Bearer ${token}` },
            });
            this.loadActiveSessions();
          } catch (_) {}
        });
      });

    } catch (err) {
      container.innerHTML = '<div class="ssh-no-sessions">Error loading sessions</div>';
    }
  }

  // Send input to SSH (called from terminal component)
  sendInput(data) {
    if (this.sshWebSocket && this.sshWebSocket.readyState === WebSocket.OPEN) {
      this.sshWebSocket.send(JSON.stringify({ type: 'input', data }));
    }
  }
}
