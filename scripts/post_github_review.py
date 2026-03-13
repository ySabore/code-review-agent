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
from typing import Dict, List, Optional, Set, Tuple

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


def _build_added_line_set(patch: str) -> Set[int]:
    """
    Track NEW-file line numbers that are actually added in the PR diff.
    Inline comments are only attempted on added lines, which GitHub can
    reliably resolve by `line` + `side`.
    """
    new_line = None
    added_lines: Set[int] = set()
    for raw in patch.splitlines():
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
            added_lines.add(new_line)
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            # deletion: does not advance new line
            continue
        elif raw.startswith("\\"):
            # "\ No newline at end of file" is patch metadata, not a file line.
            continue
        else:
            # context line
            new_line += 1
    return added_lines


def _list_pr_review_comments(owner: str, repo_name: str, pr_number: str, token: str) -> List[dict]:
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/comments?per_page=100"
    status, body = _gh_request(url, token, "GET")
    if status != 200:
        raise RuntimeError(f"Failed to list PR review comments: HTTP {status} {body}")
    return json.loads(body)


def _list_issue_comments(owner: str, repo_name: str, pr_number: str, token: str) -> List[dict]:
    url = f"https://api.github.com/repos/{owner}/{repo_name}/issues/{pr_number}/comments?per_page=100"
    status, body = _gh_request(url, token, "GET")
    if status != 200:
        raise RuntimeError(f"Failed to list issue comments: HTTP {status} {body}")
    return json.loads(body)


def _delete_issue_comment(owner: str, repo_name: str, comment_id: int, token: str) -> Tuple[bool, str]:
    url = f"https://api.github.com/repos/{owner}/{repo_name}/issues/comments/{comment_id}"
    try:
        status, resp_body = _gh_request(url, token, "DELETE")
        if status == 204:
            return True, resp_body
        return False, f"HTTP {status} {resp_body}"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        return False, f"HTTP {e.code} {err_body}"
    except Exception as e:
        return False, str(e)


def _delete_pr_review_comment(owner: str, repo_name: str, comment_id: int, token: str) -> Tuple[bool, str]:
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/comments/{comment_id}"
    try:
        status, resp_body = _gh_request(url, token, "DELETE")
        if status == 204:
            return True, resp_body
        return False, f"HTTP {status} {resp_body}"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        return False, f"HTTP {e.code} {err_body}"
    except Exception as e:
        return False, str(e)


def _is_summary_comment(body: str) -> bool:
    return body.startswith("## Code review agent")


def _is_generated_finding_comment(body: str) -> bool:
    return body.startswith("**") and "**:" in body


def _cleanup_old_inline_comments(
    owner: str,
    repo_name: str,
    pr_number: str,
    token: str,
) -> None:
    try:
        review_comments = _list_pr_review_comments(owner, repo_name, pr_number, token)
        deleted = 0
        failed = 0
        for comment in review_comments:
            comment_id = comment.get("id")
            body = comment.get("body") or ""
            if not isinstance(comment_id, int) or not _is_generated_finding_comment(body):
                continue

            ok, err = _delete_pr_review_comment(owner, repo_name, comment_id, token)
            if ok:
                deleted += 1
            else:
                failed += 1
                print(
                    f"Could not delete old inline comment {comment_id}: {err}",
                    file=sys.stderr,
                )

        if deleted:
            print(f"Deleted {deleted} old inline comment(s).")
        if failed:
            print(f"Failed to delete {failed} old inline comment(s).", file=sys.stderr)
    except Exception as e:
        print(f"Could not clean up old inline comments: {e}", file=sys.stderr)


