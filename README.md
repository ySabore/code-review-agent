# Code Review Agent

A lightweight agent that performs automated code review checks on a codebase. Use it locally or in CI to catch common issues before human review.

## Features

- **Static checks**: Basic style, complexity, and pattern checks
- **Configurable rules**: Enable/disable rules via config
- **CI-friendly**: Exit codes and structured output for pipelines
- **Extensible**: Add custom rules or integrate with linters

## Requirements

- Python 3.8+

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

```bash
# Review current directory
python -m code_review_agent

# Review a specific path
python -m code_review_agent --path /path/to/repo

# Output as JSON for CI
python -m code_review_agent --format json
```

## Configuration

Copy `config.example.yaml` to `config.yaml` and adjust rules and paths as needed.

## License

MIT
