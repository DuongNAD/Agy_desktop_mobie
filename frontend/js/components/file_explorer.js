/**
 * frontend/js/components/file_explorer.js
 * Workspace Directory Tree, Mobile Code Editor & Visual Diff Viewer.
 */

export class FileExplorer {
  constructor(state, api) {
    this.state = state;
    this.api = api;
    this.currentPath = '';
    this.activeFilePath = null;
    this.originalContent = '';
    this.isDirty = false;

    this.init();
  }

  init() {
    this.bindDOMEvents();
    this.loadDirectory('');
  }

  bindDOMEvents() {
    // Breadcrumbs Navigation
    const breadcrumbs = document.getElementById('file-breadcrumbs') || document.getElementById('files-breadcrumbs');
    if (breadcrumbs) {
      breadcrumbs.addEventListener('click', (e) => {
        const crumb = e.target.closest('.crumb');
        if (!crumb) return;
        const targetPath = crumb.dataset.path || '';
        this.loadDirectory(targetPath);
      });
    }

    // Search input
    const searchInput = document.getElementById('input-file-search') || document.getElementById('files-search-input');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        this.filterTree(e.target.value);
      });
    }

    // Refresh Files Button
    const btnRefresh = document.getElementById('btn-file-refresh');
    if (btnRefresh) {
      btnRefresh.addEventListener('click', () => {
        this.loadDirectory(this.currentPath);
      });
    }

    // Editor Back Button
    const btnBack = document.getElementById('btn-editor-back') || document.getElementById('btn-close-editor');
    if (btnBack) {
      btnBack.addEventListener('click', () => {
        if (this.isDirty && !confirm('You have unsaved changes. Discard and go back?')) {
          return;
        }
        this.closeEditor();
      });
    }

    // Editor Save Button
    const btnSave = document.getElementById('btn-editor-save') || document.getElementById('btn-save-file');
    if (btnSave) {
      btnSave.addEventListener('click', () => {
        this.saveCurrentFile();
      });
    }

    // Editor Diff Toggle Button
    const btnDiff = document.getElementById('btn-toggle-diff');
    if (btnDiff) {
      btnDiff.addEventListener('click', () => {
        this.showDiff();
      });
    }

    // Close Diff Button
    const btnCloseDiff = document.getElementById('btn-close-diff');
    if (btnCloseDiff) {
      btnCloseDiff.addEventListener('click', () => {
        const diffContainer = document.getElementById('editor-diff-container') || document.getElementById('diff-viewer-panel');
        if (diffContainer) diffContainer.classList.add('hidden');
      });
    }

    // Editor Textarea Input & Line Numbers Update
    const textarea = document.getElementById('editor-textarea');
    if (textarea) {
      textarea.addEventListener('input', () => {
        this.updateLineNumbers(textarea.value);
        this.checkDirtyState(textarea.value);
      });
    }

    // Discard Changes Button
    const btnDiscard = document.getElementById('btn-discard-changes');
    if (btnDiscard) {
      btnDiscard.addEventListener('click', () => {
        if (textarea) {
          textarea.value = this.originalContent;
          this.updateLineNumbers(this.originalContent);
          this.checkDirtyState(this.originalContent);
        }
      });
    }
  }

  async loadDirectory(path = '') {
    this.currentPath = path;
    this.updateBreadcrumbs(path);

    const listEl = document.getElementById('file-tree-list') || document.getElementById('tree-list-items');
    if (listEl) {
      listEl.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);">Loading workspace files...</div>';
    }

    try {
      const res = await this.api.getFileTree(path, 1);
      const items = Array.isArray(res) ? res : (res && res.files ? res.files : []);
      this.renderTree(items);
    } catch (err) {
      if (listEl) {
        listEl.innerHTML = `<div style="padding:16px;text-align:center;color:var(--neon-rose);">Failed to load directory: ${err.message}</div>`;
      }
    }
  }

  updateBreadcrumbs(path) {
    const breadcrumbs = document.getElementById('file-breadcrumbs') || document.getElementById('files-breadcrumbs');
    if (!breadcrumbs) return;

    breadcrumbs.innerHTML = '';
    const rootCrumb = document.createElement('span');
    rootCrumb.className = 'crumb root';
    rootCrumb.dataset.path = '';
    rootCrumb.textContent = 'workspace';
    breadcrumbs.appendChild(rootCrumb);

    if (!path) return;

    const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
    let accum = '';

    parts.forEach((part, index) => {
      accum += (index === 0 ? '' : '/') + part;
      const sep = document.createElement('span');
      sep.textContent = ' / ';
      sep.style.color = 'var(--text-muted)';
      breadcrumbs.appendChild(sep);

      const crumb = document.createElement('span');
      crumb.className = 'crumb';
      crumb.dataset.path = accum;
      crumb.textContent = part;
      breadcrumbs.appendChild(crumb);
    });
  }

  renderTree(items) {
    const listEl = document.getElementById('file-tree-list') || document.getElementById('tree-list-items');
    if (!listEl) return;

    listEl.innerHTML = '';
    if (!items || items.length === 0) {
      listEl.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);">Empty folder.</div>';
      return;
    }

    // Sort folders first, then files alphabetically
    const sorted = [...items].sort((a, b) => {
      if (a.is_dir === b.is_dir) return a.name.localeCompare(b.name);
      return a.is_dir ? -1 : 1;
    });

    sorted.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'tree-row';
      row.dataset.path = item.path;
      row.dataset.isDir = item.is_dir ? 'true' : 'false';

      const icon = item.is_dir ? '📁' : this._getFileIcon(item.name);
      const sizeText = item.is_dir ? '' : this._formatSize(item.size);

      row.innerHTML = `
        <span class="tree-icon">${icon}</span>
        <span class="tree-name">${this._escape(item.name)}</span>
        <span class="tree-size">${sizeText}</span>
      `;

      row.addEventListener('click', () => {
        if (item.is_dir) {
          this.loadDirectory(item.path);
        } else {
          this.openFile(item.path);
        }
      });

      listEl.appendChild(row);
    });
  }

  filterTree(query) {
    const q = (query || '').toLowerCase().trim();
    const rows = document.querySelectorAll('.tree-row');
    rows.forEach((row) => {
      const name = (row.querySelector('.tree-name') || {}).textContent || '';
      if (!q || name.toLowerCase().includes(q)) {
        row.classList.remove('hidden');
      } else {
        row.classList.add('hidden');
      }
    });
  }

  async openFile(filePath) {
    this.activeFilePath = filePath;
    const editorView = document.getElementById('file-editor-view') || document.getElementById('mobile-editor-panel');
    const filenameLabel = document.getElementById('editor-filename') || document.getElementById('editor-active-filename');
    const textarea = document.getElementById('editor-textarea');

    if (filenameLabel) filenameLabel.textContent = filePath;
    if (editorView) editorView.classList.remove('hidden');

    try {
      const data = await this.api.getFileContent(filePath);
      const content = typeof data === 'string' ? data : data.content || '';
      this.originalContent = content;
      this.isDirty = false;

      if (textarea) {
        textarea.value = content;
        this.updateLineNumbers(content);
        this.checkDirtyState(content);
      }
    } catch (err) {
      this.state.addToast({
        type: 'error',
        title: 'File Open Error',
        message: err.message || 'Unable to read file',
      });
    }
  }

  closeEditor() {
    const editorView = document.getElementById('file-editor-view') || document.getElementById('mobile-editor-panel');
    const diffContainer = document.getElementById('editor-diff-container') || document.getElementById('diff-viewer-panel');
    if (editorView) editorView.classList.add('hidden');
    if (diffContainer) diffContainer.classList.add('hidden');
    this.activeFilePath = null;
    this.isDirty = false;
  }

  updateLineNumbers(text) {
    const lineNumbersEl = document.getElementById('editor-line-numbers');
    if (!lineNumbersEl) return;

    const lines = (text || '').split('\n').length;
    let numbersHtml = '';
    for (let i = 1; i <= lines; i++) {
      numbersHtml += `<div>${i}</div>`;
    }
    lineNumbersEl.innerHTML = numbersHtml;
  }

  checkDirtyState(currentText) {
    this.isDirty = currentText !== this.originalContent;
    const unsavedBar = document.getElementById('editor-unsaved-bar');
    if (unsavedBar) {
      if (this.isDirty) {
        unsavedBar.classList.remove('hidden');
      } else {
        unsavedBar.classList.add('hidden');
      }
    }
  }

  async saveCurrentFile() {
    if (!this.activeFilePath) return;

    const textarea = document.getElementById('editor-textarea');
    const newContent = textarea ? textarea.value : '';

    try {
      await this.api.saveFile(this.activeFilePath, newContent);
      this.originalContent = newContent;
      this.checkDirtyState(newContent);
      this.state.addToast({
        type: 'success',
        title: 'Saved',
        message: `${this.activeFilePath} saved successfully!`,
      });
    } catch (err) {
      this.state.addToast({
        type: 'error',
        title: 'Save Failed',
        message: err.message || 'Unable to save file',
      });
    }
  }

  async showDiff() {
    if (!this.activeFilePath) return;

    const textarea = document.getElementById('editor-textarea');
    const newContent = textarea ? textarea.value : '';
    const diffContainer = document.getElementById('editor-diff-container') || document.getElementById('diff-viewer-panel');
    const diffContent = document.getElementById('diff-content-view') || diffContainer;
    const diffFilename = document.getElementById('diff-filename');

    if (diffFilename) diffFilename.textContent = `Diff: ${this.activeFilePath}`;
    if (diffContainer) diffContainer.classList.remove('hidden');
    if (diffContent) diffContent.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-muted);">Computing visual diff...</div>';

    try {
      const diffData = await this.api.getFileDiff(this.activeFilePath, newContent, this.originalContent);
      this.renderDiff(diffData, diffContent);
    } catch (err) {
      if (diffContent) {
        diffContent.innerHTML = `<div style="padding:16px;color:var(--neon-rose);">Diff calculation error: ${err.message}</div>`;
      }
    }
  }

  renderDiff(diffData, container) {
    if (!container) return;

    container.innerHTML = '';
    const rawDiff = typeof diffData === 'string' ? diffData : (diffData && diffData.diff ? diffData.diff : '');
    const hunks = diffData && diffData.hunks ? diffData.hunks : null;

    if (hunks && hunks.length > 0) {
      hunks.forEach((hunk) => {
        const hunkEl = document.createElement('div');
        hunkEl.className = 'diff-hunk';
        hunkEl.innerHTML = `<div class="hunk-header">${this._escape(hunk.header || '@@ Chunk @@')}</div>`;

        (hunk.lines || []).forEach((line) => {
          const lineEl = document.createElement('div');
          const type = line.type === 'add' ? 'addition' : line.type === 'delete' ? 'deletion' : 'context';
          lineEl.className = `diff-line ${type}`;
          lineEl.textContent = `${line.type === 'add' ? '+' : line.type === 'delete' ? '-' : ' '} ${line.content || ''}`;
          hunkEl.appendChild(lineEl);
        });
        container.appendChild(hunkEl);
      });
      return;
    }

    if (!rawDiff || rawDiff.trim().length === 0) {
      container.innerHTML = '<div class="no-diff" style="padding:20px;text-align:center;color:var(--text-muted);">No changes detected.</div>';
      return;
    }

    const lines = rawDiff.split('\n');
    lines.forEach((line) => {
      const lineEl = document.createElement('div');
      if (line.startsWith('+') && !line.startsWith('+++')) {
        lineEl.className = 'diff-line addition';
      } else if (line.startsWith('-') && !line.startsWith('---')) {
        lineEl.className = 'diff-line deletion';
      } else if (line.startsWith('@@')) {
        lineEl.className = 'hunk-header';
      } else {
        lineEl.className = 'diff-line context';
      }
      lineEl.textContent = line;
      container.appendChild(lineEl);
    });
  }

  _getFileIcon(name = '') {
    const ext = name.split('.').pop().toLowerCase();
    if (['js', 'ts', 'jsx', 'tsx'].includes(ext)) return '📜';
    if (['json', 'yaml', 'yml', 'toml'].includes(ext)) return '⚙️';
    if (['py', 'ps1', 'bat', 'sh'].includes(ext)) return '🐍';
    if (['html', 'css', 'scss', 'svg'].includes(ext)) return '🎨';
    if (['md', 'txt', 'log'].includes(ext)) return '📄';
    return '📝';
  }

  _formatSize(bytes) {
    if (!bytes && bytes !== 0) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  _escape(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
}
