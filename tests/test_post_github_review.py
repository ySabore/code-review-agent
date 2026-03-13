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
