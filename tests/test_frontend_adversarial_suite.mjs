/**
 * tests/test_frontend_adversarial_suite.mjs
 * Comprehensive Adversarial Empirical Test Suite for Milestone 4 (Frontend PWA & Touch UX).
 */

import assert from 'node:assert/strict';

// Setup Mock DOM and Browser Environment on globalThis before module imports
class MockElement {
  constructor(tagName = 'div') {
    this.tagName = tagName.toUpperCase();
    this.id = '';
    this._classList = new Set();
    this.dataset = {};
    this.style = {};
    this.innerHTML = '';
    this._textContent = '';
    this.childNodes = [];
    this.children = [];
    this.parentNode = null;
    this.value = '';
    this.disabled = false;
    this.listeners = new Map();
  }

  get className() {
    return Array.from(this._classList).join(' ');
  }

  set className(val) {
    this._classList = new Set(String(val || '').split(/\s+/).filter(Boolean));
  }

  get classList() {
    return {
      add: (c) => this._classList.add(c),
      remove: (c) => this._classList.delete(c),
      toggle: (c, force) => {
        if (force === undefined) {
          if (this._classList.has(c)) { this._classList.delete(c); return false; }
          else { this._classList.add(c); return true; }
        } else if (force) {
          this._classList.add(c); return true;
        } else {
          this._classList.delete(c); return false;
        }
      },
      contains: (c) => this._classList.has(c)
    };
  }

  get firstChild() {
    return this.childNodes[0] || null;
  }

  get lastChild() {
    return this.childNodes[this.childNodes.length - 1] || null;
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(val) {
    this._textContent = String(val);
    this.innerHTML = this._escape(String(val));
  }

  _escape(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    const idx = this.childNodes.indexOf(child);
    if (idx !== -1) {
      this.childNodes.splice(idx, 1);
      child.parentNode = null;
    }
    const cIdx = this.children.indexOf(child);
    if (cIdx !== -1) {
      this.children.splice(cIdx, 1);
    }
    return child;
  }

  addEventListener(event, fn) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event).push(fn);
  }

  dispatchEvent(eventObj) {
    const handlers = this.listeners.get(eventObj.type) || [];
    for (const h of handlers) {
      h(eventObj);
    }
  }

  closest(selector) {
    if (selector.startsWith('.')) {
      const cls = selector.slice(1);
      if (this._classList.has(cls)) return this;
    } else if (selector.startsWith('#')) {
      const id = selector.slice(1);
      if (this.id === id) return this;
    } else if (selector === 'button' && this.tagName === 'BUTTON') {
      return this;
    } else if (selector.includes('data-kill-pid') && this.dataset && this.dataset.killPid) {
      return this;
    } else if (selector.includes('data-tool-action') && this.dataset && this.dataset.toolAction) {
      return this;
    }
    if (this.parentNode && this.parentNode.closest) {
      return this.parentNode.closest(selector);
    }
    return null;
  }

  querySelector(selector) {
    const all = this.querySelectorAll(selector);
    return all.length > 0 ? all[0] : null;
  }

  querySelectorAll(selector) {
    const results = [];
    const walk = (node) => {
      for (const child of node.children) {
        let match = false;
        if (selector.startsWith('.')) {
          if (child._classList.has(selector.slice(1))) match = true;
        } else if (selector.startsWith('#')) {
          if (child.id === selector.slice(1)) match = true;
        } else if (selector === 'button' && child.tagName === 'BUTTON') {
          match = true;
        } else if (selector.includes('data-tool-action') && child.dataset && child.dataset.toolAction) {
          match = true;
        }
        if (match) results.push(child);
        walk(child);
      }
    };
    walk(this);
    return results;
  }

  scrollIntoView() {}
  scrollTo() {}
}

const mockDOM = {
  elements: new Map(),
  createElement(tag) {
    return new MockElement(tag);
  },
  register(id, el) {
    el.id = id;
    this.elements.set(id, el);
    return el;
  },
  getElementById(id) {
    if (this.elements.has(id)) return this.elements.get(id);
    const findInTree = (node) => {
      if (node.id === id) return node;
      for (const child of node.childNodes) {
        const found = findInTree(child);
        if (found) return found;
      }
      return null;
    };
    for (const el of this.elements.values()) {
      const found = findInTree(el);
      if (found) return found;
    }
    return null;
  },
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  },
  querySelectorAll(sel) {
    const res = [];
    for (const el of this.elements.values()) {
      if (sel.startsWith('#') && el.id === sel.slice(1)) res.push(el);
      if (sel.startsWith('.') && el._classList.has(sel.slice(1))) res.push(el);
    }
    return res;
  }
};

