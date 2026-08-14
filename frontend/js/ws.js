/**
 * frontend/js/ws.js
 * High-resilience, multi-channel WebSocket client & multiplexer for AgyMobile.
 * Features 15s ping/pong heartbeat, exponential backoff (1s -> 15s), and state re-sync.
 */

import { state as defaultState } from './state.js';

export class WebSocketMultiplexer {
  constructor(stateInstance = null) {
    this.state = stateInstance || defaultState;
    this.sessionWs = null;
    this.terminalWs = null;

    // Reconnection Settings
    this.sessionAttempts = 0;
    this.terminalAttempts = 0;
    this.maxReconnectDelay = 15000;
    this.baseReconnectDelay = 1000;

    // Heartbeat Timers
    this.sessionPingInterval = null;
    this.sessionPongTimeout = null;
    this.lastPingTimestamp = 0;

    // Active session stream sync state
    this.lastByteOffset = 0;

    // Local Event Handlers
    this._eventHandlers = new Map();

    // Bind browser lifecycle events
    if (typeof window !== 'undefined') {
      this._bindLifecycleEvents();
    }
  }

  // --- Event Subscriptions ---

  on(event, handler) {
    if (!this._eventHandlers.has(event)) {
      this._eventHandlers.set(event, new Set());
    }
    this._eventHandlers.get(event).add(handler);
    return () => this.off(event, handler);
  }

  off(event, handler) {
    const set = this._eventHandlers.get(event);
    if (set) set.delete(handler);
  }

  emit(event, data) {
    const set = this._eventHandlers.get(event);
    if (set) {
      for (const handler of set) {
        try {
          handler(data);
        } catch (e) {
          console.error(`Error in WS event handler for '${event}':`, e);
        }
      }
    }
  }

  isConnected() {
    return Boolean(this.sessionWs && this.sessionWs.readyState === WebSocket.OPEN);
  }

  connect(token = null) {
    if (token) {
      this.state.set('authToken', token);
    }
    this.connectSession();
  }

  // =====================================================================
  // 1. Session Channel Multiplexer (/ws/session)
  // =====================================================================

  connectSession() {
    const token = this.state.get('authToken');
    if (!token) {
      console.warn('WS Session: Cannot connect without authToken.');
      return;
    }

    if (this.sessionWs && (this.sessionWs.readyState === WebSocket.OPEN || this.sessionWs.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.state.set('connectionStatus', 'CONNECTING');
    const wsUrl = this._resolveWsUrl(`/ws/session?token=${encodeURIComponent(token)}`);

    try {
      this.sessionWs = new WebSocket(wsUrl);

      this.sessionWs.onopen = () => {
        console.log('WS Session: Connected successfully.');
        this.sessionAttempts = 0;
        this.state.set('connectionStatus', 'CONNECTED');
        this._startSessionHeartbeat();
        this.emit('connected', { channel: 'session' });

        // State Re-sync: Resubscribe to active conversation if selected
        const activeCid = this.state.get('activeConversationId');
        if (activeCid) {
          this.subscribeSession(activeCid, this.lastByteOffset);
        }
      };

      this.sessionWs.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          this._handleSessionMessage(msg);
        } catch (e) {
          console.error('WS Session: Failed to parse message JSON:', e);
        }
      };

      this.sessionWs.onclose = (event) => {
        console.warn(`WS Session: Closed (code: ${event.code}).`);
        this._cleanupSessionHeartbeat();
        this.emit('disconnected', { channel: 'session', code: event.code });
        if (event.code === 1008) {
          this.state.set('authStatus', 'unauthenticated');
          this.state.set('isAuthenticated', false);
          this.state.set('connectionStatus', 'DISCONNECTED');
        } else {
          this._scheduleSessionReconnect();
        }
      };

      this.sessionWs.onerror = (err) => {
        console.error('WS Session Error:', err);
        if (this.sessionWs) this.sessionWs.close();
      };
    } catch (e) {
      console.error('WS Session: Connection initialization error:', e);
      this._scheduleSessionReconnect();
    }
  }

  disconnectSession() {
    this._cleanupSessionHeartbeat();
    if (this.sessionWs) {
      this.sessionWs.close();
      this.sessionWs = null;
    }
    this.state.set('connectionStatus', 'DISCONNECTED');
  }

  subscribeSession(conversationId, fromOffset = 0) {
    if (!conversationId) return;
    this.sendSessionAction({
      action: 'subscribe',
      conversation_id: conversationId,
      from_offset: fromOffset,
    });
  }

  unsubscribeSession(conversationId) {
    if (!conversationId) return;
    this.sendSessionAction({
      action: 'unsubscribe',
      conversation_id: conversationId,
    });
  }

  sendPrompt(conversationId, prompt, mode = 'accept-edits') {
    this.sendSessionAction({
      action: 'send_prompt',
      conversation_id: conversationId,
      prompt,
      mode,
    });
  }

