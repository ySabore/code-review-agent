"""Entry point for running the code review agent."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


POTENTIAL_SECRET_PATTERNS = [
    ("generic_api_key", re.compile(r"(?:api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]", re.IGNORECASE)),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
]

DEBUG_STATEMENT_PATTERNS = [
    re.compile(r"\bprint\s*\("),
    re.compile(r"\bconsole\.log\s*\("),
    re.compile(r"\bSystem\.out\.println\s*\("),
    re.compile(r"\blogger\.debug\s*\("),
]


def load_config(path: Path) -> dict:
    """Load config from YAML if available (config.yaml, config.example.yaml, or .code-review.yaml in path)."""
    config = {"rules": {}, "paths": {"include": [], "exclude": []}, "output": {"format": "text", "fail_on_issues": True}}
    for name in ("config.yaml", "config.example.yaml", ".code-review.yaml"):
        config_path = path / name
        if config_path.exists() and yaml:
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            if data:
                config = {**config, **data}
                if "paths" in data:
                    config["paths"] = {**config.get("paths", {}), **data["paths"]}
    return config


def check_line_length(file_path: Path, content: str, max_length: int) -> list:
    """Report lines exceeding max length."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        if len(line) > max_length:
            issues.append({"file": str(file_path), "line": i, "rule": "line_length", "message": f"Line exceeds {max_length} chars"})
    return issues


def check_todo_without_ticket(file_path: Path, content: str) -> list:
    """Report TODO/FIXME without a ticket reference."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        line_lower = line.upper()
        if "TODO" in line_lower or "FIXME" in line_lower:
            if not any(c in line for c in ["#", "JIRA", "TODO-", "FIXME-", "GH-", "["]):
                issues.append({"file": str(file_path), "line": i, "rule": "todo_without_ticket", "message": "TODO/FIXME without ticket reference"})
    return issues


def check_trailing_whitespace(file_path: Path, content: str) -> list:
    """Report lines with trailing spaces or tabs."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        if line != line.rstrip(" \t"):
            issues.append({"file": str(file_path), "line": i, "rule": "trailing_whitespace", "message": "Line has trailing whitespace"})
    return issues


def check_tab_indentation(file_path: Path, content: str) -> list:
    """Report lines indented with tabs."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        if line.startswith("\t"):
            issues.append({"file": str(file_path), "line": i, "rule": "tab_indentation", "message": "Line starts with a tab; use spaces"})
    return issues


def check_file_length(file_path: Path, content: str, max_lines: int) -> list:
    """Report files that exceed a max line count."""
    line_count = len(content.splitlines())
    if line_count > max_lines:
        return [{"file": str(file_path), "line": 1, "rule": "file_length", "message": f"File has {line_count} lines (max {max_lines})"}]
    return []


def check_merge_conflict_markers(file_path: Path, content: str) -> list:
    """Report unresolved merge conflict markers."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        if line.startswith(("<<<<<<<", "=======", ">>>>>>>")):
            issues.append({"file": str(file_path), "line": i, "rule": "merge_conflict_marker", "message": "Unresolved merge conflict marker"})
    return issues


def check_missing_newline_eof(file_path: Path, content: str) -> list:
    """Report files that do not end with a newline."""
    if content and not content.endswith("\n"):
        return [{"file": str(file_path), "line": max(len(content.splitlines()), 1), "rule": "missing_newline_eof", "message": "File does not end with a newline"}]
    return []


def check_potential_secrets(file_path: Path, content: str) -> list:
    """Report probable leaked credentials or tokens."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        for label, pattern in POTENTIAL_SECRET_PATTERNS:
            if pattern.search(line):
                issues.append({"file": str(file_path), "line": i, "rule": "potential_secret", "message": f"Potential secret detected ({label})"})
                break
    return issues


def check_debug_statements(file_path: Path, content: str) -> list:
    """Report likely leftover debug statements."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        if any(pattern.search(line) for pattern in DEBUG_STATEMENT_PATTERNS):
            issues.append({"file": str(file_path), "line": i, "rule": "debug_statement", "message": "Potential debug statement left in code"})
    return issues


def _excluded(path: Path, exclude: list) -> bool:
    """True if path should be excluded (simple pattern match by path segment)."""
    parts = path.parts
    name = path.name
    for pat in exclude:
        # Normalize **/foo/** and **/foo to a segment or path snippet
        part = pat.replace("**/", "").replace("/**", "").strip("/")
        if not part:
            continue
        # Match path segment (e.g. "nas" matches .../nas/... but not nas-file-processor)
        if "/" in part:
            if part in str(path):
                return True
        else:
            if part in parts or name == part:
                return True
    return False


