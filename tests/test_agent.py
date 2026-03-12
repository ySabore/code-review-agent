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
