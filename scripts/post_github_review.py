#!/usr/bin/env python3
"""
Post code-review-agent JSON output as a GitHub PR review with line comments.
Use in GitHub Actions: reads review-output.json, posts to the current PR.
Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_WORKSPACE, and pass PR number + head SHA.
Works for any repo that runs this from a pull_request workflow.
"""
import json
import os
import sys
import urllib.request

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

    # Paths from agent are absolute; make relative to repo root for GitHub API
    workspace = workspace.rstrip("/")
    comments = []
    for i in issues:
        path = i.get("file", "")
        if workspace and path.startswith(workspace):
            path = path[len(workspace):].lstrip("/").replace("\\", "/")
        line = i.get("line", 1)
        rule = i.get("rule", "review")
        msg = i.get("message", "")
        body = f"**[{rule}]** {msg}"
        comments.append({"path": path, "line": line, "body": body})

    # GitHub allows max 100 comments per review
    chunk_size = 90
    owner, repo_name = repo.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }

    for start in range(0, len(comments), chunk_size):
        chunk = comments[start : start + chunk_size]
        body_text = f"Code review agent found {len(issues)} issue(s). Showing {len(chunk)} comment(s) in this batch."
        if start > 0:
            body_text = f"Continued: comments {start + 1}-{start + len(chunk)} of {len(issues)}."
        payload = {
            "commit_id": commit_id,
            "body": body_text,
            "event": "COMMENT",
            "comments": chunk,
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status not in (200, 201):
                    print(f"Review post returned {resp.status}", file=sys.stderr)
                    return 1
        except Exception as e:
            print(f"Failed to post review: {e}", file=sys.stderr)
            return 1

    print(f"Posted {len(comments)} review comment(s) to PR #{pr_number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
