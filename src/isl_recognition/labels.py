from __future__ import annotations

import json
from pathlib import Path


def load_label_map(path: Path) -> dict[str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Label map must be a JSON object: {path}")
    normalized = {str(label): int(index) for label, index in data.items()}
    if len(normalized) != 50:
        raise ValueError(f"Expected 50 classes, got {len(normalized)}")
    indices = sorted(normalized.values())
    if indices != list(range(50)):
        raise ValueError("Label-map indices must be exactly 0..49")
    if normalized.get("court") != 8:
        raise ValueError("Official INCLUDE-50 label ordering check failed: court != 8")
    # The bundled map is the canonical official order verified from AI4Bharat/INCLUDE.
    # A caller-supplied map must match it exactly, not merely contain 50 labels.
    canonical_path = Path(__file__).resolve().parents[2] / "config" / "label_map_include50.json"
    if canonical_path.is_file():
        canonical_raw = json.loads(canonical_path.read_text(encoding="utf-8"))
        canonical = {str(label): int(index) for label, index in canonical_raw.items()}
        if normalized != canonical:
            raise ValueError("Label map differs from the bundled official INCLUDE-50 vocabulary/order")
    return normalized


def invert_label_map(label_map: dict[str, int]) -> dict[int, str]:
    return {index: label for label, index in label_map.items()}
