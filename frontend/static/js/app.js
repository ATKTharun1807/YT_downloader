/**
 * app.js — YT_DOWNLOADER frontend SPA logic
 *
 * Handles:
 *  - SPA navigation (sidebar + mobile nav)
 *  - URL analysis (POST /api/analyze)
 *  - Quality selection
 *  - Playlist single/full choice
 *  - Download initiation (POST /api/download)
 *  - Real-time progress via SSE (GET /api/progress/<job_id>)
 *  - Completion display + open file/folder
 *  - Download history (GET/DELETE /api/downloads)
 *  - Settings (GET/POST /api/settings)
 *  - FFmpeg status check
 *  - Theme persistence
 */

'use strict';

/* ============================================================
   State
   ============================================================ */
const state = {
  currentPage: 'home',
  videoInfo: null,          // result from /api/analyze
  selectedQuality: 'best',
  noplaylist: true,
  currentJobId: null,
  currentJobHistory: null,  // { id, title, quality, audio_only, directory }
  settings: {},
  sseSource: null,
};

/* ============================================================
   DOM refs
   ============================================================ */
const $ = id => document.getElementById(id);

const dom = {
  // Nav
  navItems: document.querySelectorAll('[data-page]'),

  // Home page
  urlInput:          $('urlInput'),
  analyzeBtn:        $('analyzeBtn'),
  analyzeBtnText:    $('analyzeBtnText'),
  analyzeBtnIcon:    $('analyzeBtnIcon'),
  analyzeError:      $('analyzeError'),

  playlistCard:      $('playlistCard'),
  playlistCount:     $('playlistCount'),
  radioSingle:       $('radioSingle'),
  radioPlaylist:     $('radioPlaylist'),

  skeletonCard:      $('skeletonCard'),
  videoCard:         $('videoCard'),
  videoThumbnail:    $('videoThumbnail'),
  videoTitle:        $('videoTitle'),
  videoChannel:      $('videoChannel'),
  videoDuration:     $('videoDuration'),
  videoViews:        $('videoViews'),
  qualityGrid:       $('qualityGrid'),

  downloadSection:   $('downloadSection'),
  downloadDirDisplay:$('downloadDirDisplay'),
  changeDownloadDir: $('changeDownloadDir'),
  downloadDirError:  $('downloadDirError'),
  downloadBtn:       $('downloadBtn'),
  downloadBtnIcon:   $('downloadBtnIcon'),
  downloadError:     $('downloadError'),

  progressCard:      $('progressCard'),
  progressStageText: $('progressStageText'),
  progressBarFill:   $('progressBarFill'),
  progressBarWrap:   $('progressBarWrap'),
  progressPct:       $('progressPct'),
  progressSpeed:     $('progressSpeed'),
  progressEta:       $('progressEta'),

  completeCard:      $('completeCard'),
  completeTitle:     $('completeTitle'),
  completeQuality:   $('completeQuality'),
  completeFormat:    $('completeFormat'),
  completeDir:       $('completeDir'),
  openFileBtn:       $('openFileBtn'),
  openFolderBtn:     $('openFolderBtn'),
  downloadAnotherBtn:$('downloadAnotherBtn'),

  // Downloads page
  historyBody:       $('historyBody'),
  historyEmpty:      $('historyEmpty'),
  historyTableWrap:  $('historyTableWrap'),
  refreshHistoryBtn: $('refreshHistoryBtn'),

  // Settings page
  settingsDirDisplay:    $('settingsDirDisplay'),
  settingsChangeDirBtn:  $('settingsChangeDirBtn'),
  settingsQuality:       $('settingsQuality'),
  settingsMaxConcurrent: $('settingsMaxConcurrent'),
  settingsTheme:         $('settingsTheme'),
  clearHistoryBtn:       $('clearHistoryBtn'),
  saveSettingsBtn:       $('saveSettingsBtn'),
  settingsFeedback:      $('settingsFeedback'),

  // Sidebar footer
  ffmpegDot:   $('ffmpegDot'),
  ffmpegLabel: $('ffmpegLabel'),
};

