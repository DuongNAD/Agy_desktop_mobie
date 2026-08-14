/**
 * frontend/js/components/dashboard.js
 * Live Streaming Chat, Thinking Accordions, Markdown Renderer, and Slash Commands.
 */

export class Dashboard {
  constructor(state, api, ws) {
    this.state = state;
    this.api = api;
    this.ws = ws;
    this.activeConversationId = null;
    this.isAutoScrollPinned = true;
    this.activeStreamingNode = null;
    this.activeThinkingNode = null;

    this.init();
  }

  init() {
    this.bindDOMEvents();
    this.subscribeState();
  }

  bindDOMEvents() {
    // Scroll monitoring
    const feed = document.getElementById('chat-stream-container') || document.getElementById('chat-feed');
    if (feed) {
      feed.addEventListener('scroll', () => {
        const distFromBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight;
        this.isAutoScrollPinned = distFromBottom <= 50;
        const scrollBtn = document.getElementById('btn-scroll-bottom');
        if (scrollBtn) {
          if (this.isAutoScrollPinned) {
            scrollBtn.classList.add('hidden');
          } else {
            scrollBtn.classList.remove('hidden');
          }
        }
      });
    }

    // Scroll to bottom button
    const scrollBtn = document.getElementById('btn-scroll-bottom');
    if (scrollBtn) {
      scrollBtn.addEventListener('click', () => {
        this.scrollToBottom(true);
      });
    }

    // Prompt Form & Textarea
    const promptForm = document.getElementById('form-prompt');
    const promptInput = document.getElementById('input-prompt') || document.getElementById('prompt-input');
    const sendBtn = document.getElementById('btn-send-prompt');

    if (promptInput) {
      promptInput.addEventListener('input', () => {
        // Auto-grow
        promptInput.style.height = 'auto';
        promptInput.style.height = Math.min(promptInput.scrollHeight, 120) + 'px';

        const val = promptInput.value;
        if (sendBtn) {
          sendBtn.disabled = val.trim().length === 0;
        }

        // Slash command trigger
        const slashPopup = document.getElementById('slash-popup');
        if (slashPopup) {
          if (val.startsWith('/')) {
            slashPopup.classList.remove('hidden');
          } else {
            slashPopup.classList.add('hidden');
          }
        }
      });

      promptInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.submitPrompt();
        }
      });
    }

    if (promptForm) {
      promptForm.addEventListener('submit', (e) => {
        e.preventDefault();
        this.submitPrompt();
      });
    }

    if (sendBtn) {
      sendBtn.addEventListener('click', (e) => {
        e.preventDefault();
        this.submitPrompt();
      });
    }

    // Slash command quick bar chips
    const slashBar = document.getElementById('slash-commands-bar');
    if (slashBar) {
      slashBar.addEventListener('click', (e) => {
        const chip = e.target.closest('button');
        if (!chip) return;
        const slash = chip.dataset.slash;
        if (slash && promptInput) {
          promptInput.value = slash + ' ';
          promptInput.focus();
          if (sendBtn) sendBtn.disabled = false;
        }
      });
    }

    // Slash command popup list
    const slashPopup = document.getElementById('slash-popup');
    if (slashPopup) {
      slashPopup.addEventListener('click', (e) => {
        const item = e.target.closest('.slash-item');
        if (!item) return;
        const cmd = item.dataset.cmd;
        if (cmd && promptInput) {
          promptInput.value = cmd + ' ';
          promptInput.focus();
          slashPopup.classList.add('hidden');
          if (sendBtn) sendBtn.disabled = false;
        }
      });
    }

    // Session Selector Change
    const convSelect = document.getElementById('select-conversation') || document.getElementById('conv-select');
    if (convSelect) {
      convSelect.addEventListener('change', (e) => {
        const cid = e.target.value;
        if (cid) {
          this.state.set('activeConversationId', cid);
        }
      });
    }

    // Refresh Sessions Button
    const btnRefresh = document.getElementById('btn-refresh-sessions') || document.getElementById('btn-new-conv');
    if (btnRefresh) {
      btnRefresh.addEventListener('click', () => {
        this.loadConversationList();
      });
    }

    // Artifacts Trigger
    const btnArtifacts = document.getElementById('btn-view-artifacts') || document.getElementById('btn-artifacts-drawer');
    if (btnArtifacts) {
      btnArtifacts.addEventListener('click', () => {
        const modal = document.getElementById('modal-artifacts') || document.getElementById('artifacts-drawer');
        if (modal) modal.classList.remove('hidden');
      });
    }
  }

  subscribeState() {
    this.state.subscribe('activeConversationId', (cid) => {
      if (cid && cid !== this.activeConversationId) {
        this.activeConversationId = cid;
        this.loadTranscript(cid);
        this.ws.subscribeSession(cid);
      }
    });

    this.state.subscribe('conversations', (list) => {
      this.renderConversationDropdown(list);
    });

    // Handle WebSocket Stream Events
    this.ws.on('token_stream', (data) => this.handleTokenStream(data));
    this.ws.on('thought_chunk', (data) => this.handleThoughtChunk(data));
    this.ws.on('step_update', (data) => this.handleStepUpdate(data));
  }

  async loadConversationList() {
    try {
      const list = await this.api.listConversations();
      const items = Array.isArray(list) ? list : (list && list.conversations ? list.conversations : []);
      this.state.set('conversations', items);

      if (items.length > 0 && !this.state.get('activeConversationId')) {
        this.state.set('activeConversationId', items[0].id);
      }
    } catch (err) {
      console.warn('Failed to load conversations:', err);
    }
  }

  renderConversationDropdown(conversations) {
    const select = document.getElementById('select-conversation') || document.getElementById('conv-select');
    if (!select) return;

    select.innerHTML = '';
    if (!conversations || conversations.length === 0) {
      select.innerHTML = '<option value="">No active sessions</option>';
      return;
    }

    conversations.forEach((conv) => {
      const opt = document.createElement('option');
      opt.value = conv.id;
      opt.textContent = `${conv.title || conv.id.slice(0, 16)} (${conv.step_count || 0} steps)`;
      if (conv.id === this.state.get('activeConversationId')) {
        opt.selected = true;
      }
      select.appendChild(opt);
    });
  }

  async loadTranscript(conversationId) {
    const container = document.getElementById('chat-messages');
    if (container) {
      container.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">Loading transcript...</div>';
    }

    try {
      const data = await this.api.getTranscript(conversationId);
      this.renderFullTranscript(data);
    } catch (err) {
      if (container) {
        container.innerHTML = `<div style="text-align:center;padding:20px;color:var(--neon-rose);">Failed to load transcript: ${err.message}</div>`;
      }
    }
  }

  renderFullTranscript(data) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    container.innerHTML = '';
    const steps = (data && data.steps) || [];

    steps.forEach((step) => {
      this.appendStepNode(step, container);
    });

    this.scrollToBottom(false);
  }

  appendStepNode(step, container) {
    const type = (step.type || '').toLowerCase();

    if (type === 'user_prompt' || type === 'user') {
      const msg = document.createElement('div');
      msg.className = 'chat-msg user';
      msg.innerHTML = `<div class="chat-msg-header">User</div><div>${this.renderMarkdown(step.content || '')}</div>`;
      container.appendChild(msg);
      this.activeStreamingNode = null;
      this.activeThinkingNode = null;
    } else if (type === 'agent_response' || type === 'assistant' || type === 'agent') {
      const msg = document.createElement('div');
      msg.className = 'chat-msg agent';
      msg.innerHTML = `<div class="chat-msg-header">Agent</div><div class="message-body">${this.renderMarkdown(step.content || '')}</div>`;
      container.appendChild(msg);
      this.activeStreamingNode = null;
      this.activeThinkingNode = null;
    } else if (type === 'thought' || type === 'thinking') {
      const thoughtCard = document.createElement('div');
      thoughtCard.className = 'thought-card';
      thoughtCard.innerHTML = `
        <div class="thought-header">
          <span>🧠 Thought Process</span>
          <span>▼</span>
        </div>
        <div class="thought-body">${this.renderMarkdown(step.content || '')}</div>
      `;
      thoughtCard.querySelector('.thought-header').addEventListener('click', () => {
        const body = thoughtCard.querySelector('.thought-body');
        body.classList.toggle('hidden');
      });
      container.appendChild(thoughtCard);
    } else if (type === 'tool_call' || type === 'tool_call_request') {
      this.state.emit('tool_call_request', { tool_call: step.tool_call || step });
    }
  }

  handleTokenStream(data) {
    const cid = data.conversation_id;
    if (cid && cid !== this.activeConversationId) return;

    const delta = data.delta || '';
    const kind = data.kind || 'content';

    if (kind === 'thinking') {
      this.appendThinkingDelta(delta);
    } else {
      this.appendContentDelta(delta);
    }
    this.autoScrollIfNeeded();
  }

  handleThoughtChunk(data) {
    const delta = data.delta || '';
    this.appendThinkingDelta(delta);
    this.autoScrollIfNeeded();
  }

  appendThinkingDelta(delta) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    if (!this.activeThinkingNode) {
      this.activeThinkingNode = document.createElement('div');
      this.activeThinkingNode.className = 'thought-card';
      this.activeThinkingNode.innerHTML = `
        <div class="thought-header">
          <span>🧠 Thinking...</span>
          <span>▼</span>
        </div>
        <div class="thought-body"></div>
      `;
      this.activeThinkingNode._rawText = '';
      this.activeThinkingNode.querySelector('.thought-header').addEventListener('click', () => {
        const body = this.activeThinkingNode.querySelector('.thought-body');
        body.classList.toggle('hidden');
      });
      container.appendChild(this.activeThinkingNode);
    }

    this.activeThinkingNode._rawText = (this.activeThinkingNode._rawText || '') + delta;
    const body = this.activeThinkingNode.querySelector('.thought-body');
    body.textContent = this.activeThinkingNode._rawText;
  }

  appendContentDelta(delta) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    if (!this.activeStreamingNode) {
      this.activeStreamingNode = document.createElement('div');
      this.activeStreamingNode.className = 'chat-msg agent';
      this.activeStreamingNode.innerHTML = `
        <div class="chat-msg-header">Agent</div>
        <div class="message-body"></div>
      `;
      this.activeStreamingNode._rawText = '';
      container.appendChild(this.activeStreamingNode);
    }

    this.activeStreamingNode._rawText = (this.activeStreamingNode._rawText || '') + delta;
    const body = this.activeStreamingNode.querySelector('.message-body');
    body.innerHTML = this.renderMarkdown(this.activeStreamingNode._rawText);
  }

  handleStepUpdate(data) {
    const step = data.step;
    if (!step) return;

    // Reset active stream nodes on completed step
    this.activeStreamingNode = null;
    this.activeThinkingNode = null;
  }

  async submitPrompt() {
    const promptInput = document.getElementById('input-prompt') || document.getElementById('prompt-input');
    if (!promptInput) return;

    const text = promptInput.value.trim();
    if (!text) return;

    // Clear input
    promptInput.value = '';
    promptInput.style.height = 'auto';
    const sendBtn = document.getElementById('btn-send-prompt');
    if (sendBtn) sendBtn.disabled = true;

    // Handle local clear slash command
    if (text === '/clear') {
      const container = document.getElementById('chat-messages');
      if (container) container.innerHTML = '';
      return;
    }

    const cid = this.state.get('activeConversationId');
    if (!cid) {
      this.state.addToast({
        type: 'warning',
        title: 'No Active Session',
        message: 'Please select a session before sending a prompt.',
      });
      return;
    }

    // Append User Bubble locally for instant responsiveness
    const container = document.getElementById('chat-messages');
    if (container) {
      const userMsg = document.createElement('div');
      userMsg.className = 'chat-msg user';
      userMsg.innerHTML = `<div class="chat-msg-header">User</div><div>${this.renderMarkdown(text)}</div>`;
      container.appendChild(userMsg);
      this.scrollToBottom(true);
    }

    try {
      if (this.ws && this.ws.isConnected()) {
        this.ws.sendPrompt(cid, text);
      } else {
        await this.api.sendPrompt(cid, text);
      }
    } catch (err) {
      this.state.addToast({
        type: 'error',
        title: 'Prompt Dispatch Failed',
        message: err.message || 'Unable to send prompt',
      });
    }
  }

  renderMarkdown(text) {
    if (!text) return '';
    return text
      // Escape HTML entities
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      // Code blocks with syntax copy
      .replace(/```([a-zA-Z0-9_\-\+]*)\n([\s\S]*?)```/g, (match, lang, code) => {
        const encoded = encodeURIComponent(code);
        return `<div class="code-block-container">
          <div class="code-header">
            <span>${lang || 'code'}</span>
            <button class="btn-copy-code" onclick="navigator.clipboard.writeText(decodeURIComponent('${encoded}'))">Copy</button>
          </div>
          <pre style="padding:8px;font-family:var(--font-mono);font-size:12px;overflow-x:auto;color:#4ade80;"><code>${code}</code></pre>
        </div>`;
      })
      // Inline code
      .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
      // Headings
      .replace(/^### (.*$)/gim, '<h3 style="font-size:14px;margin:6px 0;color:var(--cyber-cyan);">$1</h3>')
      .replace(/^## (.*$)/gim, '<h2 style="font-size:15px;margin:8px 0;color:var(--cyber-cyan);">$1</h2>')
      .replace(/^# (.*$)/gim, '<h1 style="font-size:16px;margin:10px 0;color:var(--cyber-cyan);">$1</h1>')
      // Bold & Italic
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      // Task lists
      .replace(/^\s*-\s+\[ \]\s+(.*$)/gim, '<li class="task-item"><input type="checkbox" disabled> $1</li>')
      .replace(/^\s*-\s+\[x\]\s+(.*$)/gim, '<li class="task-item"><input type="checkbox" checked disabled> $1</li>')
      .replace(/^\s*-\s+(.*$)/gim, '<li style="margin-left:14px;">$1</li>')
      // Line breaks
      .replace(/\n/g, '<br/>');
  }

  scrollToBottom(smooth = false) {
    const feed = document.getElementById('chat-stream-container') || document.getElementById('chat-feed');
    if (feed) {
      feed.scrollTo({ top: feed.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
      this.isAutoScrollPinned = true;
      const scrollBtn = document.getElementById('btn-scroll-bottom');
      if (scrollBtn) scrollBtn.classList.add('hidden');
    }
  }

  autoScrollIfNeeded() {
    if (this.isAutoScrollPinned) {
      this.scrollToBottom(false);
    }
  }
}
