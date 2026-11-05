#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Produce the reviewable patch from the pinned runtime-dev3 source members."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASE = "7.2.11-psx-ps4-baikal-uvd-runtime-dev3"


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("source anchor is not unique: " + old[:100])
    return text.replace(old, new, 1)


def changed(source):
    header = (source / "drivers/ata/ahci.h").read_text()
    body = (source / "drivers/ata/libahci.c").read_text()
    header = replace_once(header,
        "\tstruct ata_port\t\t*ap;\t\t/* owning port for PS4 poll */\n"
        "\tstruct timer_list\tps4_poll_timer;\t/* PS4 completion poll */\n",
        "\tstruct ata_port\t\t*ps4_probe_ap;\n"
        "\tstruct timer_list\tps4_probe_timer;\n"
        "\tbool\t\t\tps4_probe_enabled;\n"
        "\tbool\t\t\tps4_probe_stopped;\n"
        "\tbool\t\t\tps4_probe_expired;\n"
        "\tunsigned long\t\tps4_probe_interval;\n"
        "\tunsigned long\t\tps4_probe_deadline;\n"
        "\tu64\t\t\tps4_probe_completed;\n"
        "\tunsigned int\t\tps4_probe_logs;\n"
        "\tunsigned int\t\tps4_probe_faults;\n")
    body = replace_once(body,
        "module_param_named(ps4_ahci_poll_ms, ps4_ahci_poll_ms, uint, 0644);",
        "module_param_named(ps4_ahci_poll_ms, ps4_ahci_poll_ms, uint, 0444);")
    body = replace_once(body, '"PS4 AHCI completion poll interval in ms (0=off)");',
        '"Retired PS4 timer; must be 0 (use ps4_ahci_completion_probe_ms)");\n'
        '\n#include "ahci-ps4-probe.h"')
    start = body.index("static void ps4_ahci_poll_timer(")
    end = body.index("static irqreturn_t ahci_multi_irqs_intr_hard", start)
    body = body[:start] + body[end:]
    body = replace_once(body, "\tu32 qc_active = 0;\n\tint rc;",
        "\tu32 qc_active = 0;\n\tu64 before = ap->qc_active;\n\tint rc;")
    body = replace_once(body, "\trc = ata_qc_complete_multiple(ap, qc_active);",
        "\trc = ata_qc_complete_multiple(ap, qc_active);\n"
        "\tif (rc >= 0)\n\t\tps4_ahci_probe_progress(ap, before);")
    body = replace_once(body, "\tahci_sw_activity(qc->dev->link);\n\n\treturn 0;",
        "\tahci_sw_activity(qc->dev->link);\n"
        "\tps4_ahci_probe_arm(qc);\n\n\treturn 0;")
    body = replace_once(body, "\t\tahci_pmp_detach(ap);\n\n\treturn 0;",
        "\t\tahci_pmp_detach(ap);\n\n\tps4_ahci_probe_enable(ap);\n\n\treturn 0;")
    start = body.index("static int ahci_port_suspend(struct ata_port *ap, pm_message_t mesg)\n{")
    body = body[:start] + replace_once(body[start:],
        "\tint rc;\n\n\trc = ahci_deinit_port(ap, &emsg);",
        "\tint rc;\n\n\tps4_ahci_probe_disable(ap, false);\n\n"
        "\trc = ahci_deinit_port(ap, &emsg);")
    body = replace_once(body,
        "\tpp->ap = ap;\n\ttimer_setup(&pp->ps4_poll_timer, ps4_ahci_poll_timer, 0);\n"
        "\tif (ps4_ahci_poll_ms)\n\t\tmod_timer(&pp->ps4_poll_timer,\n"
        "\t\t\t  jiffies + msecs_to_jiffies(ps4_ahci_poll_ms));",
        "\tps4_ahci_probe_init(ap);")
    body = replace_once(body,
        "\tstruct ahci_port_priv *pp = ap->private_data;\n\tint rc;\n\n"
        "\tif (pp)\n\t\ttimer_delete_sync(&pp->ps4_poll_timer);",
        "\tint rc;\n\n\tps4_ahci_probe_disable(ap, true);")
    return {"drivers/ata/ahci.h": header, "drivers/ata/libahci.c": body,
            "drivers/ata/ahci-ps4-probe.h": (HERE / "ahci-ps4-probe.h").read_text()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    records = json.loads((HERE / "BASELINE.json").read_text())["members"]
    for name, digest in records.items():
        if hashlib.sha256((args.source / name).read_bytes()).hexdigest() != digest:
            raise ValueError("baseline changed: " + name)
    result = """From: Meerzulee and Linux psX contributors <17146298+meerzulee@users.noreply.github.com>
Subject: [EXPERIMENT] ata: scope Baikal HDD completion probing and own timer lifetime

Adapt the 2026-10-22 psX command-scoped probe for the exact runtime-dev3
source. Preserve upstream libahci FIS, error and tag-completion semantics.
Restrict probing to Sony 104d:90de at 0000:00:14.7, hardware-local port 0.
Retire the unsafe global legacy timer; its old parameter must be zero.
Invalid profiles refuse probe activation, preserving the normal host path.
Use a read-only boot interval, one lock for issue/IRQ/poll/lifecycle, actual
jiffies deadline, and terminal timer shutdown. Unchanged IRQ routing, PHY,
DMA setup, UVD and ICC. No hardware acceptance is claimed.

Source ancestry: psX workspace command-scoped probe, 2026-10-22, original
author label 'psX Linux workspace <psx@localhost>'; upstream libahci and
community Baikal work retain their original authors and licenses.
---
"""
    for name, new in changed(args.source).items():
        old_path = args.source / name
        old = old_path.read_text() if old_path.exists() else ""
        result += "diff --git a/" + name + " b/" + name + "\n"
        if not old_path.exists():
            result += "new file mode 100644\n"
        result += "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                           fromfile="a/" + name if old else "/dev/null", tofile="b/" + name))
    (HERE / "0001-ahci-baikal-scoped-completion.patch").write_text(result)


if __name__ == "__main__":
    main()
