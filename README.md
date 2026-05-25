# fresh-docs

A Claude Code skill for pulling **authoritative, version-current library documentation** without depending on any single source.

## Why

The default docs-search tools each have failure modes:

- **context7** has a monthly quota that runs out mid-session.
- **DevDocs.io** (and `dsearch` on top of it) misses many modern libraries — no TanStack Query, no react-hook-form, no Vercel AI SDK, no Motion.
- **WebFetch on Google** is slow and noisy.

Most maintainers of modern libraries publish `llms.txt` — a single curated text file specifically designed for LLM consumption. When it exists, it's the gold path. `fresh-docs` routes there first, then falls back to GitHub Releases / raw CHANGELOG at the installed version tag / official docs WebFetch.

## Install

Drop the `SKILL.md` into your Claude Code skills directory:

```bash
mkdir -p ~/.claude/skills/fresh-docs
curl -fsSL https://raw.githubusercontent.com/vkumar04/fresh-docs/main/SKILL.md \
  -o ~/.claude/skills/fresh-docs/SKILL.md
```

Restart your Claude Code session (or wait for skills to reload). Then:

```
/fresh-docs ai streamText
/fresh-docs next 16 migration
/fresh-docs @tanstack/react-query useMutation
```

## What it does

For a given library + topic, tries sources in order of signal density and stops at the first one that confidently answers:

```
1. llms.txt (when published)              ← AI-curated, current, terse
2. GitHub Releases API for installed tag  ← changelog distilled by maintainers
3. CHANGELOG.md / MIGRATING.md at tag     ← raw maintainer notes
4. Official docs WebFetch (specific page) ← human-readable, may be slightly stale
5. npm registry README                    ← bare minimum, but version-pinned
```

The skill maintains a vetted map of canonical doc URLs and known-good `llms.txt` endpoints for the libraries we ship most often:

- **Vercel AI SDK**, `@ai-sdk/*`, **Next.js**, **React**, **TanStack Query/Table**, **Prisma**, **better-auth**, **zod**, **shadcn-ui**, **Vitest**, **Stripe**, **Resend** — `llms.txt` available
- **react-hook-form**, **Motion**, **Tailwind CSS**, **Playwright** — no `llms.txt`, falls through to targeted WebFetch
- Anything else — tries `<homepage>/llms.txt` (from `npm view`), then GitHub README at the version tag

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

The skill body ships a vetted table of known `llms.txt` endpoints. If a vendor changes their URL, edit `SKILL.md` and update the row.

Quick check:

```bash
for url in <urls>; do
  printf "%3s  %s\n" "$(curl -sI -o /dev/null -w '%{http_code}' -L --max-time 8 "$url")" "$url"
done
```

`200` = good. `404` = move it to the "no llms.txt" column. `000` = network issue, retry.

## License

MIT.
