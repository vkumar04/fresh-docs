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


class ParseGithubUrlTests(unittest.TestCase):
    def test_dotted_repo_names(self):
        # Regression: `[^/.]+` rejected vercel/next.js, so `next` fell
        # through to PyPI and resolved to an unrelated repo.
        self.assertEqual(fd.parse_github_url("git+https://github.com/vercel/next.js.git"), "vercel/next.js")
        self.assertEqual(fd.parse_github_url("https://github.com/socketio/socket.io"), "socketio/socket.io")

    def test_url_forms(self):
        for url, want in (
            ("git+https://github.com/radix-ui/primitives.git", "radix-ui/primitives"),
            ("git+ssh://git@github.com/a/b.git", "a/b"),
            ("git@github.com:a/b.git", "a/b"),
            ("https://github.com/a/b/tree/main/packages/x", "a/b"),
            ("https://github.com/a/b#readme", "a/b"),
            ("github:a/b", "a/b"),
            ("a/b", "a/b"),
        ):
            self.assertEqual(fd.parse_github_url(url), want, url)

    def test_non_github_returns_none(self):
        for url in ("", "https://gitlab.com/a/b", "https://example.com", "https://github.com/a"):
            self.assertIsNone(fd.parse_github_url(url), url)


class ResolveGithubRepoTests(unittest.TestCase):
    def setUp(self):
        self._real = (fd.npm_view, fd._pypi_urls, fd._hex_urls)
        self.pypi_called = False

        def fake_pypi(_):
            self.pypi_called = True
            return ["https://github.com/someone/unrelated"]

        fd._pypi_urls = fake_pypi
        fd._hex_urls = lambda _: []

    def tearDown(self):
        fd.npm_view, fd._pypi_urls, fd._hex_urls = self._real

    def test_prefers_version_pinned_metadata(self):
        # prisma@7.10.1 lives in prisma/orm; `latest` points at prisma-cli.
        def fake_npm(spec, *fields):
            return {
                "prisma@7.10.1": json.dumps({"url": "https://github.com/prisma/orm.git", "directory": "packages/cli"}),
                "prisma": json.dumps({"url": "https://github.com/prisma/prisma-cli.git"}),
            }.get(spec, "")

        fd.npm_view = fake_npm
        self.assertEqual(fd.resolve_github_repo("prisma", "auto", "7.10.1"), ("prisma/orm", "packages/cli", "npm"))
        self.assertEqual(fd.resolve_github_repo("prisma", "auto", "9.9.9"), ("prisma/prisma-cli", None, "npm"))

    def test_npm_known_package_does_not_fall_through(self):
        fd.npm_view = lambda spec, *f: json.dumps({"url": "https://gitlab.com/a/b.git"})
        self.assertIsNone(fd.resolve_github_repo("x", "auto"))
        self.assertFalse(self.pypi_called)

    def test_unknown_on_npm_falls_through_to_pypi(self):
        fd.npm_view = lambda spec, *f: ""
        self.assertEqual(fd.resolve_github_repo("x", "auto"), ("someone/unrelated", None, "pypi"))


class PrereleaseOrderingTests(unittest.TestCase):
    def test_numeric_identifiers_compare_numerically(self):
        self.assertGreater(fd.parse_semver("7.10.0-dev.10"), fd.parse_semver("7.10.0-dev.9"))

    def test_numeric_sorts_before_alphanumeric(self):
        self.assertLess(fd.parse_semver("1.0.0-1"), fd.parse_semver("1.0.0-alpha"))

    def test_semver_spec_example_chain(self):
        chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
                 "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
        parsed = [fd.parse_semver(v) for v in chain]
        self.assertEqual(parsed, sorted(parsed))

    def test_format_roundtrip(self):
        for v in ("1.2.3", "8.0.0-rc.23", "@scope/pkg@1.0.0-beta.1"):
            self.assertEqual(fd.format_semver(fd.parse_semver(v)), v.rsplit("@", 1)[-1])
        self.assertTrue(fd.is_prerelease(fd.parse_semver("1.0.0-rc.1")))
        self.assertFalse(fd.is_prerelease(fd.parse_semver("1.0.0")))


CHANGELOG = """# radix-ui

## 1.8.2-rc.1

- unreleased thing

## 1.8.1

### Patch Changes

- fixed callback props

## 1.8.0

### Major Changes

- removed asChild default

## 1.7.0

- old stuff

## 1.6.0
"""


