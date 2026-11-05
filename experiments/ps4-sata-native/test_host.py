#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Apply the real patch, compile and execute its header against a fake device."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    baseline = json.loads((HERE / "BASELINE.json").read_text())["members"]
    with tempfile.TemporaryDirectory(prefix="psx-sata-fixture-") as tmp:
        root = Path(tmp)
        for name, sha in baseline.items():
            data = (args.source / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == sha, name
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i",
                        str(HERE / "0001-ahci-baikal-scoped-completion.patch")],
                       cwd=root, check=True)
        source = (root / "drivers/ata/libahci.c").read_text()
        assert "ps4_ahci_poll_timer" not in source
        assert "ps4_poll_timer" not in source
        for anchor in ["ps4_ahci_probe_progress(ap, before);", "ps4_ahci_probe_arm(qc);",
                       "ps4_ahci_probe_enable(ap);", "ps4_ahci_probe_disable(ap, false);",
                       "ps4_ahci_probe_disable(ap, true);",
                       "ps4_ahci_probe_init(ap);"]:
            assert source.count(anchor) == 1, anchor
        header = (root / "drivers/ata/ahci-ps4-probe.h").read_bytes()
        assert header == (HERE / "ahci-ps4-probe.h").read_bytes()
        assert b"module_param_named(ps4_probe_ms, ps4_ahci_completion_probe_ms, uint, 0444);" in header
        assert b"ps4_ahci_completion_probe_ms, uint, 0444);" in header
        fields = (root / "drivers/ata/ahci.h").read_text()
        fields = fields[fields.index("\tstruct ata_port\t\t*ps4_probe_ap;"):]
        fields = fields[:fields.index("\n};")]
        (root / "probe-fields.h").write_text(fields + "\n")
        shutil.copyfile(HERE / "fixture.c", root / "fixture.c")
        for hz in (100, 250, 1000):
            exe = root / f"fixture-{hz}"
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O1",
                            "-g", "-fsanitize=undefined,address", f"-DFIXTURE_HZ={hz}",
                            "-I", str(root / "drivers/ata"), str(root / "fixture.c"),
                            "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