/* ============================================================
   Navigation
   ============================================================ */
function navigateTo(page) {
  state.currentPage = page;

  // Update page visibility
  document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));
  const target = $(`page-${page}`);
  if (target) target.classList.add('active');

  // Update nav items
  document.querySelectorAll('[data-page]').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
    if (el.tagName === 'A') {
      el.setAttribute('aria-current', el.dataset.page === page ? 'page' : 'false');
    }
  });

  // Page-specific init
  if (page === 'downloads') loadHistory();
  if (page === 'settings') loadSettings();
}

document.querySelectorAll('[data-page]').forEach(el => {
  el.addEventListener('click', e => {
    e.preventDefault();
    navigateTo(el.dataset.page);
  });
});

/* ============================================================
   FFmpeg status
   ============================================================ */
async function checkFfmpeg() {
  try {
    const res = await fetch('/api/ffmpeg-status');
    const data = await res.json();
    dom.ffmpegDot.className = 'status-dot ' + (data.available ? 'ok' : 'err');
    dom.ffmpegLabel.textContent = data.available ? 'FFmpeg ready' : 'FFmpeg missing';
  } catch {
    dom.ffmpegLabel.textContent = 'FFmpeg unknown';
  }
}

/* ============================================================
   Analyze
   ============================================================ */
dom.analyzeBtn.addEventListener('click', handleAnalyze);
dom.urlInput.addEventListener('keydown', e => { if (e.key === 'Enter') handleAnalyze(); });

async function handleAnalyze() {
  const url = dom.urlInput.value.trim();
  if (!url) { showAnalyzeError('Please enter a YouTube URL.'); return; }

  // UI: analyzing state
  hideAll();
  dom.skeletonCard.classList.remove('hidden');
  setAnalyzeBtnLoading(true);
  clearAnalyzeError();

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    const data = await res.json();

    if (!data.success) {
      dom.skeletonCard.classList.add('hidden');
      setAnalyzeBtnLoading(false);
      showAnalyzeError(data.error || 'Failed to analyze video.');
      return;
    }

    state.videoInfo = data;
    state.noplaylist = true;

    dom.skeletonCard.classList.add('hidden');
    setAnalyzeBtnLoading(false);

    if (data.is_mixed && data.has_playlist) {
      dom.playlistCount.textContent = `${data.playlist_count || '?'} videos`;
      dom.playlistCard.classList.remove('hidden');
    }

    if (data.is_playlist && !data.is_mixed) {
      // Pure playlist URL
      dom.playlistCount.textContent = `${data.playlist_count || '?'} videos`;
      dom.playlistCard.classList.remove('hidden');
      // Show playlist thumbnail/title if available
      renderPlaylistInfo(data);
    } else {
      renderVideoInfo(data);
    }

  } catch (err) {
    dom.skeletonCard.classList.add('hidden');
    setAnalyzeBtnLoading(false);
    showAnalyzeError('Network error. Please check your connection.');
  }
}

// Playlist radio: re-analyze when user changes choice
dom.radioSingle.addEventListener('change', () => {
  state.noplaylist = true;
  if (state.videoInfo && state.videoInfo.is_mixed) {
    renderVideoInfo(state.videoInfo);
  }
});

dom.radioPlaylist.addEventListener('change', () => {
  state.noplaylist = false;
  if (state.videoInfo) {
    renderPlaylistInfo(state.videoInfo);
  }
});

function renderVideoInfo(data) {
  const info = data.is_playlist ? (data.entries?.[0] || {}) : data;

  dom.videoThumbnail.src = info.thumbnail || '';
  dom.videoThumbnail.alt = info.title || 'Video thumbnail';
  dom.videoTitle.textContent = info.title || 'Unknown Title';
  dom.videoChannel.textContent = info.channel ? `📺 ${info.channel}` : '';
  dom.videoDuration.textContent = `⏱ ${info.duration_str || 'Unknown'}`;

  if (info.view_count) {
    dom.videoViews.textContent = `👁 ${formatViews(info.view_count)}`;
    dom.videoViews.style.display = 'inline-flex';
  } else {
    dom.videoViews.style.display = 'none';
  }

  // Quality grid
  buildQualityGrid(info.quality_options || []);

  dom.videoCard.classList.remove('hidden');
  dom.downloadSection.classList.remove('hidden');

  // Set download dir display from settings
  dom.downloadDirDisplay.textContent = state.settings.download_dir || './downloads';
}

