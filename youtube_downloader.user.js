// ==UserScript==
// @name         YouTube Local HD Downloader
// @namespace    https://local.dev/
// @version      1.1.0
// @description  Submit YouTube download jobs to local backend and track progress.
// @match        https://www.youtube.com/*
// @match        https://m.youtube.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  'use strict';

  const API_BASE = 'http://127.0.0.1:5050';
  const BOOTSTRAP_URL = `${API_BASE}/auth/bootstrap?client=yt-userscript-v1`;
  const DEFAULT_LOCAL_TOKEN = 'change-me-local-token';
  const BTN_ID = 'yt-local-downloader-btn';
  const DASHBOARD_BTN_ID = 'yt-local-downloader-dashboard-btn';
  const CONFIG_KEY = 'yt-local-downloader-config-v1';
  const INJECTION_RETRY_DELAY_MS = 150;
  const INJECTION_MAX_RETRIES = 40;

  let pollTimer = null;
  let lastUrl = location.href;
  let bootstrapPromise = null;
  let activeProgressModal = null;
  let buttonRetryTimer = null;
  let buttonRetryCount = 0;

  function loadConfig() {
    const fallback = { mode: 'video', quality: 'best', token: DEFAULT_LOCAL_TOKEN };
    try {
      const raw = localStorage.getItem(CONFIG_KEY);
      if (!raw) return fallback;
      const parsed = JSON.parse(raw);
      const mode = parsed.mode === 'audio' ? 'audio' : 'video';
      const qualitySet = new Set(['best', '1080', '720', '480', '360', '240', '144', '320', '256', '128']);
      const quality = qualitySet.has(parsed.quality) ? parsed.quality : 'best';
      const token = typeof parsed.token === 'string' && parsed.token.trim()
        ? parsed.token.trim()
        : DEFAULT_LOCAL_TOKEN;
      return { mode, quality, token };
    } catch (_) {
      return fallback;
    }
  }

  function saveConfig(config) {
    localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
  }

  function gmRequest(options) {
    const fn = (typeof GM_xmlhttpRequest === 'function')
      ? GM_xmlhttpRequest
      : (typeof GM !== 'undefined' && typeof GM.xmlHttpRequest === 'function' ? GM.xmlHttpRequest : null);
    if (!fn) {
      throw new Error('GM_xmlhttpRequest is not available');
    }
    fn(options);
  }

  function alignTokenFromBackend(force = false) {
    if (!force && bootstrapPromise) {
      return bootstrapPromise;
    }

    bootstrapPromise = new Promise((resolve) => {
      try {
        gmRequest({
          method: 'GET',
          url: BOOTSTRAP_URL,
          timeout: 4000,
          onload: (resp) => {
            if (resp.status < 200 || resp.status >= 300) {
              resolve(false);
              return;
            }
            let parsed = null;
            try {
              parsed = resp.responseText ? JSON.parse(resp.responseText) : null;
            } catch (_) {
              parsed = null;
            }
            const token = parsed && typeof parsed.token === 'string' ? parsed.token.trim() : '';
            if (!token) {
              resolve(false);
              return;
            }
            const current = loadConfig();
            if (current.token !== token) {
              saveConfig({ ...current, token });
            }
            resolve(true);
          },
          ontimeout: () => resolve(false),
          onerror: () => resolve(false),
        });
      } catch (_) {
        resolve(false);
      }
    });

    return bootstrapPromise;
  }

  function request(method, url, data, timeout = 10000, hasRetried = false, networkRetryCount = 0) {
    return new Promise((resolve, reject) => {
      const token = loadConfig().token;
      gmRequest({
        method,
        url,
        timeout,
        headers: {
          'Content-Type': 'application/json',
          'X-Local-Token': token,
        },
        data: data ? JSON.stringify(data) : undefined,
        onload: async (resp) => {
          let parsed = null;
          try {
            parsed = resp.responseText ? JSON.parse(resp.responseText) : null;
          } catch (_) {
            parsed = null;
          }
          if (resp.status === 401 && !hasRetried) {
            const aligned = await alignTokenFromBackend(true);
            if (aligned) {
              request(method, url, data, timeout, true).then(resolve).catch(reject);
              return;
            }
          }
          if (resp.status >= 200 && resp.status < 300) {
            resolve(parsed);
          } else {
            reject(new Error((parsed && parsed.error) || `HTTP ${resp.status}`));
          }
        },
        ontimeout: () => {
          if (networkRetryCount < 2) {
            setTimeout(() => {
              request(method, url, data, timeout, hasRetried, networkRetryCount + 1).then(resolve).catch(reject);
            }, 250 * (networkRetryCount + 1));
            return;
          }
          reject(new Error('请求超时：本地后端未响应'));
        },
        onerror: () => {
          if (networkRetryCount < 2) {
            setTimeout(() => {
              request(method, url, data, timeout, hasRetried, networkRetryCount + 1).then(resolve).catch(reject);
            }, 250 * (networkRetryCount + 1));
            return;
          }
          reject(new Error('连接失败：请确认本地后端已启动'));
        },
      });
    });
  }

  function setButtonState(btn, state, text) {
    const styles = {
      idle: {
        text: '下载',
        bg: '#d62828',
        color: '#ffffff',
        disabled: false,
      },
      submitting: {
        text: '提交中...',
        bg: '#f77f00',
        color: '#ffffff',
        disabled: true,
      },
      running: {
        text: '下载中...',
        bg: '#2a9d8f',
        color: '#ffffff',
        disabled: true,
      },
      success: {
        text: '已完成',
        bg: '#2a9d8f',
        color: '#ffffff',
        disabled: false,
      },
      error: {
        text: '失败重试',
        bg: '#6c757d',
        color: '#ffffff',
        disabled: false,
      },
    };

    const selected = styles[state] || styles.idle;
    btn.textContent = text || selected.text;
    btn.style.background = selected.bg;
    btn.style.color = selected.color;
    btn.disabled = selected.disabled;
    btn.style.opacity = selected.disabled ? '0.85' : '1';
    btn.dataset.state = state;
  }

  function clearPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function closeActiveProgressModal() {
    if (activeProgressModal && typeof activeProgressModal.close === 'function') {
      activeProgressModal.close();
    }
    activeProgressModal = null;
  }

  function modeLabel(mode) {
    return mode === 'audio' ? '音频' : '视频';
  }

  function qualityLabel(quality) {
    if (quality === '320') return '320kbps';
    if (quality === '256') return '256kbps';
    if (quality === '128') return '128kbps';
    if (quality === '1080') return '1080p';
    if (quality === '720') return '720p';
    if (quality === '480') return '480p';
    if (quality === '360') return '360p';
    if (quality === '240') return '240p';
    if (quality === '144') return '144p';
    return '最佳';
  }

  function openDownloadOptionsModal(currentConfig) {
    return new Promise((resolve) => {
      const root = document.createElement('div');
      root.style.position = 'fixed';
      root.style.inset = '0';
      root.style.zIndex = '2147483647';

      const shadow = root.attachShadow({ mode: 'open' });

      const overlay = document.createElement('div');
      overlay.style.position = 'fixed';
      overlay.style.inset = '0';
      overlay.style.background = 'rgba(0, 0, 0, 0.45)';
      overlay.style.display = 'flex';
      overlay.style.alignItems = 'center';
      overlay.style.justifyContent = 'center';
      overlay.style.padding = '24px';
      overlay.style.boxSizing = 'border-box';
      overlay.style.fontFamily = 'Arial, sans-serif';

      const card = document.createElement('div');
      card.style.width = 'min(96vw, 1180px)';
      card.style.background = '#ffffff';
      card.style.borderRadius = '4px';
      card.style.boxShadow = '0 14px 36px rgba(0, 0, 0, 0.28)';
      card.style.color = '#222222';
      card.style.overflow = 'hidden';

      const tabDefs = [
        { key: 'audio', icon: '♫', label: 'Audio' },
        { key: 'video', icon: '▮', label: 'Video' },
      ];
      const rowsByTab = {
        audio: [
          { mode: 'audio', quality: '320', fileType: 'MP3 - 320kbps', format: 'Auto' },
          { mode: 'audio', quality: '256', fileType: 'MP3 - 256kbps', format: 'Auto' },
          { mode: 'audio', quality: '128', fileType: 'MP3 - 128kbps', format: 'Auto' },
        ],
        video: [
          { mode: 'video', quality: '1080', fileType: '1080p (.mp4)', format: 'Auto' },
          { mode: 'video', quality: '720', fileType: '720p (.mp4)', format: 'Auto' },
          { mode: 'video', quality: '480', fileType: '480p (.mp4)', format: 'Auto' },
          { mode: 'video', quality: '360', fileType: '360p (.mp4)', format: 'Auto' },
          { mode: 'video', quality: '240', fileType: '240p (.mp4)', format: 'Auto' },
          { mode: 'video', quality: '144', fileType: '144p (.mp4)', format: 'Auto' },
          { mode: 'video', quality: 'best', fileType: 'Best Quality (.mp4)', format: 'Auto' },
        ],
      };

      const normalizeTab = (config) => {
        if (config.mode === 'audio') return 'audio';
        return 'video';
      };

      let activeTab = normalizeTab(currentConfig);

      const tabs = document.createElement('div');
      tabs.style.display = 'flex';
      tabs.style.borderBottom = '1px solid #d1d5db';
      tabs.style.background = '#f9fafb';

      const grid = document.createElement('div');
      grid.style.width = '100%';
      grid.style.background = '#ffffff';

      const headerRow = document.createElement('div');
      headerRow.style.display = 'grid';
      headerRow.style.gridTemplateColumns = '2fr 1fr 1.25fr';
      headerRow.style.borderBottom = '1px solid #d1d5db';

      const headCells = ['File type', 'Format', 'Action'];
      for (let i = 0; i < headCells.length; i += 1) {
        const headCell = document.createElement('div');
        headCell.textContent = headCells[i];
        headCell.style.padding = '22px 34px';
        headCell.style.fontSize = '16px';
        headCell.style.fontWeight = '700';
        headCell.style.color = '#222222';
        headCell.style.borderRight = i < headCells.length - 1 ? '1px solid #d1d5db' : 'none';
        headerRow.appendChild(headCell);
      }
      grid.appendChild(headerRow);

      const bodyRows = document.createElement('div');
      bodyRows.style.minHeight = '120px';
      grid.appendChild(bodyRows);

      const closeWrap = document.createElement('div');
      closeWrap.style.display = 'flex';
      closeWrap.style.justifyContent = 'flex-end';
      closeWrap.style.padding = '12px 14px';
      closeWrap.style.borderTop = '1px solid #e5e7eb';
      closeWrap.style.background = '#ffffff';

      const cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.textContent = '关闭';
      cancelBtn.style.padding = '8px 14px';
      cancelBtn.style.border = '1px solid #d1d5db';
      cancelBtn.style.borderRadius = '6px';
      cancelBtn.style.background = '#ffffff';
      cancelBtn.style.cursor = 'pointer';
      cancelBtn.style.fontWeight = '600';
      closeWrap.appendChild(cancelBtn);

      const tabButtons = new Map();
      const tabPanels = new Map();

      const close = (value) => {
        root.remove();
        document.removeEventListener('keydown', onKeydown);
        resolve(value);
      };

      const createRow = (row) => {
        const rowLine = document.createElement('div');
        rowLine.style.display = 'grid';
        rowLine.style.gridTemplateColumns = '2fr 1fr 1.25fr';
        rowLine.style.borderBottom = '1px solid #d1d5db';

        const typeCell = document.createElement('div');
        typeCell.textContent = row.fileType;
        typeCell.style.padding = '18px 34px';
        typeCell.style.fontSize = '16px';
        typeCell.style.lineHeight = '1.15';
        typeCell.style.borderRight = '1px solid #d1d5db';
        typeCell.style.display = 'flex';
        typeCell.style.alignItems = 'center';

        const formatCell = document.createElement('div');
        formatCell.textContent = row.format;
        formatCell.style.padding = '18px 34px';
        formatCell.style.fontSize = '16px';
        formatCell.style.lineHeight = '1.15';
        formatCell.style.borderRight = '1px solid #d1d5db';
        formatCell.style.display = 'flex';
        formatCell.style.alignItems = 'center';

        const actionCell = document.createElement('div');
        actionCell.style.padding = '12px 24px';
        actionCell.style.display = 'flex';
        actionCell.style.alignItems = 'center';

        const rowBtn = document.createElement('button');
        rowBtn.type = 'button';
        rowBtn.textContent = '⬇ Download';
        rowBtn.style.display = 'inline-flex';
        rowBtn.style.alignItems = 'center';
        rowBtn.style.justifyContent = 'center';
        rowBtn.style.padding = '12px 20px';
        rowBtn.style.minWidth = '210px';
        rowBtn.style.border = 'none';
        rowBtn.style.borderRadius = '8px';
        rowBtn.style.background = '#59b85a';
        rowBtn.style.color = '#ffffff';
        rowBtn.style.fontSize = '18px';
        rowBtn.style.fontWeight = '700';
        rowBtn.style.lineHeight = '1';
        rowBtn.style.cursor = 'pointer';
        rowBtn.addEventListener('mouseenter', () => {
          rowBtn.style.background = '#4ba84d';
        });
        rowBtn.addEventListener('mouseleave', () => {
          rowBtn.style.background = '#59b85a';
        });
        rowBtn.addEventListener('click', () => {
          close({ mode: row.mode, quality: row.quality });
        });

        actionCell.appendChild(rowBtn);
        rowLine.appendChild(typeCell);
        rowLine.appendChild(formatCell);
        rowLine.appendChild(actionCell);
        return rowLine;
      };

      const ensureTabPanel = (tabKey) => {
        const panel = document.createElement('div');
        panel.style.display = 'none';

        const rows = rowsByTab[tabKey] || [];
        if (rows.length === 0) {
          const empty = document.createElement('div');
          empty.textContent = '暂无可用选项';
          empty.style.padding = '18px 34px';
          empty.style.fontSize = '14px';
          empty.style.color = '#6b7280';
          panel.appendChild(empty);
        } else {
          for (const row of rows) {
            panel.appendChild(createRow(row));
          }
        }
        return panel;
      };

      const syncTabState = () => {
        for (const tab of tabDefs) {
          const btn = tabButtons.get(tab.key);
          const panel = tabPanels.get(tab.key);
          if (!btn || !panel) continue;
          const selected = tab.key === activeTab;
          btn.style.background = selected ? '#ffffff' : '#f3f4f6';
          btn.style.color = selected ? '#c8104f' : '#4b5563';
          btn.style.borderBottom = selected ? '2px solid #ffffff' : '2px solid #f3f4f6';
          panel.style.display = selected ? 'block' : 'none';
        }
      };

      for (const tab of tabDefs) {
        const tabBtn = document.createElement('button');
        tabBtn.type = 'button';
        tabBtn.textContent = `${tab.icon} ${tab.label}`;
        tabBtn.style.padding = '16px 26px';
        tabBtn.style.border = 'none';
        tabBtn.style.borderRight = '1px solid #d1d5db';
        tabBtn.style.fontSize = '16px';
        tabBtn.style.fontWeight = '700';
        tabBtn.style.cursor = 'pointer';
        tabBtn.style.background = '#f3f4f6';
        tabBtn.style.color = '#4b5563';
        tabBtn.style.lineHeight = '1';
        tabBtn.addEventListener('click', () => {
          activeTab = tab.key;
          syncTabState();
        });
        tabButtons.set(tab.key, tabBtn);
        tabs.appendChild(tabBtn);

        const panel = ensureTabPanel(tab.key);
        tabPanels.set(tab.key, panel);
        bodyRows.appendChild(panel);
      }

      try {
        console.info('[yt-local-downloader] option rows prepared', {
          activeTab,
          audio: rowsByTab.audio.length,
          video: rowsByTab.video.length,
        });
      } catch (_) {
      }

      card.appendChild(tabs);
      card.appendChild(grid);
      card.appendChild(closeWrap);
      overlay.appendChild(card);
      shadow.appendChild(overlay);
      document.body.appendChild(root);

      const onKeydown = (event) => {
        if (event.key === 'Escape') {
          close(null);
        }
      };
      document.addEventListener('keydown', onKeydown);

      overlay.addEventListener('click', (event) => {
        if (event.target === overlay) {
          close(null);
        }
      });

      cancelBtn.addEventListener('click', () => close(null));
      syncTabState();
    });
  }

  function openProgressModal(config) {
    closeActiveProgressModal();

    const overlay = document.createElement('div');
    overlay.style.position = 'fixed';
    overlay.style.right = '20px';
    overlay.style.bottom = '20px';
    overlay.style.zIndex = '999999';
    overlay.style.width = 'min(92vw, 360px)';
    overlay.style.background = '#ffffff';
    overlay.style.border = '1px solid #e5e7eb';
    overlay.style.borderRadius = '14px';
    overlay.style.padding = '14px';
    overlay.style.boxShadow = '0 18px 40px rgba(0, 0, 0, 0.26)';
    overlay.style.color = '#111827';

    const title = document.createElement('div');
    title.textContent = '下载进度';
    title.style.fontSize = '15px';
    title.style.fontWeight = '700';
    title.style.marginBottom = '6px';
    overlay.appendChild(title);

    const meta = document.createElement('div');
    meta.textContent = `${modeLabel(config.mode)} / ${qualityLabel(config.quality)}`;
    meta.style.fontSize = '12px';
    meta.style.color = '#4b5563';
    meta.style.marginBottom = '8px';
    overlay.appendChild(meta);

    const status = document.createElement('div');
    status.textContent = '准备提交任务...';
    status.style.fontSize = '13px';
    status.style.marginBottom = '8px';
    overlay.appendChild(status);

    const progressBarBg = document.createElement('div');
    progressBarBg.style.width = '100%';
    progressBarBg.style.height = '8px';
    progressBarBg.style.borderRadius = '999px';
    progressBarBg.style.background = '#e5e7eb';
    progressBarBg.style.overflow = 'hidden';

    const progressBar = document.createElement('div');
    progressBar.style.width = '0%';
    progressBar.style.height = '100%';
    progressBar.style.background = '#2a9d8f';
    progressBar.style.transition = 'width 0.25s ease';
    progressBarBg.appendChild(progressBar);
    overlay.appendChild(progressBarBg);

    const footer = document.createElement('div');
    footer.style.display = 'flex';
    footer.style.justifyContent = 'space-between';
    footer.style.alignItems = 'center';
    footer.style.marginTop = '10px';

    const pct = document.createElement('div');
    pct.textContent = '0%';
    pct.style.fontSize = '12px';
    pct.style.color = '#4b5563';

    const hideBtn = document.createElement('button');
    hideBtn.type = 'button';
    hideBtn.textContent = '关闭';
    hideBtn.style.padding = '5px 9px';
    hideBtn.style.border = '1px solid #d1d5db';
    hideBtn.style.borderRadius = '7px';
    hideBtn.style.background = '#ffffff';
    hideBtn.style.cursor = 'pointer';

    footer.appendChild(pct);
    footer.appendChild(hideBtn);
    overlay.appendChild(footer);

    document.body.appendChild(overlay);

    const modal = {
      update: (message, progress) => {
        status.textContent = message || '下载中...';
        if (typeof progress === 'number' && Number.isFinite(progress)) {
          const value = Math.max(0, Math.min(100, Math.round(progress)));
          progressBar.style.width = `${value}%`;
          pct.textContent = `${value}%`;
        }
      },
      success: (message) => {
        status.textContent = message || '下载完成';
        progressBar.style.width = '100%';
        progressBar.style.background = '#2a9d8f';
        pct.textContent = '100%';
      },
      fail: (message) => {
        status.textContent = message || '下载失败';
        progressBar.style.background = '#d62828';
      },
      close: () => {
        overlay.remove();
      },
    };

    hideBtn.addEventListener('click', () => {
      modal.close();
      if (activeProgressModal === modal) {
        activeProgressModal = null;
      }
    });

    activeProgressModal = modal;
    return modal;
  }

  function handleAuthRiskControl(result) {
    const detail = result && result.error && result.error.detail
      ? String(result.error.detail)
      : '';
    const hint = [
      '检测到 YouTube 风控或挑战页。',
      '建议重启后端并确保可读取浏览器 Cookies（推荐 Chrome）。',
      '如果仍失败，请在后端启用 YTDLP_REMOTE_COMPONENTS=ejs:github。',
      detail ? `\n错误摘要:\n${detail.slice(0, 260)}` : '',
    ].join('\n');
    alert(hint);
  }

  async function pollJob(jobId, btn, progressModal) {
    try {
      const result = await request('GET', `${API_BASE}/jobs/${jobId}`);
      if (!result || !result.status) {
        throw new Error('状态响应无效');
      }

      if (result.status === 'queued') {
        setButtonState(btn, 'running', '排队中...');
        if (progressModal) progressModal.update('任务排队中...', result.progress);
        pollTimer = setTimeout(() => pollJob(jobId, btn, progressModal), 1200);
        return;
      }

      if (result.status === 'running' || result.status === 'merging') {
        const progress = typeof result.progress === 'number' ? result.progress : 0;
        const text = result.status === 'merging'
          ? '合并中...'
          : `下载中 ${progress}%`;
        setButtonState(btn, 'running', text);
        if (progressModal) {
          progressModal.update(
            result.status === 'merging' ? '正在合并音视频...' : `下载中 ${progress}%`,
            progress,
          );
        }
        pollTimer = setTimeout(() => pollJob(jobId, btn, progressModal), 1200);
        return;
      }

      if (result.status === 'completed') {
        setButtonState(btn, 'success', '已完成');
        if (progressModal) progressModal.success('下载完成，文件已保存到本地目录。');
        setTimeout(() => setButtonState(btn, 'idle'), 2200);
        return;
      }

      const detail = result.error && result.error.detail ? result.error.detail : '未知错误';
      const errType = result.error && result.error.type ? result.error.type : '';
      setButtonState(btn, 'error', '失败重试');
      if (progressModal) progressModal.fail(`下载失败: ${detail}`);
      if (errType === 'auth_required' || errType === 'cookies_required' || /not a bot|sign in to confirm/i.test(detail)) {
        handleAuthRiskControl(result);
      }
      alert(`下载失败:\n${detail}`);
    } catch (err) {
      setButtonState(btn, 'error', '状态查询失败');
      if (progressModal) progressModal.fail(err.message || '状态查询失败');
      alert(err.message || '状态查询失败');
    }
  }

  async function submitDownload(btn, config) {
    clearPolling();
    closeActiveProgressModal();
    setButtonState(btn, 'submitting');
    const progressModal = openProgressModal(config);
    progressModal.update('正在提交下载任务...', 0);

    const url = location.href;
    const titleNode = document.querySelector('h1.ytd-watch-metadata yt-formatted-string') || document.querySelector('title');
    const title = (titleNode && titleNode.textContent ? titleNode.textContent.trim() : document.title) || '';

    try {
      const created = await request('POST', `${API_BASE}/jobs`, {
        url,
        title,
        mode: config.mode,
        quality: config.quality,
      });
      if (!created || !created.job_id) {
        throw new Error('后端未返回 job_id');
      }
      setButtonState(btn, 'running', `${modeLabel(config.mode)} ${qualityLabel(config.quality)} 0%`);
      progressModal.update('任务已提交，等待下载开始...', 0);
      pollJob(created.job_id, btn, progressModal);
    } catch (err) {
      setButtonState(btn, 'error', '连接后端失败');
      progressModal.fail(err.message || '提交任务失败');
      alert(err.message || '提交任务失败');
    }
  }

  function openDashboard() {
    const url = `${API_BASE}/dashboard?token=${encodeURIComponent(loadConfig().token)}`;
    window.open(url, '_blank', 'noopener,noreferrer');
  }

  function makeButton() {
    const btn = document.createElement('button');
    btn.id = BTN_ID;
    btn.type = 'button';
    btn.style.marginLeft = '12px';
    btn.style.padding = '8px 14px';
    btn.style.border = 'none';
    btn.style.borderRadius = '20px';
    btn.style.fontWeight = '700';
    btn.style.fontSize = '13px';
    btn.style.cursor = 'pointer';
    btn.style.letterSpacing = '0.2px';
    setButtonState(btn, 'idle');

    btn.addEventListener('click', () => {
      const current = loadConfig();
      openDownloadOptionsModal(current).then((selected) => {
        if (!selected) return;
        const nextConfig = {
          ...current,
          mode: selected.mode,
          quality: selected.quality,
        };
        saveConfig(nextConfig);
        submitDownload(btn, nextConfig);
      });
    });

    return btn;
  }

  function makeDashboardButton() {
    const btn = document.createElement('button');
    btn.id = DASHBOARD_BTN_ID;
    btn.type = 'button';
    btn.textContent = '面板';
    btn.style.marginLeft = '8px';
    btn.style.padding = '8px 10px';
    btn.style.border = '1px solid #1d3557';
    btn.style.borderRadius = '20px';
    btn.style.fontWeight = '700';
    btn.style.fontSize = '12px';
    btn.style.cursor = 'pointer';
    btn.style.background = '#f1faee';
    btn.style.color = '#1d3557';
    btn.addEventListener('click', openDashboard);
    return btn;
  }

  function clearButtonRetryTimer() {
    if (buttonRetryTimer) {
      clearTimeout(buttonRetryTimer);
      buttonRetryTimer = null;
    }
  }

  function findHostContainer() {
    const selectors = [
      '#top-row #owner',
      'ytd-watch-metadata #owner',
      'ytd-video-secondary-info-renderer #owner',
      '#secondary #owner',
      '#above-the-fold #top-row',
    ];
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      if (node) {
        return node;
      }
    }
    return null;
  }

  function ensureButton() {
    if (!location.pathname.startsWith('/watch')) {
      return false;
    }

    const existing = document.getElementById(BTN_ID);
    if (existing) {
      return true;
    }

    const host = findHostContainer();
    if (!host) {
      return false;
    }

    const wrapper = document.createElement('div');
    wrapper.style.display = 'inline-flex';
    wrapper.style.alignItems = 'center';
    wrapper.style.marginLeft = '12px';
    wrapper.appendChild(makeButton());
    wrapper.appendChild(makeDashboardButton());
    host.appendChild(wrapper);
    return true;
  }

  function scheduleEnsureButton(delay = 0, resetRetryCount = false, replaceExisting = false) {
    if (resetRetryCount) {
      buttonRetryCount = 0;
    }

    if (buttonRetryTimer && !replaceExisting) {
      return;
    }

    clearButtonRetryTimer();
    buttonRetryTimer = setTimeout(() => {
      buttonRetryTimer = null;

      if (ensureButton()) {
        buttonRetryCount = 0;
        return;
      }

      if (!location.pathname.startsWith('/watch')) {
        buttonRetryCount = 0;
        return;
      }

      if (document.getElementById(BTN_ID)) {
        buttonRetryCount = 0;
        return;
      }

      if (buttonRetryCount >= INJECTION_MAX_RETRIES) {
        return;
      }

      buttonRetryCount += 1;
      scheduleEnsureButton(INJECTION_RETRY_DELAY_MS);
    }, delay);
  }

  function setupSpaHooks() {
    const attachObserver = () => {
      const root = document.documentElement;
      if (!root) {
        document.addEventListener('DOMContentLoaded', attachObserver, { once: true });
        return;
      }

      const observer = new MutationObserver(() => {
        scheduleEnsureButton(100);
      });
      observer.observe(root, {
        childList: true,
        subtree: true,
      });
    };

    attachObserver();

    setInterval(() => {
      if (lastUrl !== location.href) {
        lastUrl = location.href;
        clearPolling();
        scheduleEnsureButton(0, true, true);
      }
    }, 800);

    document.addEventListener('yt-navigate-finish', () => {
      clearPolling();
      scheduleEnsureButton(50, true, true);
    });
  }

  scheduleEnsureButton(0, true, true);
  alignTokenFromBackend(false);
  setupSpaHooks();
})();
