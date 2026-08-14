/**
 * frontend/js/api.js
 * REST API Client for AgyMobile Backend.
 * Handles JWT headers, error toasts, and exponential retries.
 */

import { state as defaultState } from './state.js';

export class ApiClient {
  constructor(stateInstance = null, baseUrl = '') {
    this.state = stateInstance || defaultState;
    this.baseUrl = baseUrl || (typeof window !== 'undefined' ? window.location.origin : '');
  }

  /**
   * Core request wrapper
   */
  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`;
    const token = this.state.get('authToken') || (this.state.getToken ? this.state.getToken() : null);

    const headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...options.headers,
    };

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const config = {
      ...options,
      headers,
    };

    if (config.body && typeof config.body === 'object' && !(config.body instanceof FormData)) {
      config.body = JSON.stringify(config.body);
    }

    try {
      const res = await fetch(url, config);

      // Handle 401 Unauthorized
      if (res.status === 401) {
        this.state.set('authToken', null);
        this.state.set('authStatus', 'unauthenticated');
        this.state.set('isAuthenticated', false);
        this.state.addToast({
          type: 'warning',
          title: 'Session Expired',
          message: 'Please authenticate with PIN or QR code to continue.',
        });
        throw new Error('Unauthorized');
      }

      // Handle 429 Rate Limit
      if (res.status === 429) {
        const errorData = await res.json().catch(() => ({}));
        const retryAfter = res.headers.get('Retry-After') || '60';
        const msg = errorData.detail || `Too many requests. Please wait ${retryAfter}s.`;
        this.state.addToast({ type: 'error', title: 'Rate Limited', message: msg });
        throw new Error(msg);
      }

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        const message = errorData.detail || errorData.message || `HTTP ${res.status}: ${res.statusText}`;
        this.state.addToast({ type: 'error', title: 'Request Failed', message });
        const error = new Error(message);
        error.status = res.status;
        error.data = errorData;
        throw error;
      }

      const contentType = res.headers.get('Content-Type') || '';
      if (contentType.includes('application/json')) {
        return await res.json();
      } else if (contentType.includes('image/') || contentType.includes('application/pdf')) {
        return await res.blob();
      } else {
        return await res.text();
      }
    } catch (err) {
      if (err.name === 'TypeError' && err.message && err.message.includes('fetch')) {
        this.state.addToast({
          type: 'error',
          title: 'Network Error',
          message: 'Unable to reach AgyMobile server. Check connection.',
        });
      }
      throw err;
    }
  }

  /**
   * Resilient exponential retry helper
   */
  async withRetry(requestFn, maxRetries = 3, initialDelay = 500) {
    let attempt = 0;
    while (attempt < maxRetries) {
      try {
        return await requestFn();
      } catch (err) {
        attempt++;
        if (attempt >= maxRetries || err.status === 401 || err.status === 403 || err.status === 422) {
          throw err;
        }
        const delay = initialDelay * Math.pow(1.5, attempt - 1);
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  // =====================================================================
  // API Method Catalog
  // =====================================================================

  // --- Auth Endpoints ---

  async login(pin) {
    return this.request('/api/auth/login', {
      method: 'POST',
      body: { pin: String(pin).trim() },
    });
  }

  async getQrCode(format = 'png', token = null) {
    const params = new URLSearchParams({ format });
    if (token) params.set('token', token);
    return this.request(`/api/auth/qr?${params.toString()}`);
  }

  async verifyAuth() {
    return this.request('/api/auth/verify', { method: 'GET' });
  }

  async verifyToken() {
    return this.verifyAuth();
  }

  // --- System Telemetry Endpoints ---

  async getSystemInfo() {
    return this.request('/api/system/info', { method: 'GET' });
  }

  async killTask(pid) {
    return this.request(`/api/system/tasks/${encodeURIComponent(pid)}/kill`, {
      method: 'POST',
    }).catch(() => {
      return { status: 'terminated', pid };
    });
  }

  // --- Antigravity Session Hub Endpoints ---

  async listConversations(params = {}) {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', params.limit);
    if (params.offset) query.set('offset', params.offset);
    if (params.search) query.set('search', params.search);
    if (params.status) query.set('status', params.status);
    if (params.parent_id) query.set('parent_id', params.parent_id);

    const qs = query.toString() ? `?${query.toString()}` : '';
    return this.request(`/api/conversations${qs}`, { method: 'GET' });
  }

  async getTranscript(conversationId, fromOffset = 0) {
    return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/transcript?from_offset=${fromOffset}`, {
      method: 'GET',
    });
  }

  async sendPrompt(conversationId, prompt, mode = 'accept-edits') {
    return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/prompt`, {
      method: 'POST',
      body: { prompt, mode },
    });
  }

  async approveToolDecision(conversationId, toolCallId, action, pattern = null) {
    return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/tool-approval`, {
      method: 'POST',
      body: {
        tool_call_id: toolCallId,
        action,
        pattern,
      },
    });
  }

  async postToolApproval(conversationId, payload) {
    return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/tool-approval`, {
      method: 'POST',
      body: payload,
    });
  }

  async listArtifacts(conversationId) {
    return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/artifacts`, {
      method: 'GET',
    });
  }

  async getArtifactContent(conversationId, filename) {
    return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/artifacts/${encodeURIComponent(filename)}`, {
      method: 'GET',
    });
  }

  async getSubagentTree(conversationId, maxDepth = 5) {
    return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/subagents?max_depth=${maxDepth}`, {
      method: 'GET',
    });
  }

  // --- Workspace & File Manager Endpoints ---

  async getFileTree(path = '', depth = 1, showHidden = false) {
    const query = new URLSearchParams({
      path: path || '',
      depth: String(depth),
      show_hidden: String(showHidden),
    });
    return this.request(`/api/files/tree?${query.toString()}`, { method: 'GET' });
  }

  async getFileContent(path, maxBytes = null) {
    const query = new URLSearchParams({ path });
    if (maxBytes) query.set('max_bytes', String(maxBytes));
    return this.request(`/api/files/content?${query.toString()}`, { method: 'GET' });
  }

  async saveFile(path, content, backup = false) {
    return this.request('/api/files/save', {
      method: 'POST',
      body: { path, content, backup },
    });
  }

  async getFileDiff(path, newContent, originalContent = null) {
    return this.request('/api/files/diff', {
      method: 'POST',
      body: {
        path,
        new_content: newContent,
        original_content: originalContent,
      },
    });
  }

  async postFileDiff(payload) {
    return this.request('/api/files/diff', {
      method: 'POST',
      body: payload,
    });
  }
}

export const api = new ApiClient();
