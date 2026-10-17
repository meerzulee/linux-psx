# PS4 kernel targets

The base snapshot is a byte-preserving import from `meerzulee/linux-ps4` at
`f61663b369b76f1fc7e8cf29dd946a1bfc5b1e75`. The MTS2 addition comes from
`386df1ad97d92defee1794bf961a0e2f5dc85a1b`; original headers/licenses remain intact.

| Target | Base | Active patches | Status |
| --- | --- | --- | --- |
| **Root target `6.18-ps4-baikal`** | Linux 6.18.44 | 55 | Active firmware-free psX build; experimental MTS2 Ethernet; new binaries unqualified. |
| `6.18-baikal` | Linux 6.18.44 | 54 | Imported development baseline; historical tests cover PS4 Slim Baikal B1. |
| `6.18-baikal-ultrawide` | Linux 6.18.44 | 55 | Experimental; the baseline plus one ultrawide delta. |

The inherited `patches/`, `config/` and `targets/` retain their original relative layout.
Resolve `CONFIG_FILE`, `SERIES_FILE`, `PATCHES_DIR` and `CONFIG_FRAGMENTS` from
this directory. They are historical source metadata, not active root targets.
The root `targets/6.18-ps4-baikal.env` instead resolves paths from the repository
root and selects `config/6.18-psx-baikal.config` plus the MTS2 series.
Build with the [root builder](../../docs/BUILDING.md).

Only non-comment entries in a target's `series` are active. Disabled patches
and `.candidate` files remain historical references. The ultrawide series
reuses the baseline through relative paths instead of duplicating it.

See [the import guide](../../docs/PS4-PATCHES.md),
[credits](../../CREDITS.md) and
[the file manifest](../../provenance/ps4-import.json).
