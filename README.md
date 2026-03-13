# Code Review Agent

A lightweight agent that performs automated code review checks on a codebase. Use it locally or in CI to catch common issues before human review.

## Features

- **Static checks**: Broad sanity checks for formatting, unsafe leftovers, and risky patterns
- **Configurable rules**: Enable/disable rules via config
- **CI-friendly**: Exit codes and structured output for pipelines
- **Extensible**: Add custom rules or integrate with linters
- **Org-wide defaults**: Scans all text-like files by default (not only language-specific extensions)

## Requirements

- Python 3.8+

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

**See all options:**
```bash
python3 -m code_review_agent --help
```

**Examples:**
```bash
# Review current directory
python3 -m code_review_agent

# Review a specific path
python3 -m code_review_agent --path /path/to/repo

# Output as JSON for CI
python3 -m code_review_agent --format json

# Use a custom config file
python3 -m code_review_agent --config config.yaml
```

**Options:**

| Option | Description |
|--------|-------------|
| `--path PATH` | Path to the folder to review (default: current directory) |
| `--format {text,json}` | Output format: `text` (default) or `json` |
| `--config CONFIG` | Path to a config file (overrides `config.yaml` in the reviewed path) |
| `-h, --help` | Show help and exit |

## Configuration

Copy `config.example.yaml` to `config.yaml` and adjust rules and paths as needed.

### Built-in checks

- `line_length`: flags lines longer than `rules.max_line_length`
- `todo_without_ticket`: optional TODO/FIXME check requiring a ticket hint
- `trailing_whitespace`: flags lines ending in spaces/tabs
- `tab_indentation`: optional tab-indentation check
- `file_length`: optional file line-count limit (`rules.max_file_lines`)
- `merge_conflict_marker`: flags unresolved `<<<<<<<`, `=======`, `>>>>>>>`
- `missing_newline_eof`: ensures files end with newline
- `potential_secret`: catches common credential/token patterns
- `debug_statement`: optional check for debug leftovers (`print`, `console.log`, `pdb.set_trace`, `debugger`)

By default, the agent includes `**/*` and skips common binaries/noisy directories. This makes it practical for mixed-language repositories and multi-team organizations without extension-by-extension tuning.

## Use in any repo (GitHub Actions + PR review comments)

1. **Add the workflow** to your repo: create `.github/workflows/code-review.yml` with the same content as in [nas-file-processor](https://github.com/ySabore/nas-file-processor/blob/master/.github/workflows/code-review.yml). It will:
   - Run on every pull request to `main` or `master`
   - Clone this agent repo, run the agent on your code, then post findings as **PR review comments** (on the relevant lines)

2. **Optional:** Add a `.code-review.yaml` in your repo root to customize rules and paths (e.g. `scan_only_under: ["src"]`, `max_line_length`, `fail_on_issues`). If you don’t add it, the agent still runs with defaults.

3. No secrets needed: the workflow uses `secrets.GITHUB_TOKEN` to post the review.

Works for any repo that uses this workflow; the agent repo stays at `https://github.com/ySabore/code-review-agent`.

## License

MIT
