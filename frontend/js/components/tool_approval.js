/**
 * frontend/js/components/tool_approval.js
 * Interactive Real-Time Tool Call Approval/Rejection Cards.
 */

export class ToolApprovalManager {
  constructor(state, api, ws) {
    this.state = state;
    this.api = api;
    this.ws = ws;

    this.init();
  }

  init() {
    this.subscribeState();
    this.bindDOMEvents();
  }

  subscribeState() {
    this.state.on('tool_call_request', (data) => {
      const toolCall = data.tool_call || data;
      if (toolCall) {
        this.renderToolCallCard(toolCall);
      }
    });

    this.ws.on('tool_call_request', (data) => {
      const toolCall = data.tool_call || data;
      if (toolCall) {
        this.renderToolCallCard(toolCall);
      }
    });
  }

  bindDOMEvents() {
    // Event delegation on chat messages for tool actions
    const container = document.getElementById('chat-messages');
    if (container) {
      container.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-tool-action]');
        if (!btn) return;

        const action = btn.dataset.toolAction;
        const callId = btn.dataset.callId;
        const cid = btn.dataset.cid || this.state.get('activeConversationId');

        if (action && callId && cid) {
          this.submitDecision(cid, callId, action);
        }
      });
    }
  }

  renderToolCallCard(toolCall) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    const callId = toolCall.id || `call_${Date.now()}`;
    const name = toolCall.name || toolCall.tool || 'Command / Action';
    const args = toolCall.args || toolCall.parameters || {};
    const argsString = typeof args === 'string' ? args : JSON.stringify(args, null, 2);
    const cid = this.state.get('activeConversationId') || '';

    // Check if card already exists
    if (document.getElementById(`tool-card-${callId}`)) return;

    const card = document.createElement('div');
    card.id = `tool-card-${callId}`;
    card.className = 'tool-approval-card';
    card.innerHTML = `
      <div class="tool-header">
        <span class="tool-badge">⚠️ TOOL CALL: ${this._escape(name)}</span>
        <span class="status-badge status-running" style="font-size:10px;">APPROVAL NEEDED</span>
      </div>
      <div class="tool-payload-box">
        <pre style="font-family:var(--font-mono);font-size:11px;white-space:pre-wrap;word-break:break-all;"><code>${this._escape(argsString)}</code></pre>
      </div>
      <div class="tool-action-group">
        <button class="btn-tool-approve" data-tool-action="approve" data-call-id="${callId}" data-cid="${cid}">✅ Approve</button>
        <button class="btn-grant-session" data-tool-action="grant-session" data-call-id="${callId}" data-cid="${cid}">🛡️ Grant Session</button>
        <button class="btn-tool-reject" data-tool-action="reject" data-call-id="${callId}" data-cid="${cid}">❌ Reject</button>
      </div>
    `;

    container.appendChild(card);
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    // Haptic vibration
    if (typeof navigator !== 'undefined' && 'vibrate' in navigator) {
      try {
        navigator.vibrate([80, 40, 80]);
      } catch (_) {}
    }
  }

  async submitDecision(conversationId, callId, action) {
    const card = document.getElementById(`tool-card-${callId}`);
    if (card) {
      card.querySelectorAll('button').forEach((b) => (b.disabled = true));
    }

    const decision = action === 'grant-session' ? 'approve' : action;
    const pattern = action === 'grant-session' ? '*' : null;

    try {
      if (this.ws && this.ws.isConnected()) {
        this.ws.sendToolDecision(conversationId, callId, decision, pattern);
      } else {
        await this.api.approveToolDecision(conversationId, callId, decision, pattern);
      }

      if (card) {
        card.innerHTML = `
          <div style="padding:8px;text-align:center;font-family:var(--font-mono);font-size:12px;color:${decision === 'approve' ? 'var(--neon-green)' : 'var(--neon-rose)'};">
            Decision: ${action.toUpperCase()} sent
          </div>
        `;
        setTimeout(() => {
          if (card.parentNode) card.parentNode.removeChild(card);
        }, 2000);
      }

      this.state.addToast({
        type: 'info',
        title: 'Tool Decision',
        message: `Tool action ${action} dispatched.`,
      });
    } catch (err) {
      if (card) {
        card.querySelectorAll('button').forEach((b) => (b.disabled = false));
      }
      this.state.addToast({
        type: 'error',
        title: 'Approval Error',
        message: err.message || 'Failed to dispatch decision',
      });
    }
  }

  _escape(text) {
    if (!text) return '';
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
}