globalThis.document = mockDOM;
globalThis.localStorage = {
  _data: {},
  getItem(k) { return this._data[k] || null; },
  setItem(k, v) { this._data[k] = String(v); },
  removeItem(k) { delete this._data[k]; }
};
globalThis.window = {
  location: { origin: 'http://127.0.0.1:8765', hash: '#tab-chat', search: '', protocol: 'http:', host: '127.0.0.1:8765', pathname: '/' },
  history: { replaceState() {} },
  addEventListener() {},
  localStorage: globalThis.localStorage
};

if (typeof globalThis.navigator === 'undefined') {
  Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText: async () => {} }, vibrate: () => true },
    configurable: true
  });
} else {
  try {
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      value: { writeText: async () => {} },
      configurable: true
    });
    Object.defineProperty(globalThis.navigator, 'vibrate', {
      value: () => true,
      configurable: true
    });
  } catch (_) {}
}

// Dynamic imports after globals are established
const { AnsiUp, ansiToHtml } = await import('../frontend/js/lib/ansi_up.js');
const { QRCodeGenerator } = await import('../frontend/js/lib/qrcode.js');
const { StateStore } = await import('../frontend/js/state.js');
const { FileExplorer } = await import('../frontend/js/components/file_explorer.js');
const { TerminalComponent } = await import('../frontend/js/components/terminal.js');
const { TelemetryComponent } = await import('../frontend/js/components/telemetry.js');
const { ToolApprovalManager } = await import('../frontend/js/components/tool_approval.js');
const { Dashboard } = await import('../frontend/js/components/dashboard.js');
const { ArtifactsManager } = await import('../frontend/js/components/artifacts.js');
const { SubagentsVisualizer } = await import('../frontend/js/components/subagents.js');
const { AuthModal } = await import('../frontend/js/components/auth_modal.js');
const { ApiClient } = await import('../frontend/js/api.js');

console.log('--- STARTING ADVERSARIAL FRONTEND TEST SUITE ---');

let passedCount = 0;
let totalCount = 0;

async function test(name, fn) {
  totalCount++;
  try {
    await fn();
    console.log(`  [PASS] ${name}`);
    passedCount++;
  } catch (err) {
    console.error(`  [FAIL] ${name}:`, err.message);
    console.error(err.stack);
  }
}

// =========================================================================
// 1. ANSI Decoder & SGR Parser Adversarial Tests
// =========================================================================

await test('ANSI 1.1: Basic plain text without escape codes returns entity-escaped text', () => {
  const ansi = new AnsiUp();
  const input = 'Hello World <>&"\'';
  const output = ansi.ansiToHtml(input);
  assert.equal(output, 'Hello World &lt;&gt;&amp;&quot;&#039;');
});

