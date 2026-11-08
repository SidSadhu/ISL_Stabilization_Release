/*
 * Optional camera-guidance worker. This uses MediaPipe Tasks Vision in the browser
 * solely for visualization. Its output is NOT used to construct INCLUDE-50 inputs.
 * Requires network access to jsDelivr and Google's public task-model hosting on first use.
 */
import {
  FilesetResolver,
  HandLandmarker,
  PoseLandmarker,
} from 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.2.0/vision_bundle.mjs';

let handLandmarker = null;
let poseLandmarker = null;
let lastTimestamp = 0;

function compactLandmarks(landmarks) {
  return (landmarks || []).map((lm) => ({
    x: Number(lm.x) || 0,
    y: Number(lm.y) || 0,
    z: Number(lm.z) || 0,
    visibility: Number.isFinite(lm.visibility) ? lm.visibility : 1,
  }));
}

async function initialize() {
  const vision = await FilesetResolver.forVisionTasks(
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.2.0/wasm'
  );
  handLandmarker = await HandLandmarker.createFromOptions(vision, {
    baseOptions: {
      modelAssetPath: 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',
      delegate: 'CPU',
    },
    runningMode: 'VIDEO',
    numHands: 2,
    minHandDetectionConfidence: 0.45,
    minHandPresenceConfidence: 0.45,
    minTrackingConfidence: 0.45,
  });
  poseLandmarker = await PoseLandmarker.createFromOptions(vision, {
    baseOptions: {
      modelAssetPath: 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task',
      delegate: 'CPU',
    },
    runningMode: 'VIDEO',
    numPoses: 1,
    minPoseDetectionConfidence: 0.45,
    minPosePresenceConfidence: 0.45,
    minTrackingConfidence: 0.45,
    outputSegmentationMasks: false,
  });
  self.postMessage({ type: 'ready' });
}

function detect(bitmap, timestamp) {
  // MediaPipe VIDEO timestamps must increase monotonically.
  const safeTimestamp = Math.max(Number(timestamp) || 0, lastTimestamp + 1);
  lastTimestamp = safeTimestamp;
  try {
    const hands = handLandmarker.detectForVideo(bitmap, safeTimestamp);
    const pose = poseLandmarker.detectForVideo(bitmap, safeTimestamp);
    const handPoints = (hands.landmarks || []).map(compactLandmarks);
    const handLabels = (hands.handedness || []).map((items) => items?.[0]?.categoryName || 'Hand');
    self.postMessage({
      type: 'result',
      timestamp: safeTimestamp,
      width: bitmap.width,
      height: bitmap.height,
      poseLandmarks: compactLandmarks(pose.landmarks?.[0] || []),
      hands: handPoints,
      handLabels,
    });
  } catch (error) {
    self.postMessage({ type: 'frame_error', message: error?.message || String(error) });
  } finally {
    bitmap.close?.();
  }
}

self.onmessage = async (event) => {
  const message = event.data || {};
  if (message.type === 'init') {
    try {
      await initialize();
    } catch (error) {
      self.postMessage({ type: 'init_error', message: error?.message || String(error) });
    }
  } else if (message.type === 'frame' && handLandmarker && poseLandmarker && message.bitmap) {
    detect(message.bitmap, message.timestamp);
  }
};
