# Linux psX contributor instructions

## Current scope

- This repository currently contains PS4 kernel patches and source research.
  Build tooling and PS5 integration have not been migrated.
- Read `docs/PS4-PATCHES.md`, `CREDITS.md`, `provenance/ps4-import.json` and
  `docs/UNIVERSAL-KERNEL.md` before changing a patch or making support claims.
- `platforms/ps4/` preserves the original source layout. Paths in its inherited
  target files resolve relative to that directory, not the repository root.
- Use the explicit ordered `series` files. Disabled patches and `.candidate`
  files are retained for reference and must stay inactive without new evidence.
- Preserve patch bytes, author headers, copyrights and licenses during imports.
  Record adaptations separately; do not attribute aggregate patches solely to
  their packaging author or silently rewrite a recorded patch hash.
- Pin source commits and record current working locations when old links fail.
  Distinguish direct imports, adaptations and comparison-only references.

## Verification and hardware

- Check `provenance/ps4-SHA256SUMS` and series integrity after import changes.
  Update provenance deliberately whenever imported bytes change.
- Source inspection, patch application and successful builds do not establish
  hardware support. Existing PS4 evidence has its recorded hardware scope;
  experimental ultrawide and PS5 are not accepted by this import.
- Console operation, payload delivery, storage writes, public releases and
  history rewrites require explicit task authority. Keep raw private logs,
  credentials, firmware binaries and generated kernel artifacts out of Git.
- For consequential PS4 hardware work, follow the workspace UART procedure in
  `../omarchy-ps4/docs/DEVELOPMENT-MODE.md` and record evidence in its experiment
  ledger. Obtain that procedure before hardware work from a standalone clone.

## Hosts and publication

- Native x86-64 Linux is suitable for source and patch checks. Preserve the
  Apple Silicon build path when build tooling is migrated.
- Use OrbStack exclusively for local containers and Linux VMs. Verify it is
  running and the Docker context is `orbstack` before Docker work.
- Preserve existing local work and inspect live remote refs before handoff.
- Use a GitHub noreply author email for public commits when email privacy is
  enabled. Publishing requires task authority; never disable that protection
  merely to push a commit.
- Provide full raw URLs in operational handoffs, with absolute paths for local
  artifacts and pasteable macOS `open` commands when immediately useful.
