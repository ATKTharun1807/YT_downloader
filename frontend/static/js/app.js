/**
 * app.js — YT_DOWNLOADER Frontend SPA Logic
 *
 * Supports:
 *  - YouTube: Videos (4K, 1080p, etc.), Playlists, MP3 Audio
 *  - Instagram: Reels, Single Image Posts, Single Video Posts, Carousels (ZIP / Direct)
 *  - Live URL analysis, real-time SSE progress, download history, native folder selector
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
  selectedCarouselItems: new Set(),
  currentJobId: null,
  currentJobHistory: null,
  settings: {},
  sseSource: null,
};

/* ============================================================
   DOM Elements
   ============================================================ */
const $ = id => document.getElementById(id);

const dom = {
  // Nav
  navItems: document.querySelectorAll('[data-page]'),

  // Home Page
  urlInput:          $('urlInput'),
  analyzeBtn:        $('analyzeBtn'),
  analyzeBtnText:    $('analyzeBtnText'),
  analyzeBtnIcon:    $('analyzeBtnIcon'),
  analyzeError:      $('analyzeError'),
  analyzeErrorText:  $('analyzeErrorText'),
  retryAnalyzeBtn:   $('retryAnalyzeBtn'),
  inputCardTitle:    $('inputCardTitle'),

  // YouTube Playlist Card
  playlistCard:      $('playlistCard'),
  playlistCount:     $('playlistCount'),
  radioSingle:       $('radioSingle'),
  radioPlaylist:     $('radioPlaylist'),

  // Skeleton Loader
  skeletonCard:      $('skeletonCard'),

  // Single Video / Media Card
  videoCard:         $('videoCard'),
  videoThumbnail:    $('videoThumbnail'),
  videoTitle:        $('videoTitle'),
  videoChannel:      $('videoChannel'),
  videoDuration:     $('videoDuration'),
  videoViews:        $('videoViews'),
  qualityGrid:       $('qualityGrid'),

  // Instagram Carousel Card
  carouselCard:          $('carouselCard'),
  carouselCount:         $('carouselCount'),
  carouselSubtitle:      $('carouselSubtitle'),
  carouselSelectAll:     $('carouselSelectAll'),
  carouselClearAll:      $('carouselClearAll'),
  carouselSelectedCount: $('carouselSelectedCount'),
  carouselGrid:          $('carouselGrid'),

  // Download Section
  downloadSection:   $('downloadSection'),
  downloadBtn:       $('downloadBtn'),
  downloadBtnIcon:   $('downloadBtnIcon'),
  downloadBtnText:   $('downloadBtnText'),
  downloadError:     $('downloadError'),

  // Progress Card
  progressCard:      $('progressCard'),
  progressStageText: $('progressStageText'),
  progressBarFill:   $('progressBarFill'),
  progressBarWrap:   $('progressBarWrap'),
  progressPct:       $('progressPct'),
  progressSpeed:     $('progressSpeed'),
  progressEta:       $('progressEta'),

  // Complete Card
  completeCard:      $('completeCard'),
  completeTitle:     $('completeTitle'),
  completeQuality:   $('completeQuality'),
  completeFormat:    $('completeFormat'),
  completeDir:       $('completeDir'),
  saveToDeviceBtn:   $('saveToDeviceBtn'),
  openFileBtn:       $('openFileBtn'),
  openFolderBtn:     $('openFolderBtn'),
  downloadAnotherBtn:$('downloadAnotherBtn'),

  // Downloads Page
  historyBody:       $('historyBody'),
  historyEmpty:      $('historyEmpty'),
  historyTableWrap:  $('historyTableWrap'),
  refreshHistoryBtn: $('refreshHistoryBtn'),

  // Settings Page
  settingsDirDisplay:    $('settingsDirDisplay'),
  settingsChangeDirBtn:  $('settingsChangeDirBtn'),
  settingsQuality:       $('settingsQuality'),
  settingsMaxConcurrent: $('settingsMaxConcurrent'),
  settingsTheme:         $('settingsTheme'),
  clearHistoryBtn:       $('clearHistoryBtn'),
  saveSettingsBtn:       $('saveSettingsBtn'),
  settingsFeedback:      $('settingsFeedback'),

  // Sidebar Footer
  ffmpegDot:   $('ffmpegDot'),
  ffmpegLabel: $('ffmpegLabel'),
};

