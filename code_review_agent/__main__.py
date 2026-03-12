"""Entry point for running the code review agent."""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def load_config(path: Path) -> dict:
    """Load config from YAML if available."""
    config = {"rules": {}, "paths": {"include": [], "exclude": []}, "output": {"format": "text", "fail_on_issues": True}}
    config_path = path / "config.yaml"
    if not config_path.exists():
        config_path = path / "config.example.yaml"
    if config_path.exists() and yaml:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
            config.update(data)
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


def collect_files(base_path: Path, include: list, exclude: list) -> list:
    """Collect files matching include/exclude patterns (simplified)."""
    files = []
    for root, _, names in os.walk(base_path):
        root_path = Path(root)
        if any(ex in str(root_path) for ex in ["node_modules", ".git", "vendor", "build", "dist", ".venv"]):
            continue
        for name in names:
            p = root_path / name
            if p.suffix in (".py", ".js", ".ts", ".java") or not include:
                files.append(p)
    return files[:200]  # cap for demo


def run_review(base_path: Path, config: dict) -> list:
    """Run all enabled rules and return issues."""
    issues = []
    rules = config.get("rules", {})
    paths_cfg = config.get("paths", {})
    include = paths_cfg.get("include", ["**/*.py"])
    exclude = paths_cfg.get("exclude", [])

    max_len = rules.get("max_line_length", 120)
    check_todo = rules.get("disallow_todo_without_ticket", False)

    for file_path in collect_files(base_path, include, exclude):
        try:
            text = file_path.read_text(errors="ignore")
            issues.extend(check_line_length(file_path, text, max_len))
            if check_todo:
                issues.extend(check_todo_without_ticket(file_path, text))
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
    if args.config and Path(args.config).exists():
        with open(args.config) as f:
            config = {**config, **(yaml.safe_load(f) or {})} if yaml else config

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
