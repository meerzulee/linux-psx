# Linux psX website lettering

The website takes its theme, layout and search from the Omarchy psX manual
site, and uses **LINUX PSX** in **Delta Corps Priest 1**, the FIGlet font used
by Omarchy's own ASCII renderer. The font credits **CoSMiC cHiLD**. The lettering
is one solid theme color, and the SVG outlines stay sharp without a web font.

## Assets

- `assets/linux-psx.svg` is the mark used in the header and masthead.
- `assets/favicon.svg` is the letter L on a dark Tokyo Night tile.
- `assets/og-image.png` is the 1200-by-630 card that link previews show. It
  has the same lettering with LINUX in Tokyo Night blue `#7aa2f7` and PSX in
  purple `#bb9af7`, the colors of the Omarchy psX avatar, on `#1a1b26`, with
  JetBrains Mono text. `og-image.mjs` renders it with Chromium. Run
  `bun og-image.mjs` again rather than editing the PNG.
- `assets/LICENSE-omarchy.txt` preserves the upstream MIT notice unchanged.

## How the lettering was made

Both marks are the output of `omarchy-ascii`, from the `omarchy` 4.5.0-3
package: `omarchy-ascii "LINUX PSX"` and `omarchy-ascii L`. Full and half block
characters are converted into SVG rectangles on a 15-by-30-unit terminal cell,
keeping the horizontal and vertical halves. Regenerate them this way rather
than redrawing them.

Neither mark uses Omarchy's canonical logo.

## Copied from Omarchy psX

These files come from `omarchy-psx/website` at commit
`bbff47f25249d32e867ced1a400229cdf5a28737`, where they are MIT licensed:

| File | SHA-256 of the original | Local changes |
| --- | --- | --- |
| `build.mjs` | `6be2707d33054248f74b5c62c5b9d222fbb60e8699c14051fd323a823aaea3c8` | Chapter list, project links, wordmark, status labels, a check that source links exist on `origin/master`. |
| `serve.mjs` | `0db926b96f784857160c30b306a78dfa0d78dfcf2bc57c4d5c3ca0cf18879338` | None. |
| `_headers` | `b4a63b545de570f2b008481b8d7c05ccdd4993fa98a4c3625615977dab8bc2af` | None. |
| `assets/manual.css` | `0cadd2c10894ef2dbbfa98935860999e10b491a6f19e93159f60d9d118aff7bc` | Status label colors, a narrow-screen header rule. |
| `assets/manual.js` | `bf8a07282636c2162f062d0165fd96bb621b531ef4229290ef4a6b62ad562723` | Theme storage key and the search prompt. |