class SliceChangelogTests(unittest.TestCase):
    def test_slices_exactly_the_range(self):
        out = fd.slice_changelog(CHANGELOG, fd.parse_semver("1.7.0"), fd.parse_semver("1.8.1"))
        self.assertTrue(out.startswith("## 1.8.1"))
        self.assertIn("removed asChild default", out)
        self.assertNotIn("unreleased thing", out)
        self.assertNotIn("old stuff", out)

    def test_none_when_no_heading_in_range(self):
        self.assertIsNone(fd.slice_changelog(CHANGELOG, fd.parse_semver("2.0.0"), fd.parse_semver("2.1.0")))

    def test_keepachangelog_headings(self):
        text = "## [Unreleased]\n- x\n## [3.1.0] - 2026-10-01\n- new\n## [3.0.0] - 2026-01-01\n- base\n"
        out = fd.slice_changelog(text, fd.parse_semver("3.0.0"), fd.parse_semver("3.1.0"))
        self.assertIn("new", out)
        self.assertNotIn("base", out)

    def test_version_inside_text_is_not_a_boundary(self):
        text = "## 2.0.0\n### Notes\n- requires node 1.0.0 or later\n## 1.0.0\n- base\n"
        out = fd.slice_changelog(text, fd.parse_semver("1.0.0"), fd.parse_semver("2.0.0"))
        self.assertIn("requires node", out)
        self.assertNotIn("base", out)


class FakeGh:
    """Stands in for `run` so _do_diff can be exercised without gh/network."""

    def __init__(self, releases=(), bodies=None, tags=(), compare=None):
        self.releases, self.bodies, self.tags, self.compare = list(releases), bodies or {}, set(tags), compare
        self.calls = []

    def __call__(self, argv, timeout=15):
        self.calls.append(argv)
        if argv[:3] == ["gh", "release", "list"]:
            return json.dumps([{"tagName": t} for t in self.releases]), ""
        if argv[:3] == ["gh", "release", "view"]:
            tag = argv[3]
            return (self.bodies[tag], "") if tag in self.bodies else ("", "release not found")
        if argv[:2] == ["gh", "api"] and "/git/ref/tags/" in argv[2]:
            tag = argv[2].split("/git/ref/tags/", 1)[1]
            return (f"refs/tags/{tag}", "") if tag in self.tags else ("", "Not Found")
        if argv[:2] == ["gh", "api"] and "/compare/" in argv[2] and self.compare:
            return json.dumps(self.compare), ""
        return "", "unexpected"


