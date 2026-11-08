(() => {
  const $ = (id) => document.getElementById(id);
  const video = $('video');
  const videoPlaceholder = $('videoPlaceholder');
  const videoWrap = video.closest('.video-wrap');
  // Update both the HTML hidden state and explicit styles. The live class also
  // hides the overlay if an old author CSS rule overrides the native [hidden].
  function setCameraPreviewState(isLive) {
    if (isLive) {
      videoPlaceholder.hidden = true;
      videoPlaceholder.setAttribute('aria-hidden', 'true');
      videoPlaceholder.style.setProperty('display', 'none', 'important');
      videoWrap?.classList.add('camera-live');
      video.style.setProperty('display', 'block', 'important');
      video.style.setProperty('visibility', 'visible', 'important');
      video.style.setProperty('opacity', '1', 'important');
    } else {
      videoPlaceholder.hidden = false;
      videoPlaceholder.removeAttribute('aria-hidden');
      videoPlaceholder.style.removeProperty('display');
      videoWrap?.classList.remove('camera-live');
    }
  }
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { alpha: false });
  let ws = null, stream = null, frameTimer = null, durationTimer = null;
  let latestCommandId = null;
  // Fail closed: the pre-IoT UI remains locked until health says otherwise.
  window.islPreIoTLockdown = true;
  let referenceClasses = [];
  let selectedReference = null;
  let expectedLabel = null;
  let capturing = false, submitting = false, captureStartedAt = null, captureMaxSeconds = 6.0, lastVideoTime = -1, lastFrameCount = 0, clientDropped = 0, serverDropped = 0;
  let captureCountdownToken = 0;

  function setDot(id, state) { const el = $(id); el.className = `dot ${state}`; }
  function message(text, kind = '') { $('captureMessage').textContent = text; $('captureMessage').className = `message ${kind}`.trim(); }
  function escapeHtml(s) { return String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function fmtTime(s) { if (!s) return '—'; try { return new Date(s).toLocaleString(undefined, {hour12:false, timeZone:'UTC'}); } catch { return s; } }
  function fmtPct(v) { return Number.isFinite(Number(v)) ? `${(Number(v)*100).toFixed(2)}%` : '—'; }
  function fmtMs(v) { return Number.isFinite(Number(v)) ? `${Number(v).toFixed(1)} ms` : '—'; }

  async function api(path, options = {}) {
    const res = await fetch(path, {headers: {'Content-Type':'application/json'}, ...options});
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
    return body;
  }

  async function refreshHealth() {
    try {
      const h = await api('/api/health');
      $('overallStatus').textContent = h.status === 'ready' ? 'Runtime ready' : 'Setup needed';
      $('overallStatus').className = `status-pill ${h.status === 'ready' ? 'status-good' : 'status-warn'}`;
      $('modelState').textContent = h.model_ready ? 'Ready' : 'Unavailable';
      $('visionState').textContent = h.extractor_ready ? 'Available' : 'Not installed';
      $('dbState').textContent = 'Connected';
      window.islPreIoTLockdown = Boolean(h.pre_iot_lockdown);
      $('iotState').textContent = h.pre_iot_lockdown ? 'Locked for pre-IoT' : (h.iot_enabled ? 'Enabled' : 'Disabled');
      $('iotToggle').checked = Boolean(h.iot_enabled);
      $('iotToggle').disabled = Boolean(h.pre_iot_lockdown);
      $('iotToggle').title = h.pre_iot_lockdown ? 'IoT is intentionally locked in this pre-IoT release.' : 'Enable or disable IoT commands for this app session.';
      if (h.pre_iot_lockdown) {
        $('iotMessage').textContent = 'Pre-IoT safety lock active. No device command can be published.';
        $('iotMessage').className = 'iot-status';
      }
      setDot('modelDot', h.model_ready ? 'good' : 'bad');
      setDot('visionDot', h.extractor_ready ? 'good' : 'bad');
      setDot('dbDot', 'good'); setDot('iotDot', h.iot_enabled ? 'good' : '');
      window._confidenceThreshold = Number(h.confidence_threshold);
      $('thresholdFooter').textContent = Number(h.confidence_threshold).toFixed(2);
      captureMaxSeconds = Math.max(1, Math.min(6, Number(h.max_capture_seconds) || 6.0));
      if (!h.model_ready) message(h.model_error || h.label_error || 'Configure the official model assets before capturing a gesture.', 'error');
      else if (!h.extractor_ready) message(h.extractor_error || 'Install MediaPipe/OpenCV before capture.', 'error');
      return h;
    } catch (e) {
      $('overallStatus').textContent = 'Backend unavailable'; $('overallStatus').className = 'status-pill status-bad';
      message(e.message, 'error'); return null;
    }
  }

  async function ensureCamera() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('This browser does not support getUserMedia. Open the app on localhost using a modern browser.');
    const streamIsLive = stream && stream.getVideoTracks().some((track) => track.readyState === 'live');
    if (!streamIsLive) {
      stream = await navigator.mediaDevices.getUserMedia({video: {width:{ideal:640}, height:{ideal:480}, facingMode:'user'}, audio:false});
    }
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    await video.play();
    // The frame sender only runs when videoWidth is nonzero. Wait briefly for
    // metadata on browsers that resolve play() before the first frame arrives.
    if (video.readyState < 2 || video.videoWidth < 2 || video.videoHeight < 2) {
      await new Promise((resolve, reject) => {
        const cleanup = () => {
          video.removeEventListener('loadeddata', onReady);
          video.removeEventListener('error', onError);
        };
        const onReady = () => { cleanup(); resolve(); };
        const onError = () => { cleanup(); reject(new Error('Camera opened, but the browser did not deliver a video frame. Close other camera apps and retry.')); };
        video.addEventListener('loadeddata', onReady, {once:true});
        video.addEventListener('error', onError, {once:true});
      });
    }
    if (video.videoWidth < 2 || video.videoHeight < 2) throw new Error('Camera opened but no usable video frame is available. Check Windows camera privacy settings and retry.');
    // Match the preview viewport to the camera's real aspect ratio. Otherwise a 4:3
    // webcam feed inside the old 16:10 box was cropped by object-fit: cover, hiding
    // some of the user's arms/hands in the UI even though the raw frame was uncropped.
    videoWrap.style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
    videoWrap.dataset.cameraResolution = `${video.videoWidth}x${video.videoHeight}`;
    setCameraPreviewState(true);
    $('captureStatus').textContent = 'Camera ready';
    window.dispatchEvent(new CustomEvent('isl-camera-ready'));
  }

  function connectSocket() {
    return new Promise((resolve, reject) => {
      if (ws && ws.readyState === WebSocket.OPEN) return resolve(ws);
      const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${scheme}//${location.host}/ws/capture`);
      const timeout = setTimeout(() => reject(new Error('WebSocket connection timed out.')), 5000);
      ws.addEventListener('open', () => { clearTimeout(timeout); resolve(ws); }, {once:true});
      ws.addEventListener('error', () => { clearTimeout(timeout); reject(new Error('WebSocket could not connect to the local backend.')); }, {once:true});
      ws.addEventListener('message', (event) => { try { handleServerMessage(JSON.parse(event.data)); } catch {} });
      ws.addEventListener('close', () => { if (capturing) onCaptureEnded('Connection closed. Try again.'); });
    });
  }

  function handleServerMessage(msg) {
    if (msg.type === 'connected') {
      if (!msg.model_ready) message(msg.model_error || 'Official model is not configured.', 'error');
      else if (!msg.extractor_ready) message(msg.extractor_error || 'MediaPipe extractor is not available.', 'error');
    } else if (msg.type === 'capture_started') {
      setCameraPreviewState(Boolean(video.srcObject && video.videoWidth > 1));
      capturing = true; submitting = false; captureStartedAt = performance.now(); lastVideoTime = video.currentTime; lastFrameCount = 0; clientDropped = 0; serverDropped = 0;
      window.dispatchEvent(new CustomEvent('isl-capture-state', {detail:{state:'capturing'}}));
      $('captureStatus').textContent = 'Capturing one gesture'; $('startButton').disabled = true; $('stopButton').disabled = false;
      $('framesCount').textContent = '0 / 169 captured'; $('frameBadge').textContent = '0 captured frames'; $('progressBar').style.width = '0%';
      $('droppedCount').textContent = '0'; $('captureDuration').textContent = '0.0 s';
      $('predictionWord').textContent = '…'; $('predictionLabel').textContent = 'Capture in progress'; $('outcomeValue').textContent = 'Capturing';
      $('confidenceValue').textContent = '—'; $('confidenceBar').style.width = '0%';
      durationTimer = setInterval(() => {
        if (!captureStartedAt || !capturing) return;
        const elapsed = (performance.now() - captureStartedAt) / 1000;
        $('captureDuration').textContent = `${elapsed.toFixed(1)} / ${captureMaxSeconds.toFixed(1)} s`;
        $('progressBar').style.width = `${Math.min(100, elapsed / captureMaxSeconds * 100)}%`;
        if (elapsed >= captureMaxSeconds) submitCapture('time-limit');
      }, 50);
      frameTimer = setInterval(sendFrame, 40);
      message(`Recording now. Perform one complete isolated sign, then press Stop & recognise. The recording auto-stops at ${captureMaxSeconds.toFixed(0)} seconds; extraction runs afterward so slow MediaPipe processing does not drop camera frames.`, 'success');
    } else if (msg.type === 'progress' && msg.phase === 'recording') {
      lastFrameCount = Number(msg.frames || 0); serverDropped = Number(msg.queue_dropped || 0);
      $('framesCount').textContent = `${lastFrameCount} / 169 captured`; $('frameBadge').textContent = `${lastFrameCount} captured frames`;
      $('droppedCount').textContent = String(clientDropped + serverDropped);
    } else if (msg.type === 'time_limit_reached' || msg.type === 'frame_limit_reached') {
      if (capturing) submitCapture(msg.type === 'time_limit_reached' ? 'time-limit' : 'frame-limit');
    } else if (msg.type === 'processing_started') {
      submitting = true; window.dispatchEvent(new CustomEvent('isl-capture-state', {detail:{state:'processing'}})); $('captureStatus').textContent = 'Extracting landmarks'; $('startButton').disabled = true; $('stopButton').disabled = true;
      $('framesCount').textContent = `0 / ${Number(msg.total_frames || 0)} extracted`; $('frameBadge').textContent = `0 / ${Number(msg.total_frames || 0)} extracted`; $('progressBar').style.width = '0%';
      message(msg.message || 'Processing the recorded clip. Please wait.', 'success');
    } else if (msg.type === 'processing_progress') {
      const count = Number(msg.frames || 0), total = Number(msg.total_frames || 0);
      $('framesCount').textContent = `${Number(msg.features_extracted || 0)} features / ${total} frames`; $('frameBadge').textContent = `${count} / ${total} processed`;
      $('progressBar').style.width = `${total ? Math.min(100, count / total * 100) : 0}%`;
    } else if (msg.type === 'frame_error') {
      message(msg.message || 'Frame extraction error.', 'error');
    } else if (msg.type === 'backpressure') {
      // Intentionally visible in the protocol but not shown for every dropped frame to avoid alert spam.
      serverDropped = Math.max(serverDropped, Number(msg.queue_dropped || serverDropped)); $('droppedCount').textContent = String(clientDropped + serverDropped);
    } else if (msg.type === 'result') {
      onCaptureEnded('Capture submitted.');
      renderResult(msg.result || {}); renderIoTResult(msg.iot);
      loadHistory();
      if (msg.result?.diagnostic_mode) {
        const suffix = msg.result?.diagnostic_override_used ? ' Low hand coverage was overridden for this diagnostic attempt.' : '';
        message(`Diagnostic mode was enabled; IoT commands were suppressed.${suffix} Do not treat a single output as an accuracy result.`, 'success');
      } else {
        message(msg.result?.reason || `Recognition outcome: ${msg.result?.outcome || msg.result?.status || 'unknown'}.`, msg.result?.outcome === 'accepted' ? 'success' : (msg.result?.outcome === 'error' || msg.result?.outcome === 'invalid_capture' ? 'error' : ''));
      }
    } else if (msg.type === 'cancelled') {
      onCaptureEnded('Capture cancelled.'); message(msg.message || 'Capture cancelled; no inference or command was run.');
    } else if (msg.type === 'error') {
      message(`${msg.code ? msg.code + ': ' : ''}${msg.message || 'Unknown backend error'}`, 'error');
      if (msg.code === 'model_unavailable' || msg.code === 'extractor_unavailable') onCaptureEnded('Setup required');
    }
  }

  function submitCapture(reason = 'manual') {
    if (!capturing || submitting || !ws || ws.readyState !== WebSocket.OPEN) return;
    capturing = false; submitting = true; stopFrameSender();
    if (durationTimer) clearInterval(durationTimer); durationTimer = null;
    captureStartedAt = null; $('captureStatus').textContent = 'Processing recorded clip';
    $('startButton').disabled = true; $('stopButton').disabled = true;
    const note = reason === 'time-limit' ? 'Maximum recording time reached. Extracting every captured frame now.' : 'Recording stopped. Extracting every captured frame now.';
    message(note + ' Processing may take several seconds; do not close this tab.', 'success');
    window.dispatchEvent(new CustomEvent('isl-capture-state', {detail:{state:'processing'}}));
    ws.send(JSON.stringify({type:'stop', client_frames_dropped:clientDropped, diagnostic_mode:Boolean($('diagnosticOverride')?.checked)}));
  }

  function sendFrame() {
    if (!capturing || !ws || ws.readyState !== WebSocket.OPEN || video.readyState < 2 || video.videoWidth < 2) return;
    if (video.currentTime <= lastVideoTime) return; // Never send duplicate camera frames.
    if (ws.bufferedAmount > 900000) { clientDropped += 1; $('droppedCount').textContent = String(clientDropped + serverDropped); return; }
    lastVideoTime = video.currentTime;
    canvas.width = 640; canvas.height = Math.round(640 * video.videoHeight / video.videoWidth);
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    // Provisional capture setting: 640px/Q92 preserved hand detail better in a small 5-clip validation screening; this is not an accuracy claim. Keep classifier input unmirrored.
    const dataUrl = canvas.toDataURL('image/jpeg', 0.92);
    try { ws.send(JSON.stringify({type:'frame', timestamp:Date.now()/1000, jpeg_b64:dataUrl})); } catch {}
  }
  function stopFrameSender() { if (frameTimer) clearInterval(frameTimer); frameTimer = null; }
  function cancelCaptureCountdown() { captureCountdownToken += 1; }
  async function countdownThenStart() {
    const token = ++captureCountdownToken;
    $('startButton').disabled = true;
    $('captureStatus').textContent = 'Get ready';
    for (const remaining of [2, 1]) {
      message(`Get ready — recording starts in ${remaining} second${remaining === 1 ? '' : 's'}. Keep the complete gesture area inside the frame.`, 'success');
      await new Promise(resolve => setTimeout(resolve, 1000));
      if (token !== captureCountdownToken) return;
    }
    if (token !== captureCountdownToken) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      $('startButton').disabled = false;
      $('captureStatus').textContent = 'Camera ready';
      throw new Error('The capture connection closed during the countdown. Please retry.');
    }
    ws.send(JSON.stringify({type:'start'}));
  }
  function onCaptureEnded(status) {
    cancelCaptureCountdown();
    capturing = false; submitting = false; stopFrameSender();
    if (durationTimer) clearInterval(durationTimer); durationTimer = null;
    captureStartedAt = null; $('captureStatus').textContent = status;
    window.dispatchEvent(new CustomEvent('isl-capture-state', {detail:{state:'ready'}}));
    $('startButton').textContent = 'Start capture';
    $('startButton').disabled = false; $('stopButton').disabled = true;
  }

  function renderResult(r) {
    const label = r.predicted_label || '—';
    $('predictionWord').textContent = label;
    const thresh = (typeof window._confidenceThreshold === 'number') ? window._confidenceThreshold : 0.40;
    let predLabel;
    if (r.outcome === 'accepted') {
      predLabel = `Accepted ≥ ${(thresh*100).toFixed(0)}% (provisional threshold — not independently calibrated)`;
    } else if (r.outcome === 'rejected') {
      const sc = typeof r.confidence === 'number' ? ` (score ${(r.confidence*100).toFixed(1)}%)` : '';
      predLabel = `Candidate below ${(thresh*100).toFixed(0)}% threshold${sc} — shown for diagnostics only`;
    } else {
      predLabel = r.reason || r.outcome || 'No result';
    }
    $('predictionLabel').textContent = predLabel;
    // Show confidence hint for rejected predictions
    const hint = $('confidenceHint');
    if (hint) {
      if (r.outcome === 'rejected' && typeof r.confidence === 'number') {
        hint.textContent = `Candidate: ${r.predicted_label || '—'} · Score: ${(r.confidence*100).toFixed(1)}% · Below the ${(thresh*100).toFixed(0)}% provisional threshold. This is NOT a confirmed recognition.`;
        hint.hidden = false;
      } else {
        hint.hidden = true;
      }
    }
    renderExpectedComparison(label);
    $('confidenceValue').textContent = fmtPct(r.confidence);
    $('confidenceBar').style.width = `${Math.max(0,Math.min(100,(Number(r.confidence)||0)*100))}%`;
    const candidates = Array.isArray(r.top5_predictions) ? r.top5_predictions : [];
    const candidateSection = $('candidateSection');
    const candidateList = $('candidateList');
    if (candidateSection && candidateList) {
      candidateList.innerHTML = candidates.map((candidate, index) =>
        `<li><span class="candidate-rank">${index + 1}</span><span class="candidate-label">${escapeHtml(candidate.label || 'Unknown')}</span><strong>${escapeHtml(fmtPct(candidate.confidence))}</strong></li>`
      ).join('');
      candidateSection.hidden = candidates.length === 0;
    }
    $('outcomeValue').textContent = r.outcome || r.status || 'Unknown';
    $('inferenceLatencyValue').textContent = fmtMs(r.inference_latency_ms);
    $('processingLatencyValue').textContent = fmtMs(r.processing_latency_ms);
    $('endToEndLatencyValue').textContent = fmtMs(r.end_to_end_latency_ms);
    $('poseValue').textContent = r.pose_ratio == null ? '—' : fmtPct(r.pose_ratio);
    $('handValue').textContent = r.hand_ratio == null ? '—' : `${fmtPct(r.hand_ratio)} any hand`;
    const handDetail = `Hand slots: ${fmtPct(r.hand1_ratio)} / ${fmtPct(r.hand2_ratio)} · both: ${fmtPct(r.both_hand_ratio)}`;
    $('handValue').title = handDetail;
    $('hand1Value').textContent = fmtPct(r.hand1_ratio);
    $('hand2Value').textContent = fmtPct(r.hand2_ratio);
    $('bothHandsValue').textContent = fmtPct(r.both_hand_ratio);
    $('featureExtractionLatencyValue').textContent = fmtMs(r.feature_extraction_latency_ms);
    $('mediapipeP95Value').textContent = fmtMs(r.mediapipe_p95_latency_ms);
    $('framesCount').textContent = `${r.frame_count || 0} features / 169 max`; $('frameBadge').textContent = `${r.frame_count || 0} extracted features`;
    $('droppedCount').textContent = String(r.frames_dropped || 0);
    $('captureDuration').textContent = `${((Number(r.capture_duration_ms)||0)/1000).toFixed(1)} s`;
    $('progressBar').style.width = '100%';
  }
  function renderIoTResult(r) {
    if (!r) { $('iotMessage').textContent = 'No command requested.'; $('iotMessage').className = 'iot-status'; return; }
    latestCommandId = r.command_id || null;
    $('iotMessage').textContent = `${r.status || 'unknown'} — ${r.message || ''}`;
    $('iotMessage').className = `iot-status ${['pending','acknowledged'].includes(r.status) ? 'good' : ['publish_failed','rejected'].includes(r.status) ? 'bad' : ''}`.trim();
    loadIoTHistory(); refreshHealth();
  }

  async function loadHistory() {
    try {
      const [rec, iot] = await Promise.all([api('/api/history?limit=100'), api('/api/iot/history?limit=100')]);
      const rows = rec.events || [];
      $('recognitionHistory').innerHTML = rows.length ? rows.map(e => `<tr><td>${escapeHtml(fmtTime(e.created_at))}</td><td><strong>${escapeHtml(e.predicted_label || '—')}</strong></td><td>${escapeHtml(fmtPct(e.confidence))}</td><td><span class="status-tag ${escapeHtml(e.outcome)}">${escapeHtml(e.outcome)}</span></td><td>${Number(e.frame_count)||0}${Number(e.frames_dropped)?` <span title="Frames dropped">(−${Number(e.frames_dropped)})</span>`:''}</td><td>${escapeHtml(fmtMs(e.processing_latency_ms))}</td><td>${escapeHtml(e.reason || '—')}</td></tr>`).join('') : '<tr><td colspan="7" class="empty-cell">No events recorded yet.</td></tr>';
      renderIoTHistory(iot.events || []);
    } catch (e) { message(`Could not load local history: ${e.message}`, 'error'); }
  }
  async function loadIoTHistory() {
    try { const data = await api('/api/iot/history?limit=100'); renderIoTHistory(data.events || []); } catch {}
  }
  function renderIoTHistory(rows) {
    if (latestCommandId) {
      const recent = rows.find((item) => item.command_id === latestCommandId);
      if (recent) {
        $('iotMessage').textContent = `${recent.ack_status} — publish: ${recent.publish_status}${recent.ack_detail ? ` · ${recent.ack_detail}` : ''}`;
        $('iotMessage').className = `iot-status ${recent.ack_status === 'acknowledged' ? 'good' : ['rejected','duplicate','timed_out','publish_failed'].includes(recent.ack_status) ? 'bad' : ''}`.trim();
      }
    }
    $('iotHistory').innerHTML = rows.length ? rows.map(e => `<tr><td>${escapeHtml(fmtTime(e.created_at))}</td><td>${escapeHtml(e.source_label)}</td><td><code>${escapeHtml(e.mapped_command)}</code></td><td><span class="status-tag ${escapeHtml(e.publish_status)}">${escapeHtml(e.publish_status)}</span></td><td><span class="status-tag ${escapeHtml(e.ack_status)}">${escapeHtml(e.ack_status)}</span></td><td>${escapeHtml(e.ack_detail || '—')}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">No IoT command events.</td></tr>';
  }

  function displayLabel(item) { return item?.display_name || String(item?.label || '').replace(/([a-z])([A-Z])/g, '$1 $2'); }
  function currentClass() { return referenceClasses.find((item) => item.label === $('referenceClass').value) || null; }
  function renderReferenceList() {
    const query = $('referenceSearch').value.trim().toLowerCase();
    const selected = $('referenceClass').value;
    const filtered = referenceClasses.filter((item) => `${item.display_name} ${item.label}`.toLowerCase().includes(query));
    $('referenceList').innerHTML = filtered.length ? filtered.map(item => `<button class="reference-item ${item.label === selected ? 'selected' : ''} ${item.label === expectedLabel ? 'expected' : ''}" data-label="${escapeHtml(item.label)}"><span class="reference-index">${String(item.index).padStart(2,'0')}</span><span class="reference-item-name">${escapeHtml(item.display_name)}</span><span class="reference-item-count">${item.available_count} local</span></button>`).join('') : '<p class="empty-cell">No class matches that search.</p>';
    $('referenceList').querySelectorAll('[data-label]').forEach(button => button.addEventListener('click', () => selectReferenceClass(button.dataset.label)));
    const classesWithVideos = referenceClasses.filter(c => Number(c.available_count) > 0).length;
    $('referenceCount').textContent = `${classesWithVideos}/${referenceClasses.length} classes with local videos · ${referenceClasses.reduce((n,c)=>n+c.available_count,0)} clips ready`;
  }
  function selectReferenceClip(item, video) {
    if (!item || !video) return;
    selectedReference = {...video, index:item.index, display_name:item.display_name};
    $('referenceVideo').pause();
    $('referenceVideo').src = video.url;
    $('referenceVideo').load();
    $('referenceVideoPlaceholder').hidden = true;
    $('referenceVideoTitle').textContent = `${item.display_name} — ${video.category || 'INCLUDE-50'}`;
    const splitLabel = String(video.split || 'unknown').toUpperCase();
    $('referenceVideoDetail').textContent = `${splitLabel} split · ${video.video_path}`;
    $('setExpectedButton').disabled = false;
    $('referenceHelp').textContent = `Showing an official ${splitLabel} example. Choose another local clip from the selector to compare recording variations. This is a reference aid, not an accuracy measurement.`;
  }
  function selectReferenceClass(label) {
    const item = referenceClasses.find((c) => c.label === label);
    if (!item) return;
    $('referenceClass').value = item.label;
    selectedReference = null;
    $('setExpectedButton').disabled = true;
    $('referenceVideo').pause(); $('referenceVideo').removeAttribute('src'); $('referenceVideo').load();
    $('referenceVideoPlaceholder').hidden = false;
    $('referenceVideoTitle').textContent = item.display_name;
    $('referenceVideoDetail').textContent = item.video_count ? `${item.video_count} dataset examples indexed · ${item.available_count} local clips available` : 'No video metadata mapped to this class yet.';
    const available = (item.videos || []).filter(v => v.available);
    const selector = $('referenceClipSelect');
    selector.innerHTML = '';
    if (available.length) {
      available.forEach((video, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        const split = String(video.split || 'unknown').toUpperCase();
        option.textContent = `${String(index + 1).padStart(2, '0')} · ${split} · ${video.video_path.split('/').pop()}`;
        selector.appendChild(option);
      });
      selector.disabled = false;
      $('referenceClipCount').textContent = `${available.length} local clip${available.length === 1 ? '' : 's'}`;
      selectReferenceClip(item, available[0]);
      selector.onchange = () => {
        const selected = available[Number(selector.value)];
        if (selected) selectReferenceClip(item, selected);
      };
    } else {
      selector.disabled = true;
      selector.innerHTML = '<option value="">No local clips available</option>';
      $('referenceClipCount').textContent = '0 local clips';
      $('referenceHelp').textContent = item.video_count ? 'Dataset examples are indexed, but no matching clip for this class is present locally. Download selected clips with the split-aware reference downloader.' : 'No matching reference clip found. Prepare metadata and point ISL_REFERENCE_VIDEO_DIR to the extracted INCLUDE video directory.';
    }
    renderReferenceList();
  }
  function renderExpectedComparison(predictedLabel) {
    const box = $('comparisonBox');
    if (!expectedLabel) { box.hidden = true; return; }
    box.hidden = false;
    const expected = referenceClasses.find(c => c.label === expectedLabel);
    const predicted = String(predictedLabel || '').toLowerCase().replace(/[^a-z0-9]/g,'');
    $('expectedResultLabel').textContent = expected?.display_name || expectedLabel;
    const normalizedExpected = String(expectedLabel).toLowerCase().replace(/[^a-z0-9]/g,'');
    const match = predicted === normalizedExpected;
    $('expectedMatchValue').textContent = predictedLabel === '—' ? 'No prediction' : (match ? 'MATCH' : `Different: ${predictedLabel}`);
    $('expectedMatchValue').className = match ? 'comparison-match' : 'comparison-mismatch';
    $('comparisonNote').textContent = match ? 'The predicted class matches the selected expected gloss. This is a single practice result, not a validated accuracy measurement.' : 'A mismatch does not prove the model is wrong: ensure you copied the example, the entire sign is visible, and capture only one isolated gesture.';
  }
  async function loadReferenceGallery() {
    try {
      const data = await api('/api/reference-videos');
      referenceClasses = data.classes || [];
      $('referenceClass').innerHTML = '<option value="">Select an INCLUDE-50 class…</option>' + referenceClasses.map(c => `<option value="${escapeHtml(c.label)}">${String(c.index).padStart(2,'0')} · ${escapeHtml(c.display_name)}</option>`).join('');
      $('referenceSearch').addEventListener('input', renderReferenceList);
      $('referenceClass').addEventListener('change', () => { const c = currentClass(); if (c) selectReferenceClass(c.label); });
      $('setExpectedButton').addEventListener('click', () => {
        const c = currentClass(); if (!c) return;
        expectedLabel = c.label;
        $('expectedSignStatus').textContent = `Expected sign: ${c.display_name}`;
        $('expectedSignStatus').className = 'expected-status is-set';
        renderReferenceList();
        renderExpectedComparison($('predictionWord').textContent);
      });
      if (!data.metadata_ready) $('referenceHelp').textContent = 'Reference metadata is not downloaded yet. Follow docs/INCLUDE_REFERENCE_VIDEOS.md, then refresh the page. The 50 class labels are still available below.';
      else if (!data.available_video_count) $('referenceHelp').textContent = 'Metadata is ready, but no local videos were found. Configure ISL_REFERENCE_VIDEO_DIR to the extracted INCLUDE video directory, restart the app, and refresh.';
      renderReferenceList();
    } catch (e) { $('referenceCount').textContent = 'Gallery unavailable'; $('referenceList').innerHTML = `<p class="empty-cell">${escapeHtml(e.message)}</p>`; }
  }

  $('startButton').addEventListener('click', async () => {
    try {
      const cameraWasReady = Boolean(stream && stream.getVideoTracks().some((track) => track.readyState === 'live')
        && video.readyState >= 2 && video.videoWidth > 1);
      await ensureCamera();
      if (!cameraWasReady) {
        $('startButton').textContent = 'Start capture';
        message('Camera preview is ready. Check the live landmarks, adjust until your wrists and fingertips are visible, then press Start capture.', 'success');
        return;
      }
      const health = await refreshHealth();
      if (!health?.model_ready) throw new Error(health?.model_error || 'Configure the official checkpoint and label map before recognition.');
      if (!health?.extractor_ready) throw new Error(health?.extractor_error || 'MediaPipe/OpenCV not available.');
      await connectSocket();
      if (ws.readyState !== WebSocket.OPEN) throw new Error('WebSocket is not ready.');
      await countdownThenStart();
    } catch (e) {
      $('startButton').disabled = false;
      $('captureStatus').textContent = 'Camera ready';
      message(e.message, 'error');
    }
  });
  $('stopButton').addEventListener('click', () => submitCapture('manual'));
  $('resetButton').addEventListener('click', () => {
    cancelCaptureCountdown();
    stopFrameSender();
    if (capturing && ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type:'cancel'}));
    if (submitting) { message('Clip processing is underway; wait for its result before retrying.', ''); return; }
    onCaptureEnded('Ready to retry'); $('predictionWord').textContent = '—'; $('predictionLabel').textContent = 'No gesture submitted'; $('confidenceValue').textContent = '—'; $('confidenceBar').style.width='0%'; if ($('candidateSection')) $('candidateSection').hidden = true; $('outcomeValue').textContent='Waiting'; $('inferenceLatencyValue').textContent='—'; $('processingLatencyValue').textContent='—'; $('endToEndLatencyValue').textContent='—'; $('poseValue').textContent='—'; $('handValue').textContent='—'; $('progressBar').style.width='0%';
  });
  $('iotToggle').addEventListener('change', async (e) => {
    if (e.target.disabled) return;
    e.target.disabled = true;
    try { const result = await api('/api/iot/enable', {method:'POST', body:JSON.stringify({enabled:e.target.checked})}); $('iotState').textContent = result.iot_enabled ? 'Enabled' : 'Disabled'; setDot('iotDot', result.iot_enabled ? 'good' : ''); $('iotMessage').textContent = result.message; $('iotMessage').className = 'iot-status'; }
    catch (err) { e.target.checked = false; message(`Could not change IoT setting: ${err.message}`, 'error'); }
    finally { e.target.disabled = Boolean(window.islPreIoTLockdown); }
  });
  $('refreshButton').addEventListener('click', loadHistory);
  $('clearHistoryButton').addEventListener('click', async () => {
    if (!confirm('Delete all local recognition and IoT history? This does not send or repeat any device command.')) return;
    try { await api('/api/history/reset', {method:'POST', body:JSON.stringify({confirm:true})}); await loadHistory(); message('Local history cleared. No IoT command was executed.', 'success'); }
    catch (e) { message(e.message, 'error'); }
  });

  refreshHealth(); loadHistory(); loadReferenceGallery();
  setInterval(() => { refreshHealth(); loadIoTHistory(); }, 5000);
})();
