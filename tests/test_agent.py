"""Basic tests for the code review agent."""

import tempfile
from pathlib import Path

import pytest


def test_import():
    import code_review_agent
    assert code_review_agent.__version__ == "0.1.0"


def test_load_config_empty_dir():
    from code_review_agent.__main__ import load_config
    with tempfile.TemporaryDirectory() as d:
        config = load_config(Path(d))
    assert "rules" in config
    assert config["output"]["format"] == "text"


def test_line_length_check():
    from code_review_agent.__main__ import check_line_length
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("a" * 50 + "\n")
        f.write("b" * 150 + "\n")
        path = Path(f.name)
    try:
        text = path.read_text()
        issues = check_line_length(path, text, max_length=120)
        assert len(issues) == 1
        assert issues[0]["line"] == 2
    finally:
        path.unlink()


def test_new_rules_detect_issues(tmp_path):
    from code_review_agent.__main__ import run_review

    trigger = tmp_path / "src" / "reviewer_trigger.py"
    trigger.parent.mkdir(parents=True, exist_ok=True)
    trigger.write_text(
        "\tprint('debug')   \n"
        "token = \"ABCDEFGHIJKLMNOPQRSTUV123456\"   \n"
        "<<<<<<< HEAD\n"
        "=======\n"
        ">>>>>>> branch\n",
        encoding="utf-8",
    )

    no_newline = tmp_path / "src" / "no_newline.py"
    no_newline.write_bytes(b"value = 1")

    config = {
        "rules": {
            "max_line_length": 120,
            "disallow_todo_without_ticket": False,
            "disallow_trailing_whitespace": True,
            "disallow_tab_indentation": True,
            "enforce_file_length": False,
            "disallow_merge_conflict_markers": True,
            "require_newline_at_eof": True,
            "disallow_potential_secrets": True,
            "disallow_debug_statements": True,
        },
        "paths": {
            "include": ["**/*.py"],
            "exclude": [],
            "scan_only_under": ["src"],
        },
        "output": {"format": "text", "fail_on_issues": True},
    }

    issues = run_review(tmp_path, config)
    rules = {i["rule"] for i in issues}

    assert "trailing_whitespace" in rules
    assert "tab_indentation" in rules
    assert "merge_conflict_marker" in rules
    assert "missing_newline_eof" in rules
    assert "potential_secret" in rules
    assert "debug_statement" in rules
