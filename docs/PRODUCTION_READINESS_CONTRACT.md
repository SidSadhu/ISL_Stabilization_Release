# ISL Recognition — Pre-IoT Production Readiness Contract

**Status:** Required acceptance contract for the next consolidated release  
**Scope:** Local single-camera isolated-word ISL recognition using AI4Bharat INCLUDE-50. IoT activation is explicitly out of scope until all gates pass.

## Product truth and scope

This system recognises a closed set of isolated signs from a webcam. It is not continuous sign-language translation, not a sentence-level interpreter, and not yet validated for arbitrary signers or uncontrolled environments. The UI and project documentation must communicate that scope honestly. “Production-level” means a repeatable, diagnosable, tested local application within that scope—not a claim of universal recognition accuracy.

## Decision: no production confidence threshold has been established

The previous `0.30` suggestion is **not a production decision**. It was a candidate operating point selected from a tiny exploratory sample and must not be represented as validated. Keep the existing value as a temporary safety gate until a held-out validation set is available. The UI must show separately:

- the raw top-1 candidate and score;
- the acceptance decision;
- the reason for rejection (capture quality, confidence policy, processing failure);
- top-2 label/score or a useful ambiguity indicator.

A softmax score is not automatically the probability that a class is correct. Threshold selection must use a validation dataset and a pre-declared operating objective. For this project, report a **risk-versus-coverage curve**: at each threshold, show the fraction accepted and accuracy among accepted predictions. Select a threshold on validation data to meet the chosen maximum accepted-error rate while retaining useful coverage; evaluate that frozen threshold once on a separate holdout set. Report per-class results as well as aggregate results.

The current 29-clip gallery smoke test is not sufficient for this choice. It has one clip per class, covers only 29 classes in the reported run, may contain videos seen during model development/training, and was used to inspect several threshold settings. Its threshold sweep is exploratory only.

## Frozen model contract

Do not change these without a separately versioned experiment and a before/after evaluation:

- class/label mapping and output index order;
- input shape: 169 frames × 134 features per frame;
- feature concatenation order and coordinate conventions;
- temporal interpolation, padding and truncation policy;
- checkpoint identity and model architecture;
- deterministic evaluation behavior (`model.eval()` plus inference mode, with no unintended active dropout).

The legacy INCLUDE generator requests `upper_body_only=True`, but current installed MediaPipe 0.10.21 rejects this argument. The adapter that selects the first 25 points from full-pose output is shape-compatible, **not an exact reproduction of the historical upper-body model**. The release must either (a) reproduce the historical extractor with a verified compatible implementation, or (b) keep the compatibility adapter, explicitly disclose it, and validate model quality with the actual serving extractor. Never call feature parity exact while this discrepancy remains.

## Release gates

### Gate 1 — Reproducibility and configuration

- One canonical source tree and one versioned release; no stack of untracked patch ZIPs.
- Pin Python package versions and record Python, OS, CPU/GPU, MediaPipe, OpenCV, PyTorch, checkpoint hash and label-map hash in benchmark reports.
- Read configurable values from one validated settings source; fail startup on invalid values rather than silently using inconsistent defaults.
- Keep secrets out of source control; `.env.example` contains placeholders only.
- Startup/preflight verifies checkpoint, label map, video/model tensor contract, required folders and write permissions.

**Pass:** clean setup from documented commands; deterministic startup; reported configuration matches the running server.

### Gate 2 — Feature and model contract

- Tests cover 134 features/frame, 169-frame sequence, expected dtype and finite values, correct class count/output mapping, short/long/empty sequences, missing-landmark interpolation and left/right hand-slot assignment.
- Repeated inference on identical features returns numerically stable logits/probabilities within a declared tolerance.
- Regression fixture: save a small, non-sensitive set of known input tensors and expected top-k labels/logits within tolerance.
- Tests specifically detect accidental active dropout in evaluation mode.

**Pass:** all unit/contract tests pass and no untracked preprocessing change is introduced.

### Gate 3 — Capture and user experience

- Separate preparation countdown from capture duration; allow manual stop and safe automatic stop.
- Show distinct live camera, pose/hand guidance, capture elapsed time/frames/FPS, post-capture extraction progress, model result and acceptance status.
- Live overlay reports measured result age/latency; it must not be called “real time” unless measured latency supports that description.
- No stale responses may overwrite newer overlay results; there is at most one in-flight preview request per client.
- Keep live diagnostic processing separate from the classifier preprocessing path; pause/de-rate it while capture extraction is underway if CPU competition is material.
- Do not save raw camera video by default. Store event metadata and aggregate timings needed for debugging.

**Pass:** at least 20 repeated local webcam attempts across multiple sessions, with capture FPS, dropped frames, hand coverage, overlay latency and errors recorded; all failure states are understandable to the user.

### Gate 4 — Performance

Benchmark native and 640px paths in alternating order, after a warm-up, for at least three runs per mode on the target laptop. Separate video decode, resize/conversion, pose extraction, hand extraction, feature assembly, model load and model inference. Report median and p95, not only mean.

Provisional engineering targets for a local demonstration (must be measured, not assumed):

