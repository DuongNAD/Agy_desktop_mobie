/**
 * frontend/js/components/auth_modal.js
 * 6-Digit PIN Keypad & Camera-Based QR Code Scanner Modal.
 */

import { QRScannerHelper } from '../lib/qrcode.js';

export class AuthModal {
  constructor(state, api, ws) {
    this.state = state;
    this.api = api;
    this.ws = ws;
    this.pin = '';
    this.maxPinLength = 6;
    this.scanner = null;
    this.isSubmitting = false;

    this.init();
  }

  init() {
    this.bindEvents();
    this.subscribeState();
  }

  bindEvents() {
    // Mode Switcher Tabs
    const tabBtnOtp = document.getElementById('tab-btn-otp');
    const tabBtnPin = document.getElementById('tab-btn-pin');
    const tabBtnQr = document.getElementById('tab-btn-qr');
    const tabBtnToken = document.getElementById('tab-btn-token');

    const panelOtp = document.getElementById('auth-panel-otp');
    const panelPin = document.getElementById('auth-panel-pin');
    const panelQr = document.getElementById('auth-panel-qr');
    const panelToken = document.getElementById('auth-panel-token');

    const allTabs = [tabBtnOtp, tabBtnPin, tabBtnQr, tabBtnToken].filter(Boolean);
    const allPanels = [panelOtp, panelPin, panelQr, panelToken].filter(Boolean);

    if (tabBtnOtp) {
      tabBtnOtp.addEventListener('click', () => {
        this.switchAuthTab('otp', allTabs, allPanels);
        this.stopCamera();
      });
    }
    if (tabBtnPin) {
      tabBtnPin.addEventListener('click', () => {
        this.switchAuthTab('pin', allTabs, allPanels);
        this.stopCamera();
      });
    }
    if (tabBtnQr) {
      tabBtnQr.addEventListener('click', () => {
        this.switchAuthTab('qr', allTabs, allPanels);
        this.startCamera();
      });
    }
    if (tabBtnToken) {
      tabBtnToken.addEventListener('click', () => {
        this.switchAuthTab('token', allTabs, allPanels);
        this.stopCamera();
      });
    }

    // OTP Input & Email Handlers
    const inputOtp = document.getElementById('input-otp-code');
    const btnSubmitOtp = document.getElementById('btn-submit-otp');
    const btnSendEmailOtp = document.getElementById('btn-send-email-otp');
    const inputEmail = document.getElementById('input-auth-email');

    if (inputOtp) {
      inputOtp.addEventListener('input', (e) => {
        const val = e.target.value.replace(/\D/g, '');
        e.target.value = val;
        if (val.length === 6) {
          this.submitOtp(val);
        }
      });
      inputOtp.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          this.submitOtp(inputOtp.value);
        }
      });
    }

    if (btnSubmitOtp && inputOtp) {
      btnSubmitOtp.addEventListener('click', () => {
        this.submitOtp(inputOtp.value);
      });
    }

    if (btnSendEmailOtp && inputEmail) {
      btnSendEmailOtp.addEventListener('click', async () => {
        const email = inputEmail.value.trim();
        if (!email || !email.includes('@')) {
          this.showOtpAlert('Vui lòng nhập địa chỉ email hợp lệ');
          return;
        }
        btnSendEmailOtp.disabled = true;
        btnSendEmailOtp.textContent = '⏳ Đang gửi mã...';
        try {
          const res = await this.api.requestOtp(email);
          this.showOtpAlert(res.message || 'Mã OTP đã được gửi về email!', 'success');
          this.state.addToast({
            type: 'info',
            title: 'Mã OTP đã gửi',
            message: `Kiểm tra hòm thư ${email} để lấy mã 6 số.`,
          });
          if (inputOtp) inputOtp.focus();
        } catch (err) {
          this.showOtpAlert(err.message || 'Không thể gửi mã OTP');
        } finally {
          setTimeout(() => {
            btnSendEmailOtp.disabled = false;
            btnSendEmailOtp.textContent = '📧 Gửi lại mã OTP';
          }, 3000);
        }
      });
    }

    // PIN Keypad Numbers & Actions
    const keypad = document.querySelector('.pin-keypad') || document.querySelector('.keypad-grid');
    if (keypad) {
      keypad.addEventListener('click', (e) => {
        const target = e.target.closest('button');
        if (!target) return;

        const num = target.dataset.num || target.dataset.key;
        const action = target.dataset.action;

        if (num !== undefined && num !== null && !isNaN(parseInt(num, 10))) {
          this.handleKeyInput(String(num));
        } else if (action === 'clear' || target.id === 'btn-pin-clear') {
          this.clearPin();
        } else if (action === 'backspace' || target.id === 'btn-pin-backspace') {
          this.handleBackspace();
        }
      });
    }

    // Start Camera Button
    const btnStartCam = document.getElementById('btn-start-scanner');
    if (btnStartCam) {
      btnStartCam.addEventListener('click', () => this.startCamera());
    }

    // Token Submit Button
    const btnSubmitToken = document.getElementById('btn-submit-token');
    if (btnSubmitToken) {
      btnSubmitToken.addEventListener('click', () => this.submitPastedToken());
    }

    // Close Modal Button
    const btnClose = document.getElementById('btn-close-auth-modal');
    if (btnClose) {
      btnClose.addEventListener('click', () => this.hide());
    }

    // Header Auth Trigger
    const btnAuthTrigger = document.getElementById('btn-auth-trigger');
    if (btnAuthTrigger) {
      btnAuthTrigger.addEventListener('click', () => this.show());
    }
  }

  subscribeState() {
    this.state.subscribe('authStatus', (status) => {
      if (status === 'unauthenticated') {
        this.show();
      }
    });
  }

  switchAuthTab(mode, tabBtns, panels) {
    tabBtns.forEach((btn) => btn.classList.remove('active'));
    panels.forEach((panel) => panel.classList.add('hidden'));

    const tabMap = {
      otp: { tab: document.getElementById('tab-btn-otp'), panel: document.getElementById('auth-panel-otp') },
      pin: { tab: document.getElementById('tab-btn-pin'), panel: document.getElementById('auth-panel-pin') },
      qr: { tab: document.getElementById('tab-btn-qr'), panel: document.getElementById('auth-panel-qr') },
      token: { tab: document.getElementById('tab-btn-token'), panel: document.getElementById('auth-panel-token') },
    };

    if (tabMap[mode] && tabMap[mode].tab && tabMap[mode].panel) {
      tabMap[mode].tab.classList.add('active');
      tabMap[mode].panel.classList.remove('hidden');
    }
  }

  show() {
    const modal = document.getElementById('modal-auth') || document.getElementById('auth-modal');
    if (modal) {
      modal.classList.remove('hidden');
      this.clearPin();
      this.clearAlert();
    }
  }

  hide() {
    const modal = document.getElementById('modal-auth') || document.getElementById('auth-modal');
    if (modal) {
      modal.classList.add('hidden');
      this.stopCamera();
    }
  }

  // --- PIN Keypad Actions ---

  handleKeyInput(digit) {
    if (this.pin.length < this.maxPinLength) {
      this.pin += digit;
      this.updatePinDisplay();
      this.vibrate(20);
      if (this.pin.length === this.maxPinLength) {
        this.submitPin();
      }
    }
  }

  handleBackspace() {
    if (this.pin.length > 0) {
      this.pin = this.pin.slice(0, -1);
      this.updatePinDisplay();
      this.vibrate(15);
    }
  }

  clearPin() {
    this.pin = '';
    this.updatePinDisplay();
  }

  updatePinDisplay() {
    const dots = document.querySelectorAll('.pin-dot');
    dots.forEach((dot, index) => {
      if (index < this.pin.length) {
        dot.classList.add('filled');
      } else {
        dot.classList.remove('filled');
      }
    });
  }

  async submitPin() {
    if (this.isSubmitting) return;
    this.isSubmitting = true;
    this.clearAlert();

    try {
      const res = await this.api.login(this.pin);
      if (res && res.token) {
        this.onAuthSuccess(res.token);
      } else {
        throw new Error('No token returned from server');
      }
    } catch (err) {
      this.showAlert(err.message || 'Invalid PIN code');
      this.vibrate([100, 50, 100]);
      setTimeout(() => this.clearPin(), 800);
    } finally {
      this.isSubmitting = false;
    }
  }

  // --- QR Scanning ---

  startCamera() {
    const video = document.getElementById('qr-video-preview') || document.getElementById('qr-video');
    if (!video) return;

    this.stopCamera();
    this.scanner = new QRScannerHelper(video, {
      onScan: (qrData) => this.handleQrData(qrData),
      onError: (err) => this.showQrAlert('Camera error: ' + (err.message || 'Access denied')),
    });
    this.scanner.start();
  }

  stopCamera() {
    if (this.scanner) {
      this.scanner.stop();
      this.scanner = null;
    }
  }

  handleQrData(qrUrl) {
    if (!qrUrl) return;
    this.vibrate([40, 30, 40]);
    this.stopCamera();

    let extractedToken = null;
    try {
      const url = new URL(qrUrl);
      extractedToken = url.searchParams.get('token');
    } catch (_) {
      // Raw token string
      if (qrUrl.startsWith('eyJ')) {
        extractedToken = qrUrl;
      }
    }

    if (extractedToken) {
      this.onAuthSuccess(extractedToken);
    } else {
      this.showQrAlert('Scanned QR code does not contain a valid pairing token.');
    }
  }

  // --- Manual Token Submission ---

  submitPastedToken() {
    const textarea = document.getElementById('input-token-paste') || document.getElementById('token-input');
    if (!textarea) return;

    let raw = textarea.value.trim();
    if (!raw) return;

    let token = raw;
    try {
      const parsedUrl = new URL(raw);
      const qToken = parsedUrl.searchParams.get('token');
      if (qToken) token = qToken;
    } catch (_) {}

    this.onAuthSuccess(token);
  }

  async submitOtp(code) {
    if (this.isSubmitting) return;
    const cleanCode = String(code || '').trim();
    if (!cleanCode || cleanCode.length < 6) {
      this.showOtpAlert('Vui lòng nhập đủ 6 chữ số mã OTP');
      return;
    }

    this.isSubmitting = true;
    this.clearAlert();

    const inputEmail = document.getElementById('input-auth-email');
    const email = inputEmail ? inputEmail.value.trim() : null;

    try {
      const res = await this.api.verifyOtp(cleanCode, email || null);
      if (res && res.token) {
        this.onAuthSuccess(res.token);
      } else {
        throw new Error('Không nhận được token từ máy chủ');
      }
    } catch (err) {
      this.showOtpAlert(err.message || 'Mã OTP không đúng hoặc đã hết hạn');
      this.vibrate([100, 50, 100]);
    } finally {
      this.isSubmitting = false;
    }
  }

  onAuthSuccess(token) {
    this.state.setToken(token);
    this.hide();
    this.ws.connect(token);
    this.state.addToast({
      type: 'success',
      title: 'Đăng nhập thành công',
      message: 'Thiết bị di động đã được kết nối với Antigravity PC!',
    });
  }

  showAlert(message) {
    const alertEl = document.getElementById('pin-alert');
    if (alertEl) {
      alertEl.textContent = message;
      alertEl.classList.remove('hidden');
    }
  }

  showOtpAlert(message, type = 'error') {
    const alertEl = document.getElementById('otp-alert');
    if (alertEl) {
      alertEl.textContent = message;
      alertEl.className = `auth-alert ${type === 'success' ? 'success' : ''}`;
      alertEl.classList.remove('hidden');
    }
  }

  showQrAlert(message) {
    const alertEl = document.getElementById('qr-alert');
    if (alertEl) {
      alertEl.textContent = message;
      alertEl.classList.remove('hidden');
    }
  }

  clearAlert() {
    const pinAlert = document.getElementById('pin-alert');
    const qrAlert = document.getElementById('qr-alert');
    const otpAlert = document.getElementById('otp-alert');
    if (pinAlert) pinAlert.classList.add('hidden');
    if (qrAlert) qrAlert.classList.add('hidden');
    if (otpAlert) otpAlert.classList.add('hidden');
  }

  vibrate(pattern) {
    if (typeof navigator !== 'undefined' && 'vibrate' in navigator) {
      try {
        navigator.vibrate(pattern);
      } catch (_) {}
    }
  }
}
