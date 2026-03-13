"""Basic tests for the code review agent."""

import tempfile
from pathlib import Path


def test_import():
    import code_review_agent
    assert code_review_agent.__version__ == "0.1.0"


def test_load_config_empty_dir():
    from code_review_agent.__main__ import load_config
    with tempfile.TemporaryDirectory() as d:
        config = load_config(Path(d))
    assert "rules" in config
    assert config["output"]["format"] == "text"
    assert "skip_dirs" in config["paths"]


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


def test_collect_files_matches_glob_and_skips_binary_and_dir(tmp_path):
    from code_review_agent.__main__ import collect_files

    text_file = tmp_path / "docs" / "guide.md"
    text_file.parent.mkdir(parents=True)
    text_file.write_text("hello\n")

    png_file = tmp_path / "assets" / "logo.png"
    png_file.parent.mkdir(parents=True)
    png_file.write_bytes(b"\x89PNG\x00\x00")

    hidden = tmp_path / ".git" / "meta.txt"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("ignored\n")

    files = collect_files(
        tmp_path,
        include=["**/*.md", "**/*.png", "**/*.txt"],
        exclude=[],
        skip_dirs=[".git"],
        skip_suffixes=[".png"],
    )
    names = {p.name for p in files}

    assert "guide.md" in names
    assert "logo.png" not in names
    assert "meta.txt" not in names


def test_new_rules_detect_issues(tmp_path):
    from code_review_agent.__main__ import run_review

    bad = tmp_path / "bad.py"
    bad.write_text(
        "x = 1  \n"
        "<<<<<<< HEAD\n"
        "print('debug')\n"
        "=======\n"
        "value = 'AKIA1234567890ABCDEF'\n"
        ">>>>>>> main"
    )

    config = {
        "rules": {
            "max_line_length": 120,
            "disallow_todo_without_ticket": False,
            "disallow_trailing_whitespace": True,
            "disallow_tab_indentation": False,
            "enforce_file_length": False,
            "disallow_merge_conflict_markers": True,
            "require_newline_at_eof": True,
            "disallow_potential_secrets": True,
            "disallow_debug_statements": True,
        },
        "paths": {"include": ["**/*.py"], "exclude": []},
        "output": {"format": "text", "fail_on_issues": True},
    }

    issues = run_review(tmp_path, config)
    rules = {i["rule"] for i in issues}

    assert "trailing_whitespace" in rules
    assert "merge_conflict_marker" in rules
    assert "missing_newline_eof" in rules
    assert "potential_secret" in rules
    assert "debug_statement" in rules
