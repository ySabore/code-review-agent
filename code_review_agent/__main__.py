"""Entry point for running the code review agent."""

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("generic_api_key", re.compile(r"(?:api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]", re.IGNORECASE)),
]


DEFAULT_SKIP_DIRS = ["node_modules", ".git", "vendor", "build", "dist", ".venv", ".tox", "coverage", ".mypy_cache"]
DEFAULT_SKIP_SUFFIXES = [".jar", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".pdf", ".tar", ".gz", ".so", ".dll", ".exe"]


def load_config(path: Path) -> dict:
    """Load config from YAML if available (config.yaml, config.example.yaml, or .code-review.yaml in path)."""
    config = {
        "rules": {},
        "paths": {
            "include": ["**/*"],
            "exclude": [],
            "skip_dirs": DEFAULT_SKIP_DIRS,
            "skip_suffixes": DEFAULT_SKIP_SUFFIXES,
        },
        "output": {"format": "text", "fail_on_issues": True},
    }
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
        line_upper = line.upper()
        if "TODO" in line_upper or "FIXME" in line_upper:
            if not any(c in line for c in ["#", "JIRA", "TODO-", "FIXME-", "GH-", "["]):
                issues.append({"file": str(file_path), "line": i, "rule": "todo_without_ticket", "message": "TODO/FIXME without ticket reference"})
    return issues


def check_trailing_whitespace(file_path: Path, content: str) -> list:
    """Report lines ending with trailing whitespace."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        if line.rstrip(" \t") != line:
            issues.append({"file": str(file_path), "line": i, "rule": "trailing_whitespace", "message": "Line has trailing whitespace"})
    return issues


def check_tabs_for_indentation(file_path: Path, content: str) -> list:
    """Report tab-indented lines."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        if line.startswith("\t"):
            issues.append({"file": str(file_path), "line": i, "rule": "tab_indentation", "message": "Line starts with a tab; use spaces"})
    return issues


def check_file_too_long(file_path: Path, content: str, max_lines: int) -> list:
    """Report files exceeding max line count."""
    line_count = len(content.splitlines())
    if line_count > max_lines:
        return [{"file": str(file_path), "line": 1, "rule": "file_length", "message": f"File has {line_count} lines (max {max_lines})"}]
    return []


