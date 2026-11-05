/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Experimental Baikal embedded-HDD completion polling.
 * Derived from the psX 2026-10-22 command-scoped probe; see README.md.
 * Included privately by libahci.c. All mutable state is under ap->lock.
 */

static unsigned int ps4_ahci_completion_probe_ms;
module_param_named(ps4_ahci_completion_probe_ms,
		   ps4_ahci_completion_probe_ms, uint, 0444);
MODULE_PARM_DESC(ps4_ahci_completion_probe_ms,
		 "Baikal HDD command completion probe (0=off, 1..1000 ms)");
/* Same storage, shorter spelling for the loader's bounded command line. */
module_param_named(ps4_probe_ms, ps4_ahci_completion_probe_ms, uint, 0444);
MODULE_PARM_DESC(ps4_probe_ms, "Alias for ps4_ahci_completion_probe_ms");

#define PS4_AHCI_PROBE_IDLE_MS 20000
#define PS4_AHCI_PROBE_LOG_MAX 8
#define PS4_AHCI_PROBE_FAULT_MAX 4

static void ahci_port_intr(struct ata_port *ap);

static bool ps4_ahci_probe_target(struct ata_port *ap)
{
	struct pci_dev *pdev;

	if (!IS_ENABLED(CONFIG_X86_PS4) || !dev_is_pci(ap->host->dev))
		return false;
	pdev = to_pci_dev(ap->host->dev);
	return pdev->vendor == PCI_VENDOR_ID_SONY && pdev->device == 0x90de &&
	       pci_domain_nr(pdev->bus) == 0 && pdev->bus->number == 0 &&
	       pdev->devfn == PCI_DEVFN(0x14, 7) && ap->port_no == 0;
}

static int ps4_ahci_probe_validate(struct ata_port *ap)
{
	struct ahci_host_priv *hpriv = ap->host->private_data;

	if (!ps4_ahci_probe_target(ap))
		return 0;
	if (ps4_ahci_poll_ms || ps4_ahci_completion_probe_ms > 1000 ||
	    (ps4_ahci_completion_probe_ms &&
	     (hpriv->flags & AHCI_HFLAG_MULTI_MSI))) {
		ata_port_err(ap,
			     "ps4: probe refused: requires legacy poll=0, interval=0..1000 and single host IRQ\n");
		return -EINVAL;
	}
	return 0;
}

static bool ps4_ahci_probe_can_run(struct ata_port *ap)
{
	struct ahci_port_priv *pp = ap->private_data;

	lockdep_assert_held(ap->lock);
	/* EH_IN_PROGRESS is permitted: initial IDENTIFY is issued by EH. */
	return pp->ps4_probe_enabled && ap->qc_active &&
	       !(ap->pflags & (ATA_PFLAG_FROZEN | ATA_PFLAG_RESETTING |
			       ATA_PFLAG_SUSPENDED | ATA_PFLAG_UNLOADING |
			       ATA_PFLAG_UNLOADED));
}

/* Called after normal libata completion, including the real IRQ path. */
static void ps4_ahci_probe_progress(struct ata_port *ap, u64 before)
{
	struct ahci_port_priv *pp = ap->private_data;
	u64 retired = before & ~ap->qc_active;

	if (!pp->ps4_probe_ap || !retired)
		return;
	lockdep_assert_held(ap->lock);
	pp->ps4_probe_completed += hweight64(retired);
	pp->ps4_probe_deadline = jiffies +
				 msecs_to_jiffies(PS4_AHCI_PROBE_IDLE_MS);
	if (!ap->qc_active) {
		pp->ps4_probe_expired = false;
		timer_delete(&pp->ps4_probe_timer);
	}
}

static void ps4_ahci_probe_arm(struct ata_queued_cmd *qc)
{
	struct ata_port *ap = qc->ap;
	struct ahci_port_priv *pp = ap->private_data;

	if (!pp->ps4_probe_ap)
		return;
	if (!ps4_ahci_probe_can_run(ap))
		return;
	/* qc_active uses the software tag; ATA_TAG_INTERNAL maps to hw tag 0. */
	if (ap->qc_active == BIT_ULL(qc->tag)) {
		pp->ps4_probe_expired = false;
		pp->ps4_probe_deadline = jiffies +
					 msecs_to_jiffies(PS4_AHCI_PROBE_IDLE_MS);
	}
	/* Do not postpone a tick when more NCQ commands join the same burst. */
	if (!pp->ps4_probe_expired && !timer_pending(&pp->ps4_probe_timer))
		mod_timer(&pp->ps4_probe_timer, jiffies + pp->ps4_probe_interval);
}

