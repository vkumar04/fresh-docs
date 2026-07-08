# fresh-docs

A Claude Code skill + a single-file Python CLI for pulling **authoritative, version-current library documentation** without depending on any single docs source.

## Why

The default docs-search tools each have failure modes:

- **context7** has a monthly quota that runs out mid-session.
- **DevDocs.io** (and `dsearch` on top of it) misses many modern libraries — no TanStack Query, no react-hook-form, no Vercel AI SDK, no Motion.
- **WebFetch** runs a small fast model to pre-summarize, which on long `llms.txt` files drops critical detail (it told us `Output.object` was deprecated in Vercel AI SDK v6 — it wasn't).
- **WebFetch** also fails on `403 Forbidden` from sites that filter its user agent (react-hook-form.com).
- **Hand-written `jq` filters** on GitHub release tags do lexicographic comparison, which is broken for semver (`"v11.14.0"` is lex-less than `"v11.5.0"`).

Most modern libraries publish `llms.txt` — a single curated text file specifically designed for LLM consumption. When it exists, it's the gold path. `fresh-docs` routes there first, then falls back to GitHub Releases (with proper semver), CHANGELOG/MIGRATING at the version tag, official-docs WebFetch (with a real UA), or npm README.

Works across **npm, PyPI, and Hex** — Elixir packages resolve through hex.pm, and recent ExDoc emits llms.txt on HexDocs for free (the CLI content-checks it, because hexdocs.pm serves an HTML fallback page with status 200 for missing files).

## Install

The CLI is a single-file Python 3.10+ script with a PEP 723 inline-metadata block and a `#!/usr/bin/env -S uv run --script` shebang. It launches via `uv` with zero `pip install` overhead.

**Prerequisite**: `uv` (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
mkdir -p ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/vkumar04/fresh-docs/main/fresh-docs \
  -o ~/.local/bin/fresh-docs
chmod +x ~/.local/bin/fresh-docs

# Make sure ~/.local/bin is on PATH
export PATH="$HOME/.local/bin:$PATH"

# Drop the Claude Code skill into place too:
mkdir -p ~/.claude/skills/fresh-docs
curl -fsSL https://raw.githubusercontent.com/vkumar04/fresh-docs/main/SKILL.md \
  -o ~/.claude/skills/fresh-docs/SKILL.md
```

Verify:

```bash
fresh-docs --help
fresh-docs llms ai --grep "stopWhen"
```

Restart your Claude Code session (or wait for skills to reload). Then `/fresh-docs <library> <topic>` becomes a first-class command.

## CLI subcommands

```bash
# Raw llms.txt fetch — no LLM pre-summary, optional grep extraction
fresh-docs llms <library>
fresh-docs llms <library> --grep <regex>
fresh-docs llms <library> --subpage <regex>   # for index-format llms.txt (absolute or relative links)

# Any docs page — browser UA, HTML strip + entity decode
fresh-docs page <url>

# GitHub release notes between two semver tags (breaking-change-aware,
# falls back to CHANGELOG.md at the tag when the repo doesn't publish releases)
fresh-docs diff <library> <fromVersion> <toVersion> [--json] [--ecosystem npm|pypi|hex]

# Audit every package.json dep bump since a commit (dirty tree → diffs against HEAD)
fresh-docs audit [--since <ref>] [--include-patch] [--packages a,b] [--fetch-diffs] [--json]
fresh-docs audit --flag-breaking-only   # exits 1 if any bump has a Breaking/Major Changes block → CI gate

# Verify every URL in the built-in llms.txt map (status + HTML-fallback detection)
fresh-docs check-map
```

Examples:

```bash
fresh-docs llms ai --grep "stopWhen"                          # AI SDK v6 stop predicate
fresh-docs llms uv --subpage "scripts"                        # uv PEP 723 inline metadata
fresh-docs llms @tanstack/react-query --grep "isPending"      # v5 mutation pending flag
fresh-docs llms phoenix_live_view --subpage "bindings" --grep "phx-click"   # Elixir/HexDocs
fresh-docs page "https://react-hook-form.com/docs/useform"    # site that 403s WebFetch
fresh-docs page "https://hexdocs.pm/phoenix/routing.html"     # HexDocs page without llms.txt
fresh-docs diff @testcontainers/postgresql 11.14.0 12.0.0     # v11→v12 breaking changes
fresh-docs diff ecto 3.12.0 3.13.0 --ecosystem hex            # Elixir; name also exists on npm
fresh-docs audit --flag-breaking-only                          # post-`ncu -u` CI gate
```

## What the skill does

For a given library + topic, tries sources in order of signal density and stops at the first one that confidently answers:

```
1. llms.txt (raw `curl` or `fresh-docs llms`)    ← AI-curated, current, terse
2. GitHub Releases API for installed tag         ← changelog distilled by maintainers
3. CHANGELOG.md / MIGRATING.md at tag            ← raw maintainer notes
   3.5. `fresh-docs diff` shortcut               ← release notes between two tags
4. Official docs WebFetch (specific page)        ← human-readable, may be slightly stale
5. npm registry README                           ← bare minimum, but version-pinned
```

The skill ships a vetted map of canonical `llms.txt` endpoints (20+ libraries across npm, PyPI, and Hex, verified 2026-07-08 via `fresh-docs check-map`) and known fallback paths for the libraries we ship most often. Unmapped libraries auto-discover across npm → PyPI → hex.pm with a liveness check on each candidate.

## When to use it

- About to recommend a library API (`useQuery({ ... })`, `streamText({ tools, stopWhen })`) and you want to verify the shape isn't deprecated.
- After `ncu -u` / `bun update` — quick audit pass over upgraded libraries.
- Migrating across a major version — pull the migration guide directly.
- "What's the latest pattern for X in library Y?" questions.

## When NOT to use it

- Provider-neutral programming questions (algorithms, generics, type theory) — your training is the source.
- Questions about a specific codebase — read the code.
- Trivia where being wrong is cheap — answer from memory.
- You genuinely have current, confident knowledge — state that and move on.

## Output format

When the skill answers from a fetched source:

1. **States the source URL explicitly** — *"From llms.txt at ai-sdk.dev (v6, fetched 2026-05-25):"*
2. **Quotes the relevant snippet**, not the whole page.
3. **Names the version pinning** — `v5.100.14` etc.
4. **Flags deprecations** when the user's code matches a pattern marked deprecated.

When the skill answers from training, it says so — *"From training (Jan 2026 cutoff)"* — instead of pretending it fetched.

## Verifying / extending the URL map

The skill body + the CLI both ship a vetted table of known `llms.txt` endpoints. If a vendor changes their URL, edit `SKILL.md` and `fresh-docs` (the `LLMS_URL_MAP` dict near the top), then re-publish.

Quick check:

```bash
fresh-docs check-map
```

`ok 200` = good. `DEAD` = move it to the "no llms.txt" column (this includes 200-status responses whose content-type is `text/html` — hexdocs.pm serves an HTML fallback page for missing files, so a bare status check lies). Exit code 2 when anything is dead, so it can run in CI.

## Tests

```bash
python3 test_fresh_docs.py
```

Stdlib-only `unittest` covering semver/tag parsing (including Changesets `@scope/pkg@1.2.3` tags), breaking-block extraction (`Breaking` and Changesets `Major Changes` headings), bump classification, HTML-fallback detection, and the `--flag-breaking-only` exit-code contract.

## License

MIT.