function renderPlaylistInfo(data) {
  // For playlist, show summary card
  dom.videoThumbnail.src = data.thumbnail || (data.entries?.[0]?.thumbnail || '');
  dom.videoTitle.textContent = data.playlist_title || 'Playlist';
  dom.videoChannel.textContent = data.uploader ? `📺 ${data.uploader}` : '';
  dom.videoDuration.textContent = `🎬 ${data.playlist_count || '?'} videos`;
  dom.videoViews.style.display = 'none';

  // Use "best" as default for playlists
  buildQualityGrid([
    { value: 'best', label: 'Best Available', description: 'Highest quality for each video' },
    { value: '1080', label: 'Full HD (1080p)', description: 'MP4 video' },
    { value: '720',  label: 'HD (720p)',       description: 'MP4 video' },
    { value: '480',  label: 'SD (480p)',        description: 'MP4 video' },
    { value: 'audio',label: 'Audio Only — MP3 192kbps', description: 'Extract audio' },
  ]);

  dom.videoCard.classList.remove('hidden');
  dom.downloadSection.classList.remove('hidden');
  dom.downloadDirDisplay.textContent = state.settings.download_dir || './downloads';
}

/* ============================================================
   Quality Grid
   ============================================================ */
function buildQualityGrid(options) {
  dom.qualityGrid.innerHTML = '';
  const defaultQ = state.settings.default_quality || 'best';
  state.selectedQuality = defaultQ;

  options.forEach(opt => {
    const card = document.createElement('div');
    card.className = 'quality-card' + (opt.value === 'audio' ? ' audio-card' : '');
    card.setAttribute('role', 'radio');
    card.setAttribute('aria-checked', opt.value === defaultQ ? 'true' : 'false');
    card.setAttribute('tabindex', '0');
    card.dataset.value = opt.value;

    card.innerHTML = `
      <div class="quality-card-label">${escHtml(opt.label)}</div>
      <div class="quality-card-desc">${escHtml(opt.description || '')}</div>
    `;

    if (opt.value === defaultQ) card.classList.add('selected');

    card.addEventListener('click', () => selectQuality(opt.value));
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectQuality(opt.value); }
    });

    dom.qualityGrid.appendChild(card);
  });
}

function selectQuality(value) {
  state.selectedQuality = value;
  dom.qualityGrid.querySelectorAll('.quality-card').forEach(c => {
    const sel = c.dataset.value === value;
    c.classList.toggle('selected', sel);
    c.setAttribute('aria-checked', sel ? 'true' : 'false');
  });
}

/* ============================================================
   Download directory — native folder picker
   ============================================================ */
dom.changeDownloadDir.addEventListener('click', openFolderPicker);

async function openFolderPicker(targetDirDisplay, targetErrorEl) {
  // targetDirDisplay and targetErrorEl are optional DOM refs;
  // defaults to the Home page elements.
  const dirDisplay = (targetDirDisplay instanceof HTMLElement) ? targetDirDisplay : dom.downloadDirDisplay;
  const errorEl    = (targetErrorEl   instanceof HTMLElement) ? targetErrorEl   : dom.downloadDirError;

  const btn = dom.changeDownloadDir;
  const originalText = btn.innerHTML;

  // Loading state
  btn.disabled = true;
  btn.innerHTML = '⏳ Opening folder selector…';
  errorEl.classList.add('hidden');

  try {
    const res = await fetch('/api/select-folder');
    const data = await res.json();

    if (data.cancelled) {
      // User closed the dialog — keep existing directory, no error
      return;
    }

    if (!data.success) {
      errorEl.textContent = data.error || 'Unable to select download folder.';
      errorEl.classList.remove('hidden');
      return;
    }

    // Update displayed path
    const newDir = data.directory;
    state.settings.download_dir = newDir;
    dirDisplay.textContent = newDir;

    // Brief success feedback on the button
    btn.innerHTML = '✅ Location updated';
    setTimeout(() => { btn.innerHTML = originalText; }, 2000);

  } catch (err) {
    errorEl.textContent = 'Network error while opening folder selector.';
    errorEl.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    // Restore button text if not already done via the success path
    if (btn.innerHTML === '⏳ Opening folder selector…') {
      btn.innerHTML = originalText;
    }
  }
}

