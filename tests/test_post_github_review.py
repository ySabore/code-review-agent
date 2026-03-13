import json

import pytest

from scripts import post_github_review
from scripts.post_github_review import _build_added_line_set


def test_build_added_line_set_for_new_file():
    patch = """@@ -0,0 +1,3 @@
+first
+second
+third
"""

    assert _build_added_line_set(patch) == {1, 2, 3}


def test_build_added_line_set_ignores_patch_metadata():
    patch = """@@ -0,0 +1 @@
+value = 1
\\ No newline at end of file
"""

    assert _build_added_line_set(patch) == {1}


def test_main_keeps_summary_body_when_existing_inline_comments_present(tmp_path, monkeypatch):
    review_output = tmp_path / "review-output.json"
    review_output.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "file": str(tmp_path / "src" / "Example.java"),
                        "line": 10,
                        "rule": "line_length",
                        "message": "Line exceeds 120 chars",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PR_NUMBER", "5")
    monkeypatch.setenv("COMMIT_ID", "abc123")
    monkeypatch.setenv("REVIEW_JSON_PATH", str(review_output))

    monkeypatch.setattr(
        post_github_review,
        "_list_pr_files",
        lambda *args, **kwargs: [{"filename": "src/Example.java", "patch": "@@ -0,0 +1,10 @@\n+line\n"}],
    )
    monkeypatch.setattr(
        post_github_review,
        "_list_pr_review_comments",
        lambda *args, **kwargs: [{"path": "src/Example.java", "line": 10, "body": "**debug_statement**: old"}],
    )
    monkeypatch.setattr(
        post_github_review,
        "_post_inline_pr_comment",
        lambda *args, **kwargs: (True, ""),
    )

    captured = {}

    def fake_post_issue_comment(owner, repo_name, pr_number, token, body):
        captured["body"] = body
        return True, ""

    monkeypatch.setattr(post_github_review, "_post_issue_comment", fake_post_issue_comment)

    with pytest.raises(SystemExit) as exc_info:
        post_github_review.main()

    assert exc_info.value.code == 0
    assert "## Code review agent found **1** issue(s)" in captured["body"]
    assert "**line_length** Line exceeds 120 chars" in captured["body"]


def test_main_deletes_older_summary_comments_after_posting_new_one(tmp_path, monkeypatch):
    review_output = tmp_path / "review-output.json"
    review_output.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "file": str(tmp_path / "src" / "Example.java"),
                        "line": 1,
                        "rule": "line_length",
                        "message": "Line exceeds 120 chars",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PR_NUMBER", "5")
    monkeypatch.setenv("COMMIT_ID", "")
    monkeypatch.setenv("REVIEW_JSON_PATH", str(review_output))

    monkeypatch.setattr(
        post_github_review,
        "_post_issue_comment",
        lambda *args, **kwargs: (
            True,
            json.dumps({"id": 200, "body": kwargs["body"] if "body" in kwargs else args[-1]}),
        ),
    )
    monkeypatch.setattr(
        post_github_review,
        "_list_issue_comments",
        lambda *args, **kwargs: [
            {"id": 100, "body": "## Code review agent\n\nOld summary"},
            {"id": 200, "body": "## Code review agent\n\nNew summary"},
            {"id": 300, "body": "**debug_statement**: Potential debug statement left in code"},
        ],
    )

    deleted_ids = []

    def fake_delete_issue_comment(owner, repo_name, comment_id, token):
        deleted_ids.append(comment_id)
        return True, ""

    monkeypatch.setattr(
        post_github_review,
        "_delete_issue_comment",
        fake_delete_issue_comment,
    )

    with pytest.raises(SystemExit) as exc_info:
        post_github_review.main()

    assert exc_info.value.code == 0
    assert deleted_ids == [100, 300]


def test_main_deletes_old_inline_review_comments_before_posting_new_ones(tmp_path, monkeypatch):
    review_output = tmp_path / "review-output.json"
    review_output.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "file": str(tmp_path / "src" / "Example.java"),
                        "line": 1,
                        "rule": "line_length",
                        "message": "Line exceeds 120 chars",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PR_NUMBER", "5")
    monkeypatch.setenv("COMMIT_ID", "abc123")
    monkeypatch.setenv("REVIEW_JSON_PATH", str(review_output))

    monkeypatch.setattr(
        post_github_review,
        "_list_pr_review_comments",
        lambda *args, **kwargs: [
            {"id": 10, "body": "**line_length**: old", "path": "src/Example.java", "line": 1},
            {"id": 11, "body": "non-generated comment", "path": "src/Example.java", "line": 1},
        ],
    )
    monkeypatch.setattr(
        post_github_review,
        "_list_pr_files",
        lambda *args, **kwargs: [{"filename": "src/Example.java", "patch": "@@ -0,0 +1 @@\n+line\n"}],
    )

    deleted_ids = []
    posted_inline = []

    monkeypatch.setattr(
        post_github_review,
        "_delete_pr_review_comment",
        lambda owner, repo_name, comment_id, token: (deleted_ids.append(comment_id) or True, ""),
    )
    monkeypatch.setattr(
        post_github_review,
        "_post_inline_pr_comment",
        lambda *args, **kwargs: (posted_inline.append(kwargs["body"]) or True, ""),
    )
    monkeypatch.setattr(
        post_github_review,
        "_post_issue_comment",
        lambda *args, **kwargs: (True, json.dumps({"id": 200})),
    )
    monkeypatch.setattr(
        post_github_review,
        "_list_issue_comments",
        lambda *args, **kwargs: [{"id": 200, "body": "## Code review agent\n\nLatest"}],
    )

    with pytest.raises(SystemExit) as exc_info:
        post_github_review.main()

    assert exc_info.value.code == 0
    assert deleted_ids == [10]
    assert posted_inline == ["**line_length**: Line exceeds 120 chars"]