/* ============================================================
   Navigation
   ============================================================ */
function navigateTo(page) {
  state.currentPage = page;

  document.querySelectorAll('.page').forEach(el => el.classList.remove('active'));
  const target = $(`page-${page}`);
  if (target) target.classList.add('active');

  document.querySelectorAll('[data-page]').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
    if (el.tagName === 'A') {
      el.setAttribute('aria-current', el.dataset.page === page ? 'page' : 'false');
    }
  });

  if (page === 'downloads') loadDownloadHistory();
  if (page === 'settings')  loadSettingsUI();
}

dom.navItems.forEach(el => {
  el.addEventListener('click', e => {
    e.preventDefault();
    const page = el.dataset.page;
    if (page) navigateTo(page);
  });
});

/* ============================================================
   FFmpeg Status
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
   Analyze URL
   ============================================================ */
dom.analyzeBtn.addEventListener('click', handleAnalyze);
dom.urlInput.addEventListener('keydown', e => { if (e.key === 'Enter') handleAnalyze(); });
if (dom.retryAnalyzeBtn) {
  dom.retryAnalyzeBtn.addEventListener('click', handleAnalyze);
}

let currentAnalyzeAbortController = null;

// State machine tracker: 'IDLE' | 'ANALYZING' | 'SUCCESS' | 'ERROR' | 'TIMEOUT'
let analyzeState = 'IDLE';

function setAnalyzeState(newState) {
  analyzeState = newState;
  console.log(`[STATE] ${newState}`);
}

async function handleAnalyze() {
  const url = dom.urlInput.value.trim();
  if (!url) {
    setAnalyzeState('ERROR');
    showAnalyzeError('Please enter a YouTube or Instagram URL.');
    return;
  }

  // Cancel any existing pending request
  if (currentAnalyzeAbortController) {
    currentAnalyzeAbortController.abort();
    currentAnalyzeAbortController = null;
  }

  currentAnalyzeAbortController = new AbortController();
  const signal = currentAnalyzeAbortController.signal;

  // Frontend timeout of 20 seconds
  const timeoutTimer = setTimeout(() => {
    if (currentAnalyzeAbortController) {
      currentAnalyzeAbortController.abort('TIMEOUT');
    }
  }, 20000);

  setAnalyzeState('ANALYZING');
  hideAll();
  dom.skeletonCard.classList.remove('hidden');
  setAnalyzeBtnLoading(true);
  clearAnalyzeError();

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
      signal,
    });

    clearTimeout(timeoutTimer);

    if (res.status === 502) {
      setAnalyzeState('ERROR');
      showAnalyzeError('Server is currently starting up (HTTP 502). Please retry in a few seconds.');
      return;
    }

    if (res.status === 504) {
      setAnalyzeState('TIMEOUT');
      showAnalyzeError('Analysis timed out. The server took too long to respond. Please try again.');
      return;
    }

    let data;
    try {
      data = await res.json();
    } catch {
      setAnalyzeState('ERROR');
      throw new Error(`Server returned HTTP ${res.status}.`);
    }

    if (!res.ok || !data.success) {
      setAnalyzeState('ERROR');
      const errMsg = data.error || 'Failed to analyze URL.';
      showAnalyzeError(errMsg);
      return;
    }

    setAnalyzeState('SUCCESS');
    state.videoInfo = data;
    state.noplaylist = true;

    // Instagram Carousel
    if (data.platform === 'instagram' && data.is_carousel) {
      renderCarouselInfo(data);
      return;
    }

    // YouTube Playlist
    if (data.is_mixed && data.has_playlist) {
      dom.playlistCount.textContent = `${data.playlist_count || '?'} videos`;
      dom.playlistCard.classList.remove('hidden');
    }

    if (data.is_playlist && !data.is_mixed) {
      dom.playlistCount.textContent = `${data.playlist_count || '?'} videos`;
      dom.playlistCard.classList.remove('hidden');
      renderPlaylistInfo(data);
    } else {
      renderVideoInfo(data);
    }

  } catch (err) {
    clearTimeout(timeoutTimer);
    if (err.name === 'AbortError' || err === 'TIMEOUT' || signal.aborted) {
      setAnalyzeState('TIMEOUT');
      showAnalyzeError('Analysis timed out. Please try again.');
    } else {
      setAnalyzeState('ERROR');
      showAnalyzeError(err.message || 'Network error. Please check your internet connection.');
    }
  } finally {
    // Guarantees loading state and skeleton are ALWAYS cleared
    dom.skeletonCard.classList.add('hidden');
    setAnalyzeBtnLoading(false);
    currentAnalyzeAbortController = null;
    if (analyzeState === 'ANALYZING') {
      setAnalyzeState('IDLE');
    }
  }
}