/* ============================================================
   Download
   ============================================================ */
dom.downloadBtn.addEventListener('click', handleDownload);

async function handleDownload() {
  if (!state.videoInfo) return;

  clearErrors();
  const url = state.videoInfo.url || dom.urlInput.value.trim();
  const quality = state.selectedQuality;
  const audioOnly = quality === 'audio';
  const noplaylist = state.noplaylist;
  const downloadDir = state.settings.download_dir || '';

  // Get metadata for the job
  const info = state.videoInfo.is_playlist && !noplaylist
    ? state.videoInfo
    : (state.videoInfo.is_playlist ? (state.videoInfo.entries?.[0] || state.videoInfo) : state.videoInfo);

  const title = info.title || info.playlist_title || '';
  const thumbnail = info.thumbnail || '';
  const channel = info.channel || info.uploader || '';
  const durationStr = info.duration_str || '';

  // UI
  dom.downloadBtn.disabled = true;
  dom.downloadBtnIcon.textContent = '⟳';
  showProgress('preparing', 0, '', '');

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        quality,
        audio_only: audioOnly,
        noplaylist,
        download_dir: downloadDir,
        title,
        thumbnail,
        channel,
        duration_str: durationStr,
      }),
    });

    const data = await res.json();

    if (!data.success) {
      dom.downloadBtn.disabled = false;
      dom.downloadBtnIcon.textContent = '⬇';
      hideProgress();
      showDownloadError(data.error || 'Failed to start download.');
      return;
    }

    state.currentJobId = data.job_id;
    state.currentJobHistory = {
      id: data.job_id,
      title,
      quality,
      audio_only: audioOnly,
      directory: downloadDir,
    };

    // Start SSE progress stream
    startSSE(data.job_id, { title, quality, audioOnly, channel, durationStr });

  } catch (err) {
    dom.downloadBtn.disabled = false;
    dom.downloadBtnIcon.textContent = '⬇';
    hideProgress();
    showDownloadError('Network error. Please try again.');
  }
}

/* ============================================================
   SSE Progress
   ============================================================ */
function startSSE(jobId, meta) {
  // Close any existing SSE
  if (state.sseSource) { state.sseSource.close(); state.sseSource = null; }

  const source = new EventSource(`/api/progress/${jobId}`);
  state.sseSource = source;

  source.onmessage = e => {
    try {
      const ev = JSON.parse(e.data);
      handleProgressEvent(ev, meta);
    } catch {/* ignore parse errors */}
  };

  source.onerror = () => {
    source.close();
    state.sseSource = null;
    // If job already completed we'll be fine; if not show error
    if (state.currentJobId === jobId) {
      // Poll once as fallback
      pollJobStatus(jobId, meta);
    }
  };
}

async function pollJobStatus(jobId, meta) {
  try {
    const res = await fetch(`/api/job/${jobId}`);
    const data = await res.json();
    if (data.success) handleProgressEvent(data, meta);
  } catch { /* ignore */ }
}

const STAGE_LABELS = {
  pending:           '⏳ Preparing download…',
  preparing:         '⏳ Preparing download…',
  downloading:       '⬇ Downloading…',
  merging:           '⚙ Merging video + audio…',
  extracting_audio:  '🎵 Extracting audio…',
  cleaning:          '🧹 Cleaning temporary files…',
  completed:         '✅ Download complete!',
  failed:            '❌ Download failed.',
};