static void ps4_ahci_probe_timer(struct timer_list *t)
{
	struct ahci_port_priv *pp = timer_container_of(pp, t, ps4_probe_timer);
	struct ata_port *ap = pp->ps4_probe_ap;
	struct ahci_host_priv *hpriv = ap->host->private_data;
	void __iomem *port_mmio = ahci_port_base(ap);
	unsigned long flags;
	u64 before;
	u32 status;

	spin_lock_irqsave(ap->lock, flags);
	if (!ps4_ahci_probe_can_run(ap) || pp->ps4_probe_expired)
		goto out;
	if (time_after_eq(jiffies, pp->ps4_probe_deadline)) {
		pp->ps4_probe_expired = true;
		if (pp->ps4_probe_faults < PS4_AHCI_PROBE_FAULT_MAX) {
			pp->ps4_probe_faults++;
			ata_port_warn(ap,
				"ps4: completion probe stopped after 20s without retired tags; libata timeout/EH retains ownership\n");
		}
		goto out;
	}
	status = readl(port_mmio + PORT_IRQ_STAT);
	if (status) {
		before = pp->ps4_probe_completed;
		/* No completion from time or activity bits alone; use normal FIS/EH. */
		ahci_port_intr(ap);
		if (before != pp->ps4_probe_completed &&
		    pp->ps4_probe_logs < PS4_AHCI_PROBE_LOG_MAX) {
			pp->ps4_probe_logs++;
			ata_port_info(ap,
				"ps4: probe retired %llu tag(s), total=%llu, pxis=0x%x (not proof of IRQ loss)\n",
				(unsigned long long)(pp->ps4_probe_completed - before),
				(unsigned long long)pp->ps4_probe_completed, status);
		}
		/* The summary is W1C. Never acknowledge an unserviced port. */
		if (!readl(port_mmio + PORT_IRQ_STAT))
			writel(BIT(ap->port_no), hpriv->mmio + HOST_IRQ_STAT);
	}
	if (ps4_ahci_probe_can_run(ap))
		mod_timer(&pp->ps4_probe_timer, jiffies + pp->ps4_probe_interval);
out:
	spin_unlock_irqrestore(ap->lock, flags);
}

static void ps4_ahci_probe_init(struct ata_port *ap)
{
	struct ahci_port_priv *pp = ap->private_data;

	if (!ps4_ahci_probe_target(ap))
		return;
	/* Refuse the probe without entering the inherited shared-host failure path. */
	if (ps4_ahci_probe_validate(ap))
		return;
	ata_port_info(ap, "ps4: scoped completion probe=%u ms, legacy poll retired\n",
		      ps4_ahci_completion_probe_ms);
	if (!ps4_ahci_completion_probe_ms)
		return;
	pp->ps4_probe_ap = ap;
	pp->ps4_probe_interval = msecs_to_jiffies(ps4_ahci_completion_probe_ms);
	timer_setup(&pp->ps4_probe_timer, ps4_ahci_probe_timer, 0);
}

static void ps4_ahci_probe_enable(struct ata_port *ap)
{
	struct ahci_port_priv *pp = ap->private_data;
	unsigned long flags;

	/* acard-ahci uses the common resume/stop but has its own port_start. */
	if (!pp || pp->ps4_probe_ap != ap)
		return;
	spin_lock_irqsave(ap->lock, flags);
	if (!pp->ps4_probe_stopped)
		pp->ps4_probe_enabled = true;
	spin_unlock_irqrestore(ap->lock, flags);
}

static void ps4_ahci_probe_disable(struct ata_port *ap, bool shutdown)
{
	struct ahci_port_priv *pp = ap->private_data;
	unsigned long flags;

	if (!pp || pp->ps4_probe_ap != ap)
		return;
	/* Serialize against both issue and the callback before waiting. */
	spin_lock_irqsave(ap->lock, flags);
	pp->ps4_probe_enabled = false;
	if (shutdown)
		pp->ps4_probe_stopped = true;
	spin_unlock_irqrestore(ap->lock, flags);
	if (shutdown)
		timer_shutdown_sync(&pp->ps4_probe_timer);
	else
		timer_delete_sync(&pp->ps4_probe_timer);
}