  sendToolDecision(conversationId, toolCallId, decision, pattern = null) {
    this.sendSessionAction({
      action: 'tool_decision',
      conversation_id: conversationId,
      tool_call_id: toolCallId,
      decision,
      pattern,
    });
  }

  send(type, payload = {}) {
    if (this.sessionWs && this.sessionWs.readyState === WebSocket.OPEN) {
      if (typeof payload === 'object') {
        this.sessionWs.send(JSON.stringify({ action: type, type, ...payload }));
      } else {
        this.sessionWs.send(JSON.stringify({ action: type, type, data: payload }));
      }
    }
  }

  sendSessionAction(actionPayload) {
    if (this.sessionWs && this.sessionWs.readyState === WebSocket.OPEN) {
      this.sessionWs.send(JSON.stringify(actionPayload));
    } else {
      console.warn('WS Session: Socket not open, message dropped or queued.');
    }
  }

  // --- Session Event Message Dispatcher ---

  _handleSessionMessage(msg) {
    const type = msg.type || msg.action;

    if (type === 'pong' || type === 'system:pong') {
      clearTimeout(this.sessionPongTimeout);
      const rtt = Date.now() - this.lastPingTimestamp;
      this.state.set('latency', rtt);
      this.state.set('lastPong', Date.now());
      return;
    }

    if (msg.byte_offset !== undefined) {
      this.lastByteOffset = msg.byte_offset;
    }

    this.emit(type, msg);

    switch (type) {
      case 'subscribed':
        console.log(`WS Session: Subscribed to conversation ${msg.conversation_id}`);
        break;

      case 'step_update':
        this._onStepUpdate(msg);
        break;

      case 'token_stream':
        this._onTokenStream(msg);
        break;

      case 'thought_chunk':
        this._onThoughtChunk(msg);
        break;

      case 'tool_call_request':
        this._onToolCallRequest(msg);
        break;

      case 'subagent_status':
        this._onSubagentStatus(msg);
        break;

      case 'prompt_dispatched':
      case 'tool_decision_ack':
        this.state.emit(type, msg);
        break;

      case 'error':
        this.state.addToast({
          type: 'error',
          title: 'Session Stream Error',
          message: msg.message || 'Unknown stream error',
        });
        break;

      default:
        this.state.emit(type, msg);
    }
  }

  _onStepUpdate(msg) {
    const step = msg.step;
    if (step) {
      this.state.update('activeConversation', (curr) => {
        if (!curr) return { steps: [step] };
        const steps = [...(curr.steps || [])];
        const existingIdx = steps.findIndex((s) => s.step_index === step.step_index);
        if (existingIdx !== -1) {
          steps[existingIdx] = step;
        } else {
          steps.push(step);
        }
        return { ...curr, steps };
      });
    }
    this.state.emit('step_update', msg);
  }

  _onTokenStream(msg) {
    const delta = msg.delta || '';
    const kind = msg.kind || 'content';

    this.state.update('chatStream', (stream) => {
      if (kind === 'thinking') {
        return {
          ...stream,
          isStreaming: true,
          currentThought: (stream.currentThought || '') + delta,
        };
      } else {
        return {
          ...stream,
          isStreaming: true,
          currentContent: (stream.currentContent || '') + delta,
        };
      }
    });
    this.state.emit('token_stream', msg);
  }

  _onThoughtChunk(msg) {
    const delta = msg.delta || '';
    this.state.update('chatStream', (stream) => ({
      ...stream,
      isStreaming: true,
      currentThought: (stream.currentThought || '') + delta,
    }));
  }

  _onToolCallRequest(msg) {
    const toolCall = msg.tool_call;
    if (toolCall) {
      this.state.update('pendingApprovals', (approvals) => {
        const exists = approvals.some((a) => a.id === toolCall.id);
        return exists ? approvals : [...approvals, toolCall];
      });

      if (typeof navigator !== 'undefined' && 'vibrate' in navigator) {
        navigator.vibrate([80, 40, 80]);
      }

      this.state.addToast({
        type: 'warning',
        title: 'Tool Approval Required',
        message: `Agent requests permission to run ${toolCall.name || 'tool'}`,
      });
    }
    this.state.emit('tool_call_request', msg);
  }

  _onSubagentStatus(msg) {
    this.state.set('subagents', msg.hierarchy || []);
    this.state.emit('subagent_status', msg);
  }

  // --- Session Heartbeat & Reconnection ---

  _startSessionHeartbeat() {
    this._cleanupSessionHeartbeat();
    this.sessionPingInterval = setInterval(() => {
      if (this.sessionWs && this.sessionWs.readyState === WebSocket.OPEN) {
        this.lastPingTimestamp = Date.now();
        this.sessionWs.send(JSON.stringify({ type: 'ping' }));

        this.sessionPongTimeout = setTimeout(() => {
          console.warn('WS Session: Heartbeat missed pong timeout. Force closing.');
          if (this.sessionWs) this.sessionWs.close();
        }, 5000);
      }
    }, 15000);
  }

