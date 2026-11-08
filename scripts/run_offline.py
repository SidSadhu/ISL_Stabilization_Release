from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import argparse
import json
from pathlib import Path

from isl_recognition.config import AppConfig
from isl_recognition.labels import load_label_map, invert_label_map
from isl_recognition.model import INCLUDETransformerSmall
from isl_recognition.preprocess import preprocess_record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("keypoints_json", type=Path)
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--label-map", type=Path, default=None)
    args = parser.parse_args()

    cfg = AppConfig()
    repo = args.repo or cfg.repo
    checkpoint = args.checkpoint or cfg.checkpoint
    label_map_path = args.label_map or cfg.label_map

    record = json.loads(args.keypoints_json.read_text(encoding="utf-8"))
    _, padded = preprocess_record(record)
    label_map = load_label_map(label_map_path)
    idx_to_label = invert_label_map(label_map)
    model = INCLUDETransformerSmall(repo, checkpoint)
    result = model.predict(padded)

    print("Checkpoint :", checkpoint.name)
    print("Ground truth:", record.get("label"))
    print("Prediction  :", idx_to_label[result["predicted_index"]])
    print("Confidence  :", f"{result['confidence']*100:.2f}%")
    print("Top-5:")
    for i, p in zip(result["top5_indices"], result["top5_probabilities"]):
        print(f"  {idx_to_label[i]}: {p*100:.2f}%")


if __name__ == "__main__":
    main()
