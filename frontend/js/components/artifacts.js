/**
 * frontend/js/components/artifacts.js
 * Markdown Artifacts & Plan Checklist Viewer.
 */

export class ArtifactsManager {
  constructor(state, api) {
    this.state = state;
    this.api = api;
    this.activeArtifactFile = null;
    this.artifactsList = [];

    this.init();
  }

  init() {
    this.bindDOMEvents();
    this.subscribeState();
  }

  bindDOMEvents() {
    // Close Modal Button
    const btnClose = document.getElementById('btn-close-artifacts-modal') || document.getElementById('btn-close-artifacts');
    if (btnClose) {
      btnClose.addEventListener('click', () => this.hide());
    }

    // Copy Markdown Button
    const btnCopy = document.getElementById('btn-copy-artifact');
    if (btnCopy) {
      btnCopy.addEventListener('click', () => this.copyCurrentArtifact());
    }

    // Refresh Artifacts Button
    const btnRefresh = document.getElementById('btn-refresh-artifact');
    if (btnRefresh) {
      btnRefresh.addEventListener('click', () => {
        const cid = this.state.get('activeConversationId');
        if (cid) this.loadArtifacts(cid);
      });
    }

    // Tab Bar Clicks
    const tabBar = document.getElementById('artifacts-tab-bar') || document.getElementById('artifacts-tabs');
    if (tabBar) {
      tabBar.addEventListener('click', (e) => {
        const tab = e.target.closest('.artifact-tab');
        if (!tab) return;
        const filename = tab.dataset.filename;
        if (filename) {
          this.selectArtifact(filename);
        }
      });
    }
  }

  subscribeState() {
    this.state.subscribe('activeConversationId', (cid) => {
      if (cid) {
        this.loadArtifacts(cid);
      }
    });
  }

  async loadArtifacts(conversationId) {
    try {
      const res = await this.api.listArtifacts(conversationId);
      const items = Array.isArray(res) ? res : (res && res.artifacts ? res.artifacts : []);
      this.artifactsList = items;
      this.renderArtifactTabs(items);

      if (items.length > 0) {
        const defaultFile = items[0].filename || items[0].name || items[0];
        this.selectArtifact(defaultFile);
      } else {
        const view = document.getElementById('artifact-content-view');
        if (view) {
          view.innerHTML = '<p class="empty-text">No active artifacts found for this session.</p>';
        }
        const chip = document.getElementById('artifacts-progress-chip');
        if (chip) chip.textContent = '';
      }
    } catch (_) {
      // Artifacts list fallback
    }
  }

  renderArtifactTabs(items) {
    const tabBar = document.getElementById('artifacts-tab-bar') || document.getElementById('artifacts-tabs');
    if (!tabBar) return;

    tabBar.innerHTML = '';
    items.forEach((item, index) => {
      const filename = typeof item === 'string' ? item : item.filename || item.name || `artifact_${index + 1}`;
      const btn = document.createElement('button');
      btn.className = `artifact-tab ${index === 0 ? 'active' : ''}`;
      btn.dataset.filename = filename;
      btn.textContent = filename;
      tabBar.appendChild(btn);
    });
  }

  async selectArtifact(filename) {
    this.activeArtifactFile = filename;

    // Update active tab class
    const tabs = document.querySelectorAll('.artifact-tab');
    tabs.forEach((tab) => {
      if (tab.dataset.filename === filename) {
        tab.classList.add('active');
      } else {
        tab.classList.remove('active');
      }
    });

    const view = document.getElementById('artifact-content-view');
    if (view) {
      view.innerHTML = '<p style="color:var(--text-muted);">Loading artifact content...</p>';
    }

    const cid = this.state.get('activeConversationId');
    if (!cid) return;

    try {
      const data = await this.api.getArtifactContent(cid, filename);
      const rawText = typeof data === 'string' ? data : data.content || '';
      this._currentRawText = rawText;
      if (view) {
        view.innerHTML = this.renderMarkdownWithChecklist(rawText);
      }
      this.computeProgress(rawText);
    } catch (err) {
      if (view) {
        view.innerHTML = `<p style="color:var(--neon-rose);">Failed to load ${filename}: ${err.message}</p>`;
      }
    }
  }

  computeProgress(markdown) {
    const chip = document.getElementById('artifacts-progress-chip');
    if (!chip) return;

    const checkedMatches = markdown.match(/-\s*\[x\]/gi) || [];
    const uncheckedMatches = markdown.match(/-\s*\[\s*\]/gi) || [];
    const total = checkedMatches.length + uncheckedMatches.length;

    if (total > 0) {
      const pct = Math.round((checkedMatches.length / total) * 100);
      chip.textContent = `${checkedMatches.length}/${total} (${pct}%)`;
    } else {
      chip.textContent = '';
    }
  }

  renderMarkdownWithChecklist(text) {
    if (!text) return '<p class="empty-text">Empty artifact.</p>';
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/^### (.*$)/gim, '<h3 style="font-size:13px;color:var(--cyber-cyan);margin:6px 0;">$1</h3>')
      .replace(/^## (.*$)/gim, '<h2 style="font-size:14px;color:var(--cyber-cyan);margin:8px 0;">$1</h2>')
      .replace(/^# (.*$)/gim, '<h1 style="font-size:15px;color:var(--cyber-cyan);margin:10px 0;">$1</h1>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
      .replace(/^\s*-\s+\[ \]\s+(.*$)/gim, '<li class="task-item"><input type="checkbox" disabled> $1</li>')
      .replace(/^\s*-\s+\[x\]\s+(.*$)/gim, '<li class="task-item"><input type="checkbox" checked disabled> <span style="text-decoration:line-through;color:var(--text-muted);">$1</span></li>')
      .replace(/^\s*-\s+(.*$)/gim, '<li style="margin-left:14px;">$1</li>')
      .replace(/\n/g, '<br/>');
  }

  copyCurrentArtifact() {
    if (this._currentRawText && typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(this._currentRawText);
      this.state.addToast({
        type: 'success',
        title: 'Copied',
        message: `${this.activeArtifactFile || 'Artifact'} copied to clipboard!`,
      });
    }
  }

  show() {
    const modal = document.getElementById('modal-artifacts') || document.getElementById('artifacts-drawer');
    if (modal) modal.classList.remove('hidden');
  }

  hide() {
    const modal = document.getElementById('modal-artifacts') || document.getElementById('artifacts-drawer');
    if (modal) modal.classList.add('hidden');
  }
}
