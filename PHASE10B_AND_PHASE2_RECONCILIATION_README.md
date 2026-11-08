# ISL stabilization — consolidated Phase 10B and Phase 2 reconciliation patch

## Why this patch exists

This is a targeted repair to the existing ISL Recognition Pipeline Stabilization campaign. It does not restart the project or change the model. It resolves two independent issues proven by the uploaded diagnostics archive.

### Phase 2: the data is already complete

The uploaded current `summary.json`, `per_clip_summary.csv`, and 77 individual `per_clip/*.json` reports agree on 77 successful clips and zero failures. The successful resumable run reused 76 prior successes and recomputed the one missing item.

The archived `phase2_run.log` is from the earlier first attempt (76/77), not the later completed resumable run. It records `Adjectives/79. short/MVI_9302.MOV` failing because the upstream compatibility code attempted to read `.landmark` from a `None` pose result. A later NaN-slot/resume-compatible run completed the missing item. The old raw log must remain as historical evidence; it must not be presented as the final run log.

`repair_phase2_completed_results.py` validates the existing summary, CSV and all 77 JSON results, optionally checks the current official 77-row manifest, regenerates the analytical Markdown report, creates a separate reconciliation log, and creates `PHASE2_RESULT_BUNDLE_RECONCILED.zip`. It does **not** rerun MediaPipe/model inference. The per-clip JSON `video_path` is the canonical official split identity; the `video` field is a resolved local path and may contain a supported extension variant such as `.MP4` where the manifest lists `.MOV`.

### Phase 10B: why visible-unfocused repeatedly failed

The failing diagnostic clip, `Adjectives/83. big large/MVI_9396.MOV`, has 56 frames at 25 fps (about 2.24 seconds). The old procedure started playback immediately, then asked the operator to switch to a second application after playback had begun. This leaves less than 2.24 seconds for the focus change and can waste most of the clip's short playback window before visible/unfocused exposure begins.

The visible-unfocused pass uses an explicit **ARMED → visible focus-loss → video playback** sequence. The operator clicks a second application window only after the page displays ARMED; the capture page must remain visible and unoccluded. Playback starts when the browser reports `window.blur`, or when the 100 ms monitor detects the equivalent actual state (`document.visibilityState === 'visible'` and `document.hasFocus() === false`). The polling path is a trigger fallback only: the clip still has to satisfy the same independently measured visible/unfocused/actively-playing heartbeat condition. If the page is already visible and unfocused (for subsequent clips), playback starts immediately after the initial paused frame is captured.

The previous patch version also exposed a JavaScript temporal-dead-zone regression: `caseHeader` read `captureFinishEpoch` before its `const` declaration. That made the capture abort during result assembly after playback. The declaration is now moved before header construction, and a regression test asserts the ordering. The UI instructions are corrected so they no longer tell the operator to click the second app after playback has already begun.

This preserves the original acceptance threshold: at least **1,500 ms** of visible, unfocused, actively playing state. The threshold is not reduced. A failed arm attempt or a browser exception does not upload a clip or count it as accepted.

The 100 ms heartbeat stream now processes *all* samples, not just active samples filtered in advance. A focus regain, hidden transition, pause, or end event splits the interval even when that event occurs between two timer ticks. Lifecycle transition events are injected as exact state-boundary observations. Intervals use actual observed timestamps; the code does not extend duration by guessing the time between the last heartbeat and capture completion. The maximum permitted gap between consecutive observations remains 450 ms.

Rejected attempts are recorded in `phase10b_attempt_diagnostics.jsonl` through a separate loopback-only endpoint. They contain lifecycle/heartbeat metadata, not JPEG image payloads, and do not count as accepted capture clips. The result packager includes these JSONL diagnostics for each mode.

## Frozen project contracts and safety

- AI4Bharat repository commit: `df348f4a640060d4d39f02556d9f96851e8c46c7`
- Official INCLUDE-50 validation path-list SHA-256: `044f56003b3af73e13bb889386cd9a424b25d74a172abcad2848fad2f1679541`
- Checkpoint SHA-256: `8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4`
- Vocabulary: 50 labels, unchanged
- Feature contract: 134 dimensions, unchanged
- Maximum model input: 169 frames, unchanged
- Canvas diagnostic: 640 px, JPEG q92, synchronous `toDataURL`, ungated `requestVideoFrameCallback`
- Shared ISLBRW01 metadata remains compatible (`timer_interval_ms=40`, `timer_ticks >= frame_count`); Phase 10B callback acceptance gate remains 0
- IoT disabled; empty mappings retained; no MQTT/ESP32 calls
- No classifier/checkpoint loaded during Phase 10B
- No production recognizer, model architecture, model weight, feature code, confidence policy, class ordering or test split changed

## What we are building — reminder

The project is an **isolated Indian Sign Language gesture-recognition system** using the AI4Bharat INCLUDE-50 vocabulary and the locked pretrained Transformer-small checkpoint. The intended processing path is:

`video/webcam → MediaPipe landmarks → 134-D features → temporal sequence (up to 169 frames) → pretrained Transformer-small → recognized ISL word + confidence`.

The stabilization campaign is about making that path faithful, reproducible and reliable for a demonstration. Phase 2 checks whether preprocessing/transport paths change the extracted features and prediction. Phase 10B checks whether browser focus/visibility lifecycle conditions interrupt capture. It is not a project restart, model redesign, continuous-sentence translation experiment, or IoT activation phase. IoT is deliberately deferred and remains disabled during these diagnostics.

## Install

Extract the patch ZIP into the existing project root, overwriting only the files it contains. The original Phase 10B result verifier (`scripts/verify_phase10b_bundle.py`) is not replaced by this patch; keep the already-installed verifier in place.

## Execute once from PowerShell

The consolidated command block should:

1. Validate the patch SHA-256 and install the overlay.
2. Reconcile Phase 2 using the existing 77/77 artifacts and the official validation manifest (no expensive inference rerun).
3. Locate the successful foreground Phase 10B output and pass its directory to `-ResumeForegroundDir`.
4. Run the complete project test suite before Phase 10B.
5. Run only the remaining visible-unfocused, hidden-background and focus-recovery modes.

For visible-unfocused: keep the browser and a second application side-by-side. When the page shows **ARMED**, click the second application and leave the browser page visible (do not minimize it or switch to a browser tab). Playback starts on the focus-loss event. Keep focus in the second application until the page reports at least 1.5 seconds of observed evidence. You may remain unfocused through subsequent clips; they auto-start while already visible/unfocused.

The first foreground pass is reused only after the orchestrator verifies the six payloads, manifest count, per-clip hashes, run identity and client condition evidence. If verification fails, resume is refused without modifying the prior captures.

## Validation performed in the build workspace

- Phase 10B/Python focused tests: 42 passed (with temporary build-host shims for application modules intentionally absent from the diagnostics ZIP; shims are not included in the patch). Two added regressions cover `captureFinishEpoch` initialization order and visible-unfocused focus-state polling fallback.
- Python compilation: passed for the modified Phase 10B scripts and Phase 2 repair script.
- Browser inline JavaScript syntax: checked with `node --check`.
- Phase 2 reconciliation against the uploaded diagnostics: completed; 77/77 successful, 0 failed; reconciled ZIP CRC and SHA-256 verification passed.
- Full Windows project test suite and browser focus experiment must be run in the user's original Windows environment by the included PowerShell execution block. They are not claimed as run here.