def check_merge_conflict_markers(file_path: Path, content: str) -> list:
    """Report unresolved merge conflict markers."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        if line.startswith("<<<<<<<") or line.startswith("=======") or line.startswith(">>>>>>>"):
            issues.append({"file": str(file_path), "line": i, "rule": "merge_conflict_marker", "message": "Unresolved merge conflict marker"})
    return issues


def check_no_newline_at_eof(file_path: Path, content: str) -> list:
    """Report files that do not end with a newline."""
    if content and not content.endswith("\n"):
        return [{"file": str(file_path), "line": max(len(content.splitlines()), 1), "rule": "missing_newline_eof", "message": "File does not end with a newline"}]
    return []


def check_potential_secrets(file_path: Path, content: str) -> list:
    """Report probable leaked credentials/secrets."""
    issues = []
    for i, line in enumerate(content.splitlines(), start=1):
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                issues.append({"file": str(file_path), "line": i, "rule": "potential_secret", "message": f"Potential secret detected ({label})"})
                break
    return issues


def check_debug_statements(file_path: Path, content: str) -> list:
    """Report debug prints/loggers likely left in production code."""
    issues = []
    debug_patterns = [
        re.compile(r"\bprint\s*\("),
        re.compile(r"\bconsole\.log\s*\("),
        re.compile(r"\bdebugger\b"),
        re.compile(r"\bpdb\.set_trace\s*\("),
    ]
    for i, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        if any(p.search(line) for p in debug_patterns):
            issues.append({"file": str(file_path), "line": i, "rule": "debug_statement", "message": "Potential debug statement left in code"})
    return issues


def _excluded(path: Path, exclude: list) -> bool:
    """True if path should be excluded (simple pattern match by path segment)."""
    parts = path.parts
    name = path.name
    for pat in exclude:
        part = pat.replace("**/", "").replace("/**", "").strip("/")
        if not part:
            continue
        if "/" in part:
            if part in str(path):
                return True
        else:
            if part in parts or name == part:
                return True
    return False


def _included(path: Path, include: list, base_path: Path) -> bool:
    """True if path matches include glob patterns (repo-relative)."""
    if not include:
        return True
    rel = path.relative_to(base_path).as_posix()
    for pattern in include:
        normalized = pattern[3:] if pattern.startswith("**/") else pattern
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, normalized):
            return True
    return False


def _is_binary(path: Path) -> bool:
    """Best-effort binary detection."""
    try:
        chunk = path.read_bytes()[:2048]
    except Exception:
        return True
    return b"\x00" in chunk


def collect_files(base_path: Path, include: list, exclude: list, scan_only_under: list = None, skip_dirs: list = None, skip_suffixes: list = None) -> list:
    """Collect files matching include/exclude patterns (simplified)."""
    effective_skip_dirs = skip_dirs or DEFAULT_SKIP_DIRS
    effective_skip_suffixes = tuple(skip_suffixes or DEFAULT_SKIP_SUFFIXES)
    skip_names = (".DS_Store",)

    roots_to_walk = [base_path]
    if scan_only_under:
        roots_to_walk = [base_path / d for d in scan_only_under if (base_path / d).exists()]
        if not roots_to_walk:
            roots_to_walk = [base_path]

    allowed_prefixes = tuple(str(base_path / d) for d in (scan_only_under or []))
    files = []
    for walk_root in roots_to_walk:
        for root, _, names in os.walk(walk_root):
            root_path = Path(root)
            if any(part in effective_skip_dirs for part in root_path.parts):
                continue
            for name in names:
                p = root_path / name
                if name in skip_names or p.suffix.lower() in effective_skip_suffixes:
                    continue
                if _excluded(p, exclude):
                    continue
                if allowed_prefixes and not any(str(p).startswith(prefix) for prefix in allowed_prefixes):
                    continue
                if not _included(p, include, base_path):
                    continue
                if _is_binary(p):
                    continue
                files.append(p)
    return files[:2000]


def run_review(base_path: Path, config: dict) -> list:
    """Run all enabled rules and return issues."""
    issues = []
    rules = config.get("rules", {})
    paths_cfg = config.get("paths", {})
    include = paths_cfg.get("include", ["**/*"])
    exclude = paths_cfg.get("exclude", [])
    skip_dirs = paths_cfg.get("skip_dirs", DEFAULT_SKIP_DIRS)
    skip_suffixes = paths_cfg.get("skip_suffixes", DEFAULT_SKIP_SUFFIXES)

    max_len = rules.get("max_line_length", 120)
    max_file_lines = rules.get("max_file_lines", 1500)

    check_todo = rules.get("disallow_todo_without_ticket", False)
    check_trailing = rules.get("disallow_trailing_whitespace", True)
    check_tabs = rules.get("disallow_tab_indentation", False)
    check_file_length = rules.get("enforce_file_length", False)
    check_conflicts = rules.get("disallow_merge_conflict_markers", True)
    check_eof_newline = rules.get("require_newline_at_eof", True)
    check_secrets = rules.get("disallow_potential_secrets", True)
    check_debug = rules.get("disallow_debug_statements", False)

    scan_only_under = paths_cfg.get("scan_only_under")
    if scan_only_under is None and "nas-file-processor" in str(base_path):
        scan_only_under = ["src", "gradle"]

    for file_path in collect_files(base_path, include, exclude, scan_only_under, skip_dirs=skip_dirs, skip_suffixes=skip_suffixes):
        try:
            text = file_path.read_text(errors="ignore")
            issues.extend(check_line_length(file_path, text, max_len))
            if check_todo:
                issues.extend(check_todo_without_ticket(file_path, text))
            if check_trailing:
                issues.extend(check_trailing_whitespace(file_path, text))
            if check_tabs:
                issues.extend(check_tabs_for_indentation(file_path, text))
            if check_file_length:
                issues.extend(check_file_too_long(file_path, text, max_file_lines))
            if check_conflicts:
                issues.extend(check_merge_conflict_markers(file_path, text))
            if check_eof_newline:
                issues.extend(check_no_newline_at_eof(file_path, text))
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
