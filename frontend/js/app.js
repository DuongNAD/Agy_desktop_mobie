/**
 * frontend/js/app.js
 * Main Application Coordinator, Router, Toast Manager & PWA Bootstrap.
 */

import { state } from './state.js';
import { api } from './api.js';
import { ws } from './ws.js';
import { AnsiUp } from './lib/ansi_up.js';
import { AuthModal } from './components/auth_modal.js';
import { Dashboard } from './components/dashboard.js';
import { SubagentsVisualizer } from './components/subagents.js';
import { ToolApprovalManager } from './components/tool_approval.js';
import { ArtifactsManager } from './components/artifacts.js';
import { FileExplorer } from './components/file_explorer.js';
import { TerminalComponent } from './components/terminal.js';
import { TelemetryComponent } from './components/telemetry.js';
import { SSHComponent } from './components/ssh.js';

export class App {
  constructor() {
    this.state = state;
    this.api = api;
    this.ws = ws;
    this.ansiUp = new AnsiUp();
    this.deferredInstallPrompt = null;
  }

  async init() {
    this.initServiceWorker();
    this.initVisualViewport();
    this.initPWAInstallPrompt();
    this.initToastManager();
    this.initComponents();
    this.initTabRouter();
    await this.checkAuthStatus();
  }

  initComponents() {
    this.authModal = new AuthModal(this.state, this.api, this.ws);
    this.dashboard = new Dashboard(this.state, this.api, this.ws);
    this.subagents = new SubagentsVisualizer(this.state, this.api, this.ws);
    this.toolApprovals = new ToolApprovalManager(this.state, this.api, this.ws);
    this.artifacts = new ArtifactsManager(this.state, this.api);
    this.fileExplorer = new FileExplorer(this.state, this.api);
    this.terminal = new TerminalComponent(this.state, this.ws, this.ansiUp);
    this.ssh = new SSHComponent(this.state, this.ws, this.api);
    this.telemetry = new TelemetryComponent(this.state, this.api, this.ws);
  }

  initTabRouter() {
    const navTabs = document.querySelectorAll('.nav-tab') || document.querySelectorAll('.bottom-nav-btn');
    navTabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        const target = tab.dataset.target || (tab.dataset.view ? `tab-${tab.dataset.view}` : '');
        if (target) {
          const viewName = target.replace('tab-', '').replace('view-', '');
          this.switchView(viewName);
        }
      });
    });

    window.addEventListener('hashchange', () => {
      const hash = window.location.hash.replace('#tab-', '').replace('#view-', '').replace('#', '') || 'chat';
      this.switchView(hash, false);
    });

    // Initial view from hash
    const initialView = window.location.hash.replace('#tab-', '').replace('#view-', '').replace('#', '') || 'chat';
    this.switchView(initialView, false);
  }

  switchView(viewName, updateHash = true) {
    const validViews = ['chat', 'files', 'terminal', 'system'];
    const active = validViews.includes(viewName) ? viewName : 'chat';

    this.state.set('activeView', active);

    // Hide all panels
    const panels = document.querySelectorAll('.tab-panel') || document.querySelectorAll('.app-view');
    panels.forEach((p) => p.classList.add('hidden'));

    // Deactivate all nav tabs
    const navTabs = document.querySelectorAll('.nav-tab') || document.querySelectorAll('.bottom-nav-btn');
    navTabs.forEach((t) => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });

    // Activate selected panel & tab
    const targetPanel = document.getElementById(`tab-${active}`) || document.getElementById(`view-${active}`);
    if (targetPanel) {
      targetPanel.classList.remove('hidden');
    }

    const activeNavTab = document.getElementById(`nav-tab-${active}`) ||
      document.querySelector(`[data-target="tab-${active}"]`) ||
      document.querySelector(`[data-view="${active}"]`);
    if (activeNavTab) {
      activeNavTab.classList.add('active');
      activeNavTab.setAttribute('aria-selected', 'true');
    }

    if (updateHash) {
      window.location.hash = `#tab-${active}`;
    }
  }

  initVisualViewport() {
    if (typeof window !== 'undefined' && window.visualViewport) {
      const handleResize = () => {
        const vh = window.visualViewport.height;
        document.documentElement.style.setProperty('--visual-vh', `${vh}px`);
      };
      window.visualViewport.addEventListener('resize', handleResize);
      window.visualViewport.addEventListener('scroll', handleResize);
      handleResize();
    }
  }

  initServiceWorker() {
    if (typeof navigator !== 'undefined' && 'serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js')
        .then((reg) => {
          console.log('[PWA] ServiceWorker registered with scope:', reg.scope);
        })
        .catch((err) => {
          console.warn('[PWA] ServiceWorker registration failed:', err);
        });
    }
  }

  initPWAInstallPrompt() {
    if (typeof window !== 'undefined') {
      const banner = document.getElementById('pwa-install-banner');
      const btnInstall = document.getElementById('btn-pwa-install');
      const btnDismiss = document.getElementById('btn-pwa-dismiss');

      window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        this.deferredInstallPrompt = e;
        if (banner) {
          banner.classList.remove('hidden');
        }
      });

      if (btnInstall) {
        btnInstall.addEventListener('click', async () => {
          if (this.deferredInstallPrompt) {
            this.deferredInstallPrompt.prompt();
            const { outcome } = await this.deferredInstallPrompt.userChoice;
            if (outcome === 'accepted') {
              this.state.addToast({
                type: 'success',
                title: 'Đã cài đặt App',
                message: 'AgyMobile đã được thêm vào màn hình chính!',
              });
            }
            this.deferredInstallPrompt = null;
            if (banner) banner.classList.add('hidden');
          }
        });
      }

      if (btnDismiss && banner) {
        btnDismiss.addEventListener('click', () => {
          banner.classList.add('hidden');
        });
      }
    }
  }

  initToastManager() {
    this.state.subscribe('toasts', (toasts) => {
      const container = document.getElementById('toast-container');
      if (!container) return;

      container.innerHTML = '';
      (toasts || []).forEach((t) => {
        const toastEl = document.createElement('div');
        toastEl.className = `toast ${t.type || 'info'}`;
        toastEl.innerHTML = `
          ${t.title ? `<strong>${t.title}</strong><br>` : ''}
          <span>${t.message || ''}</span>
        `;
        container.appendChild(toastEl);
      });
    });
  }

  async checkAuthStatus() {
    // 1. Check for token in URL query parameter
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const queryToken = params.get('token');
      if (queryToken) {
        this.state.setToken(queryToken);
        window.history.replaceState({}, document.title, window.location.pathname);
      }
    }

    const token = this.state.getToken();
    if (!token) {
      this.authModal.show();
      return;
    }

    try {
      await this.api.verifyAuth();
      this.ws.connect(token);
      if (this.dashboard) {
        this.dashboard.loadConversationList();
      }
    } catch (_) {
      this.state.setToken(null);
      this.authModal.show();
    }
  }
}

// Global Toast helper object for direct invocation if needed
export const Toast = {
  show(message, type = 'info', title = '') {
    state.addToast({ message, type, title });
  },
  success(message, title = '') {
    state.addToast({ message, type: 'success', title });
  },
  error(message, title = '') {
    state.addToast({ message, type: 'error', title });
  },
  info(message, title = '') {
    state.addToast({ message, type: 'info', title });
  },
  warning(message, title = '') {
    state.addToast({ message, type: 'warning', title });
  },
};

// Bootstrap application on DOM ready
if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    window.app = new App();
    window.app.init();
  });
}