function handleProgressEvent(ev, meta) {
  const status = ev.status || ev.stage || '';

  if (status === 'downloading') {
    showProgress('downloading', ev.percentage || 0, ev.speed || '', ev.eta || '');
  } else if (status === 'merging' || status === 'extracting_audio') {
    showProgress(status, 100, '', '');
  } else if (status === 'cleaning') {
    showProgress('cleaning', 100, '', '');
  } else if (status === 'completed') {
    showCompletionCard(ev, meta);
  } else if (status === 'failed') {
    hideProgress();
    dom.downloadBtn.disabled = false;
    dom.downloadBtnIcon.textContent = '⬇';
    showDownloadError(ev.error || 'Download failed. Please try again.');
    if (state.sseSource) { state.sseSource.close(); state.sseSource = null; }
  }
}

function showProgress(stage, pct, speed, eta) {
  dom.progressCard.classList.remove('hidden');
  dom.completeCard.classList.add('hidden');

  const label = STAGE_LABELS[stage] || `⟳ ${stage}…`;
  dom.progressStageText.textContent = label;

  const fillPct = Math.min(100, Math.max(0, pct));
  dom.progressBarFill.style.width = fillPct + '%';
  dom.progressBarWrap.setAttribute('aria-valuenow', fillPct);
  dom.progressPct.textContent = fillPct.toFixed(0) + '%';
  dom.progressSpeed.textContent = speed || '';
  dom.progressEta.textContent = eta ? `ETA ${eta}` : '';
}

function hideProgress() {
  dom.progressCard.classList.add('hidden');
}

function showCompletionCard(ev, meta) {
  if (state.sseSource) { state.sseSource.close(); state.sseSource = null; }
  dom.downloadBtn.disabled = false;
  dom.downloadBtnIcon.textContent = '⬇';
  hideProgress();

  dom.completeTitle.textContent = meta.title || ev.filename || 'Your video';
  dom.completeQuality.textContent = qualityLabel(meta.quality);
  dom.completeFormat.textContent = meta.audioOnly ? 'MP3' : 'MP4';
  dom.completeDir.textContent = ev.directory || state.settings.download_dir || './downloads';

  dom.completeCard.classList.remove('hidden');

  // Store job ID on buttons
  const jobId = state.currentJobId;
  dom.openFileBtn.dataset.jobId = jobId;
  dom.openFolderBtn.dataset.jobId = jobId;
}

dom.openFileBtn.addEventListener('click', () => {
  const jobId = dom.openFileBtn.dataset.jobId;
  if (!jobId) return;
  fetch(`/api/open-file/${jobId}`, { method: 'POST' }).catch(() => {});
});

dom.openFolderBtn.addEventListener('click', () => {
  const jobId = dom.openFolderBtn.dataset.jobId;
  if (!jobId) return;
  fetch(`/api/open-folder/${jobId}`, { method: 'POST' }).catch(() => {});
});

dom.downloadAnotherBtn.addEventListener('click', () => {
  resetHomeForNew();
});

/* ============================================================
   History page
   ============================================================ */
dom.refreshHistoryBtn.addEventListener('click', loadHistory);

async function loadHistory() {
  try {
    const res = await fetch('/api/downloads');
    const data = await res.json();
    renderHistory(data.downloads || []);
  } catch {
    renderHistory([]);
  }
}

