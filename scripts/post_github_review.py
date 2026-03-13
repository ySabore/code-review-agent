#!/usr/bin/env python3
"""
Post code-review-agent JSON output as a comment on the PR.
Prefer inline PR review comments (when the finding is in the PR diff),
and always post a summary comment as a fallback.
Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_WORKSPACE, PR_NUMBER, REVIEW_JSON_PATH.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple

def rel_path(full_path: str, workspace: str) -> str:
    workspace = workspace.rstrip("/")
    if workspace and full_path.startswith(workspace):
        return full_path[len(workspace):].lstrip("/").replace("\\", "/")
    return full_path


def _gh_request(url: str, token: str, method: str = "GET", payload: Optional[dict] = None, timeout_s: int = 30):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read().decode() if resp is not None else ""
        return resp.status, body


def _list_pr_files(owner: str, repo_name: str, pr_number: str, token: str) -> List[dict]:
    # 100 should be enough for most repos; keep it simple for now.
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/files?per_page=100"
    status, body = _gh_request(url, token, "GET")
    if status != 200:
        raise RuntimeError(f"Failed to list PR files: HTTP {status} {body}")
    return json.loads(body)


def _build_position_map(patch: str) -> Dict[int, int]:
    """
    Map NEW-file line number -> GitHub 'position' in the unified diff.
    Only lines that exist in the patch are mappable.
    """
    new_line = None
    position = 0
    mapping: Dict[int, int] = {}
    for raw in patch.splitlines():
        position += 1
        if raw.startswith("@@"):
            # @@ -oldStart,oldCount +newStart,newCount @@
            try:
                plus = raw.split("+", 1)[1]
                new_part = plus.split(" ", 1)[0]
                new_start = int(new_part.split(",", 1)[0])
                new_line = new_start
            except Exception:
                new_line = None
            continue

        if new_line is None:
            continue

        if raw.startswith("+") and not raw.startswith("+++"):
            mapping[new_line] = position
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            # deletion: does not advance new line
            continue
        else:
            # context line
            mapping[new_line] = position
            new_line += 1
    return mapping


def _post_pr_review(owner: str, repo_name: str, pr_number: str, token: str, commit_id: str, comments: List[dict], body: str) -> Tuple[bool, str]:
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"
    payload = {
        "commit_id": commit_id,
        "event": "COMMENT",
        "body": body,
        "comments": comments,
    }
    try:
        status, resp_body = _gh_request(url, token, "POST", payload)
        if status in (200, 201):
            return True, resp_body
        return False, f"HTTP {status} {resp_body}"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        return False, f"HTTP {e.code} {err_body}"
    except Exception as e:
        return False, str(e)


def _post_issue_comment(owner: str, repo_name: str, pr_number: str, token: str, body: str) -> Tuple[bool, str]:
    url = f"https://api.github.com/repos/{owner}/{repo_name}/issues/{pr_number}/comments"
    try:
        status, resp_body = _gh_request(url, token, "POST", {"body": body})
        if status in (200, 201):
            return True, resp_body
        return False, f"HTTP {status} {resp_body}"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        return False, f"HTTP {e.code} {err_body}"
    except Exception as e:
        return False, str(e)


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    workspace = os.environ.get("GITHUB_WORKSPACE", "")
    pr_number = os.environ.get("PR_NUMBER")
    commit_id = os.environ.get("COMMIT_ID", "")

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
    owner, repo_name = repo.split("/", 1)

    workspace_clean = workspace.rstrip("/")
    if not issues:
        ok, err = _post_issue_comment(owner, repo_name, pr_number, token, "## Code review agent\n\nNo issues found.")
        if ok:
            print("Posted PR comment: No issues found.")
        else:
            print(f"Could not post comment: {err}", file=sys.stderr)
        sys.exit(0)

    summary_lines = [f"## Code review agent found **{len(issues)}** issue(s)", ""]
    for i in issues[:80]:
        path = rel_path(i.get("file", ""), workspace_clean)
        summary_lines.append(f"- `{path}` line {i.get('line')} — **{i.get('rule')}** {i.get('message')}")
    if len(issues) > 80:
        summary_lines.append(f"- _... and {len(issues) - 80} more (see Actions log)._")
    body = "\n".join(summary_lines)

    # Try inline PR review comments first (only works for findings within the PR diff).
    inline_posted = False
    if commit_id:
        try:
            pr_files = _list_pr_files(owner, repo_name, pr_number, token)
            patch_maps: Dict[str, Dict[int, int]] = {}
            for f in pr_files:
                path = f.get("filename")
                patch = f.get("patch")
                if path and patch:
                    patch_maps[path] = _build_position_map(patch)

            inline_comments: List[dict] = []
            for issue in issues:
                path = rel_path(issue.get("file", ""), workspace_clean)
                line = issue.get("line")
                if not path or not isinstance(line, int):
                    continue
                pos = patch_maps.get(path, {}).get(line)
                if pos is None:
                    continue
                inline_comments.append({
                    "path": path,
                    "position": pos,
                    "body": f"**{issue.get('rule')}**: {issue.get('message')}",
                })

            if inline_comments:
                ok, err = _post_pr_review(
                    owner=owner,
                    repo_name=repo_name,
                    pr_number=pr_number,
                    token=token,
                    commit_id=commit_id,
                    comments=inline_comments,
                    body=f"Code review agent: inline comments for {len(inline_comments)} finding(s) in the PR diff.",
                )
                if ok:
                    inline_posted = True
                    print(f"Posted PR review with {len(inline_comments)} inline comment(s).")
                else:
                    print(f"Could not post inline PR review comments: {err}", file=sys.stderr)
        except Exception as e:
            print(f"Inline comment attempt failed: {e}", file=sys.stderr)

    # Always post a summary issue comment too (so there is always a trace).
    ok, err = _post_issue_comment(owner, repo_name, pr_number, token, body)
    if ok:
        print(f"Posted PR comment with {len(issues)} issue(s) to PR #{pr_number}")
    else:
        print(f"API error posting PR comment: {err}", file=sys.stderr)

    # Don't fail the job - review ran; posting is best-effort
    sys.exit(0)


if __name__ == "__main__":
    main()
