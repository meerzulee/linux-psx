# Exact-plan live boot installer

The public source uses synthetic USB/partition identities. These historical
media tools intentionally reject a real device with those defaults. An
operator must supply and review their own authorized target plan; the examples
do not identify the development console or its media. No storage guard was
removed for publication. Frozen private experiment receipts remain unchanged.


`install_live.py` is a local, release-generic installer for a reviewed private
experiment. Validation is read-only by default. It writes only when explicitly
passed `--write`, an owner confirmation reference and an active bounded UART
session ID. Those references are caller assertions; the tool cannot establish
owner authorization or UART continuity. The primary must obtain confirmation
and start the one coherent hardware session before invoking writes.

The tool does not use SSH, reboot, power off, load a driver, operate UART or
modify the plan. It never claims hardware acceptance. Importing it performs
no actions. All actual filesystem targets and running identities come from
the exact SHA-pinned plan; the candidate release is not hardcoded.

## Additive plan contract

The existing `PLAN.json` must retain `schema: 1`, `experiment`, `release`,
`kernel_sha256`, `boot_files`, `rollback_boot_files` and `modules_archive`.
Both boot maps must contain exactly `bzImage`, `bootargs.txt`,
`initramfs.cpio.gz`, `release.json` and `vram.txt`; every record is exactly
`{"bytes": N, "sha256": "..."}`. Only `bzImage` and `bootargs.txt` may differ
between the maps. The staged `candidate-boot/` and `rollback-boot/` directories
must each contain those exact five regular files without symlinks.

Add an `install` object:

```json
{
  "schema": 1,
  "status": "REVIEWED_FOR_EXPLICIT_LIVE_INSTALL",
  "installer_sha256": "FINAL_INSTALL_LIVE_PY_SHA256",
  "running": {
    "release": "EXACT_RUNNING_RELEASE",
    "boot_id": "EXACT_RUNNING_BOOT_UUID"
  },
  "disk": {"path": "/dev/sda", "bytes": 123983626240},
  "boot": {
    "mountpoint": "/boot",
    "source": "/dev/sda1",
    "fstype": "vfat",
    "uuid": "11111111-2222-4333-8444-555555555555",
    "bytes": 4294967296
  },
  "root": {
    "mountpoint": "/",
    "source": "/dev/sda2",
    "fstype": "ext4",
    "uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "bytes": 119687610368
  },
  "modules_parent": "/usr/lib/modules",
  "module_tree": {"path": "MODULE-TREE.json", "sha256": "EXACT_SIDECAR_SHA256"},
  "runtime_identity": {"path": "RUNTIME-IDENTITY.json", "sha256": "EXACT_SIDECAR_SHA256"},
  "tools": {
    "/usr/bin/findmnt": "EXACT_TARGET_TOOL_SHA256",
    "/usr/bin/lsblk": "EXACT_TARGET_TOOL_SHA256",
    "/usr/bin/zstd": "EXACT_TARGET_TOOL_SHA256"
  }
}
```

The device values above document this experiment's expected USB layout;
the primary must verify and explicitly pin them in the actual plan. The
module parent must be an existing canonical directory, determined from the
target rather than inferred from an archive path. Sidecar and module archive
paths resolve beneath the plan's canonical staging directory. The complete
`MODULE-TREE.json` must be the built inventory, and `RUNTIME-IDENTITY.json`
must bind its exact SHA, candidate kernel SHA, archive name/SHA and all 41
per-module hashes/vermagic. Archive members are regular files directly under
`lib/modules/RELEASE/`, including the full depmod metadata; links, missing,
duplicate and unexpected members are rejected.

Running release and boot ID must both match exactly. Local pinned `findmnt`
and `lsblk` reads must agree on source device, mountpoint, filesystem, UUID,
partition size, parent disk and its exact byte size. Exactly the declared
boot/root partition set must be present; both mounts must be read/write.
The tool makes no fallback from boot ID to release alone.
`lsblk` explicitly requests `--tree`: the selected output omits `NAME`, so
JSON output alone can otherwise flatten the disk and partitions. The strict
single-disk-root/two-child check remains in force.

## Invocation and output

Prepare an explicitly private existing backup parent with mode `0700`.
The output must be a new absolute directory beneath it, outside the stage,
boot mount and module parent. Validation creates no output or temporary
files and invokes only the local identity tools and local decompression:

```text
/usr/bin/python3 -B -I install_live.py \
  --plan ABSOLUTE_PLAN_PATH --plan-sha256 EXACT_PLAN_SHA256 \
  --output NEW_ABSOLUTE_PRIVATE_BACKUP_DIRECTORY \
  --experiment EXP-YYYYMMDD-NNN
```

Only after the primary has obtained explicit owner confirmation and confirmed
the active bounded UART session, append these arguments to that exact command:

```text
--write --owner-confirmation OWNER_MESSAGE_REFERENCE --session-id ACTIVE_SESSION_ID
```

Actual writes require local Linux root. No remote command or sudo command is
run by the tool. Before creating output it validates every staged artifact,
all five old live boot hashes, the current running identity and disk layout,
the complete module archive and the fresh module destination/temp names.
It also snapshots the existing running-release module tree for preservation.

The write sequence is:

1. Create a private output directory, durable journal, fresh five-file boot
   backup, backup readback receipt and old-module inventory.
2. Extract modules manually into a new sibling pending directory, fsync and
   verify the entire tree. Install it with Linux
   `renameat2(RENAME_NOREPLACE)`, preserving every existing release directory.
3. Write and fsync only the new kernel and boot arguments into fresh pending
   files on `/boot`. Recheck original boot files and all frozen inputs.
4. Replace `bzImage`, fsync and verify. Recheck the live identity and every
   boot file before replacing `bootargs.txt` as the final boot-file commit.
5. Sync, recheck all five candidate boot files, complete new modules, frozen
   inputs and the unchanged old module tree. Write `INSTALL.json`.

The other three boot files remain byte-identical. `PASS_INSTALL_READBACK`
means installation and readback passed; it does not mean the image booted.
The receipt explicitly keeps `hardware_acceptance: false` and
`reboot_performed: false`.

Failure stops the sequence without automatic rollback or cleanup. Fresh
backups, journals, pending files, pending module directories and any already
installed new tree remain available. A failure after kernel replacement can
leave the new kernel with the old arguments; `INSTALL.json` records current
boot hashes and pending paths. Treat that as a partial transaction requiring
the primary's reviewed recovery sequence. Existing failed output and module
destinations are never reused or overwritten.

## Synthetic checks

```text
python3 -B -I test_install_live.py --report NEW_ABSOLUTE_REPORT_PATH
```

The bounded suite uses only synthetic temporary files and an injected host.
It verifies no-write validation, exact running/device/boot identities,
archive completeness, forbidden links, additional concatenated tar members,
private/fresh output, write assertions, full backup/readback and module
preservation, extraction failure and a partial kernel-first transaction.
It does not execute findmnt, lsblk, zstd, the Linux rename primitive, SSH,
real sync, or any real device action. Reports are never overwritten.