/* ============================================================
   Render Video / Post Info
   ============================================================ */
function renderVideoInfo(data) {
  const info = data.is_playlist ? (data.entries?.[0] || {}) : data;

  dom.videoThumbnail.src = info.thumbnail || '';
  dom.videoThumbnail.alt = info.title || 'Media thumbnail';
  dom.videoTitle.textContent = info.title || 'Unknown Title';
  dom.videoChannel.textContent = info.channel || (info.uploader ? `@${info.uploader}` : '');

  if (info.duration_str) {
    dom.videoDuration.textContent = `⏱ ${info.duration_str}`;
    dom.videoDuration.style.display = 'inline-flex';
  } else {
    dom.videoDuration.style.display = 'none';
  }

  if (info.view_count) {
    dom.videoViews.textContent = `👁 ${formatViews(info.view_count)}`;
    dom.videoViews.style.display = 'inline-flex';
  } else {
    dom.videoViews.style.display = 'none';
  }

  buildQualityGrid(info.quality_options || []);

  // Set CTA button text
  if (data.platform === 'instagram') {
    if (data.media_type === 'image') {
      dom.downloadBtnText.textContent = 'Download Image (Full Resolution)';
    } else if (data.media_type === 'reel') {
      dom.downloadBtnText.textContent = 'Download Instagram Reel (MP4)';
    } else {
      dom.downloadBtnText.textContent = 'Download Instagram Video';
    }
  } else {
    dom.downloadBtnText.textContent = 'Download Video';
  }

  dom.videoCard.classList.remove('hidden');
  dom.downloadSection.classList.remove('hidden');
}

function renderPlaylistInfo(data) {
  dom.videoThumbnail.src = data.thumbnail || (data.entries?.[0]?.thumbnail || '');
  dom.videoTitle.textContent = data.playlist_title || 'Playlist';
  dom.videoChannel.textContent = data.uploader ? `📺 ${data.uploader}` : '';
  dom.videoDuration.textContent = `🎬 ${data.playlist_count || '?'} videos`;
  dom.videoViews.style.display = 'none';

  buildQualityGrid([
    { value: 'best', label: 'Best Available', description: 'Highest quality for each video' },
    { value: '1080', label: 'Full HD (1080p)', description: 'MP4 video' },
    { value: '720',  label: 'HD (720p)',       description: 'MP4 video' },
    { value: '480',  label: 'SD (480p)',        description: 'MP4 video' },
    { value: 'audio',label: 'Audio Only — MP3 192kbps', description: 'Extract audio' },
  ]);

  dom.downloadBtnText.textContent = 'Download Entire Playlist';
  dom.videoCard.classList.remove('hidden');
  dom.downloadSection.classList.remove('hidden');
}

