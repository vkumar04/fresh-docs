---
name: fresh-docs
description: "Use when you need authoritative, version-current documentation for a library, framework, or API — anything you'd previously reach for a docs-search MCP to answer. Auto-routes to llms.txt → GitHub Releases → CHANGELOG/MIGRATING at the installed-version tag → official docs WebFetch. Trigger: `/fresh-docs <library>` or whenever you're about to recommend a library API and need to verify it isn't out of date."
trigger: /fresh-docs
---

# /fresh-docs

Pull authoritative, version-current documentation for a library without relying on a single throttled source. Designed for the case where your training knowledge MIGHT be current but you need to verify — and where docs-search MCPs (context7, dsearch via DevDocs) are unavailable, rate-limited, or simply don't cover the library.

## Why this skill exists

Context7 throttles (monthly quota), DevDocs.io misses major libraries (TanStack Query, react-hook-form, Vercel AI SDK), and any single docs source is a single point of failure. Modern libraries increasingly publish `llms.txt` — a curated text file specifically designed for LLM consumption — which IS the canonical answer when it exists. After that, GitHub raw files at the installed version tag are the closest thing to "what the maintainers think the docs are right now."

## When to use

- About to recommend an API call (`useQuery({ ... })`, `streamText({ tools, stopWhen })`, `<motion.div animate={...}>`) and you want to confirm the shape isn't deprecated.
- After `ncu -u` / `bun update` and you want a quick audit pass over upgraded libraries.
- Migrating across a major version — pull the migration guide.
- User asks "what's the latest pattern for X in library Y?"

## When NOT to use

