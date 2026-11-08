# Evaluation and Benchmark Methodology

## Historical evidence

The preserved `experiments/2026-09-30_no_cnn_probe.json` records one `court` ground-truth video predicted as `court` with confidence 0.8873 and input/output dimensions `(1,169,134)` → `(1,50)`. The checkpoint is not bundled and the notebook final inference cell lacks saved output. This remains historical one-sample evidence, not a reproduced test or dataset accuracy. Older checkpoint confidence values must not be compared as model-quality results.

## Offline dataset evaluation

`scripts/evaluate_dataset.py` requires a normalized CSV with `video_path,label` and an optional `split` column. It resolves file paths against `--data-root` and checks labels against the official 50-class map. It uses accessible real files only, decodes frames in order, and extracts at most the first 169 successful feature frames. It does not perform an FPS resampling experiment. Short inputs follow the preserved interpolation/zero-pad preprocessing; insufficient feature sequence length is recorded as a failure.

Generated `metrics.json` distinguishes:

- `manifest_count`: all manifest records in the normalized CSV;
- `split_manifest_count`: rows selected by split rule;
- `samples_selected_for_run`: rows left after optional `--max-samples` cap;
- `accessible_count`: selected rows with an existing source file;
- `attempted_count`, `successful_count`, `failed_count`, `missing_count`;
- top-1 accuracy, macro precision/recall/F1, weighted F1, classification report and confusion matrix when at least one inference succeeds.

Metrics only include successful inference rows; sample-level outputs preserve failures/missing rows. Verify the checkpoint training provenance and whether the official test split was independently held out before making generalisation claims. The reported official manifest counts (689 train, 77 validation, 192 test; 958 total) and historical availability counts (872 found / 86 missing, 178 accessible test) are source-context numbers and should be re-audited from the actual manifest/files before publication.

## Model-runtime benchmark

`scripts/benchmark_runtime.py` runs warm-up and measured inference against a supplied `.npy` array of real preprocessed inputs, and reports sample counts and latency distribution. It does not measure webcam frame extraction, app queueing, or hardware command latency. Use the target model and target machine, and never insert fake timings.

## Local system benchmark

`scripts/benchmark_system.py` summarises the stored app history: capture duration, MediaPipe processing latency per capture, model inference latency, post-capture processing, capture-start-to-result, processed FPS, feature-frame counts, delivered/dropped frames, MQTT acknowledgement latency, outcome counts and optional resource snapshot. If no captures or ACKs exist, values are null/zero samples rather than imputed.

## Required report tables

When actual data/assets are available, report model classification metrics separately from local interactive system metrics. Include sample count and exact command/configuration, CPU/GPU/device, warmup and measurement count, failed samples and resource environment. For MQTT, separate broker publish result, subscriber ACK and physical LED confirmation; the latter requires the hardware gate.

## Claims explicitly prohibited without further evidence

Do not claim a dataset-wide accuracy from the single probe, checkpoint split independence without source evidence, an optimised confidence threshold, continuous signing/sentence translation, real-time target performance, physical LED operation, or novel deep-learning architecture. The current code implements measurement tools but no dataset/system metrics are reported by this build because the needed assets and devices were not present.
