"""Gitignore-flavoured glob ignore rules matched against posix relative paths.

Semantics:
- ``*`` matches within one path segment (does not cross ``/``).
- ``**`` crosses directories: as a whole segment it matches zero or more
  path segments; at the end of a pattern (``dir/**``) it matches everything
  under that directory.
- ``?`` matches a single character, not ``/``.
- A pattern containing ``/`` is anchored to the root of the pair and matched
  against the full relative path.
- A pattern with no ``/`` matches the basename at any depth (like gitignore).
"""

from __future__ import annotations

import re

#: Always ignored, regardless of the pair's configured patterns: this is
#: where folder-mode conflict backups live (task 29b, ``conflict.py``), and
#: it must never be treated as regular pair content - a user's existing
#: config predates this feature and would not otherwise exclude it.
_ALWAYS_IGNORE = (".androsync/**",)


def _translate_segment(segment: str) -> str:
    """Translate one path segment (no ``/``) with ``*``/``?`` wildcards to regex."""
    out = []
    for ch in segment:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _translate_pattern(pattern: str) -> str:
    """Translate a full glob pattern (possibly containing ``/`` and ``**``) to a regex."""
    parts = pattern.split("/")
    out = []
    last_index = len(parts) - 1
    for i, part in enumerate(parts):
        is_last = i == last_index
        if part == "**":
            if is_last:
                # "everything under here": at least one more path component.
                out.append(".+")
            else:
                # zero or more whole path segments, each followed by "/".
                out.append("(?:.*/)?")
        else:
            seg_regex = _translate_segment(part)
            if is_last:
                out.append(seg_regex)
            else:
                out.append(seg_regex + "/")
    return "^" + "".join(out) + "$"


class IgnoreRules:
    """A set of glob patterns to exclude relative paths from sync."""

    def __init__(self, patterns: list[str]) -> None:
        self.patterns = list(patterns)
        self._compiled: list[tuple[re.Pattern[str], bool]] = []
        # Patterns that can prune a whole directory during a scan, before
        # descending into it: "dir/**" style (matched against the full
        # relative directory path) and bare-name patterns with no "/"
        # (matched against the directory's basename, like gitignore).
        self._dir_compiled: list[tuple[re.Pattern[str], bool]] = []
        all_patterns = self.patterns + [p for p in _ALWAYS_IGNORE if p not in self.patterns]
        for pattern in all_patterns:
            has_slash = "/" in pattern
            regex = re.compile(_translate_pattern(pattern))
            self._compiled.append((regex, has_slash))

            if has_slash and pattern.endswith("/**"):
                prefix = pattern[: -len("/**")]
                self._dir_compiled.append((re.compile(_translate_pattern(prefix)), True))
            elif not has_slash:
                self._dir_compiled.append((re.compile("^" + _translate_segment(pattern) + "$"), False))

    def matches(self, rel: str) -> bool:
        """Return True if the posix relative path ``rel`` should be ignored."""
        basename = rel.rsplit("/", 1)[-1]
        for regex, has_slash in self._compiled:
            target = rel if has_slash else basename
            if regex.match(target):
                return True
        return False

    def matches_dir(self, rel_dir: str) -> bool:
        """Return True if the whole directory ``rel_dir`` can be pruned from a scan.

        This is a cheap pre-check used by transport scanners to avoid walking
        into directories that are entirely ignored, rather than walking every
        file underneath and filtering each one with :meth:`matches`. It only
        recognises the two pattern shapes that unambiguously ignore an entire
        directory tree: ``dir/**`` (matched against the full relative
        directory path) and a bare pattern with no ``/`` (matched against the
        directory's basename, at any depth). Other patterns - e.g. those that
        only ignore specific files inside a directory - never prune it here,
        so per-file filtering with :meth:`matches` still applies.
        """
        basename = rel_dir.rsplit("/", 1)[-1]
        for regex, is_path in self._dir_compiled:
            target = rel_dir if is_path else basename
            if regex.match(target):
                return True
        return False
