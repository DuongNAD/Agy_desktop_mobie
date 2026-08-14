/**
 * frontend/js/components/subagents.js
 * Subagent Hierarchy Visualizer Tree & Detail Inspector.
 */

export class SubagentsVisualizer {
  constructor(state, api, ws) {
    this.state = state;
    this.api = api;
    this.ws = ws;

    this.init();
  }

  init() {
    this.bindDOMEvents();
    this.subscribeState();
  }

  bindDOMEvents() {
    // Header & Quick Bar Subagent Pill Click
    const btnBadge = document.getElementById('btn-subagent-badge');
    const btnDrawer = document.getElementById('btn-subagents-drawer');

    if (btnBadge) {
      btnBadge.addEventListener('click', () => this.show());
    }
    if (btnDrawer) {
      btnDrawer.addEventListener('click', () => this.show());
    }

    // Close Modal Button
    const btnClose = document.getElementById('btn-close-subagents-modal');
    if (btnClose) {
      btnClose.addEventListener('click', () => this.hide());
    }

    // Back to Tree Button
    const btnBack = document.getElementById('btn-back-to-tree');
    if (btnBack) {
      btnBack.addEventListener('click', () => {
        const detailPanel = document.getElementById('subagent-detail-panel');
        const treeContainer = document.getElementById('subagent-tree-container');
        if (detailPanel) detailPanel.classList.add('hidden');
        if (treeContainer) treeContainer.classList.remove('hidden');
      });
    }
  }

  subscribeState() {
    this.state.subscribe('subagents', (hierarchy) => {
      this.updateSubagentsUI(hierarchy);
    });

    this.ws.on('subagent_status', (data) => {
      if (data && data.hierarchy) {
        this.state.set('subagents', data.hierarchy);
      }
    });

    this.state.subscribe('activeConversationId', (cid) => {
      if (cid) {
        this.loadSubagents(cid);
      }
    });
  }

  async loadSubagents(conversationId) {
    try {
      const data = await this.api.getSubagentTree(conversationId);
      const hierarchy = Array.isArray(data) ? data : (data && data.hierarchy ? data.hierarchy : []);
      this.state.set('subagents', hierarchy);
    } catch (_) {
      // Subagent query fallback
    }
  }

  updateSubagentsUI(hierarchy) {
    const list = hierarchy || [];
    const count = this._countTotalSubagents(list);

    // Update Header Pill
    const badgeBtn = document.getElementById('btn-subagent-badge');
    const countText = document.getElementById('subagent-count-text');
    const quickBadgeCount = document.getElementById('badge-subagents-count');
    const activeChip = document.getElementById('subagents-active-chip');

    if (badgeBtn && countText) {
      if (count > 0) {
        badgeBtn.classList.remove('hidden');
        countText.textContent = `${count} Agent${count > 1 ? 's' : ''}`;
      } else {
        badgeBtn.classList.add('hidden');
      }
    }

    if (quickBadgeCount) {
      quickBadgeCount.textContent = String(count);
    }

    if (activeChip) {
      activeChip.textContent = `${count} Active`;
    }

    // Render tree
    const container = document.getElementById('subagent-tree-container');
    if (container) {
      this.renderTree(list, container);
    }
  }

  _countTotalSubagents(nodes) {
    let count = 0;
    const traverse = (arr) => {
      if (!Array.isArray(arr)) return;
      arr.forEach((node) => {
        count++;
        if (node.children && node.children.length > 0) {
          traverse(node.children);
        }
      });
    };
    traverse(nodes);
    return count;
  }

  renderTree(nodes, container) {
    container.innerHTML = '';
    if (!nodes || nodes.length === 0) {
      container.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">No subagents active for this session.</div>';
      return;
    }

    const ul = document.createElement('ul');
    nodes.forEach((root) => {
      ul.appendChild(this._buildNodeElement(root));
    });
    container.appendChild(ul);
  }

  _buildNodeElement(node) {
    const li = document.createElement('li');
    const card = document.createElement('div');
    card.className = 'subagent-card';

    const role = node.role || 'Agent';
    const status = (node.status || 'active').toLowerCase();
    const title = node.title || node.name || node.id || 'Subagent';
    const steps = node.step_count || 0;

    card.innerHTML = `
      <div class="card-top">
        <div style="display:flex;align-items:center;gap:6px;">
          <span>${this._getRoleIcon(role)}</span>
          <span class="agent-name">${title}</span>
        </div>
        <span class="status-badge status-${status}">${status}</span>
      </div>
      <div class="card-bottom">
        <span>Role: ${role}</span>
        <span>Steps: ${steps}</span>
      </div>
    `;

    card.addEventListener('click', () => {
      this.openSubagentDetail(node.id, title, role, status);
    });

    li.appendChild(card);

    if (node.children && node.children.length > 0) {
      const childUl = document.createElement('ul');
      node.children.forEach((child) => {
        childUl.appendChild(this._buildNodeElement(child));
      });
      li.appendChild(childUl);
    }

    return li;
  }

  _getRoleIcon(role = '') {
    const r = role.toLowerCase();
    if (r.includes('orch')) return '👑';
    if (r.includes('explor')) return '🔍';
    if (r.includes('work')) return '⚙️';
    if (r.includes('chall')) return '🛡️';
    if (r.includes('audit') || r.includes('qa')) return '⚖️';
    return '🤖';
  }

  async openSubagentDetail(subagentId, title, role, status) {
    const detailPanel = document.getElementById('subagent-detail-panel');
    const treeContainer = document.getElementById('subagent-tree-container');
    const nameEl = document.getElementById('detail-agent-name');
    const contentBox = document.getElementById('detail-agent-content');

    if (treeContainer) treeContainer.classList.add('hidden');
    if (detailPanel) detailPanel.classList.remove('hidden');
    if (nameEl) nameEl.textContent = `${title} (${role}) [${status}]`;
    if (contentBox) contentBox.innerHTML = '<div style="color:var(--text-muted);">Loading subagent transcript...</div>';

    try {
      const transcript = await this.api.getTranscript(subagentId);
      const steps = (transcript && transcript.steps) || [];
      if (contentBox) {
        if (steps.length === 0) {
          contentBox.innerHTML = '<div style="color:var(--text-muted);">No transcript records yet.</div>';
        } else {
          contentBox.innerHTML = steps.map((s) => `
            <div style="margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid rgba(255,255,255,0.06);">
              <span style="color:var(--cyber-cyan);font-weight:700;">#${s.step_index || 0} [${s.type || 'step'}]</span>
              <div style="margin-top:2px;color:#f9fafb;white-space:pre-wrap;">${this._sanitize(s.content || JSON.stringify(s.tool_call || ''))}</div>
            </div>
          `).join('');
        }
      }
    } catch (err) {
      if (contentBox) {
        contentBox.innerHTML = `<div style="color:var(--neon-rose);">Failed to load transcript: ${err.message}</div>`;
      }
    }
  }

  _sanitize(text) {
    if (!text) return '';
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  show() {
    const modal = document.getElementById('modal-subagents') || document.getElementById('subagents-drawer');
    if (modal) modal.classList.remove('hidden');
  }

  hide() {
    const modal = document.getElementById('modal-subagents') || document.getElementById('subagents-drawer');
    if (modal) modal.classList.add('hidden');
  }
}