function renderHistory(entries) {
  if (!entries.length) {
    dom.historyEmpty.classList.remove('hidden');
    dom.historyTableWrap.classList.add('hidden');
    return;
  }

  dom.historyEmpty.classList.add('hidden');
  dom.historyTableWrap.classList.remove('hidden');

  dom.historyBody.innerHTML = entries.map(e => `
    <tr>
      <td>
        ${e.thumbnail
          ? `<img class="history-thumb" src="${escHtml(e.thumbnail)}" alt="" loading="lazy" />`
          : `<div class="history-thumb" style="background:var(--bg-tertiary);"></div>`
        }
      </td>
      <td class="history-title">
        <p title="${escHtml(e.title || '')}">${escHtml(truncate(e.title || 'Unknown', 48))}</p>
        <small>${escHtml(e.channel || '')} ${e.duration_str ? '· ' + escHtml(e.duration_str) : ''}</small>
      </td>
      <td>
        <span class="badge">${escHtml(qualityLabel(e.quality))} · ${escHtml(e.format || 'mp4').toUpperCase()}</span>
      </td>
      <td>
        <span class="status-badge ${escHtml(e.status || 'pending')}">${escHtml(e.status || '—')}</span>
      </td>
      <td style="color:var(--text-muted); font-size:12px;">
        ${e.completed_at ? formatDate(e.completed_at) : '—'}
      </td>
      <td class="history-actions">
        ${e.status === 'completed' ? `
          <button class="btn btn-ghost" data-action="open-file" data-id="${escHtml(e.id)}" title="Open file" aria-label="Open file">📂</button>
          <button class="btn btn-ghost" data-action="open-folder" data-id="${escHtml(e.id)}" title="Open folder" aria-label="Open folder">🗂</button>
        ` : ''}
        <button class="btn btn-ghost" data-action="delete" data-id="${escHtml(e.id)}" title="Delete entry" aria-label="Delete history entry" style="color:var(--error);">🗑</button>
      </td>
    </tr>
  `).join('');

  // Attach event listeners to action buttons
  dom.historyBody.querySelectorAll('[data-action]').forEach(btn => {
    btn.addEventListener('click', handleHistoryAction);
  });
}

async function handleHistoryAction(e) {
  const btn = e.currentTarget;
  const action = btn.dataset.action;
  const id = btn.dataset.id;

  if (action === 'open-file') {
    await fetch(`/api/open-file/${id}`, { method: 'POST' });
  } else if (action === 'open-folder') {
    await fetch(`/api/open-folder/${id}`, { method: 'POST' });
  } else if (action === 'delete') {
    if (!confirm('Remove this entry from history?')) return;
    const res = await fetch(`/api/downloads/${id}`, { method: 'DELETE' });
    if ((await res.json()).success) loadHistory();
  }
}

/* ============================================================
   Settings page
   ============================================================ */
async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    if (data.success) {
      state.settings = data.settings;
      applySettingsToUI(data.settings);
    }
  } catch { /* ignore */ }
}

function applySettingsToUI(s) {
  dom.settingsDirDisplay.textContent = s.download_dir || './downloads';
  dom.settingsQuality.value = s.default_quality || 'best';
  dom.settingsMaxConcurrent.value = String(s.max_concurrent || 2);
  dom.settingsTheme.value = s.theme || 'dark';
  applyTheme(s.theme || 'dark');
}

dom.settingsChangeDirBtn.addEventListener('click', async () => {
  const btn = dom.settingsChangeDirBtn;
  const originalText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '⏳ Opening folder selector…';

  try {
    const res = await fetch('/api/select-folder');
    const data = await res.json();

    if (data.cancelled) return;

    if (!data.success) {
      showSettingsFeedback(data.error || 'Unable to select folder.', 'error');
      return;
    }

    state.settings.download_dir = data.directory;
    dom.settingsDirDisplay.textContent = data.directory;
    showSettingsFeedback('✓ Download folder updated: ' + data.directory, 'success');
  } catch {
    showSettingsFeedback('Network error while opening folder selector.', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalText;
  }
});

dom.saveSettingsBtn.addEventListener('click', async () => {
  const payload = {
    default_quality:  dom.settingsQuality.value,
    max_concurrent:   parseInt(dom.settingsMaxConcurrent.value),
    theme:            dom.settingsTheme.value,
  };

  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.success) {
      state.settings = data.settings;
      applyTheme(data.settings.theme);
      showSettingsFeedback('Settings saved successfully!', 'success');
    } else {
      showSettingsFeedback(data.error || 'Failed to save.', 'error');
    }
  } catch {
    showSettingsFeedback('Network error.', 'error');
  }
});