- For provider-neutral programming questions (loops, generics, algorithms) — your training is the source.
- For questions about THIS codebase — read the code.
- For trivia where wrong answers are cheap — just answer from memory.
- When you genuinely have current, confident knowledge — state that and move on. (Don't WebFetch out of paranoia.)

## The strategy

For a given library + topic, try sources in order of signal density. Stop at the first source that confidently answers the question. Always quote the source URL in your response.

```
1. llms.txt (raw `curl`, NOT WebFetch)    ← AI-curated, current, terse
2. GitHub Releases API for installed tag  ← changelog distilled by maintainers
3. CHANGELOG.md / MIGRATING.md at tag     ← raw maintainer notes
   3.5. `--diff <from> <to>` shortcut     ← release notes between two tags
4. Official docs WebFetch (specific page) ← human-readable, may be slightly stale
5. npm registry README                    ← bare minimum, but version-pinned
```

### Step 0 — Identify the library + version

```bash
# Find installed version from the project's lockfile / package.json
grep -m1 "\"<lib>\":" package.json
# OR for bun lockfile:
grep -m1 "<lib>@" bun.lock
```

If the user names a library without a version, default to whatever's installed. If they're considering a library not yet installed, default to "latest" via `bunx npm view <lib> version`.

### Step 1 — Try llms.txt first (raw `curl` or `fresh-docs` CLI, NOT WebFetch)

Most modern libraries publish one at `<docs_root>/llms.txt`. **Do NOT use WebFetch for these.** WebFetch runs a small fast model to pre-summarize the page before handing it back, and on long `llms.txt` files that summarizer drops critical detail or gets things outright wrong (during the 2026-05-25 audit it asserted `Output.object` was deprecated in AI SDK v6 — wrong; the targeted reference URL confirmed it's current). `llms.txt` is designed to fit in an LLM context window — pull the raw markdown directly.

**Preferred — `fresh-docs` CLI** (single-file Python script that ships in this repo, see Install in the README):

```bash
fresh-docs llms ai                              # full llms.txt
fresh-docs llms ai --grep "stopWhen"            # ±2/+20 line windows around matches
fresh-docs llms uv --subpage "scripts"          # drill into an index-format link
```

The CLI handles UA spoofing, the index-format `[Title](url.md)` drill-in, and version-aware semver comparison for `diff`.

**Fallback when the CLI isn't installed — raw curl**:

```bash
curl -fsSL --max-time 10 \
  -A "Mozilla/5.0 (compatible; fresh-docs/1.0)" \
  "https://ai-sdk.dev/llms.txt" | head -800
```

The `-A` (User-Agent) avoids 403s from sites that filter the default `curl/x.y.z` UA. For very large files (Stripe's runs into hundreds of KB), pipe through `grep -B2 -A20 "<topic keyword>"` instead of `head`.

If the response is non-empty markdown, quote the relevant section and answer. If 404, move on.

### Step 2 — GitHub Releases for the installed tag

```bash
# Find repo from npm
bunx npm view <lib> repository.url 2>/dev/null
# Then fetch the release notes for the installed version
gh api "repos/<owner>/<repo>/releases/tags/v<version>" --jq '.body' 2>/dev/null
```

Useful for "what changed between X and Y" or "is this API new in Y?". Release notes are almost always the most accurate diff source.

### Step 3 — Raw CHANGELOG / MIGRATING at the version tag

```bash
curl -fs "https://raw.githubusercontent.com/<owner>/<repo>/v<version>/CHANGELOG.md" | head -200
curl -fs "https://raw.githubusercontent.com/<owner>/<repo>/v<version>/MIGRATING.md" | head -200
curl -fs "https://raw.githubusercontent.com/<owner>/<repo>/main/docs/migration/<version>.md" | head -200
```

Some monorepos have per-package CHANGELOGs (`packages/<pkg>/CHANGELOG.md`). Try both root and per-package paths.

### Step 3.5 — `fresh-docs diff` for version-bump audits

The everyday `ncu -u` case: "I just bumped @testcontainers/postgresql 11.14 → 12.0, what broke?" The CLI handles GitHub release-notes range fetching with semver-aware comparison (lexicographic comparison on `"v11.14.0"` is broken — `"v11.14.0"` is lex-greater than `"v11.2.0"` but lex-less than `"v11.5.0"`).

```bash
fresh-docs diff @testcontainers/postgresql 11.14.0 12.0.0
```

The CLI:
1. Resolves the GitHub repo via `bunx npm view <lib> repository.url`.
2. Fetches all releases via `gh api`.
3. Parses each `tag_name` as semver and filters to the inclusive range.
4. For each release, prints the body — extracting `## Breaking` / `### 🚨 Breaking` sub-blocks when present, otherwise the first 50 lines.

If the project doesn't publish releases (some monorepos don't), fall back to:

```bash
curl -fsSL "https://raw.githubusercontent.com/<owner>/<repo>/main/MIGRATING.md" | head -300
```

### Step 4 — Targeted WebFetch on official docs

For libraries with stable doc URLs (table below), WebFetch directly with a focused prompt — don't pull the whole page if you only need one section.

### Step 5 — npm registry README (bare minimum)

```bash
bunx npm view <lib> --json | jq -r '.readme' | head -200
```

Version-pinned to whatever's installed. Lower signal than the GitHub README at the same tag, but doesn't require knowing the repo URL.

## Canonical docs URLs

Skip the npm-view roundtrip when the library is one of these — go straight to the known URL.

URLs verified 2026-05-25 via HEAD requests. Update this table if a `200` flips to `404`.

| Library | Docs root | llms.txt path |
|---|---|---|
| `ai` (Vercel AI SDK) | https://ai-sdk.dev/docs | ✅ https://ai-sdk.dev/llms.txt |
| `@ai-sdk/react` | https://ai-sdk.dev/docs/ai-sdk-ui | ✅ (same llms.txt as `ai`) |
| `@ai-sdk/anthropic` | https://ai-sdk.dev/providers/ai-sdk-providers/anthropic | ✅ (same llms.txt) |
| `next` | https://nextjs.org/docs | ✅ https://nextjs.org/docs/llms.txt |
| `react` | https://react.dev | ✅ https://react.dev/llms.txt |
| `@tanstack/react-query` | https://tanstack.com/query/latest | ✅ https://tanstack.com/llms.txt (covers all TanStack libs) |
| `@tanstack/react-table` | https://tanstack.com/table/latest | ✅ (same tanstack.com/llms.txt) |
| `react-hook-form` | https://react-hook-form.com/docs | ❌ no llms.txt — WebFetch the docs page directly |
| `@hookform/resolvers` | https://react-hook-form.com/get-started#SchemaValidation | ❌ no llms.txt |
| `motion` (Framer Motion v12+) | https://motion.dev/docs | ❌ no llms.txt — WebFetch motion.dev/docs/<feature> |
| `prisma` / `@prisma/client` | https://prisma.io/docs | ✅ https://prisma.io/llms.txt |
| `better-auth` | https://better-auth.com/docs | ✅ https://better-auth.com/llms.txt |
| `zod` | https://zod.dev | ✅ https://zod.dev/llms.txt |
| `shadcn` / shadcn-ui | https://ui.shadcn.com | ✅ https://ui.shadcn.com/llms.txt |
| `tailwindcss` | https://tailwindcss.com/docs | ❌ no llms.txt — WebFetch the docs page |
| `vercel` (platform) | https://vercel.com/docs | ✅ https://vercel.com/docs/llms.txt |
| `vitest` | https://vitest.dev/guide | ✅ https://vitest.dev/llms.txt |
| `stripe` | https://docs.stripe.com | ✅ https://docs.stripe.com/llms.txt |
| `resend` | https://resend.com/docs | ✅ https://resend.com/llms.txt |
| `playwright` / `@playwright/test` | https://playwright.dev/docs/intro | ❌ no llms.txt — WebFetch a specific page |
| `exceljs` | https://github.com/exceljs/exceljs#readme | (README only) |
| `pdf-parse` | npm view `<lib>` repository.url + README | (README only) |
| `radix-ui` / `@radix-ui/*` | https://radix-ui.com/docs | ❌ no llms.txt — per-component pages |
| `recharts` | https://recharts.org | ❌ no llms.txt — API per chart type |
| `lucide-react` | https://lucide.dev/icons | (icon catalog only) |

For libraries NOT in this table, do:

```bash
bunx npm view <lib> homepage repository.url 2>/dev/null
```

Then try `<homepage>/llms.txt` first, falling back to README at the version tag.

## Output format

When answering from one of these sources:

1. **State the source explicitly**: *"From llms.txt at ai-sdk.dev (v6, current as of fetch):"* — never paraphrase without attribution.
2. **Quote the relevant snippet** (the smallest piece that answers the question), not the whole page.
3. **Note the version pinning**: if you fetched the docs for `v5.100.14`, say so — the user might be on a different version.
4. **Flag deprecations explicitly**: if a pattern in their code matches one in the docs marked as deprecated, flag it.

When answering from training:

- Say *"From training (Jan 2026 cutoff)"*. Don't pretend you fetched anything.
- If the question is high-stakes (about to write code that ships), do a verify pass via `/fresh-docs` rather than ship-and-hope.

## Common audit prompts

These get used a lot after `ncu -u`. Memorize the shape:

```
/fresh-docs <lib>                                 # general "what's the current pattern" pass
/fresh-docs <lib> migration                       # what changed between installed version and N-1
/fresh-docs <lib> <api>                           # specific API — e.g. /fresh-docs ai streamText
/fresh-docs <lib> --diff <fromVersion> <toVersion>  # release notes + breaking-change extract
                                                  # between two tags (see Step 3.5)
/fresh-docs deprecation-audit                     # scan package.json, check each lib's CHANGELOG since
                                                  # last `git log -1 package.json`, flag deprecations
```

## Failure handling

- **WebFetch hits a 403** — many docs sites (react-hook-form.com hit us on 2026-05-25) filter the WebFetch user agent. **Before falling through, retry once with `curl -A "Mozilla/5.0 (compatible; fresh-docs/1.0)"`** — that succeeds on roughly 80% of WebFetch 403s. Only after the curl retry also fails should you move to the next source.
- **429** — back off and try the next source. Don't retry the same URL.
- **All sources 404** — report what you tried with URLs, and clearly state you couldn't verify. Better to say "I couldn't find authoritative docs for this; the pattern in your training is X" than to invent.
- **Library is on a custom domain** — try the homepage from `npm view`. If that fails, search the README for a "Documentation:" link.
- **Summarizer disagreement** — if a WebFetch-summarized answer doesn't match what you see in the code, re-fetch with a more specific URL (e.g. `/docs/reference/<api>` instead of the top-level `/llms.txt`). The summarizer on long pages loses nuance; targeted URLs return targeted summaries.

## What this skill is NOT

- It is not a substitute for reading the actual code when the question is about behavior.
- It does not fetch every page of the docs — be surgical.
- It does not maintain a cache. Each invocation is fresh (the whole point).