/* ============================================================
   Render Instagram Carousel
   ============================================================ */
function renderCarouselInfo(data) {
  const items = data.items || [];
  dom.carouselCount.textContent = `${items.length} items`;
  dom.carouselSubtitle.textContent = `Post by ${data.channel || '@' + data.uploader} — choose items to download:`;

  // Select all items by default
  state.selectedCarouselItems = new Set(items.map(i => i.index));
  buildCarouselGrid(items);
  updateCarouselSelectionUI();

  dom.carouselCard.classList.remove('hidden');
  dom.downloadSection.classList.remove('hidden');
}

function buildCarouselGrid(items) {
  dom.carouselGrid.innerHTML = '';

  items.forEach(item => {
    const card = document.createElement('div');
    const isSelected = state.selectedCarouselItems.has(item.index);
    card.className = `carousel-item-card ${isSelected ? 'selected' : ''}`;
    card.dataset.index = item.index;

    const typeIcon = item.type === 'video' ? '🎬 Video' : '📸 Photo';
    const resText = `${item.width || 1080}×${item.height || 1080}`;

    card.innerHTML = `
      <div class="carousel-thumb-wrap">
        <img src="${escHtml(item.thumbnail || '')}" alt="Item #${item.index}" loading="lazy" />
        <span class="carousel-badge-index">#${item.index}</span>
        <span class="carousel-badge-type">${typeIcon}</span>
        <div class="carousel-checkbox-wrap">
          <input type="checkbox" ${isSelected ? 'checked' : ''} aria-label="Select item ${item.index}" />
        </div>
      </div>
      <div class="carousel-item-meta">
        <span>${resText}</span>
        <span>${escHtml((item.extension || '').toUpperCase())}</span>
      </div>
    `;

    const checkbox = card.querySelector('input[type="checkbox"]');

    const toggleSelection = (e) => {
      if (e.target !== checkbox) {
        checkbox.checked = !checkbox.checked;
      }
      if (checkbox.checked) {
        state.selectedCarouselItems.add(item.index);
        card.classList.add('selected');
      } else {
        state.selectedCarouselItems.delete(item.index);
        card.classList.remove('selected');
      }
      updateCarouselSelectionUI();
    };

    card.addEventListener('click', toggleSelection);
    checkbox.addEventListener('change', (e) => {
      e.stopPropagation();
      if (checkbox.checked) {
        state.selectedCarouselItems.add(item.index);
        card.classList.add('selected');
      } else {
        state.selectedCarouselItems.delete(item.index);
        card.classList.remove('selected');
      }
      updateCarouselSelectionUI();
    });

    dom.carouselGrid.appendChild(card);
  });
}

function updateCarouselSelectionUI() {
  const total = state.videoInfo?.items?.length || 0;
  const count = state.selectedCarouselItems.size;
  dom.carouselSelectedCount.textContent = `${count} of ${total} selected`;

  if (count === 0) {
    dom.downloadBtn.disabled = true;
    dom.downloadBtnText.textContent = 'Select at least 1 item to download';
  } else if (count === 1) {
    dom.downloadBtn.disabled = false;
    const selectedIdx = Array.from(state.selectedCarouselItems)[0];
    const item = state.videoInfo.items.find(i => i.index === selectedIdx);
    const typeLabel = item?.type === 'video' ? 'Video' : 'Photo';
    dom.downloadBtnText.textContent = `Download Selected ${typeLabel} (#${selectedIdx})`;
  } else {
    dom.downloadBtn.disabled = false;
    dom.downloadBtnText.textContent = `Download ${count} Items as ZIP Archive`;
  }
}

