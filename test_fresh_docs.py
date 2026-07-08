#!/usr/bin/env python3
"""Unit tests for the fresh-docs CLI. Stdlib only:

    python3 test_fresh_docs.py

The CLI file has no .py extension, so load it as a module by path.
"""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fresh-docs")
_loader = importlib.machinery.SourceFileLoader("fresh_docs", _PATH)
_spec = importlib.util.spec_from_loader("fresh_docs", _loader)
fd = importlib.util.module_from_spec(_spec)
_loader.exec_module(fd)


class ParseSemverTests(unittest.TestCase):
    def test_plain_and_v_prefixed(self):
        self.assertEqual(fd.parse_semver("v6.0.191")[:3], (6, 0, 191))
        self.assertEqual(fd.parse_semver("6.0.191")[:3], (6, 0, 191))

    def test_changesets_monorepo_tag(self):
        self.assertEqual(fd.parse_semver("ai@6.0.191")[:3], (6, 0, 191))

    def test_scoped_monorepo_tag(self):
        # Regression: `(?:[^@\s]+@)?` couldn't match the leading `@` of a
        # scope, so every @scope/pkg@x.y.z tag was silently dropped.
        self.assertEqual(
            fd.parse_semver("@ai-sdk/anthropic@3.0.79")[:3], (3, 0, 79)
        )

    def test_semver_ordering_not_lexicographic(self):
        self.assertGreater(fd.parse_semver("11.14.0"), fd.parse_semver("11.5.0"))

    def test_prerelease_sorts_before_release(self):
        self.assertLess(fd.parse_semver("1.2.3-beta.2"), fd.parse_semver("1.2.3"))

    def test_build_metadata_ignored_for_precedence(self):
        self.assertEqual(fd.parse_semver("1.2.3+sha.abc"), fd.parse_semver("1.2.3"))

    def test_garbage_returns_none(self):
        for bad in ("", "not-a-version", "1.2", "v1", "pkg@1.2"):
            self.assertIsNone(fd.parse_semver(bad), repr(bad))


class TagMatchesLibraryTests(unittest.TestCase):
    def test_single_package_tags_always_match(self):
        self.assertTrue(fd.tag_matches_library("v6.0.191", "ai"))
        self.assertTrue(fd.tag_matches_library("6.0.191", "anything"))

    def test_monorepo_tag_matches_its_package_only(self):
        self.assertTrue(fd.tag_matches_library("ai@6.0.191", "ai"))
        self.assertFalse(fd.tag_matches_library("ai@6.0.191", "zod"))

    def test_scoped_monorepo_tag(self):
        self.assertTrue(
            fd.tag_matches_library("@ai-sdk/anthropic@3.0.79", "@ai-sdk/anthropic")
        )
        self.assertFalse(
            fd.tag_matches_library("@ai-sdk/anthropic@3.0.79", "@ai-sdk/react")
        )


class BumpTypeTests(unittest.TestCase):
    def test_bump_classification(self):
        self.assertEqual(fd._bump_type("1.2.3", "2.0.0"), "major")
        self.assertEqual(fd._bump_type("^1.2.3", "^1.3.0"), "minor")
        self.assertEqual(fd._bump_type("~1.2.3", "~1.2.4"), "patch")
        self.assertEqual(fd._bump_type("junk", "1.2.3"), "unknown")


class StripSemverPrefixTests(unittest.TestCase):
    def test_prefixes(self):
        for raw, want in (
            ("^1.2.3", "1.2.3"),
            ("~1.2.3", "1.2.3"),
            (">=1.2.3", "1.2.3"),
            ("1.2.3", "1.2.3"),
        ):
            self.assertEqual(fd._strip_semver_prefix(raw), want)


