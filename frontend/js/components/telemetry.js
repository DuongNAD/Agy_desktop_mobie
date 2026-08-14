/**
 * frontend/js/components/telemetry.js
 * Real-Time SVG Telemetry Gauges & Desktop Task Monitor.
 */

export class TelemetryComponent {
  constructor(state, api, ws) {
    this.state = state;
    this.api = api;
    this.ws = ws;
    this.circumference = 2 * Math.PI * 40; // 251.327
    this.pollInterval = null;

    this.init();
  }

  init() {
    this.bindDOMEvents();
    this.subscribeState();
  }

  bindDOMEvents() {
    const btnRefresh = document.getElementById('btn-telemetry-refresh') || document.getElementById('btn-refresh-telemetry');
    if (btnRefresh) {
      btnRefresh.addEventListener('click', () => {
        this.fetchTelemetry();
      });
    }

    // Process kill action delegation
    const taskContainer = document.getElementById('process-list-container') || document.getElementById('tasks-list-container');
    if (taskContainer) {
      taskContainer.addEventListener('click', (e) => {
        const killBtn = e.target.closest('button[data-kill-pid]');
        if (!killBtn) return;
        const pid = killBtn.dataset.killPid;
        if (pid) {
          this.killProcess(pid);
        }
      });
    }
  }

  subscribeState() {
    this.state.subscribe('activeView', (view) => {
      if (view === 'system') {
        this.fetchTelemetry();
        this.startPolling();
      } else {
        this.stopPolling();
      }
    });

    this.state.subscribe('latency', (rtt) => {
      const latEl = document.getElementById('connection-latency');
      const rttEl = document.getElementById('info-rtt');
      const text = `${rtt} ms`;
      if (latEl) latEl.textContent = text;
      if (rttEl) rttEl.textContent = text;
    });

    this.state.subscribe('connectionStatus', (status) => {
      const badge = document.getElementById('connection-badge');
      if (badge) {
        badge.className = `connection-pill ${status.toLowerCase()}`;
        if (status === 'CONNECTED') {
          const lat = this.state.get('latency') || 0;
          const latEl = document.getElementById('connection-latency');
          if (latEl) latEl.textContent = lat > 0 ? `${lat} ms` : 'Online';
        } else if (status === 'CONNECTING' || status === 'RECONNECTING') {
          const latEl = document.getElementById('connection-latency');
          if (latEl) latEl.textContent = 'Connecting...';
        } else {
          const latEl = document.getElementById('connection-latency');
          if (latEl) latEl.textContent = 'Offline';
        }
      }
    });
  }

  startPolling() {
    this.stopPolling();
    this.pollInterval = setInterval(() => {
      if (this.state.get('activeView') === 'system') {
        this.fetchTelemetry();
      }
    }, 4000);
  }

  stopPolling() {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  async fetchTelemetry() {
    try {
      const info = await this.api.getSystemInfo();
      if (info) {
        this.updateGauges(info);
        this.updateHostInfo(info);
      }
    } catch (_) {
      // Telemetry fetch fallback
    }
  }

  updateGauges(stats) {
    this.setGauge('gauge-cpu-bar', 'gauge-cpu-fill', 'metric-cpu-val', 'gauge-cpu-val', stats.cpu || 0);
    this.setGauge('gauge-ram-bar', 'gauge-ram-fill', 'metric-ram-val', 'gauge-ram-val', stats.ram || 0);
    this.setGauge('gauge-disk-bar', 'gauge-disk-fill', 'metric-disk-val', 'gauge-disk-val', stats.disk || 0);
  }

  setGauge(barId1, barId2, valId1, valId2, percent) {
    const clamped = Math.max(0, Math.min(100, percent));
    const offset = this.circumference - (clamped / 100) * this.circumference;
    const color = clamped >= 90 ? 'var(--neon-rose)' : clamped >= 75 ? 'var(--amber-alert)' : 'var(--cyber-cyan)';

    const barEl = document.getElementById(barId1) || document.getElementById(barId2);
    const valEl = document.getElementById(valId1) || document.getElementById(valId2);

    if (barEl) {
      barEl.style.strokeDashoffset = offset;
      barEl.style.stroke = color;
    }

    if (valEl) {
      valEl.textContent = `${Math.round(clamped)}`;
    }
  }

  updateHostInfo(info) {
    const lanIpEl = document.getElementById('info-lan-ip');
    const uptimeEl = document.getElementById('info-uptime') || document.getElementById('metric-uptime-val');
    const osEl = document.getElementById('info-os') || document.getElementById('metric-cpu-cores');
    const hostInfoEl = document.getElementById('metric-host-info');

    if (lanIpEl && info.lan_ip) lanIpEl.textContent = info.lan_ip;
    if (uptimeEl && info.uptime !== undefined) uptimeEl.textContent = this.formatUptime(info.uptime);
    if (osEl && info.os) osEl.textContent = `${info.os.toUpperCase()} (${info.hostname || 'Host'})`;
    if (hostInfoEl && info.lan_ip) hostInfoEl.textContent = `${info.lan_ip}:${info.port || 8765}`;

    // Render active background tasks if any
    const taskContainer = document.getElementById('process-list-container') || document.getElementById('tasks-list-container');
    if (taskContainer) {
      const tasks = info.tasks || [];
      if (tasks.length === 0) {
        taskContainer.innerHTML = `
          <div class="task-card">
            <div>
              <strong style="color:var(--cyber-cyan);">agy.EXE (Antigravity Core)</strong>
              <div style="font-size:10px;color:var(--text-muted);">Session Hub Running</div>
            </div>
            <span class="status-badge status-running">Active</span>
          </div>
        `;
      } else {
        taskContainer.innerHTML = tasks.map((t) => `
          <div class="task-card">
            <div>
              <strong>${this._escape(t.name || 'Process')}</strong>
              <div style="font-size:10px;color:var(--text-muted);">PID: ${t.pid} | CPU: ${t.cpu || 0}%</div>
            </div>
            <button class="task-kill-btn" data-kill-pid="${t.pid}">Kill</button>
          </div>
        `).join('');
      }
    }
  }

  formatUptime(sec) {
    const totalSec = Math.floor(sec || 0);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    return `${h}h ${m}m ${s}s`;
  }

  async killProcess(pid) {
    if (!confirm(`Are you sure you want to kill process PID ${pid}?`)) return;

    try {
      await this.api.killTask(pid);
      this.state.addToast({
        type: 'success',
        title: 'Process Killed',
        message: `Process PID ${pid} terminated successfully.`,
      });
      this.fetchTelemetry();
    } catch (err) {
      this.state.addToast({
        type: 'error',
        title: 'Kill Failed',
        message: err.message || 'Unable to kill process',
      });
    }
  }

  _escape(text) {
    if (!text) return '';
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
}