// Select All / Clear All
if (dom.carouselSelectAll) {
  dom.carouselSelectAll.addEventListener('click', () => {
    if (!state.videoInfo?.items) return;
    state.selectedCarouselItems = new Set(state.videoInfo.items.map(i => i.index));
    dom.carouselGrid.querySelectorAll('.carousel-item-card').forEach(card => {
      card.classList.add('selected');
      const cb = card.querySelector('input[type="checkbox"]');
      if (cb) cb.checked = true;
    });
    updateCarouselSelectionUI();
  });
}

if (dom.carouselClearAll) {
  dom.carouselClearAll.addEventListener('click', () => {
    state.selectedCarouselItems.clear();
    dom.carouselGrid.querySelectorAll('.carousel-item-card').forEach(card => {
      card.classList.remove('selected');
      const cb = card.querySelector('input[type="checkbox"]');
      if (cb) cb.checked = false;
    });
    updateCarouselSelectionUI();
  });
}

/* ============================================================
   Quality Grid Builder
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
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        selectQuality(opt.value);
      }
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
   Download Handler
   ============================================================ */
/* ============================================================
   Download Handler
   ============================================================ */
dom.downloadBtn.addEventListener('click', handleDownload);

async function handleDownload() {
  if (!state.videoInfo) return;

  clearErrors();
  const url = state.videoInfo.url || dom.urlInput.value.trim();
  const quality = state.selectedQuality;
  const audioOnly = quality === 'audio';
  const noplaylist = state.noplaylist;
  const platform = state.videoInfo.platform || 'youtube';
  const mediaType = state.videoInfo.media_type || 'video';

  let selectedItems = null;
  if (state.videoInfo.is_carousel) {
    selectedItems = Array.from(state.selectedCarouselItems);
    if (selectedItems.length === 0) {
      showDownloadError('Please select at least one item from the carousel.');
      return;
    }
  }

  const info = state.videoInfo.is_playlist && !noplaylist
    ? state.videoInfo
    : (state.videoInfo.is_playlist ? (state.videoInfo.entries?.[0] || state.videoInfo) : state.videoInfo);

  const title = info.title || info.playlist_title || '';
  const thumbnail = info.thumbnail || '';
  const channel = info.channel || info.uploader || '';
  const durationStr = info.duration_str || '';

  // Remember original button state
  const originalText = dom.downloadBtnText.textContent;
  const originalIconHTML = dom.downloadBtnIcon.innerHTML;

  // Prepare download
  dom.downloadBtn.disabled = true;
  dom.downloadBtnIcon.innerHTML = '<span class="spinner" style="width:16px;height:16px;border-width:2px;display:inline-block;"></span>';
  dom.downloadBtnText.textContent = 'Preparing download…';

  let chosenDir = null;
  try {
    const pickerRes = await fetch('/api/select-folder');
    const pickerData = await pickerRes.json();

    if (pickerData.cancelled || !pickerData.success || !pickerData.directory) {
      // User cancelled dialog gracefully: restore button without showing error
      dom.downloadBtn.disabled = false;
      dom.downloadBtnIcon.innerHTML = originalIconHTML;
      dom.downloadBtnText.textContent = originalText;
      return;
    }

    chosenDir = pickerData.directory;
  } catch (err) {
    dom.downloadBtn.disabled = false;
    dom.downloadBtnIcon.innerHTML = originalIconHTML;
    dom.downloadBtnText.textContent = originalText;
    showDownloadError('Could not open Windows folder selector.');
    return;
  }

  // Step 2: Start download automatically using the selected directory
  dom.downloadBtnText.textContent = 'Starting download…';
  showProgress('Preparing download…', 0, '', '');

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        quality,
        audio_only: audioOnly,
        noplaylist,
        download_dir: chosenDir,
        title,
        thumbnail,
        channel,
        duration_str: durationStr,
        platform,
        media_type: mediaType,
        selected_items: selectedItems,
      }),
    });

    const data = await res.json();

    if (!data.success) {
      dom.downloadBtn.disabled = false;
      dom.downloadBtnIcon.innerHTML = originalIconHTML;
      dom.downloadBtnText.textContent = originalText;
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
      directory: chosenDir,
    };

    startSSE(data.job_id, {
      title,
      quality,
      audioOnly,
      channel,
      durationStr,
      directory: chosenDir,
      originalText,
      originalIconHTML,
    });

  } catch (err) {
    dom.downloadBtn.disabled = false;
    dom.downloadBtnIcon.innerHTML = originalIconHTML;
    dom.downloadBtnText.textContent = originalText;
    hideProgress();
    showDownloadError('Network error. Please try again.');
  }
}