def _cleanup_old_summary_comments(
    owner: str,
    repo_name: str,
    pr_number: str,
    token: str,
    keep_comment_id: Optional[int],
) -> None:
    if keep_comment_id is None:
        return

    try:
        issue_comments = _list_issue_comments(owner, repo_name, pr_number, token)
        deleted = 0
        failed = 0
        for comment in issue_comments:
            comment_id = comment.get("id")
            body = comment.get("body") or ""
            if not isinstance(comment_id, int):
                continue
            if comment_id == keep_comment_id:
                continue
            if not (_is_summary_comment(body) or _is_generated_finding_comment(body)):
                continue

            ok, err = _delete_issue_comment(owner, repo_name, comment_id, token)
            if ok:
                deleted += 1
            else:
                failed += 1
                print(
                    f"Could not delete old generated issue comment {comment_id}: {err}",
                    file=sys.stderr,
                )

        if deleted:
            print(f"Deleted {deleted} old generated issue comment(s).")
        if failed:
            print(f"Failed to delete {failed} old generated issue comment(s).", file=sys.stderr)
    except Exception as e:
        print(f"Could not clean up old generated issue comments: {e}", file=sys.stderr)


def _post_inline_pr_comment(
    owner: str,
    repo_name: str,
    pr_number: str,
    token: str,
    commit_id: str,
    path: str,
    line: int,
    body: str,
) -> Tuple[bool, str]:
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}/comments"
    payload = {
        "commit_id": commit_id,
        "body": body,
        "path": path,
        "line": line,
        "side": "RIGHT",
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
            try:
                keep_comment_id = json.loads(err).get("id")
            except Exception:
                keep_comment_id = None
            _cleanup_old_summary_comments(
                owner,
                repo_name,
                pr_number,
                token,
                keep_comment_id,
            )
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
            _cleanup_old_inline_comments(owner, repo_name, pr_number, token)
            pr_files = _list_pr_files(owner, repo_name, pr_number, token)
            added_lines_by_path: Dict[str, Set[int]] = {}
            for f in pr_files:
                path = f.get("filename")
                patch = f.get("patch")
                if path and patch:
                    added_lines_by_path[path] = _build_added_line_set(patch)

            inline_comments: List[dict] = []
            for issue in issues:
                path = rel_path(issue.get("file", ""), workspace_clean)
                line = issue.get("line")
                if not path or not isinstance(line, int):
                    continue
                if line not in added_lines_by_path.get(path, set()):
                    continue
                comment_body = f"**{issue.get('rule')}**: {issue.get('message')}"
                inline_comments.append({"path": path, "line": line, "body": comment_body})

            if inline_comments:
                posted = 0
                failed = 0
                for comment in inline_comments:
                    ok, err = _post_inline_pr_comment(
                        owner=owner,
                        repo_name=repo_name,
                        pr_number=pr_number,
                        token=token,
                        commit_id=commit_id,
                        path=comment["path"],
                        line=comment["line"],
                        body=comment["body"],
                    )
                    if ok:
                        posted += 1
                    else:
                        failed += 1
                        print(
                            f"Could not post inline comment for {comment['path']}:{comment['line']}: {err}",
                            file=sys.stderr,
                        )
                if posted:
                    inline_posted = True
                    print(f"Posted {posted} inline comment(s).")
                if failed:
                    print(f"Failed to post {failed} inline comment(s).", file=sys.stderr)
        except Exception as e:
            print(f"Inline comment attempt failed: {e}", file=sys.stderr)

    # Always post a summary issue comment too (so there is always a trace).
    ok, err = _post_issue_comment(owner, repo_name, pr_number, token, body)
    if ok:
        print(f"Posted PR comment with {len(issues)} issue(s) to PR #{pr_number}")
        try:
            keep_comment_id = json.loads(err).get("id")
        except Exception:
            keep_comment_id = None
        _cleanup_old_summary_comments(
            owner,
            repo_name,
            pr_number,
            token,
            keep_comment_id,
        )
    else:
        print(f"API error posting PR comment: {err}", file=sys.stderr)

    # Don't fail the job - review ran; posting is best-effort
    sys.exit(0)


if __name__ == "__main__":
    main()