class DoDiffTests(unittest.TestCase):
    def setUp(self):
        self._real = (fd.run, fd.resolve_github_repo, fd.npm_published_versions, fd._fetch_raw)
        fd.resolve_github_repo = lambda lib, eco="auto", ver=None: ("o/r", self.directory, "npm")
        fd.npm_published_versions = lambda lib: self.published
        fd._fetch_raw = lambda repo, ref, path: self.raw.get((ref, path))
        self.directory, self.published, self.raw = None, [], {}

    def tearDown(self):
        fd.run, fd.resolve_github_repo, fd.npm_published_versions, fd._fetch_raw = self._real

    def test_direct_lookup_skips_release_listing(self):
        self.published = ["16.4.0", "16.4.1", "16.4.2", "16.5.0-canary.1"]
        gh = fd.run = FakeGh(bodies={"v16.4.1": "## Breaking\n- x", "v16.4.2": "fixes"})
        code, r = fd._do_diff("next", "16.4.0", "16.4.2")
        self.assertEqual(code, 0)
        self.assertEqual(r["lookup"], "direct")
        self.assertEqual([x["tag"] for x in r["releases"]], ["v16.4.1", "v16.4.2"])
        self.assertEqual(len(r["releases"][0]["breaking_blocks"]), 1)
        self.assertFalse(any(c[:3] == ["gh", "release", "list"] for c in gh.calls))

    def test_monorepo_tries_pkg_at_version_first(self):
        self.directory, self.published = "packages/ai", ["6.0.0", "6.0.1"]
        gh = fd.run = FakeGh(bodies={"ai@6.0.1": "patch"})
        _, r = fd._do_diff("ai", "6.0.0", "6.0.1")
        self.assertEqual(r["releases"][0]["tag"], "ai@6.0.1")
        views = [c[3] for c in gh.calls if c[:3] == ["gh", "release", "view"]]
        self.assertEqual(views[0], "ai@6.0.1")

    def test_listing_excludes_prereleases_unless_asked(self):
        fd.run = FakeGh(
            releases=["7.10.0-dev.1", "7.10.0", "7.10.1", "7.9.0"],
            bodies={"7.10.0-dev.1": "dev", "7.10.0": "a", "7.10.1": "b"},
        )
        _, r = fd._do_diff("prisma", "7.9.0", "7.10.1")
        self.assertEqual([x["version"] for x in r["releases"]], ["7.10.0", "7.10.1"])
        _, r = fd._do_diff("prisma", "7.10.0-dev.0", "7.10.1")
        self.assertIn("7.10.0-dev.1", [x["version"] for x in r["releases"]])

    def test_changelog_fallback_from_default_branch_when_no_tags(self):
        self.directory = "packages/react/radix-ui"
        self.raw = {("HEAD", "packages/react/radix-ui/CHANGELOG.md"): ("u", CHANGELOG)}
        fd.run = FakeGh()
        code, r = fd._do_diff("radix-ui", "1.7.0", "1.8.1")
        fb = r["changelog_fallback"]
        self.assertEqual(code, 0)
        self.assertFalse(fb["pinned"])
        self.assertTrue(fb["sliced"])
        self.assertEqual(len(fb["breaking_blocks"]), 1)
        self.assertEqual(len(fd.diff_breaking_blocks(r)), 1)

    def test_default_branch_changelog_without_range_heading_is_rejected(self):
        self.raw = {("HEAD", "CHANGELOG.md"): ("u", "# Changelog\n## 0.1.0\n- ancient\n")}
        fd.run = FakeGh()
        code, r = fd._do_diff("x", "1.7.0", "1.8.1")
        self.assertEqual(code, 1)
        self.assertIsNone(r["changelog_fallback"])
        self.assertIn("no tag for 1.7.0, 1.8.1", r["error"])

    def test_commits_fallback_when_tags_but_no_notes(self):
        fd.run = FakeGh(
            tags={"7.10.0", "7.10.1"},
            compare={"total": 2, "subjects": ["fix(client): a", "feat!: drop node 18"]},
        )
        code, r = fd._do_diff("prisma", "7.10.0", "7.10.1")
        cf = r["commits_fallback"]
        self.assertEqual(code, 0)
        self.assertEqual((cf["base"], cf["head"]), ("7.10.0", "7.10.1"))
        self.assertEqual(cf["breaking_blocks"], ["feat!: drop node 18"])

    def test_swapped_range_is_normalized(self):
        self.published = ["1.0.0", "1.1.0"]
        fd.run = FakeGh(bodies={"v1.1.0": "x"})
        _, r = fd._do_diff("x", "1.1.0", "1.0.0")
        self.assertEqual((r["from"], r["to"]), ("1.0.0", "1.1.0"))
        self.assertEqual(len(r["releases"]), 1)

    def test_types_packages_short_circuit_without_network(self):
        gh = fd.run = FakeGh()
        code, r = fd._do_diff("@types/node", "26.6.4", "26.6.5")
        self.assertEqual(code, 0)
        self.assertIn("DefinitelyTyped", r["no_notes"])
        self.assertIsNone(r["error"])
        self.assertEqual(gh.calls, [])

    def test_invalid_semver(self):
        code, r = fd._do_diff("x", "latest", "1.0.0")
        self.assertEqual(code, 1)
        self.assertIn("invalid semver", r["error"])


class FlagBreakingExitCodeTests(unittest.TestCase):
    """CI-gate contract: --flag-breaking-only exits 1 iff a breaking bump
    survives the filter. Runs cmd_audit in a temp git repo with _do_diff
    mocked, so no network and no gh dependency."""

    def _run_audit(self, breaking_blocks, error=None, out=None):
        def fake_do_diff(lib, from_v, to_v, ecosystem="auto"):
            if error:
                return 1, {"library": lib, "from": from_v, "to": to_v, "repo": None,
                           "releases": [], "changelog_fallback": None,
                           "commits_fallback": None, "error": error}
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
                buf = out if out is not None else io.StringIO()
                with contextlib.redirect_stdout(buf):
                    return fd.cmd_audit(args)
        finally:
            fd._do_diff = real_do_diff
            os.chdir(cwd)

    def test_exits_1_when_breaking_bump_found(self):
        self.assertEqual(self._run_audit(["## Breaking\n- gone"]), 1)

    def test_exits_0_when_all_clear(self):
        self.assertEqual(self._run_audit([]), 0)

    def test_unchecked_dep_is_not_all_clear(self):
        out = io.StringIO()
        self.assertEqual(self._run_audit([], error="couldn't resolve 'zod'", out=out), 2)
        self.assertIn("NOT all clear", out.getvalue())
        self.assertIn("zod: 3.0.0 → 4.0.0", out.getvalue())

    def test_header_counts_breaking_bumps(self):
        out = io.StringIO()
        self._run_audit(["## Breaking\n- gone"], out=out)
        self.assertIn("1 bump with breaking-change blocks", out.getvalue())
        self.assertIn("- gone", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
