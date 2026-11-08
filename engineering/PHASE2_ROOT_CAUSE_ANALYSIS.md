# ISL Recognition Stabilization — Phase 2 Root-Cause Analysis

**Evidence baseline:** Phase 1 paired validation run, 7 November 2026  
**Dataset:** pinned INCLUDE-50 official validation split, 77 clips / 50 classes  
**Checkpoint:** `include50_no_cnn_transformer_small.pth`  
**Decision:** preserve the model and preprocessing contract; execute the controlled four-path provenance ablation before modifying recognition code.

## 1. Executive finding

Phase 1 establishes a reproducible, but not yet release-acceptable, recognition baseline. The actual browser Canvas path predicts all 77 clips, with 44/77 top-1 correct (57.14%), 67/77 top-5 correct (87.01%), and macro F1 0.5207. The OpenCV native-resolution reference predicts 46/77 top-1 correct (59.74%), 69/77 top-5 correct (89.61%), and macro F1 0.5594.

The two paths produce the same top-1 prediction for 66/77 clips (85.71%). Their paired outcomes are: 43 both correct, 30 both wrong, 3 OpenCV-only correct, and 1 browser-only correct. The paired top-1 accuracy difference is 2 clips (2.60 percentage points); an exact two-sided McNemar test on the four discordant clips gives p=0.625. Wilson 95% intervals are 46.01–67.60% for browser and 48.58–69.98% for OpenCV. This small validation set does not provide evidence of a reliable accuracy difference. Most errors are shared between the paths. Browser transport alone is therefore not a sufficient explanation for the recognition ceiling.

Browser extraction itself was operationally healthy in this run: all 4,753 submitted frames were received, decoded and feature-extracted; there were zero extraction failures and zero browser decoder drops. The OpenCV reference extracted 4,727 of 4,753 frames, with 26 failed extractions. The 26-frame discrepancy is small in aggregate but must remain visible in any per-clip comparison.

## 2. Measured recognition performance

| Metric | Browser Canvas | OpenCV native reference |
|---|---:|---:|
| Predicted clips | 77/77 | 77/77 |
| Top-1 correct | 44/77 | 46/77 |
| Top-1 accuracy | 57.14% | 59.74% |
| Top-5 correct | 67/77 | 69/77 |
| Top-5 accuracy | 87.01% | 89.61% |
| Macro F1 | 0.5207 | 0.5594 |
| Accepted at diagnostic threshold 0.40 | 45/77 | 45/77 |
| Coverage at 0.40 | 58.44% | 58.44% |
| Selective accuracy at 0.40 | 77.78% (35/45) | 80.00% (36/45) |

The 0.40 threshold is explicitly uncalibrated. Risk–coverage values from this same validation set are exploratory observations only; they are not a basis for production threshold selection.

## 3. Paired predictions

| Paired outcome | Clips |
|---|---:|
| Same top-1 prediction | 66 |
| Both correct | 43 |
| Both wrong | 30 |
| OpenCV correct, browser wrong | 3 |
| Browser correct, OpenCV wrong | 1 |

The 11 top-1 disagreements include `time` (browser: `teacher`, OpenCV: `time`), `court` (browser: `storeorshop`, OpenCV: `bank`), `red` (browser: `i`, OpenCV: `youplural`), `white` (browser: `fall`, OpenCV: `red`), `year` (browser: `long`, OpenCV: `shoes`), and other individual clips. Low per-class support prohibits strong class-generalisation claims.

## 4. Detection and feature-stage measurements

| Quantity | Observed result |
|---|---:|
| Browser submitted / received / decoded / extracted frames | 4,753 / 4,753 / 4,753 / 4,753 |
| Browser extraction failures | 0 |
| Browser duplicate timer skips | 8 |
| Browser decoder-dropped frames | 0 |
| OpenCV source frames submitted | 4,753 |
| OpenCV decoded / extracted frames | 4,727 / 4,727 |
| OpenCV extraction failures | 26 |
| Mean pose-presence ratio, browser / OpenCV | 0.9995 / 1.0000 |
| Mean any-hand presence ratio, browser / OpenCV | 0.8495 / 0.8713 |

### Per-clip feature MAD distribution

MAD is the per-clip mean absolute feature-coordinate difference after interpolation. Features have already been scaled using the locked x×1920 / y×1080 coordinate contract.

| Feature stage | Mean MAD | Median MAD | P90 MAD | Maximum MAD |
|---|---:|---:|---:|---:|
| All 134 features | 21.56 | 16.63 | 42.97 | 96.55 |
| Pose (50-D) | 5.94 | 5.90 | 7.38 | 9.27 |
| Hand slot 1 (42-D) | 38.72 | 29.82 | 73.72 | 111.32 |
| Hand slot 2 (42-D) | 30.45 | 15.66 | 75.06 | 332.23 |
| Padded 169×134 sequence | 14.10 | 11.54 | 24.14 | 82.92 |
| Logits | 0.292 | 0.120 | 0.889 | 1.579 |
| Probabilities | 0.00496 | 0.00141 | 0.01477 | 0.03124 |

Hand-coordinate differences are substantially larger than pose-coordinate differences in the browser-vs-native comparison. This is a prioritisation signal for the next controlled ablation, not proof of a hand-slot assignment bug or proof that hand MAD causes classification errors.

