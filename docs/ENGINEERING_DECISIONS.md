# Engineering Decisions — ISL INCLUDE-50 Manual Recognition MVP

**Decision record date:** 6 November 2026  
**Status:** MVP scope locked; IoT remains disabled pending validation and user-selected labels

## Decision 1 — Manual Start/Stop capture

**Decision:** Keep manual capture for the MVP. Each capture contains one isolated sign. The user presses Start, performs the sign, and presses Stop/Recognise.

**Reason:** Automatic sign-boundary detection would add a separate temporal segmentation problem. The current recognition model expects a complete isolated-sign sequence, and current app diagnostics already show recognition and feature-extraction issues that should be isolated first. Manual boundaries make the capture window explicit and simplify reproducible evaluation.

**Not included:** automatic start/end detection, continuous signing, multi-word sequence segmentation, sentence translation.

## Decision 2 — Retain the 50-class INCLUDE-50 model

**Decision:** Keep the pretrained AI4Bharat INCLUDE-50 Transformer-small and its official class ordering. Do not convert the baseline into a four-class model.

**Reason:** The user wants broad text recognition plus a small number of IoT actions. Reducing the model to four classes would discard the other 46 labels. The four demonstration signs (two text-only, two IoT) are a demo shortlist, not a replacement classifier vocabulary.

## Decision 3 — Preserve the 134-D/169-frame model contract

**Decision:** Retain 25 pose landmarks ×2 coordinates + two 21-landmark hands ×2 coordinates = 134 values per frame, with the existing 169-frame pad/truncate policy.

**Reason:** The current checkpoint is trained against this exact shape and feature layout. Alternative landmark sets (including 543-point Holistic outputs) require a new model pipeline and model weights; they cannot be swapped in at inference time.

## Decision 4 — Separate text recognition from IoT mapping

**Decision:** Use a dedicated backend IoT allowlist, not a fixed global mapping. The shipped JSON has zero mappings. A future IoT release must configure exactly two official INCLUDE-50 labels selected by the project owner.

**Reason:** The classifier retains all 50 labels. Every accepted known label can be displayed as text; only the two explicitly mapped labels can request hardware actions. User sign selection is not delegated to the engineering implementation.

## Decision 5 — Independent IoT confidence threshold

**Decision:** IoT uses its own minimum confidence setting (`ISL_IOT_MIN_CONFIDENCE=0.70` provisional), separate from the app's recognition threshold. The existing classifier threshold (`ISL_CONFIDENCE_THRESHOLD=0.40` in the current app configuration) remains explicitly uncalibrated pending full validation.

**Reason:** A text prediction can be useful for display yet not reliable enough to trigger a physical action. No single score should automatically turn on hardware. The 0.70 IoT value is a conservative engineering guard for pre-release testing, not a statistically calibrated guarantee.

## Decision 6 — Fail-closed IoT behaviour

**Decision:** Do not enable IoT unless exactly two valid mappings are loaded. Reject unmapped labels, failed/rejected captures, scores below the IoT threshold, duplicates, expired commands, cooldown violations and invalid protocol payloads. Keep pre-IoT code-level lockdown enabled in this package.

**Reason:** Physical actuation has a higher consequence than text display. Missing or malformed config must not silently fall back to historical mappings.

## Decision 7 — Official validation list as split authority

**Decision:** Use the pinned upstream `train_test_paths/include50_val.txt` list (77 entries) for validation manifest construction, guarded by repository commit and SHA256. Do not derive full validation from broader metadata gloss matches.

**Reason:** The metadata cache can include additional samples whose gloss happens to map to a model label. Using those can contaminate split totals. The pinned split path list defines the official 77 validation examples.

## Decision 8 — Test split remains locked

**Decision:** The evaluation script defaults to `val`. A test evaluation requires an explicit `--allow-test-evaluation` flag.

**Reason:** The test split must not be used to choose model settings, feature-extraction options, thresholds or IoT mappings. It is reserved for a final frozen report.

## Decision 9 — Provisional browser capture fidelity

**Decision:** Keep 640px width, use JPEG quality 0.92 for browser frames, and keep classifier input unmirrored.

**Reason:** A prior five-clip paired diagnostic showed browser-like Q72 could reduce hand coverage for one difficult clip, while Q92 preserved more detail. This small sample justifies a controlled capture setting, not a claim of higher model accuracy. The mirrored video preview is a UX choice; model input remains raw orientation until a broader paired study supports changing it.

## Decision 10 — Do not blindly change MediaPipe or the model

**Decision:** Keep the current 134-D extractor baseline for initial full validation. Any MediaPipe version or extractor change must be compared on identical source frames and validation entries. Keep new model families in separate experiments.

**Reason:** The old MediaPipe `upper_body_only` parameter was removed in version 0.8.4. Current first-25-pose indexing preserves landmark slots, but does not prove numerical parity with the historical detector. Random upgrades/downgrades or feature reshaping could break the existing checkpoint contract.

## Decision 11 — Performance work follows profiling

**Decision:** Measure per-frame extraction, queue/capture behaviour, preview overhead and Stop-to-result latency before refactoring. Do not optimise the Transformer first.

**Reason:** Historical diagnostics place MediaPipe extraction above model inference in the latency budget. Optimising the wrong stage would not address the observed user-facing delay.

## Decision 12 — Real hardware is a later release gate

**Decision:** Validate protocol with a local broker and simulated ESP32 first. Physical ESP32/LED testing remains blocked until full validation, selected labels, firmware allowlist parity, authenticated/restricted broker and electrical review are complete.

**Reason:** A successful unit test or broker publish is not proof of physical action. Simulated acknowledgements, device acknowledgements and observed LED behaviour must be reported separately.
