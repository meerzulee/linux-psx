# PS4 patch snapshot

This directory is a byte-preserving import from `meerzulee/linux-ps4` at
`f61663b369b76f1fc7e8cf29dd946a1bfc5b1e75`.

| Target | Base | Active patches | Status |
| --- | --- | --- | --- |
| `6.18-baikal` | Linux 6.18.44 | 54 | Imported development baseline; historical tests cover PS4 Slim Baikal B1. |
| `6.18-baikal-ultrawide` | Linux 6.18.44 | 55 | Experimental; the baseline plus one ultrawide delta. |

`patches/`, `config/` and `targets/` retain their original relative layout.
Resolve `CONFIG_FILE`, `SERIES_FILE`, `PATCHES_DIR` and `CONFIG_FRAGMENTS` from
this directory. A build wrapper is not included in this phase.

Only non-comment entries in a target's `series` are active. Disabled patches
and `.candidate` files remain historical references. The ultrawide series
reuses the baseline through relative paths instead of duplicating it.

See [the import guide](../../docs/PS4-PATCHES.md),
[credits](../../CREDITS.md) and
[the file manifest](../../provenance/ps4-import.json).