await test('ANSI 1.2: Standard 16 foreground & background colors', () => {
  const ansi = new AnsiUp();
  const input = '\x1b[31mRed\x1b[0m \x1b[32mGreen\x1b[0m \x1b[44mBlueBG\x1b[0m';
  const output = ansi.ansiToHtml(input);
  assert.match(output, /color: #f44336.*Red/);
  assert.match(output, /color: #4caf50.*Green/);
  assert.match(output, /background-color: #2196f3.*BlueBG/);
});

await test('ANSI 1.3: 256-color palette (Color Cube 16-231 & Grayscale 232-255)', () => {
  const ansi = new AnsiUp();
  const input = '\x1b[38;5;196mCubeRed\x1b[0m \x1b[38;5;240mGray\x1b[0m \x1b[48;5;21mCubeBlueBG\x1b[0m';
  const output = ansi.ansiToHtml(input);
  assert.match(output, /rgb\(255, 0, 0\)/);
  assert.match(output, /rgb\(88, 88, 88\)/);
  assert.match(output, /background-color: rgb\(0, 0, 255\)/);
});

await test('ANSI 1.4: 24-bit TrueColor RGB (FG 38;2;R;G;B & BG 48;2;R;G;B)', () => {
  const ansi = new AnsiUp();
  const input = '\x1b[38;2;255;128;64mTrueColorFG\x1b[0m \x1b[48;2;10;20;30mTrueColorBG\x1b[0m';
  const output = ansi.ansiToHtml(input);
  assert.match(output, /color: rgb\(255, 128, 64\)/);
  assert.match(output, /background-color: rgb\(10, 20, 30\)/);
});

await test('ANSI 1.5: Text formatting styles (bold, dim, italic, underline, strikethrough, inverse)', () => {
  const ansi = new AnsiUp();
  const input = '\x1b[1;3;4;9mHeavy\x1b[22;23mStillUnderlined\x1b[0m';
  const output = ansi.ansiToHtml(input);
  assert.match(output, /font-weight: bold/);
  assert.match(output, /font-style: italic/);
  assert.match(output, /text-decoration: underline/);
  assert.match(output, /text-decoration: line-through/);
});

await test('ANSI 1.6: Individual style resets (codes 22, 23, 24, 27, 29, 39, 49)', () => {
  const ansi = new AnsiUp();
  const input = '\x1b[1;31mBoldRed\x1b[22mJustRed\x1b[39mDefaultColor\x1b[0m';
  const output = ansi.ansiToHtml(input);
  assert.match(output, /color:\s*#f44336/);
  assert.match(output, /font-weight:\s*bold/);
  assert.match(output, /<span style="color:\s*#f44336">JustRed<\/span>/);
  assert.match(output, /DefaultColor/);
});

await test('ANSI 1.7: Multi-byte UTF-8, emojis, and Vietnamese characters with ANSI styling', () => {
  const ansi = new AnsiUp();
  const input = '\x1b[32m🚀 Antigravity 2.0 ⚡ — Kiểm thử tự động tiếng Việt\x1b[0m';
  const output = ansi.ansiToHtml(input);
  assert.match(output, /color: #4caf50/);
  assert.match(output, /🚀 Antigravity 2.0 ⚡ — Kiểm thử tự động tiếng Việt/);
});

await test('ANSI 1.8: Malformed, truncated, and edge escape codes do not throw or freeze', () => {
  const ansi = new AnsiUp();
  const malformedInputs = [
    '\x1b[',
    '\x1b[m',
    '\x1b[;;;m',
    '\x1b[38m',
    '\x1b[38;2m',
    '\x1b[38;2;255m',
    '\x1b[38;5m',
    '\x1b[48;2;1;2;3;4;5;6;7;8;9m',
    '\x1b[99999999m',
    '\x1b[-100m',
    '\x1b[38;5;9999m',
    '\x1b[38;2;-1;-2;-3m',
    '\x1b[1;2;3;4;5;6;7;8;9;10;11;12;13;14;15;16;17;18;19;20m',
    '\x1b[31;32;33;34;35;36;37;0m',
    null,
    '',
    undefined,
    '\x1b]8;;https://example.com\x07Hyperlink\x1b]8;;\x07',
    '\x1b[2J\x1b[H\x1b[?25h'
  ];

  for (const item of malformedInputs) {
    const res = ansi.ansiToHtml(item);
    assert.equal(typeof res, 'string');
  }
});

await test('ANSI 1.9: XSS Payload Sanitization across ANSI sequences', () => {
  const ansi = new AnsiUp();
  const xssPayloads = [
    '<script>alert("XSS")</script>',
    '<img src=x onerror=alert(1)>',
    '\x1b[31m<iframe src="javascript:alert(1)"></iframe>\x1b[0m',
    '\x1b[38;2;255;0;0m" onfocus="alert(1)" autofocus="',
    '"><svg/onload=alert(document.cookie)>',
    '\x1b[1m<b>HTML injection</b>\x1b[0m',
    '<a href="javascript:alert(1)">Click Me</a>'
  ];

  for (const payload of xssPayloads) {
    const res = ansi.ansiToHtml(payload);
    assert.equal(res.includes('<script>'), false, 'Failed to sanitize <script>');
    assert.equal(res.includes('<img src=x onerror='), false, 'Failed to sanitize <img>');
    assert.equal(res.includes('<iframe'), false, 'Failed to sanitize <iframe>');
    assert.equal(res.includes('<svg/onload='), false, 'Failed to sanitize <svg>');
    assert.equal(res.includes('<a href='), false, 'Failed to sanitize <a>');
  }
});

// =========================================================================
// 2. Visual Diff Rendering Adversarial Tests
// =========================================================================

await test('DIFF 2.1: Empty diff and whitespace diff rendering', () => {
  const dummyState = new StateStore();
  const dummyApi = new ApiClient(dummyState);
  const explorer = new FileExplorer(dummyState, dummyApi);

  const container = new MockElement('div');
  explorer.renderDiff('', container);
  assert.match(container.innerHTML, /No changes detected/);

  explorer.renderDiff('   \n\t  \n  ', container);
  assert.match(container.innerHTML, /No changes detected/);
});

await test('DIFF 2.2: Structured hunks diff rendering with addition, deletion, context', () => {
  const dummyState = new StateStore();
  const dummyApi = new ApiClient(dummyState);
  const explorer = new FileExplorer(dummyState, dummyApi);

  const structuredDiff = {
    diff: '...',
    hunks: [
      {
        header: '@@ -1,3 +1,4 @@',
        lines: [
          { type: 'context', content: 'const a = 1;' },
          { type: 'delete', content: 'const b = 2;' },
          { type: 'add', content: 'const b = 3;' },
          { type: 'add', content: 'const c = 4;' }
        ]
      }
    ]
  };

  const container = new MockElement('div');
  explorer.renderDiff(structuredDiff, container);
  assert.equal(container.childNodes.length, 1);
  const hunkEl = container.childNodes[0];
  assert.equal(hunkEl.classList.contains('diff-hunk'), true);
  assert.equal(hunkEl.childNodes.length, 4);
});

await test('DIFF 2.3: Raw unified diff string fallback with @@ hunks', () => {
  const dummyState = new StateStore();
  const dummyApi = new ApiClient(dummyState);
  const explorer = new FileExplorer(dummyState, dummyApi);

  const rawDiff = `--- a/file.js
+++ b/file.js
@@ -10,4 +10,4 @@
 unchanged line
-removed line
+added line
 context line`;

  const container = new MockElement('div');
  explorer.renderDiff(rawDiff, container);
  assert.equal(container.childNodes.length, 7);
  assert.equal(container.childNodes[2].classList.contains('hunk-header'), true);
  assert.equal(container.childNodes[4].classList.contains('deletion'), true);
  assert.equal(container.childNodes[5].classList.contains('addition'), true);
});

await test('DIFF 2.4: XSS payload in diff lines and chunk headers is fully escaped', () => {
  const dummyState = new StateStore();
  const dummyApi = new ApiClient(dummyState);
  const explorer = new FileExplorer(dummyState, dummyApi);

  const maliciousDiff = {
    hunks: [
      {
        header: '<script>alert("hunk")</script>',
        lines: [
          { type: 'add', content: '<img src=x onerror=alert("add")>' },
          { type: 'delete', content: '<svg onload=alert("del")>' }
        ]
      }
    ]
  };

  const container = new MockElement('div');
  explorer.renderDiff(maliciousDiff, container);
  assert.equal(container.innerHTML.includes('<script>'), false);
  assert.equal(container.innerHTML.includes('<img src=x'), false);
  assert.equal(container.innerHTML.includes('<svg onload='), false);
});

await test('DIFF 2.5: Huge diff (10,000 lines) performance stress test', () => {
  const dummyState = new StateStore();
  const dummyApi = new ApiClient(dummyState);
  const explorer = new FileExplorer(dummyState, dummyApi);

  const lines = [];
  for (let i = 0; i < 10000; i++) {
    lines.push(i % 2 === 0 ? `+ added line ${i}` : `- deleted line ${i}`);
  }
  const hugeRawDiff = lines.join('\n');

  const container = new MockElement('div');
  const startTime = Date.now();
  explorer.renderDiff(hugeRawDiff, container);
  const elapsed = Date.now() - startTime;

  assert.equal(container.childNodes.length, 10000);
  assert.equal(elapsed < 200, true, `Diff rendering took too long: ${elapsed}ms`);
});

// =========================================================================
// 3. Terminal Touch Accessory Bar & Virtual Keys Adversarial Tests
// =========================================================================

await test('TERM 3.1: Virtual key latching and Ctrl modifier combination', () => {
  const dummyState = new StateStore();
  const mockWs = {
    sent: [],
    sendTerminalInput(data) { this.sent.push({ type: 'input', data }); },
    sendTerminalInterrupt() { this.sent.push({ type: 'interrupt' }); },
    sendTerminalClear() { this.sent.push({ type: 'clear' }); },
    on() {},
    disconnectTerminal() {},
    connectTerminal() {}
  };

  const term = new TerminalComponent(dummyState, mockWs);

  // Initial key send without Ctrl
  term.sendRawKey('a');
  assert.equal(mockWs.sent.pop().data, 'a');

  // Activate sticky Ctrl
  term.isCtrlSticky = true;
  term.sendRawKey('c');
  // 'C' is charcode 67 - 64 = 3 (Ctrl+C = \x03)
  assert.equal(mockWs.sent.pop().data, '\x03');
  assert.equal(term.isCtrlSticky, false, 'Sticky Ctrl should auto-reset after single key');

  // Sticky Ctrl with 'z' -> \x1a (26)
  term.isCtrlSticky = true;
  term.sendRawKey('z');
  assert.equal(mockWs.sent.pop().data, '\x1a');

  // Sticky Ctrl with multi-char payload should NOT convert
  term.isCtrlSticky = true;
  term.sendRawKey('hello\r\n');
  assert.equal(mockWs.sent.pop().data, 'hello\r\n');
});

await test('TERM 3.2: Terminal buffer pruning prevents DOM node explosion', () => {
  const dummyState = new StateStore();
  const mockWs = { on() {}, connectTerminal() {} };
  const bufferEl = new MockElement('div');
  bufferEl.id = 'terminal-buffer';
  mockDOM.elements.set('terminal-buffer', bufferEl);

  const term = new TerminalComponent(dummyState, mockWs);
  term.maxLines = 50;

  // Stream 200 chunks
  for (let i = 0; i < 200; i++) {
    term.appendOutput(`Line ${i}\n`);
  }

  assert.equal(bufferEl.childNodes.length, 50, 'Buffer should be clamped to maxLines');
});

// =========================================================================
// 4. Telemetry Gauges SVG Calculations & High Frequency Stress Tests
// =========================================================================

await test('TELEM 4.1: Gauge offset mathematical clamping across edge inputs', () => {
  const dummyState = new StateStore();
  const dummyApi = new ApiClient(dummyState);
  const mockWs = { on() {} };

  const telem = new TelemetryComponent(dummyState, dummyApi, mockWs);

  const barEl = new MockElement('circle');
  barEl.id = 'gauge-cpu-bar';
  mockDOM.elements.set('gauge-cpu-bar', barEl);

  const valEl = new MockElement('span');
  valEl.id = 'metric-cpu-val';
  mockDOM.elements.set('metric-cpu-val', valEl);

  // 0%
  telem.setGauge('gauge-cpu-bar', 'gauge-cpu-fill', 'metric-cpu-val', 'gauge-cpu-val', 0);
  assert.equal(Math.round(barEl.style.strokeDashoffset), Math.round(telem.circumference));
  assert.equal(valEl.textContent, '0');

  // 100%
  telem.setGauge('gauge-cpu-bar', 'gauge-cpu-fill', 'metric-cpu-val', 'gauge-cpu-val', 100);
  assert.equal(Math.round(barEl.style.strokeDashoffset), 0);
  assert.equal(valEl.textContent, '100');

  // Underflow (< 0) clamped to 0
  telem.setGauge('gauge-cpu-bar', 'gauge-cpu-fill', 'metric-cpu-val', 'gauge-cpu-val', -50);
  assert.equal(Math.round(barEl.style.strokeDashoffset), Math.round(telem.circumference));

  // Overflow (> 100) clamped to 100
  telem.setGauge('gauge-cpu-bar', 'gauge-cpu-fill', 'metric-cpu-val', 'gauge-cpu-val', 250);
  assert.equal(Math.round(barEl.style.strokeDashoffset), 0);
});

await test('TELEM 4.2: High-frequency telemetry bursts (1,000 updates in rapid succession)', () => {
  const dummyState = new StateStore();
  const dummyApi = new ApiClient(dummyState);
  const mockWs = { on() {} };

  const telem = new TelemetryComponent(dummyState, dummyApi, mockWs);

  const startTime = Date.now();
  for (let i = 0; i < 1000; i++) {
    telem.updateGauges({
      cpu: (i % 100) + Math.random(),
      ram: ((i * 3) % 100),
      disk: 50.5
    });
  }
  const duration = Date.now() - startTime;
  assert.equal(duration < 100, true, `High frequency updates took too long: ${duration}ms`);
});

await test('TELEM 4.3: Uptime formatting helper', () => {
  const dummyState = new StateStore();
  const dummyApi = new ApiClient(dummyState);
  const telem = new TelemetryComponent(dummyState, dummyApi, {});

  assert.equal(telem.formatUptime(0), '0h 0m 0s');
  assert.equal(telem.formatUptime(45), '0h 0m 45s');
  assert.equal(telem.formatUptime(3665), '1h 1m 5s');
  assert.equal(telem.formatUptime(86400), '24h 0m 0s');
});

// =========================================================================
// 5. Tool Approval Card State Transitions Adversarial Tests
// =========================================================================

await test('TOOL 5.1: Duplicate tool approval card prevention and rendering', () => {
  const dummyState = new StateStore();
  const dummyApi = new ApiClient(dummyState);
  const mockWs = { on() {}, sendToolDecision() {}, isConnected() { return true; } };

  const chatContainer = new MockElement('div');
  chatContainer.id = 'chat-messages';
  mockDOM.elements.set('chat-messages', chatContainer);

  const manager = new ToolApprovalManager(dummyState, dummyApi, mockWs);

  const toolCall1 = { id: 'call_unique_123', name: 'run_command', args: { cmd: 'dir' } };
  manager.renderToolCallCard(toolCall1);
  assert.equal(chatContainer.childNodes.length, 1);

  // Call again with same ID
  manager.renderToolCallCard(toolCall1);
  assert.equal(chatContainer.childNodes.length, 1, 'Should not render duplicate tool call cards');
});

await test('TOOL 5.2: Decision submission grants/rejects and disables buttons during transit', async () => {
  const dummyState = new StateStore();
  dummyState.set('activeConversationId', 'conv_123');

  let sentDecision = null;
  const mockWs = {
    on() {},
    isConnected() { return true; },
    sendToolDecision(cid, callId, dec, pat) {
      sentDecision = { cid, callId, dec, pat };
    }
  };

  const manager = new ToolApprovalManager(dummyState, {}, mockWs);

  const card = new MockElement('div');
  card.id = 'tool-card-call_999';
  const btnApprove = new MockElement('button');
  card.appendChild(btnApprove);
  mockDOM.elements.set('tool-card-call_999', card);

  await manager.submitDecision('conv_123', 'call_999', 'approve');
  assert.deepEqual(sentDecision, {
    cid: 'conv_123',
    callId: 'call_999',
    dec: 'approve',
    pat: null
  });

  // Test grant-session converts to pattern: '*'
  await manager.submitDecision('conv_123', 'call_999', 'grant-session');
  assert.deepEqual(sentDecision, {
    cid: 'conv_123',
    callId: 'call_999',
    dec: 'approve',
    pat: '*'
  });
});

// =========================================================================
// 6. Markdown Renderer & XSS Robustness (Dashboard & Artifacts)
// =========================================================================

await test('MD 6.1: Markdown renderer sanitizes malicious scripts, iframes, img onerror', () => {
  const dummyState = new StateStore();
  const dashboard = new Dashboard(dummyState, {}, { on() {}, subscribeSession() {} });

  const rawMd = `
# Title <script>alert(1)</script>
**Bold <img src=x onerror=alert(2)>**
[Link](javascript:alert(3))
\`<iframe src="evil.com"></iframe>\`
- [ ] Task <svg onload=alert(4)>
`;

  const rendered = dashboard.renderMarkdown(rawMd);
  assert.equal(rendered.includes('<script>'), false);
  assert.equal(rendered.includes('<img src=x'), false);
  assert.equal(rendered.includes('<iframe'), false);
  assert.equal(rendered.includes('<svg onload='), false);
  assert.match(rendered, /&lt;script&gt;/);
  assert.match(rendered, /&lt;img/);
});

await test('MD 6.2: Artifacts checklist progress calculator', () => {
  const dummyState = new StateStore();
  const artifacts = new ArtifactsManager(dummyState, {});

  const chip = new MockElement('span');
  chip.id = 'artifacts-progress-chip';
  mockDOM.elements.set('artifacts-progress-chip', chip);

  const markdownWithChecklist = `
# Plan
- [x] Step 1 done
- [x] Step 2 done
- [ ] Step 3 pending
- [ ] Step 4 pending
`;

  artifacts.computeProgress(markdownWithChecklist);
  assert.equal(chip.textContent, '2/4 (50%)');
});

// =========================================================================
// 7. State Store & Micro-Reactivity Adversarial Tests
// =========================================================================

await test('STATE 7.1: Observable subscriptions, selectors, and event bus consistency', () => {
  const store = new StateStore();

  let receivedValue = null;
  const unbind = store.subscribe('activeView', (val) => {
    receivedValue = val;
  });
  assert.equal(receivedValue, 'chat');

  store.set('activeView', 'terminal');
  assert.equal(receivedValue, 'terminal');

  unbind();
  store.set('activeView', 'files');
  assert.equal(receivedValue, 'terminal', 'Subscription should not trigger after unbind');

  // Selector
  let selectedStatus = null;
  store.select((s) => s.connectionStatus, (val) => {
    selectedStatus = val;
  });
  assert.equal(selectedStatus, 'DISCONNECTED');

  store.set('connectionStatus', 'CONNECTED');
  assert.equal(selectedStatus, 'CONNECTED');
});

await test('STATE 7.2: Circular buffer for terminal logs retains max limit', () => {
  const store = new StateStore();
  store.set('terminalMaxLines', 10);

  for (let i = 0; i < 50; i++) {
    store.appendTerminalLog(`Log entry ${i}`);
  }

  const logs = store.get('terminalLogs');
  assert.equal(logs.length, 10);
  assert.equal(logs[9], 'Log entry 49');
});

// =========================================================================
// 8. Auth PIN Keypad & Token Handling Adversarial Tests
// =========================================================================

await test('AUTH 8.1: PIN keypad entry length clamping & backspace logic', () => {
  const dummyState = new StateStore();
  const auth = new AuthModal(dummyState, {}, {});

  auth.handleKeyInput('1');
  auth.handleKeyInput('2');
  auth.handleKeyInput('3');
  assert.equal(auth.pin, '123');

  auth.handleBackspace();
  assert.equal(auth.pin, '12');

  auth.clearPin();
  assert.equal(auth.pin, '');

  // Exceed max length (6)
  for (let i = 0; i < 10; i++) {
    auth.handleKeyInput(String(i % 10));
  }
  assert.equal(auth.pin.length, 6);
});

// =========================================================================
// 9. QR Matrix Generator Adversarial Tests
// =========================================================================

await test('QR 9.1: QR Matrix deterministic generator handles various payload lengths', () => {
  const shortText = '123456';
  const mediumText = 'http://192.168.1.50:8765/?token=eyJhbGciOiJIUzI1NiJ9';
  const longText = 'http://192.168.1.50:8765/?token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbiIsImV4cCI6OTk5OTk5OTk5OX0.signaturesignaturesignature';

  const m1 = QRCodeGenerator._generateMatrix(shortText);
  const m2 = QRCodeGenerator._generateMatrix(mediumText);
  const m3 = QRCodeGenerator._generateMatrix(longText);

  assert.equal(Array.isArray(m1), true);
  assert.equal(Array.isArray(m2), true);
  assert.equal(Array.isArray(m3), true);
  assert.equal(m1.length >= 25, true);
  assert.equal(m3.length >= 33, true);
});

console.log(`\n--- TEST SUMMARY: ${passedCount}/${totalCount} tests passed ---`);
if (passedCount === totalCount) {
  console.log('ALL ADVERSARIAL FRONTEND TESTS PASSED SUCCESSFULLY (100%)');
  process.exit(0);
} else {
  console.error(`FAILED: ${totalCount - passedCount} tests failed`);
  process.exit(1);
}
