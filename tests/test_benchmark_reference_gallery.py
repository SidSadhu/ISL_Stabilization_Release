import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_reference_gallery.py"
spec = importlib.util.spec_from_file_location("benchmark_reference_gallery", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def test_normalize_class_folder():
    assert mod.normalize_name("48. Hello") == "hello"
    assert mod.normalize_name("Store or Shop") == "storeorshop"
    assert mod.normalize_name("20 - Good Morning") == "goodmorning"


def test_infer_expected_label_from_deep_path(tmp_path):
    video = tmp_path / "Greetings" / "48. Hello" / "MVI_0032.MOV"
    assert mod.infer_expected_label(video, {"hello": 1, "goodmorning": 2}) == "hello"


def test_threshold_sweep_counts_accuracy():
    rows = [
        {"top1_confidence": 0.8, "top1_correct": True},
        {"top1_confidence": 0.4, "top1_correct": False},
        {"top1_confidence": 0.7, "top1_correct": True},
    ]
    sweep = mod.threshold_sweep(rows, [0.5, 0.75])
    assert sweep[0]["accepted_count"] == 2
    assert sweep[0]["top1_accuracy_among_accepted"] == 1.0
    assert sweep[1]["accepted_count"] == 1
    assert sweep[1]["accepted_rate"] == 1 / 3