- Preview overlay: median response ≤150 ms and p95 ≤300 ms, or explicitly documented lower-rate guidance if the legacy detector cannot meet this.
- Capture: ≥95% of expected frames received, without unbounded queueing.
- Post-capture extraction: target p95 ≤5 seconds for a typical 2–4 second clip on the target laptop; if this cannot be met without changing semantics, document the measured limit and optimise with a paired accuracy test.
- Model inference: report median/p95 separately; do not confuse it with the much slower landmark extraction.

Do not reduce classifier frame count, resize serving frames, swap landmark model, or skip samples solely to improve speed without rerunning the same labelled benchmark.

### Gate 5 — Valid evaluation and threshold calibration

Create a labelled manifest with class, clip path, source, signer/session when known, resolution, duration, extractor version and split. Deduplicate exact/near-duplicate clips. Prefer official dataset splits and signer/session separation where metadata supports it. Never tune a threshold against the final test set.

Minimum development data collection target for this project:

- Reference set: multiple clips per supported class, with the current 29-clip gallery retained as a smoke suite only.
- Webcam set: at least 10 labelled attempts per target sign across three sessions; include failures and low-quality captures rather than excluding them.
- Generalisation test: include additional signers if available. If only the project owner participates, state that the evaluation only measures that signer and do not claim general performance across signers.
- Use separate calibration/validation and final test data. For a stronger claim across 50 classes, collect enough independent examples per class to report meaningful per-class confidence intervals; do not infer class-level reliability from a single clip.

Required metrics:

- Top-1 and top-5 accuracy;
- per-class precision/recall and confusion matrix;
- accepted coverage (acceptance rate) and selective error rate;
- capture-invalid rate and rate of low-hand/low-pose-coverage failures;
- confidence distributions for correct and incorrect predictions;
- calibration reliability diagram and a proper scoring metric such as log loss/Brier score, interpreted with class balance and dataset size in mind;
- latency median/p95 and error rate.

Set the threshold on validation data based on an explicit goal (for example, maximum acceptable error among accepted outputs subject to a minimum useful coverage). Freeze it, then report metrics once on the held-out test set. If sample size does not support a credible threshold, continue in research/demo mode and say so.

**Pass:** threshold is documented with rationale, validation manifest/hash, acceptance coverage, selective risk, and held-out performance. No threshold is called production-ready solely because a small sweep reached 100% on a few accepted clips.

### Gate 6 — Failure handling, privacy and reliability

- Validate uploaded/captured frame counts, frame dimensions, payload size, timestamps, decode errors, sequence shapes and finite features.
- Bound memory and queues; handle browser disconnect, WebSocket close, camera permission denial, missing checkpoint, extraction exceptions and storage failures with visible messages.
- No model result on invalid capture. Never report a rejected prediction as if inference had not produced a candidate.
- No IoT command from invalid capture, low-confidence output, diagnostic override, unknown label or stale result. IoT remains disabled for this release.
- No raw video persisted by default. Document what event metadata is stored and provide a simple retention/deletion approach.

### Gate 7 — Repeatable release and regression suite

- Clean install/start test on Windows following only the documented setup guide.
- Full pytest suite, Python compile check, JavaScript syntax check and dependency preflight.
- API tests for health, history, malformed requests, concurrent requests, timeout and shutdown.
- At least one reference smoke test per supported demo class and a versioned broader benchmark manifest.
- Known expected failures and limitations are documented rather than hidden.
- Version the project release and checkpoint/label map; maintain rollback for any model or inference-code change.

## Threshold and IoT policy

Until Gate 5 passes, the app is a **development candidate detector**: show its candidate and score; mark it accepted/rejected using the temporary threshold; do not describe the score as calibrated probability. IoT stays disabled.

Before later IoT enablement, add a separate safety gate: accepted class must be explicitly allowlisted; prediction must be stable across a configured number of independent/overlapping windows or captures; confidence must pass the validated operating point; recent capture quality must be acceptable; command must be idempotent/rate-limited; and action/audit logs must identify the recognition event. Hardware enablement is not proof of model readiness.

## Final definition: ready before IoT

The project can be marked **Pre-IoT Release Candidate** only when Gates 1–7 are documented and passing, with measured exceptions visible. It can be called **production-ready for its stated controlled deployment** only after the independent evaluation and threshold-selection criteria are met. It must not be described as a general-purpose real-world ISL translator without substantially broader validation and product scope.

## Technical references

- AI4Bharat INCLUDE upstream repository and workflow: https://github.com/AI4Bharat/INCLUDE
- AI4Bharat keypoint generation: https://raw.githubusercontent.com/AI4Bharat/INCLUDE/master/generate_keypoints.py
- AI4Bharat evaluation/preprocessing: https://raw.githubusercontent.com/AI4Bharat/INCLUDE/master/evaluate.py
- PyTorch inference mode and reminder to call `model.eval()`: https://docs.pytorch.org/docs/stable/generated/torch.autograd.grad_mode.inference_mode.html
- scikit-learn probability calibration and reliability diagrams: https://scikit-learn.org/stable/modules/calibration.html
- Google ML production testing: https://developers.google.com/machine-learning/crash-course/production-ml-systems/deployment-testing
- Google ML production monitoring / training-serving skew: https://developers.google.com/machine-learning/crash-course/production-ml-systems/monitoring
- MediaPipe Pose Landmarker live-stream and video tracking: https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/python