/* ============================================================
   SSE Progress Tracking
   ============================================================ */
function startSSE(jobId, meta) {
  if (state.sseSource) {
    state.sseSource.close();
    state.sseSource = null;
  }

  const source = new EventSource(`/api/progress/${jobId}`);
  state.sseSource = source;

  source.onmessage = e => {
    try {
      const ev = JSON.parse(e.data);
      handleProgressEvent(ev, meta);
    } catch { /* ignore */ }
  };

  source.onerror = () => {
    source.close();
    state.sseSource = null;
    if (state.currentJobId === jobId) {
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

function handleProgressEvent(ev, meta) {
  const status = ev.status || ev.stage || '';
  const pct = typeof ev.percentage === 'number' ? ev.percentage : 0;
  const speed = ev.speed || '';
  const eta = ev.eta || '';

  if (status === 'completed') {
    if (state.sseSource) { state.sseSource.close(); state.sseSource = null; }
    hideProgress();
    dom.downloadBtn.disabled = false;
    dom.downloadBtnIcon.innerHTML = meta.originalIconHTML || `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="7 10 12 15 17 10"></polyline>
        <line x1="12" y1="15" x2="12" y2="3"></line>
      </svg>`;
    dom.downloadBtnText.textContent = meta.originalText || 'Download Media';
    showCompleteCard(ev, meta);
    return;
  }

  if (status === 'failed') {
    if (state.sseSource) { state.sseSource.close(); state.sseSource = null; }
    hideProgress();
    dom.downloadBtn.disabled = false;
    dom.downloadBtnIcon.innerHTML = meta.originalIconHTML || `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="7 10 12 15 17 10"></polyline>
        <line x1="12" y1="15" x2="12" y2="3"></line>
      </svg>`;
    dom.downloadBtnText.textContent = meta.originalText || 'Download Media';
    showDownloadError(ev.error || 'Download failed.');
    return;
  }

  const stageLabels = {
    pending: 'Queued…',
    downloading: 'Downloading…',
    merging: 'Merging audio and video streams…',
    extracting_audio: 'Extracting MP3 audio track…',
    cleaning: 'Finalizing files…',
  };

  showProgress(stageLabels[status] || 'Processing…', pct, speed, eta);
}

function showProgress(stageText, pct, speed, eta) {
  dom.progressCard.classList.remove('hidden');
  dom.progressStageText.textContent = stageText;
  dom.progressBarFill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  dom.progressPct.textContent = `${Math.round(pct)}%`;
  dom.progressSpeed.textContent = speed;
  dom.progressEta.textContent = eta;
}

function hideProgress() {
  dom.progressCard.classList.add('hidden');
}

/* ============================================================
   Complete Card & Actions
   ============================================================ */
function showCompleteCard(result, meta) {
  dom.completeCard.classList.remove('hidden');
  const titleEl = dom.completeCard.querySelector('.complete-title');
  if (titleEl) {
    titleEl.textContent = 'Download completed successfully!';
  }
  dom.completeTitle.textContent = meta.title || result.filename || 'Downloaded media';
  dom.completeQuality.textContent = meta.audioOnly ? 'MP3 Audio (192kbps)' : (meta.quality || 'Best Available');
  dom.completeFormat.textContent = (result.filename ? result.filename.split('.').pop() : (meta.audioOnly ? 'mp3' : 'mp4')).toUpperCase();
  dom.completeDir.textContent = result.directory || meta.directory || state.settings.download_dir || './downloads';

  const entryId = state.currentJobId;
  const downloadUrl = `/api/download-file/${entryId}`;

  if (dom.saveToDeviceBtn) {
    dom.saveToDeviceBtn.href = downloadUrl;
    dom.saveToDeviceBtn.download = result.filename || '';
  }

  // Auto trigger download to user's browser
  try {
    const autoLink = document.createElement('a');
    autoLink.href = downloadUrl;
    autoLink.download = result.filename || '';
    document.body.appendChild(autoLink);
    autoLink.click();
    document.body.removeChild(autoLink);
  } catch { /* ignore */ }

  dom.openFileBtn.onclick = async () => {
    try {
      await fetch(`/api/open-file/${entryId}`, { method: 'POST' });
    } catch { /* ignore */ }
  };

  dom.openFolderBtn.onclick = async () => {
    try {
      await fetch(`/api/open-folder/${entryId}`, { method: 'POST' });
    } catch { /* ignore */ }
  };

  dom.downloadAnotherBtn.onclick = () => {
    dom.completeCard.classList.add('hidden');
    dom.urlInput.value = '';
    dom.urlInput.focus();
    hideAll();
  };
}

/* ============================================================
   Folder Picker (Native Dialog for Settings)
   ============================================================ */
if (dom.settingsChangeDirBtn) {
  dom.settingsChangeDirBtn.addEventListener('click', () => openFolderPicker(dom.settingsDirDisplay, null));
}

async function openFolderPicker(targetDisplay, targetError) {
  try {
    const res = await fetch('/api/select-folder');
    const data = await res.json();
    if (data.success && data.directory && !data.cancelled) {
      state.settings.download_dir = data.directory;
      if (targetDisplay) targetDisplay.textContent = data.directory;
      if (targetError) targetError.classList.add('hidden');
    }
  } catch (err) {
    if (targetError) {
      targetError.textContent = 'Could not open folder picker.';
      targetError.classList.remove('hidden');
    }
  }
}

/* ============================================================
   Download History
   ============================================================ */
if (dom.refreshHistoryBtn) {
  dom.refreshHistoryBtn.addEventListener('click', loadDownloadHistory);
}

async function loadDownloadHistory() {
  try {
    const res = await fetch('/api/downloads');
    const data = await res.json();
    const history = data.downloads || [];

    if (history.length === 0) {
      dom.historyEmpty.classList.remove('hidden');
      dom.historyTableWrap.classList.add('hidden');
      return;
    }

    dom.historyEmpty.classList.add('hidden');
    dom.historyTableWrap.classList.remove('hidden');
    dom.historyBody.innerHTML = '';

    history.forEach(item => {
      const tr = document.createElement('tr');
      const dateStr = item.completed_at ? new Date(item.completed_at).toLocaleDateString() : '—';
      const statusBadge = item.status === 'completed'
        ? '<span class="badge" style="background:var(--success-bg); color:var(--success);">Completed</span>'
        : '<span class="badge" style="background:var(--error-bg); color:var(--error);">Failed</span>';

      const actionBtn = item.status === 'completed'
        ? `<a href="/api/download-file/${item.id}" download class="btn btn-primary" style="font-size:11px; padding:4px 8px; text-decoration:none; margin-right:4px;">⬇ Save</a>`
        : '';

      tr.innerHTML = `
        <td><img src="${escHtml(item.thumbnail || '')}" class="history-thumb" alt="" /></td>
        <td style="font-weight:600; color:var(--text-primary); max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escHtml(item.title || item.filename || 'Unknown')}</td>
        <td><span class="badge">${escHtml((item.format || 'mp4').toUpperCase())}</span></td>
        <td>${statusBadge}</td>
        <td>${dateStr}</td>
        <td>
          ${actionBtn}
          <button class="btn btn-ghost" data-action="delete" data-id="${item.id}" style="font-size:11px; padding:4px 8px;">Delete</button>
        </td>
      `;

      tr.querySelector('[data-action="delete"]').addEventListener('click', async () => {
        await fetch(`/api/downloads/${item.id}`, { method: 'DELETE' });
        loadDownloadHistory();
      });

      dom.historyBody.appendChild(tr);
    });
  } catch { /* ignore */ }
}

/* ============================================================
   Settings Page
   ============================================================ */
async function loadSettingsUI() {
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    state.settings = data.settings || {};

    dom.settingsDirDisplay.textContent = state.settings.download_dir || './downloads';
    dom.settingsQuality.value = state.settings.default_quality || 'best';
    dom.settingsMaxConcurrent.value = state.settings.max_concurrent || 2;
    dom.settingsTheme.value = state.settings.theme || 'light';
  } catch { /* ignore */ }
}

dom.saveSettingsBtn.addEventListener('click', async () => {
  const payload = {
    default_quality: dom.settingsQuality.value,
    max_concurrent: parseInt(dom.settingsMaxConcurrent.value, 10),
    theme: dom.settingsTheme.value,
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
      showSettingsFeedback('Settings saved successfully.', 'success');
    }
  } catch {
    showSettingsFeedback('Failed to save settings.', 'error');
  }
});

dom.clearHistoryBtn.addEventListener('click', async () => {
  if (!confirm('Are you sure you want to clear all download history?')) return;
  try {
    const res = await fetch('/api/downloads');
    const data = await res.json();
    for (const item of (data.downloads || [])) {
      await fetch(`/api/downloads/${item.id}`, { method: 'DELETE' });
    }
    showSettingsFeedback('History cleared.', 'success');
  } catch {
    showSettingsFeedback('Failed to clear history.', 'error');
  }
});

function showSettingsFeedback(msg, type) {
  dom.settingsFeedback.textContent = msg;
  dom.settingsFeedback.className = `alert alert-${type}`;
  dom.settingsFeedback.classList.remove('hidden');
  setTimeout(() => dom.settingsFeedback.classList.add('hidden'), 3000);
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme || 'light');
}

