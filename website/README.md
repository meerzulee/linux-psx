# Linux psX web manual

The website at https://linux.mrz.sh/ is generated from the numbered chapters and
table of contents in `../manual/`. Edit those Markdown files. The website supplies
the layout, navigation and local search.

The chapter list in `build.mjs` is the publication allowlist. Research notes,
experiment records and other repository documents aren't copied into the site.
Links to them resolve to GitHub, and the build fails if a linked file isn't on
`origin/master`, so the site can't ship a dead source link.

Website code is MIT licensed, see `LICENSE`. It's adapted from the Omarchy psX
manual site, see `BRANDING.md`.

## Build and preview

With Bun from mise (`mise install` reads `mise.toml`), from this directory:

```sh
bun install --frozen-lockfile
bun run check
bun run dev
```

Open the printed loopback URL. The server binds only to `127.0.0.1`. After editing
Markdown, CSS or JavaScript, run `bun run build` again and refresh the page.

`bun run check` validates chapter order, relative links, local anchors and images
without writing a build. `bun run build` writes the complete static site to `dist/`.
Reading and navigation work without JavaScript. Search and code copying are
progressive enhancements.

## Status labels

In a chapter, a code span holding exactly `working`, `beta`, `untested` or
`unavailable` renders as a colored result label. Use those words only for recorded
results. Nothing becomes `beta` or `working` without a recorded hardware test.

A problem goes in the manual only after we've reproduced it. An open issue or a
single unexplained failure isn't enough.

## Search engines, link previews and LLM tools

With `SITE_URL` set, as the deploy build does, the build also writes:

- a canonical link, Open Graph and X card tags, and JSON-LD structured data on
  every page: `WebSite` on the home page, `TechArticle` and breadcrumbs on
  chapters;
- the share image `assets/og-image.png`, 1200 by 630. Regenerate it with
  `bun og-image.mjs` after the wordmark changes; see `BRANDING.md`;
- a Markdown copy of each page at `/<chapter>.md`, and `/index.md` for the home
  page, with absolute links. The generated `_headers` names each copy's HTML
  page as canonical, so search engines index the HTML;
- `llms.txt` in the https://llmstxt.org/ format, and `llms-full.txt` with the
  whole manual in one file;
- `sitemap.xml` with each chapter's last commit date, and `robots.txt`.

Titles and descriptions come from the `site` object in `build.mjs` and from
each chapter's first paragraph, so keep that paragraph a plain summary.

Cloudflare can add its own rules in front of `robots.txt` and block AI
crawlers before they reach the site. Those are zone settings in the Cloudflare
dashboard, not files in this directory.

## Deploying

The manual is hosted as static assets on the `linux-psx` Cloudflare Worker, with
the custom domain `linux.mrz.sh`. `wrangler.jsonc` records the account, domain and
`dist/` asset directory. With the intended Cloudflare account logged in:

```sh
bun install --frozen-lockfile
npx wrangler whoami
npx wrangler deploy --dry-run
npx wrangler deploy
```

Both deployment commands run the link checks and rebuild with the production
origin. `--dry-run` stays local, and plain `deploy` publishes. No Git push is
part of this workflow. Wrangler runs through `npx`, so Node.js stays installed for
that one tool.

The deployment contains only generated public documentation and assets. It has no
application API, database, credentials or analytics, and search runs in the
browser. `_headers` sets the content security policy.

Keep credentials out of chapter text and images. For a credential scan, install
Gitleaks and run this after building:

```sh
gitleaks dir dist --ignore-gitleaks-allow --redact=100 --no-banner
```
