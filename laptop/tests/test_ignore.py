"""Tests for androsync.ignore.IgnoreRules."""

from androsync.ignore import IgnoreRules


def test_dir_star_star_matches_everything_under_dir():
    rules = IgnoreRules([".git/**"])
    assert rules.matches(".git/config")
    assert rules.matches(".git/objects/ab/cdef0123")
    assert not rules.matches("other/.git/config")
    assert not rules.matches(".gitignore")


def test_star_matches_basename_at_any_depth():
    rules = IgnoreRules(["*.tmp"])
    assert rules.matches("file.tmp")
    assert rules.matches("nested/dir/file.tmp")
    assert not rules.matches("file.tmp.bak")


def test_pattern_with_slash_and_star_is_anchored_and_single_segment():
    rules = IgnoreRules([".obsidian/workspace*.json"])
    assert rules.matches(".obsidian/workspace.json")
    assert rules.matches(".obsidian/workspace-2.json")
    assert not rules.matches(".obsidian/sub/workspace.json")
    assert not rules.matches("other/.obsidian/workspace.json")


def test_leading_double_star_crosses_directories():
    rules = IgnoreRules(["**/cache/*"])
    assert rules.matches("cache/foo.txt")
    assert rules.matches("a/cache/foo.txt")
    assert rules.matches("a/b/cache/foo.txt")
    assert not rules.matches("a/cache/sub/foo.txt")


def test_non_matching_path():
    rules = IgnoreRules([".git/**", "*.tmp", ".obsidian/workspace*.json", "**/cache/*"])
    assert not rules.matches("notes/todo.md")


def test_multiple_patterns_any_match_ignores():
    rules = IgnoreRules([".git/**", "*.tmp"])
    assert rules.matches(".git/HEAD")
    assert rules.matches("a/b/c.tmp")
    assert not rules.matches("a/b/c.md")


def test_matches_dir_prunes_dir_star_star_directory():
    rules = IgnoreRules([".git/**"])
    assert rules.matches_dir(".git")
    assert not rules.matches_dir("other")
    # Only the top of the ignored tree needs to report True; scanners prune
    # there and never recurse far enough to ask about ".git/objects".


def test_matches_dir_ignores_bare_name_at_any_depth():
    rules = IgnoreRules(["node_modules"])
    assert rules.matches_dir("node_modules")
    assert rules.matches_dir("a/b/node_modules")
    assert not rules.matches_dir("node_modules_but_not_quite")


def test_matches_dir_does_not_prune_file_only_patterns():
    rules = IgnoreRules(["*.tmp", ".obsidian/workspace*.json"])
    assert not rules.matches_dir("anything")
    assert not rules.matches_dir(".obsidian")


def test_matches_dir_nested_dir_star_star_prefix_is_anchored():
    rules = IgnoreRules(["a/b/**"])
    assert rules.matches_dir("a/b")
    assert not rules.matches_dir("a")
    assert not rules.matches_dir("x/a/b")


def test_androsync_dir_always_ignored_even_when_not_configured():
    rules = IgnoreRules([])
    assert rules.matches(".androsync/conflicts/20260905-153000/notes/a.md")
    assert rules.matches_dir(".androsync")


def test_androsync_dir_still_ignored_alongside_other_configured_patterns():
    rules = IgnoreRules([".git/**", "*.tmp"])
    assert rules.matches(".androsync/conflicts/20260905-153000/a.md")
    assert rules.matches(".git/HEAD")
    assert not rules.matches("notes/todo.md")