dom.settingsTheme.addEventListener('change', () => {
  applyTheme(dom.settingsTheme.value);
});

dom.clearHistoryBtn.addEventListener('click', async () => {
  if (!confirm('Clear all download history? This cannot be undone.')) return;
  try {
    const res = await fetch('/api/downloads');
    const data = await res.json();
    const entries = data.downloads || [];
    await Promise.all(entries.map(e => fetch(`/api/downloads/${e.id}`, { method: 'DELETE' })));
    showSettingsFeedback('History cleared.', 'success');
  } catch {
    showSettingsFeedback('Failed to clear history.', 'error');
  }
});

function showSettingsFeedback(msg, type) {
  dom.settingsFeedback.textContent = msg;
  dom.settingsFeedback.className = `alert alert-${type}`;
  dom.settingsFeedback.classList.remove('hidden');
  setTimeout(() => dom.settingsFeedback.classList.add('hidden'), 4000);
}

/* ============================================================
   Theme
   ============================================================ */
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme || 'dark');
}

/* ============================================================
   UI helpers
   ============================================================ */
function setAnalyzeBtnLoading(loading) {
  dom.analyzeBtn.disabled = loading;
  dom.analyzeBtnIcon.textContent = loading ? '⟳' : '🔍';
  dom.analyzeBtnText.textContent = loading ? 'Analyzing…' : 'Analyze Video';
  if (loading) dom.analyzeBtnIcon.style.animation = 'spin 1s linear infinite';
  else dom.analyzeBtnIcon.style.animation = '';
}

function showAnalyzeError(msg) {
  dom.analyzeError.textContent = msg;
  dom.analyzeError.classList.remove('hidden');
}

function clearAnalyzeError() {
  dom.analyzeError.classList.add('hidden');
}

function showDownloadError(msg) {
  dom.downloadError.textContent = msg;
  dom.downloadError.classList.remove('hidden');
}

function clearErrors() {
  dom.downloadError.classList.add('hidden');
  dom.downloadDirError.classList.add('hidden');
}

function hideAll() {
  dom.playlistCard.classList.add('hidden');
  dom.skeletonCard.classList.add('hidden');
  dom.videoCard.classList.add('hidden');
  dom.downloadSection.classList.add('hidden');
  dom.progressCard.classList.add('hidden');
  dom.completeCard.classList.add('hidden');
  clearErrors();
}

function resetHomeForNew() {
  if (state.sseSource) { state.sseSource.close(); state.sseSource = null; }
  state.currentJobId = null;
  state.videoInfo = null;
  state.selectedQuality = 'best';
  state.noplaylist = true;

  dom.urlInput.value = '';
  dom.radioSingle.checked = true;
  dom.downloadBtn.disabled = false;
  dom.downloadBtnIcon.textContent = '⬇';
  setAnalyzeBtnLoading(false);
  hideAll();
  dom.urlInput.focus();
}

function qualityLabel(q) {
  const map = {
    best:  'Best Available',
    '2160': '4K Ultra HD (2160p)',
    '1440': '2K Quad HD (1440p)',
    '1080': 'Full HD (1080p)',
    '720':  'HD (720p)',
    '480':  'SD (480p)',
    '360':  '360p',
    '240':  '240p',
    '144':  '144p',
    audio:  'Audio Only (MP3)',
  };
  return map[q] || q || 'Unknown';
}

function formatViews(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B views';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M views';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K views';
  return n + ' views';
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch { return iso; }
}

function truncate(str, n) {
  return str.length > n ? str.slice(0, n) + '…' : str;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ============================================================
   Init
   ============================================================ */
async function init() {
  // Load settings first (need default_quality, theme)
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    if (data.success) {
      state.settings = data.settings;
      applyTheme(data.settings.theme);
    }
  } catch { /* ignore */ }

  checkFfmpeg();
  dom.urlInput.focus();
}

init();