As a descriptive cross-clip check, mean hand-slot MADs were higher on top-1 disagreement clips than agreement clips, but correlations between individual hand MAD and browser correctness were weak (Pearson: hand1 approximately -0.001; hand2 approximately -0.010; Spearman: hand1 approximately -0.009; hand2 approximately 0.139). The padded-sequence MAD Spearman correlation with browser error was approximately 0.091. Logit/probability divergence has a stronger association with disagreement, as expected when prediction rankings change, but that does not identify the upstream source of the feature change. These 77-sample descriptive associations do not support a causal inference.

## 5. Dataset support limitation

All 50 labels are represented, but the official validation set contains only 77 clips: 23 classes have support 1 and 27 classes have support 2. Consequently:

- a single clip changes a one-example class recall by 100 percentage points;
- class-wise precision/recall/F1 are descriptive, not stable estimates;
- the same validation split must not be used to tune and then claim unbiased final performance;
- the official test split remains locked until implementation decisions and thresholds are frozen.

## 6. Historical feature-generator provenance

The pinned AI4Bharat source at commit `df348f4a640060d4d39f02556d9f96851e8c46c7` specifies:

- MediaPipe Hands with detection/tracking confidence 0.5;
- MediaPipe Pose with detection/tracking confidence 0.5 and `upper_body_only=True`;
- source-video decoding with OpenCV, followed by BGR→RGB conversion;
- 25 pose landmarks, hand slots of 21 landmarks each, and single-hand placement based on pose wrist indices 15 and 16;
- linear interpolation with both-direction filling, coordinate scaling x×1920 / y×1080, 134 features per frame, and a 169-frame training input.

The upstream `requirements.txt` does not pin a MediaPipe version or provide a lockfile/build hash. The current Windows runtime is MediaPipe 0.10.21. Its full-body Pose output is sliced to the first 25 landmarks to preserve feature slot shape/order. This does not reproduce the historical `upper_body_only=True` detector model or prove coordinate equivalence. The hand-ordering logic in the current implementation matches the inspected upstream single-hand wrist-nearest rule; two-hand detector order is retained in both. No evidence currently justifies changing that mapping.

The previous handover records that no verified MediaPipe 0.8.3.1 CPython 3.9 Windows wheel was available and Python 3.9 was absent. MediaPipe 0.8.3.1 was a candidate, not a verified training-time version. Do not install a guessed legacy wheel or modify the active Python 3.12 environment.

## 7. Phase 2 controlled ablation now prepared

The new resumable runner compares four per-clip paths using the same source video frame order and the locked model:

1. **`app_direct_native`** — current application landmark mapping on original OpenCV-decoded frames.
2. **`resized_640_png`** — the same application extractor after OpenCV INTER_LINEAR resize to 640 px and lossless PNG encode/decode.
3. **`resized_640_jpeg_q92`** — the same application extractor after the same resize and JPEG quality 0.92 encode/decode.
4. **`upstream_original`** — pinned upstream generator on the original video, with a modern full-Pose/first-25 compatibility adapter where required.

This separates, approximately, source-size effects, 640-pixel resize effects, JPEG q92 effects, and current-app-vs-upstream-modern-adapter mapping behavior. OpenCV INTER_LINEAR is not pixel-identical to browser Canvas `drawImage`; the q92 branch is therefore an approximation, not a replacement for Phase 1's actual browser Canvas measurements. The obsolete JPEG quality 72 sweep is deliberately excluded.

The runner verifies the repository commit, exact deterministic-dropout worktree patch, checkpoint hash, split hash and manifest, label map, Python/MediaPipe/OpenCV/PyTorch versions, all relevant source hashes, and the pre-IoT lock. It is resumable per clip and writes independent JSON reports/logs, `summary.json`, and `per_clip_summary.csv`.

## 8. Engineering decisions

**Do not change now:** checkpoint, Transformer architecture, class ordering, 134-D feature layout, 169-frame length, coordinate scaling, production confidence threshold, or IoT implementation.

**Next evidence gate:** run the 77-clip controlled ablation in the user's pinned Windows environment. The results will show whether differences are introduced chiefly by resize, JPEG compression, or current application-vs-upstream-modern-adapter extraction. Only then propose a narrow remediation.

**Historical parity status:** remains unproven until a verified historical MediaPipe release/source/build artifact is recovered. If it cannot be recovered, record that limitation explicitly rather than pretending the current first-25 adapter is historical parity.

## 9. Reproducibility assets

- Phase 1 machine-readable metrics: `metrics.json` supplied by the target Windows run.
- Phase 1 clip-level evidence: `per_clip.csv` supplied by the target Windows run.
- Phase 1 derived summary/charts: `engineering/phase2_evidence/`.
- Phase 2 runner: `scripts/run_phase2_feature_provenance_77.py`.
- Per-clip diagnostic: `scripts/diagnose_browser_transport_stages.py`.
- Runner and report tests: `tests/test_phase2_feature_provenance_runner.py`, `tests/test_phase2_transport_variants.py`, and `tests/test_build_phase2_ablation_report.py`.
- Post-run report builder: `scripts/build_phase2_ablation_report.py`; it consumes only a completed 77/77 `summary.json` and `per_clip_summary.csv`.

Phase 2's source audit and Phase 1 statistical analysis are complete. The four-path 77-video ablation is implemented and locally test-verified, but it still requires execution on the Windows machine where the official source videos, pinned upstream checkout and checkpoint reside.