  _cleanupSessionHeartbeat() {
    clearInterval(this.sessionPingInterval);
    clearTimeout(this.sessionPongTimeout);
  }

  _scheduleSessionReconnect() {
    this.state.set('connectionStatus', 'RECONNECTING');
    this.sessionAttempts++;
    const delay = Math.min(
      this.baseReconnectDelay * Math.pow(1.5, this.sessionAttempts) + Math.random() * 500,
      this.maxReconnectDelay
    );
    console.log(`WS Session: Reconnecting in ${Math.round(delay)}ms (attempt ${this.sessionAttempts})...`);
    setTimeout(() => this.connectSession(), delay);
  }

  // =====================================================================
  // 2. Terminal Channel Multiplexer (/ws/terminal)
  // =====================================================================

  connectTerminal(sessionId = 'default', cols = 80, rows = 24) {
    const token = this.state.get('authToken');
    if (!token) return;

    if (this.terminalWs && (this.terminalWs.readyState === WebSocket.OPEN || this.terminalWs.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const query = new URLSearchParams({
      token,
      session_id: sessionId,
      cols: String(cols),
      rows: String(rows),
    });
    const wsUrl = this._resolveWsUrl(`/ws/terminal?${query.toString()}`);

    try {
      this.terminalWs = new WebSocket(wsUrl);

      this.terminalWs.onopen = () => {
        console.log('WS Terminal: Connected.');
        this.terminalAttempts = 0;
        this.emit('terminal:connected');
        this.state.emit('terminal:connected');
      };

      this.terminalWs.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'output' && msg.data) {
            this.state.appendTerminalLog(msg.data);
            this.emit('terminal_output', msg.data);
            this.state.emit('terminal:output', msg.data);
          }
        } catch (e) {
          this.state.appendTerminalLog(event.data);
          this.emit('terminal_output', event.data);
          this.state.emit('terminal:output', event.data);
        }
      };

      this.terminalWs.onclose = () => {
        console.warn('WS Terminal: Disconnected.');
        this.emit('terminal:disconnected');
        this._scheduleTerminalReconnect();
      };

      this.terminalWs.onerror = (err) => {
        console.error('WS Terminal Error:', err);
        if (this.terminalWs) this.terminalWs.close();
      };
    } catch (e) {
      console.error('WS Terminal: Failed to connect:', e);
      this._scheduleTerminalReconnect();
    }
  }

  sendTerminalInput(data) {
    if (this.terminalWs && this.terminalWs.readyState === WebSocket.OPEN) {
      this.terminalWs.send(JSON.stringify({ type: 'input', data }));
    }
  }

  sendTerminalResize(cols, rows) {
    if (this.terminalWs && this.terminalWs.readyState === WebSocket.OPEN) {
      this.terminalWs.send(JSON.stringify({ type: 'resize', cols, rows }));
    }
  }

  sendTerminalInterrupt() {
    if (this.terminalWs && this.terminalWs.readyState === WebSocket.OPEN) {
      this.terminalWs.send(JSON.stringify({ type: 'interrupt' }));
    }
  }

  sendTerminalClear() {
    if (this.terminalWs && this.terminalWs.readyState === WebSocket.OPEN) {
      this.terminalWs.send(JSON.stringify({ type: 'clear' }));
      this.state.clearTerminalLogs();
    }
  }

  disconnectTerminal() {
    if (this.terminalWs) {
      this.terminalWs.close();
      this.terminalWs = null;
    }
  }

  _scheduleTerminalReconnect() {
    this.terminalAttempts++;
    const delay = Math.min(
      this.baseReconnectDelay * Math.pow(1.5, this.terminalAttempts) + Math.random() * 500,
      this.maxReconnectDelay
    );
    setTimeout(() => {
      if (this.state.get('activeView') === 'terminal') {
        this.connectTerminal();
      }
    }, delay);
  }

  // =====================================================================
  // 3. Helper & Lifecycle Utilities
  // =====================================================================

  _resolveWsUrl(path) {
    if (typeof window === 'undefined') return path;
    const loc = window.location;
    const protocol = loc.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = loc.host || '127.0.0.1:8765';
    return `${protocol}//${host}${path.startsWith('/') ? path : `/${path}`}`;
  }

  _bindLifecycleEvents() {
    window.addEventListener('online', () => {
      console.log('Browser online event. Triggering fast reconnect.');
      this.sessionAttempts = 0;
      this.terminalAttempts = 0;
      this.connectSession();
      if (this.state.get('activeView') === 'terminal') {
        this.connectTerminal();
      }
    });

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        if (!this.sessionWs || this.sessionWs.readyState !== WebSocket.OPEN) {
          this.connectSession();
        }
        if (this.state.get('activeView') === 'terminal' && (!this.terminalWs || this.terminalWs.readyState !== WebSocket.OPEN)) {
          this.connectTerminal();
        }
      }
    });
  }
}

export const WebSocketClient = WebSocketMultiplexer;
export const ws = new WebSocketMultiplexer();
