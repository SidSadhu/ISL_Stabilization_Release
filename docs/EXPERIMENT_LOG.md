# Experiment Log

| Date | Experiment | Checkpoint | Sample | Result | Interpretation |
|---|---|---|---|---|---|
| 2026-09-30 | Strict checkpoint load | `include50_no_cnn_transformer_small.pth` | — | 40/40 params, 0 missing, 0 unexpected | Current checkpoint is compatible with the reproduced Transformer-small architecture |
| 2026-09-30 | Real probe inference | `include50_no_cnn_transformer_small.pth` | `court` | `court`, 88.73% confidence | Single-sample verification; not accuracy |
| Historical | Real probe inference | `include50_transformer_small.pth` | `court` | `court`, 76.10% confidence | Historical result; checkpoint not present in current runtime |
| 2026-10-26 | Model-independent application test suite | Mock model/extractor; real checkpoint absent | Automated tests | 32 passed, 1 skipped (real checkpoint load skipped because external repo/checkpoint were not configured) | Tests cover preprocessing, official class map, WebSocket capture lifecycle with test doubles, SQLite persistence, command gates/protocol and failure paths; not a real-model, Windows, broker or hardware result |

## Next measurements

- MediaPipe per-frame latency;
- model inference latency;
- end-to-end latency;
- effective FPS;
- buffer-induced delay;
- stabilization behavior;
- hand-ordering comparison.