class BreakingBlockTests(unittest.TestCase):
    def _blocks(self, body):
        return [m.group(0) for m in fd._BREAKING_BLOCK_RE.finditer(body)]

    def test_breaking_heading(self):
        body = "## Breaking Changes\n- removed foo\n\n## Fixes\n- bar\n"
        blocks = self._blocks(body)
        self.assertEqual(len(blocks), 1)
        self.assertIn("removed foo", blocks[0])
        self.assertNotIn("bar", blocks[0])

    def test_emoji_decorated_heading(self):
        self.assertEqual(len(self._blocks("### 🚨 Breaking\n- gone\n")), 1)

    def test_changesets_major_changes_heading(self):
        # Changesets repos (vercel/ai, TanStack) never write a "Breaking"
        # heading — their breaking changes land under "### Major Changes".
        body = "### Major Changes\n- dee8b05: ai SDK 6\n\n### Patch Changes\n- fix\n"
        blocks = self._blocks(body)
        self.assertEqual(len(blocks), 1)
        self.assertIn("ai SDK 6", blocks[0])
        self.assertNotIn("Patch Changes", blocks[0])

    def test_minor_and_patch_changes_do_not_match(self):
        self.assertEqual(
            self._blocks("### Minor Changes\n- x\n\n### Patch Changes\n- y\n"), []
        )


class LooksLikeHtmlTests(unittest.TestCase):
    def test_html_fallback_pages_detected(self):
        # hexdocs.pm serves an HTML fallback page with status 200 for
        # missing llms.txt files — status checks alone lie.
        self.assertTrue(fd._looks_like_html("<!DOCTYPE html>\n<html>…"))
        self.assertTrue(fd._looks_like_html("  \n<html lang='en'>"))

    def test_markdown_passes(self):
        self.assertFalse(fd._looks_like_html("# Ecto v3.14.0 - Table of Contents"))
        self.assertFalse(fd._looks_like_html("Plain llms.txt prose with <code> later"))


class ReadPackageJsonTests(unittest.TestCase):
    def test_reads_plain_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"dependencies": {"zod": "^4.0.0"}}, f)
        try:
            pkg = fd._read_package_json(f.name)
            self.assertEqual(pkg["dependencies"]["zod"], "^4.0.0")
        finally:
            os.unlink(f.name)

    def test_unknown_ref_returns_none(self):
        self.assertIsNone(fd._read_package_json("no-such-ref-xyz"))


class FlagBreakingExitCodeTests(unittest.TestCase):
    """CI-gate contract: --flag-breaking-only exits 1 iff a breaking bump
    survives the filter. Runs cmd_audit in a temp git repo with _do_diff
    mocked, so no network and no gh dependency."""

    def _run_audit(self, breaking_blocks):
        def fake_do_diff(lib, from_v, to_v, ecosystem="auto"):
            return 0, {
                "library": lib, "from": from_v, "to": to_v, "repo": "x/y",
                "releases": [{
                    "tag": f"v{to_v}", "version": to_v,
                    "body": "", "breaking_blocks": breaking_blocks,
                }],
                "changelog_fallback": None, "error": None,
            }

        args = argparse.Namespace(
            since=None, include_patch=False, packages=None,
            fetch_diffs=False, flag_breaking_only=True, json=False,
        )
        cwd, real_do_diff = os.getcwd(), fd._do_diff
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.chdir(tmp)
                env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                       "PATH": os.environ["PATH"], "HOME": tmp}
                subprocess.run(["git", "init", "-q"], check=True, env=env)
                with open("package.json", "w") as f:
                    json.dump({"dependencies": {"zod": "^3.0.0"}}, f)
                subprocess.run(["git", "add", "."], check=True, env=env)
                subprocess.run(["git", "commit", "-qm", "v1"], check=True, env=env)
                with open("package.json", "w") as f:
                    json.dump({"dependencies": {"zod": "^4.0.0"}}, f)
                fd._do_diff = fake_do_diff
                with contextlib.redirect_stdout(io.StringIO()):
                    return fd.cmd_audit(args)
        finally:
            fd._do_diff = real_do_diff
            os.chdir(cwd)

    def test_exits_1_when_breaking_bump_found(self):
        self.assertEqual(self._run_audit(["## Breaking\n- gone"]), 1)

    def test_exits_0_when_all_clear(self):
        self.assertEqual(self._run_audit([]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
