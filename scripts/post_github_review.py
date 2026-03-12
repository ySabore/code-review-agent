#!/usr/bin/env python3
"""
Post code-review-agent JSON output as a comment on the PR.
Uses the Issues API (PR comment) so it always works regardless of diff lines.
Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_WORKSPACE, PR_NUMBER, REVIEW_JSON_PATH.
"""
import json
import os
import sys
import urllib.request
import urllib.error

def rel_path(full_path: str, workspace: str) -> str:
    workspace = workspace.rstrip("/")
    if workspace and full_path.startswith(workspace):
        return full_path[len(workspace):].lstrip("/").replace("\\", "/")
    return full_path


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    workspace = os.environ.get("GITHUB_WORKSPACE", "")
    pr_number = os.environ.get("PR_NUMBER")

    if not all([token, repo, pr_number]):
        print("Missing: GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER", file=sys.stderr)
        sys.exit(1)

    json_path = os.environ.get("REVIEW_JSON_PATH", "review-output.json")
    if not os.path.isfile(json_path):
        print(f"No review output at {json_path}", file=sys.stderr)
        sys.exit(0)

    with open(json_path) as f:
        data = json.load(f)
    issues = data.get("issues", [])
    if not issues:
        # Post a short "no issues" comment so the reviewer always leaves a trace
        body = "## Code review agent\n\nNo issues found."
        owner, repo_name = repo.split("/", 1)
        url = f"https://api.github.com/repos/{owner}/{repo_name}/issues/{pr_number}/comments"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=json.dumps({"body": body}).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status in (200, 201):
                    print("Posted PR comment: No issues found.")
        except Exception as e:
            print(f"Could not post comment: {e}", file=sys.stderr)
        sys.exit(0)

    workspace_clean = workspace.rstrip("/")
    summary_lines = [f"## Code review agent found **{len(issues)}** issue(s)", ""]
    for i in issues[:80]:
        path = rel_path(i.get("file", ""), workspace_clean)
        summary_lines.append(f"- `{path}` line {i.get('line')} — **{i.get('rule')}** {i.get('message')}")
    if len(issues) > 80:
        summary_lines.append(f"- _... and {len(issues) - 80} more (see Actions log)._")
    body = "\n".join(summary_lines)

    owner, repo_name = repo.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{repo_name}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=json.dumps({"body": body}).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 201):
                print(f"Posted PR comment with {len(issues)} issue(s) to PR #{pr_number}")
                sys.exit(0)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        print(f"API error {e.code}: {err_body}", file=sys.stderr)
    except Exception as e:
        print(f"Failed to post comment: {e}", file=sys.stderr)

    # Don't fail the job - review ran; posting is best-effort
    sys.exit(0)


if __name__ == "__main__":
    main()
