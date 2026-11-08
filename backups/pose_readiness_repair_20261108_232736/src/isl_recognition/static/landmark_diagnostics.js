(() => {
  const $ = (id) => document.getElementById(id);
  const video = $('video');
  const canvas = $('landmarkCanvas');
  const ctx = canvas?.getContext('2d');
  const toggle = $('landmarkToggle');
  if (!video || !canvas || !ctx || !toggle) return;

  // MediaPipe Pose landmark indices, drawn only for camera guidance.
  const POSE_CONNECTIONS = [
    [11, 12], [11, 13], [13, 15], [15, 17], [15, 19], [15, 21],
    [12, 14], [14, 16], [16, 18], [16, 20], [16, 22],
    [11, 23], [12, 24], [23, 24],
  ];
  const HAND_CONNECTIONS = [
    [0, 1], [1, 2], [2, 3], [3, 4],
    [0, 5], [5, 6], [6, 7], [7, 8],
    [5, 9], [9, 10], [10, 11], [11, 12],
    [9, 13], [13, 14], [14, 15], [15, 16],
    [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
  ];

  let socket = null;
  let connecting = false;
  let connected = false;
  let frameInFlight = false;
  let scheduled = false;
  let failed = false;
  let busyState = 'ready';
  let lastResultAt = 0;
  const resultIntervals = [];
  let latest = null;
  const recentSamples = [];
  let readiness = {ready:false, samples:0, poseDetected:0, handDetected:0};
  let sampleCanvas = null;
  let sampleContext = null;

  function setMessage(text, tone = '') {
    const element = $('landmarkMessage');
    if (!element) return;
    element.textContent = text;
    element.className = `landmark-message ${tone}`.trim();
  }

  function clearOverlay() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function updateCanvasSize(width = video.videoWidth, height = video.videoHeight) {
    if (width > 0 && height > 0 && (canvas.width !== width || canvas.height !== height)) {
      canvas.width = width;
      canvas.height = height;
    }
  }

  function pointVisible(point) {
    return Boolean(point) && Number.isFinite(point.x) && Number.isFinite(point.y)
      && (point.visibility == null || point.visibility >= 0.35)
      && point.x >= -0.05 && point.x <= 1.05 && point.y >= -0.05 && point.y <= 1.05;
  }

  function lineBetween(points, from, to, color, width) {
    const a = points[from], b = points[to];
    if (!pointVisible(a) || !pointVisible(b)) return;
    ctx.beginPath();
    ctx.moveTo(a.x * canvas.width, a.y * canvas.height);
    ctx.lineTo(b.x * canvas.width, b.y * canvas.height);
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineCap = 'round';
    ctx.stroke();
  }

  function drawPoints(points, color, radius) {
    for (const point of points || []) {
      if (!pointVisible(point)) continue;
      ctx.beginPath();
      ctx.arc(point.x * canvas.width, point.y * canvas.height, radius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
    }
  }

  function render(result) {
    if (!toggle.checked) return;
    updateCanvasSize(result.width, result.height);
    clearOverlay();

    const pose = result.poseLandmarks || [];
    const visiblePoseCount = pose.slice(0, 25).filter(pointVisible).length;
    for (const [from, to] of POSE_CONNECTIONS) {
      lineBetween(pose, from, to, '#f0c66b', Math.max(2, canvas.width / 320));
    }
    drawPoints(pose.slice(0, 25), '#f0c66b', Math.max(2.5, canvas.width / 240));

    const validHands = (result.hands || []).filter((hand) => Array.isArray(hand) && hand.length >= 21);
    for (const hand of validHands) {
      for (const [from, to] of HAND_CONNECTIONS) {
        lineBetween(hand, from, to, '#39d99a', Math.max(2, canvas.width / 300));
      }
      drawPoints(hand, '#39d99a', Math.max(2.5, canvas.width / 250));
    }

    const poseOK = visiblePoseCount >= 8;
    const handOK = validHands.length >= 1;
    recentSamples.push({poseOK, handOK, handCount:validHands.length, visiblePoseCount});
    if (recentSamples.length > 5) recentSamples.shift();
    const poseDetected = recentSamples.filter(item => item.poseOK).length;
    const handDetected = recentSamples.filter(item => item.handOK).length;
    const sampleCount = recentSamples.length;
    const denominator = Math.max(1, sampleCount);
    readiness = {
      ready: sampleCount === 5 && poseDetected >= 4 && handDetected >= 4,
      samples: sampleCount,
      poseDetected,
      handDetected,
      latestHandCount: validHands.length,
      latestPoseCount: visiblePoseCount
    };
    $('livePoseStatus').textContent = `${poseDetected}/${denominator} recent`;
    $('liveHandsStatus').textContent = `${validHands.length} now · ${handDetected}/${denominator} recent`;
    if ($('liveReadyStatus')) $('liveReadyStatus').textContent = readiness.ready ? 'Ready' : `Preparing (${poseDetected}/${denominator} pose · ${handDetected}/${denominator} hand)`;
    const labels = (result.handLabels || []).filter(Boolean);
    $('liveHandsStatus').title = labels.length ? `Latest detector labels: ${labels.join(', ')}. Rolling window is the last ${sampleCount} processed preview frames.` : 'The count is the latest frame; recent count is the last five processed preview frames.';
    window.dispatchEvent(new CustomEvent('isl-landmark-readiness', {detail:readiness}));
    // Measure result-arrival intervals on this browser clock. Echoed source-frame
    // timestamps can be stale or come from a previous connection; subtracting them
    // caused impossible negative diagnostic rates in some reconnect sequences.
    const receivedAt = performance.now();
    if (lastResultAt > 0) {
      const interval = receivedAt - lastResultAt;
      if (Number.isFinite(interval) && interval > 0 && interval < 5000) {
        resultIntervals.push(interval);
        if (resultIntervals.length > 8) resultIntervals.shift();
      }
      const avg = resultIntervals.length
        ? resultIntervals.reduce((sum, value) => sum + value, 0) / resultIntervals.length
        : 0;
      $('liveRateStatus').textContent = avg > 0 ? `~${(1000 / avg).toFixed(1)} updates/s` : 'Measuring…';
    } else {
      $('liveRateStatus').textContent = 'Measuring…';
    }
    lastResultAt = receivedAt;
    const sourceTimestamp = Number(result.timestamp);
    const frameAgeMs = Number.isFinite(sourceTimestamp) ? Math.max(0, receivedAt - sourceTimestamp) : null;
    $('liveRateStatus').title = frameAgeMs == null ? 'Result update rate measured on response arrival.' : `Approximate age of the source frame when rendered: ${Math.round(frameAgeMs)} ms. This preview is sampled and not a 25 FPS tracking stream.`;
    latest = { handCount: validHands.length, visiblePoseCount, latencyMs: result.latencyMs, frameAgeMs };

    if (readiness.ready) {
      setMessage('Framing is stable across the last five samples. The 3-second start countdown can begin. This checks visibility only; it does not verify sign correctness.', 'good');
    } else if (sampleCount < 5) {
      setMessage(`Checking framing (${sampleCount}/5 samples). Keep your upper body and at least one hand visible; the readiness check does not require both hands for one-handed signs.`, '');
    } else {
      setMessage(`Not ready yet: pose ${poseDetected}/5, at least one hand ${handDetected}/5. Move slightly back and keep your signing hand inside the frame.`, 'warn');
    }
  }

  function scheduleFrame(delayOverride = null) {
    if (scheduled || failed || !toggle.checked || !connected || busyState !== 'ready'
      || video.readyState < 2 || video.videoWidth < 2 || frameInFlight
      || !socket || socket.readyState !== WebSocket.OPEN) return;
    scheduled = true;
    // Only one request is in flight at a time, so there is no client-side request
    // queue to protect against. Ask for the next diagnostic frame promptly after
    // a result; the backend enforces a small minimum interval as a safety guard.
    // True update rate is bounded by local MediaPipe latency, not a fixed timer.
    const delay = Number.isFinite(delayOverride) ? Math.max(10, delayOverride) : 10;
    setTimeout(() => {
      scheduled = false;
      sendDiagnosticFrame();
    }, delay);
  }

  function sendDiagnosticFrame() {
    if (!toggle.checked || !connected || busyState !== 'ready' || frameInFlight
      || !socket || socket.readyState !== WebSocket.OPEN
      || video.readyState < 2 || video.videoWidth < 2) return;
    try {
      // A 480px-wide JPEG keeps the local preview responsive on CPU while retaining
      // enough detail for framing. It is guidance only; capture still records 640px
      // frames and the classifier uses the untouched reference preprocessing path.
      if (!sampleCanvas) {
        sampleCanvas = document.createElement('canvas');
        sampleContext = sampleCanvas.getContext('2d', { alpha: false });
      }
      const targetWidth = Math.min(480, video.videoWidth);
      const targetHeight = Math.max(1, Math.round(targetWidth * video.videoHeight / video.videoWidth));
      sampleCanvas.width = targetWidth;
      sampleCanvas.height = targetHeight;
      sampleContext.drawImage(video, 0, 0, targetWidth, targetHeight);
      const jpegDataUrl = sampleCanvas.toDataURL('image/jpeg', 0.68);
      frameInFlight = true;
      socket.send(JSON.stringify({
        type: 'frame',
        timestamp: performance.now(),
        width: targetWidth,
        height: targetHeight,
        jpeg_b64: jpegDataUrl,
      }));
    } catch (error) {
      frameInFlight = false;
      setMessage(`Could not send a camera diagnostic frame: ${error?.message || error}`, 'warn');
      scheduleFrame();
    }
  }

  function stopDiagnostics(clear = true) {
    lastResultAt = 0;
    resultIntervals.length = 0;
    recentSamples.length = 0;
    readiness = {ready:false, samples:0, poseDetected:0, handDetected:0};
    window.dispatchEvent(new CustomEvent('isl-landmark-readiness', {detail:readiness}));
    if (socket) {
      try { socket.close(1000, 'Live diagnostics stopped'); } catch {}
    }
    socket = null;
    connected = false;
    connecting = false;
    frameInFlight = false;
    scheduled = false;
    if (clear) clearOverlay();
  }

  function startDiagnostics() {
    if (failed || connecting || socket || !toggle.checked
      || video.readyState < 2 || video.videoWidth < 2) return;
    connecting = true;
    lastResultAt = 0;
    resultIntervals.length = 0;
    $('liveRateStatus').textContent = 'Measuring…';
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    try {
      socket = new WebSocket(`${scheme}//${location.host}/ws/landmark-preview`);
      socket.addEventListener('open', () => {
        // The server sends an explicit `connected` message only after its local
        // MediaPipe session is ready.
      });
      socket.addEventListener('message', (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch {
          setMessage('The local landmark service sent an unreadable response.', 'warn');
          return;
        }
        if (message.type === 'connected') {
          connecting = false;
          connected = true;
          failed = false;
          setMessage('Local MediaPipe landmark guidance is ready. These sampled preview landmarks are diagnostic only; recognition still uses the recorded clip and original 134-D features.', 'good');
          scheduleFrame();
        } else if (message.type === 'result') {
          frameInFlight = false;
          render(message);
          scheduleFrame();
        } else if (message.type === 'throttled') {
          frameInFlight = false;
          scheduleFrame(Math.max(40, Number(message.retry_after_ms) || 100));
        } else if (message.type === 'frame_error') {
          frameInFlight = false;
          setMessage(message.message || 'Local landmark extraction failed for this preview frame.', 'warn');
          scheduleFrame();
        } else if (message.type === 'error') {
          frameInFlight = false;
          setMessage(message.message || 'The local landmark service reported an error.', 'warn');
          scheduleFrame();
        }
      });
      socket.addEventListener('error', () => {
        failed = true;
        connecting = false;
        connected = false;
        frameInFlight = false;
        $('livePoseStatus').textContent = 'Unavailable';
        $('liveHandsStatus').textContent = 'Unavailable';
        $('liveRateStatus').textContent = '—';
        setMessage('Could not connect to the local landmark-preview WebSocket. Restart the app with the updated patch and reload the page.', 'warn');
      });
      socket.addEventListener('close', (event) => {
        const wasExpected = event.code === 1000 && (!toggle.checked || busyState !== 'ready');
        socket = null;
        connected = false;
        connecting = false;
        frameInFlight = false;
        scheduled = false;
        if (!wasExpected && toggle.checked && !failed) {
          setMessage('The local landmark-preview connection closed. Toggle the overlay off and on to reconnect.', 'warn');
        }
      });
    } catch (error) {
      failed = true;
      connecting = false;
      setMessage(`Local landmark guidance is unavailable: ${error?.message || error}. Backend recognition itself is unchanged.`, 'warn');
    }
  }

  toggle.addEventListener('change', () => {
    if (toggle.checked) {
      failed = false;
      canvas.style.display = 'block';
      startDiagnostics();
    } else {
      stopDiagnostics(true);
      canvas.style.display = 'none';
      $('liveRateStatus').textContent = 'Off';
      setMessage('Live overlay disabled. Recording and backend recognition remain unchanged.');
    }
  });

  window.addEventListener('isl-camera-ready', () => {
    updateCanvasSize();
    if (toggle.checked) startDiagnostics();
  });
  window.addEventListener('isl-readiness-arm', () => {
    recentSamples.length = 0;
    readiness = {ready:false, samples:0, poseDetected:0, handDetected:0};
    if ($('livePoseStatus')) $('livePoseStatus').textContent = '0/5 recent';
    if ($('liveHandsStatus')) $('liveHandsStatus').textContent = '0 now · 0/5 recent';
    if ($('liveReadyStatus')) $('liveReadyStatus').textContent = 'Move into frame';
    setMessage('Capture is armed. Move away from the laptop and bring your upper body and at least one hand into view.', '');
    window.dispatchEvent(new CustomEvent('isl-landmark-readiness', {detail:readiness}));
    scheduleFrame();
  });

  window.addEventListener('isl-capture-state', (event) => {
    busyState = event.detail?.state || 'ready';
    if (busyState !== 'ready') {
      // Release the second MediaPipe session during countdown capture and post-capture
      // extraction. This avoids competing with the classifier's full-clip extractor.
      stopDiagnostics(true);
      return;
    }
    if (toggle.checked) startDiagnostics();
  });
  video.addEventListener('loadeddata', () => {
    updateCanvasSize();
    if (toggle.checked) startDiagnostics();
  });
  window.addEventListener('beforeunload', () => stopDiagnostics(false));
  if (video.srcObject && video.readyState >= 2) startDiagnostics();

  window.ISLLandmarkDiagnostics = {
    getLatest: () => latest,
    getReadiness: () => ({...readiness}),
    stop: () => stopDiagnostics(true),
  };
})();
