from pathlib import Path
import json
import re


def test_mode_placeholder_is_a_javascript_expression_not_a_quoted_string():
    html = (Path(__file__).parents[1] / "scripts" / "browser_validation_capture_phase7.html").read_text(encoding="utf-8")
    assert "const MODE = __PHASE7_MODE__;" in html
    assert "const MODE = '__PHASE7_MODE__';" not in html


def test_server_style_substitution_produces_exact_mode_string():
    html = (Path(__file__).parents[1] / "scripts" / "browser_validation_capture_phase7.html").read_text(encoding="utf-8")
    for mode in ("current_40ms", "every_presented_frame"):
        rendered = html.replace("__PHASE7_MODE__", json.dumps(mode))
        match = re.search(r"const MODE = (.+?);", rendered)
        assert match is not None
        assert match.group(1) == json.dumps(mode)
        # Regression: not the previous nested value, e.g. '"current_40ms"'.
        assert match.group(1) not in {repr(json.dumps(mode)), f"'\\\"{mode}\\\"'"}


def test_both_mode_branches_remain_in_template():
    html = (Path(__file__).parents[1] / "scripts" / "browser_validation_capture_phase7.html").read_text(encoding="utf-8")
    assert "MODE === 'current_40ms'" in html
    assert "MODE === 'every_presented_frame'" in html
    assert "Unsupported Phase 7 mode" in html