/* ============================================================
   Helpers
   ============================================================ */
function hideAll() {
  dom.playlistCard.classList.add('hidden');
  dom.videoCard.classList.add('hidden');
  dom.carouselCard.classList.add('hidden');
  dom.downloadSection.classList.add('hidden');
  dom.progressCard.classList.add('hidden');
  dom.completeCard.classList.add('hidden');
  dom.analyzeError.classList.add('hidden');
  dom.downloadError.classList.add('hidden');
}

function setAnalyzeBtnLoading(loading) {
  dom.analyzeBtn.disabled = loading;
  if (loading) {
    dom.analyzeBtnText.textContent = 'Analyzing…';
    dom.analyzeBtnIcon.innerHTML = '<span class="spinner"></span>';
  } else {
    dom.analyzeBtnText.textContent = 'Analyze URL';
    dom.analyzeBtnIcon.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"></circle>
        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
      </svg>
    `;
  }
}

function showAnalyzeError(msg) {
  if (dom.analyzeErrorText) {
    dom.analyzeErrorText.textContent = msg;
  } else {
    dom.analyzeError.textContent = msg;
  }
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
  dom.analyzeError.classList.add('hidden');
  dom.downloadError.classList.add('hidden');
}

function formatViews(n) {
  if (!n) return '';
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B views`;
  if (n >= 1_000_000)     return `${(n / 1_000_000).toFixed(1)}M views`;
  if (n >= 1_000)         return `${(n / 1_000).toFixed(1)}K views`;
  return `${n} views`;
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ============================================================
   Init
   ============================================================ */
(async function init() {
  checkFfmpeg();
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    state.settings = data.settings || {};
    applyTheme(state.settings.theme || 'light');
  } catch { /* ignore */ }
})();