def collect_files(base_path: Path, include: list, exclude: list, scan_only_under: list = None) -> list:
    """Collect files matching include/exclude patterns (simplified)."""
    skip_dir_names = ["node_modules", ".git", "vendor", "build", "dist", ".venv", "nas"]
    allowed_suffixes = (".py", ".js", ".ts", ".java", ".yml", ".yaml", ".gradle")
    skip_names = (".DS_Store", "gradlew", "gradlew.bat")
    skip_suffixes = (".jar",)  # binaries
    roots_to_walk = [base_path]
    if scan_only_under:
        roots_to_walk = [base_path / d for d in scan_only_under if (base_path / d).exists()]
        if not roots_to_walk:
            roots_to_walk = [base_path]
    # When limiting to certain dirs, only keep files under those dirs (path must contain /src/ or /gradle/)
    allowed_prefixes = tuple(str(base_path / d) for d in (scan_only_under or []))
    files = []
    for walk_root in roots_to_walk:
        for root, _, names in os.walk(walk_root):
            root_path = Path(root)
            if any(part in skip_dir_names for part in root_path.parts):
                continue
            for name in names:
                p = root_path / name
                if name in skip_names or p.suffix in skip_suffixes:
                    continue
                if _excluded(p, exclude):
                    continue
                if allowed_prefixes and not any(str(p).startswith(prefix) for prefix in allowed_prefixes):
                    continue
                if p.suffix in allowed_suffixes or not include:
                    files.append(p)
    return files[:500]  # cap for large repos


def run_review(base_path: Path, config: dict) -> list:
    """Run all enabled rules and return issues."""
    issues = []
    rules = config.get("rules", {})
    paths_cfg = config.get("paths", {})
    include = paths_cfg.get("include", ["**/*.py"])
    exclude = paths_cfg.get("exclude", [])

    max_len = rules.get("max_line_length", 120)
    max_file_lines = rules.get("max_file_lines", 1500)
    check_todo = rules.get("disallow_todo_without_ticket", False)
    check_trailing = rules.get("disallow_trailing_whitespace", False)
    check_tabs = rules.get("disallow_tab_indentation", False)
    check_file_length_enabled = rules.get("enforce_file_length", False)
    check_conflicts = rules.get("disallow_merge_conflict_markers", False)
    check_eof_newline = rules.get("require_newline_at_eof", False)
    check_secrets = rules.get("disallow_potential_secrets", False)
    check_debug = rules.get("disallow_debug_statements", False)

    scan_only_under = paths_cfg.get("scan_only_under")
    if scan_only_under is None and "nas-file-processor" in str(base_path):
        scan_only_under = ["src", "gradle"]
    for file_path in collect_files(base_path, include, exclude, scan_only_under):
        try:
            text = file_path.read_text(errors="ignore")
            issues.extend(check_line_length(file_path, text, max_len))
            if check_todo:
                issues.extend(check_todo_without_ticket(file_path, text))
            if check_trailing:
                issues.extend(check_trailing_whitespace(file_path, text))
            if check_tabs:
                issues.extend(check_tab_indentation(file_path, text))
            if check_file_length_enabled:
                issues.extend(check_file_length(file_path, text, max_file_lines))
            if check_conflicts:
                issues.extend(check_merge_conflict_markers(file_path, text))
            if check_eof_newline:
                issues.extend(check_missing_newline_eof(file_path, text))
            if check_secrets:
                issues.extend(check_potential_secrets(file_path, text))
            if check_debug:
                issues.extend(check_debug_statements(file_path, text))
        except Exception:
            pass
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Code Review Agent")
    parser.add_argument("--path", default=".", help="Path to review")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format")
    parser.add_argument("--config", help="Config file path")
    args = parser.parse_args()

    base = Path(args.path).resolve()
    if not base.exists():
        print(f"Path not found: {base}", file=sys.stderr)
        return 2

    config = load_config(base)
    if args.config and Path(args.config).exists() and yaml:
        with open(args.config) as f:
            over = yaml.safe_load(f) or {}
        config = {**config, **over}
        if "paths" in over:
            config["paths"] = {**config.get("paths", {}), **over["paths"]}

    issues = run_review(base, config)
    out_fmt = args.format or config.get("output", {}).get("format", "text")

    if out_fmt == "json":
        print(json.dumps({"issues": issues, "count": len(issues)}, indent=2))
    else:
        for i in issues:
            print(f"{i['file']}:{i['line']} [{i['rule']}] {i['message']}")
        if issues:
            print(f"\nTotal: {len(issues)} issue(s)")

    fail_on = config.get("output", {}).get("fail_on_issues", True)
    return 1 if issues and fail_on else 0


if __name__ == "__main__":
    sys.exit(main())
