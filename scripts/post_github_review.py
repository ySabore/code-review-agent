#!/usr/bin/env python3
"""
Post code-review-agent JSON output as a GitHub PR review with line comments.
Falls back to a single PR comment if the review API fails (e.g. comments on unchanged lines).
Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_WORKSPACE, PR_NUMBER, COMMIT_ID.
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


def post_issue_comment(owner: str, repo_name: str, pr_number: str, token: str, body: str) -> bool:
    """Post a single comment on the PR (fallback when review API fails)."""
    url = f"https://api.github.com/repos/{owner}/{repo_name}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=json.dumps({"body": body}).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        print(f"Issue comment failed: {e}", file=sys.stderr)
        return False


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    workspace = os.environ.get("GITHUB_WORKSPACE", "")
    pr_number = os.environ.get("PR_NUMBER")
    commit_id = os.environ.get("COMMIT_ID")

    if not all([token, repo, pr_number, commit_id]):
        print("Missing: GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, COMMIT_ID", file=sys.stderr)
        return 1

    json_path = os.environ.get("REVIEW_JSON_PATH", "review-output.json")
    if not os.path.isfile(json_path):
        print(f"No review output at {json_path}", file=sys.stderr)
        return 0

    with open(json_path) as f:
        data = json.load(f)
    issues = data.get("issues", [])
    if not issues:
        return 0

    workspace_clean = workspace.rstrip("/")
    comments = []
    for i in issues:
        path = rel_path(i.get("file", ""), workspace_clean)
        line = i.get("line", 1)
        rule = i.get("rule", "review")
        msg = i.get("message", "")
        comments.append({"path": path, "line": line, "body": f"**[{rule}]** {msg}"})

    owner, repo_name = repo.split("/", 1)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"
    chunk_size = 90

    for start in range(0, len(comments), chunk_size):
        chunk = comments[start : start + chunk_size]
        body_text = f"Code review agent found {len(issues)} issue(s). Showing {len(chunk)} comment(s) in this batch."
        if start > 0:
            body_text = f"Continued: comments {start + 1}-{start + len(chunk)} of {len(issues)}."
        payload = {"commit_id": commit_id, "body": body_text, "event": "COMMENT", "comments": chunk}
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status not in (200, 201):
                    print(f"Review post returned {resp.status}", file=sys.stderr)
                    return 1
        except urllib.error.HTTPError as e:
            err_body = e.read().decode() if e.fp else ""
            print(f"Review API error {e.code}: {err_body}", file=sys.stderr)
            # Fallback: post one PR comment with all findings (GitHub only allows line comments on diff lines)
            summary_lines = [f"**Code review agent** found **{len(issues)}** issue(s):", ""]
            for i in issues[:100]:  # cap for comment size
                path = rel_path(i.get("file", ""), workspace_clean)
                summary_lines.append(f"- `{path}`:{i.get('line')} — **{i.get('rule')}** {i.get('message')}")
            if len(issues) > 100:
                summary_lines.append(f"- ... and {len(issues) - 100} more (see Actions log).")
            body = "\n".join(summary_lines)
            if post_issue_comment(owner, repo_name, pr_number, token, body):
                print(f"Posted summary as PR comment ({len(issues)} issues); line comments skipped (API restricts to diff lines).")
                return 0
            return 1
        except Exception as e:
            print(f"Failed to post review: {e}", file=sys.stderr)
            body = f"**Code review agent** found **{len(issues)}** issue(s). See Actions log for details.\n\n"
            body += "\n".join(f"- `{rel_path(i.get('file',''), workspace_clean)}`:{i.get('line')} — {i.get('message')}" for i in issues[:50])
            if post_issue_comment(owner, repo_name, pr_number, token, body):
                return 0
            return 1

    print(f"Posted {len(comments)} review comment(s) to PR #{pr_number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
