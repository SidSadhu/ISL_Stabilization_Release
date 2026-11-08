from scripts.prepare_include_references import is_true_like, canonical_model_label, normalize_source_row

LABELS = {"hello", "goodmorning", "cellphone", "thankyou"}


def test_true_like_handles_boolean_string_and_numeric_values():
    assert is_true_like(True)
    assert is_true_like("TRUE")
    assert is_true_like("1")
    assert is_true_like(1)
    assert not is_true_like(False)
    assert not is_true_like("false")


def test_canonical_model_label_handles_dataset_prefix_and_aliases():
    assert canonical_model_label("Greetings/22. Hello", LABELS) == "hello"
    assert canonical_model_label("Greetings/Good Morning", LABELS) == "goodmorning"
    assert canonical_model_label("Objects/Cell Phone", LABELS) == "cellphone"


def test_normalize_row_preserves_separate_source_and_model_provenance():
    row = normalize_source_row({
        "parent_label": "Greetings",
        "label": "Greetings/22. Hello",
        "video_path": "Greetings/22. Hello/example.MOV",
        "include_50": "false",
    }, "train", LABELS)
    assert row is not None
    assert row["include_50"] is False
    assert row["model_class_match"] is True
    assert row["matched_model_label"] == "hello"
    assert row["split"] == "train"


def test_normalize_row_keeps_true_include_flag_even_without_model_class_match():
    row = normalize_source_row({
        "label": "Unknown Class",
        "video_path": "Unknown/example.mov",
        "include_50": "yes",
    }, "val", LABELS)
    assert row is not None
    assert row["include_50"] is True
    assert row["model_class_match"] is False
    assert row["matched_model_label"] == ""


def test_normalize_row_rejects_unmapped_non_include_video():
    assert normalize_source_row({
        "label": "Unknown Class",
        "video_path": "Unknown/example.mov",
        "include_50": False,
    }, "train", LABELS) is None


def test_fetch_page_retries_temporary_502_then_returns_page():
    import io
    from urllib.error import HTTPError
    from scripts.prepare_include_references import fetch_page

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def read(self):
            return b'{"num_rows_total": 0, "rows": []}'

    calls = {"count": 0}

    def fake_open(_request, timeout=0):
        calls["count"] += 1
        if calls["count"] == 1:
            raise HTTPError("https://example.invalid", 502, "Bad Gateway", {}, io.BytesIO())
        return Response()

    result = fetch_page("train", 0, opener=fake_open, sleep=lambda _seconds: None, retries=3)
    assert result["num_rows_total"] == 0
    assert calls["count"] == 2
