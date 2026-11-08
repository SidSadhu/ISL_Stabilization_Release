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

    $('livePoseStatus').textContent = visiblePoseCount >= 8
      ? `Detected (${visiblePoseCount}/25)` : `Weak (${visiblePoseCount}/25)`;
    $('liveHandsStatus').textContent = `${validHands.length} detected`;
    const labels = (result.handLabels || []).filter(Boolean);
    $('liveHandsStatus').title = labels.length ? `Detector labels: ${labels.join(', ')}` : 'Hand count from local backend MediaPipe';
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

    if (validHands.length === 0) {
      setMessage('No hands detected by the local backend detector. Move your hands fully into the camera frame and keep fingertips visible.', 'warn');
    } else if (visiblePoseCount < 8) {
      setMessage(`${validHands.length} hand(s) detected, but upper-body pose visibility is weak. Keep shoulders, elbows and wrists in view.`, 'warn');
    } else if (validHands.length === 1) {
      setMessage('One hand detected by the local backend. This can be valid for one-handed signs; bring both hands into frame for two-handed signs.', 'good');
    } else {
      setMessage(`Both hands detected by the local backend (${Number(result.latencyMs || 0).toFixed(0)} ms for this diagnostic frame). Keep wrists and fingertips in view.`, 'good');
    }
  }

  function scheduleFrame(delayOverride = null) {
    if (scheduled || failed || !toggle.checked || !connected || busyState === 'processing'
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
    if (!toggle.checked || !connected || busyState === 'processing' || frameInFlight
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
        const wasExpected = event.code === 1000 && !toggle.checked;
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
  window.addEventListener('isl-capture-state', (event) => {
    busyState = event.detail?.state || 'ready';
    if (busyState === 'processing') {
      scheduled = false;
      // Keep the socket open, but yield CPU to full recorded-clip extraction.
      return;
    }
    scheduleFrame();
  });
  video.addEventListener('loadeddata', () => {
    updateCanvasSize();
    if (toggle.checked) startDiagnostics();
  });
  window.addEventListener('beforeunload', () => stopDiagnostics(false));
  if (video.srcObject && video.readyState >= 2) startDiagnostics();

  window.ISLLandmarkDiagnostics = {
    getLatest: () => latest,
    stop: () => stopDiagnostics(true),
  };
})();
