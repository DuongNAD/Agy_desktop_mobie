/**
 * frontend/js/state.js
 * Micro-reactive observable state store & event bus for AgyMobile.
 * Zero external dependencies.
 */

const STORAGE_KEYS = ['authToken', 'activeConversationId', 'theme', 'terminalPreferences'];

export class StateStore {
  constructor(initialState = {}) {
    // 1. Initial Default State Schema
    this._state = {
      // Navigation & Layout
      activeView: 'chat', // 'chat' | 'files' | 'terminal' | 'system'
      sidebarOpen: false,
      keyboardVisible: false,
      keyboardHeight: 0,

      // Authentication & Server Info
      authToken: localStorage.getItem('agymobile_authToken') || null,
      authStatus: localStorage.getItem('agymobile_authToken') ? 'authenticated' : 'unauthenticated',
      isAuthenticated: Boolean(localStorage.getItem('agymobile_authToken')),
      lanIp: '127.0.0.1',
      serverPort: 8765,
      serverInfo: null,

      // Connection & Latency
      connectionStatus: 'DISCONNECTED', // 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED' | 'RECONNECTING' | 'ERROR'
      latency: 0,
      lastPong: 0,

      // Antigravity Sessions & Streaming
      conversations: [],
      activeConversationId: localStorage.getItem('agymobile_activeConversationId') || null,
      activeConversation: null, // { id, title, steps: [], artifacts: [], pending_tool_calls: [], byte_offset: 0 }
      chatStream: {
        isStreaming: false,
        currentThought: '',
        currentContent: '',
        currentStepIndex: null,
      },
      subagents: [], // Subagent hierarchy tree
      pendingApprovals: [], // Tool call requests awaiting user decision
      activeArtifacts: [], // Artifact files list

      // Workspace Files & Editor
      currentPath: '',
      fileTree: [],
      activeFile: null, // { path, name, content, originalContent, mime, isBinary, readOnly, isDirty: false, diff: null }
      fileSearchQuery: '',

      // Terminal & CLI
      terminalSessionId: 'default',
      terminalLogs: [], // Circular buffer of output chunks
      terminalMaxLines: 1000,
      autoScrollTerminal: true,

      // System Telemetry
      telemetry: {
        cpu: 0,
        ram: 0,
        disk: 0,
        uptime: 0,
        tasks: [],
      },

      // UI Notifications & Toasts
      toasts: [],

      ...initialState,
    };

    this._listeners = new Map(); // Property -> Set of callbacks
    this._selectors = new Set(); // Set of { selectorFn, callback, lastValue }
    this._eventHandlers = new Map(); // Event -> Set of handlers
  }

  // --- State Query & Mutation ---

  get(key) {
    return this._state[key];
  }

  getAll() {
    return { ...this._state };
  }

  set(key, value) {
    const oldValue = this._state[key];
    if (oldValue === value && typeof value !== 'object') return;

    this._state[key] = value;
    this._persistIfRequired(key, value);
    this._notifyProperty(key, value, oldValue);
    this._notifySelectors();
  }

  update(key, updaterFn) {
    const currentValue = this.get(key);
    const newValue = updaterFn(currentValue);
    this.set(key, newValue);
  }

  merge(partialState) {
    for (const [key, value] of Object.entries(partialState)) {
      this.set(key, value);
    }
  }

  getToken() {
    return this.get('authToken');
  }

  setToken(token) {
    this.set('authToken', token);
    this.set('isAuthenticated', Boolean(token));
    this.set('authStatus', token ? 'authenticated' : 'unauthenticated');
  }

  // --- Subscriptions ---

  subscribe(key, callback) {
    if (!this._listeners.has(key)) {
      this._listeners.set(key, new Set());
    }
    this._listeners.get(key).add(callback);

    // Initial immediate call with current value
    callback(this.get(key), undefined);

    // Return unbind closure
    return () => {
      const set = this._listeners.get(key);
      if (set) set.delete(callback);
    };
  }

  select(selectorFn, callback) {
    let lastValue = selectorFn(this._state);
    const item = { selectorFn, callback, lastValue };
    this._selectors.add(item);

    // Immediate callback
    callback(lastValue, undefined);

    return () => {
      this._selectors.delete(item);
    };
  }

  // --- Event Bus ---

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
          handler(data, this);
        } catch (e) {
          console.error(`Error in event handler for '${event}':`, e);
        }
      }
    }
  }

  // --- Specialized Action Helpers ---

  addToast(toast) {
    const id = toast.id || `toast_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const newToast = {
      id,
      type: toast.type || 'info',
      title: toast.title || '',
      message: toast.message || '',
      duration: toast.duration !== undefined ? toast.duration : 4000,
    };

    this.update('toasts', (list) => [...list, newToast]);

    if (newToast.duration > 0) {
      setTimeout(() => {
        this.removeToast(id);
      }, newToast.duration);
    }
    return id;
  }

  removeToast(id) {
    this.update('toasts', (list) => list.filter((t) => t.id !== id));
  }

  appendTerminalLog(chunk) {
    this.update('terminalLogs', (logs) => {
      const updated = [...logs, chunk];
      const max = this.get('terminalMaxLines') || 1000;
      return updated.length > max ? updated.slice(updated.length - max) : updated;
    });
  }

  clearTerminalLogs() {
    this.set('terminalLogs', []);
  }

  // --- Private Helpers ---

  _notifyProperty(key, newValue, oldValue) {
    const set = this._listeners.get(key);
    if (set) {
      for (const cb of set) {
        try {
          cb(newValue, oldValue);
        } catch (err) {
          console.error(`Error in state subscription for '${key}':`, err);
        }
      }
    }
  }

  _notifySelectors() {
    for (const item of this._selectors) {
      try {
        const newValue = item.selectorFn(this._state);
        if (newValue !== item.lastValue) {
          const oldVal = item.lastValue;
          item.lastValue = newValue;
          item.callback(newValue, oldVal);
        }
      } catch (err) {
        console.error('Error in selector callback:', err);
      }
    }
  }

  _persistIfRequired(key, value) {
    if (STORAGE_KEYS.includes(key)) {
      try {
        if (value === null || value === undefined) {
          localStorage.removeItem(`agymobile_${key}`);
        } else {
          localStorage.setItem(`agymobile_${key}`, typeof value === 'string' ? value : JSON.stringify(value));
        }
      } catch (e) {
        // Storage quota or private browsing error
      }
    }
  }
}

export const Store = StateStore;
export const state = new StateStore();
