/* SPDX-License-Identifier: GPL-2.0-only */
/* Executes the exact candidate header extracted from its versioned patch. */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint32_t u32;
typedef uint64_t u64;
#define __iomem
#define BIT(n) (1U << (n))
#define BIT_ULL(n) (1ULL << (n))
#define IS_ENABLED(x) (x)
#define CONFIG_X86_PS4 1
#define PCI_VENDOR_ID_SONY 0x104d
#define PCI_DEVFN(d, f) (((d) << 3) | (f))
#define AHCI_HFLAG_MULTI_MSI BIT(0)
#define ATA_PFLAG_FROZEN BIT(2)
#define ATA_PFLAG_RESETTING BIT(8)
#define ATA_PFLAG_UNLOADING BIT(9)
#define ATA_PFLAG_UNLOADED BIT(10)
#define ATA_PFLAG_SUSPENDED BIT(17)
#define PORT_IRQ_STAT 0x10
#define HOST_IRQ_STAT 0x08
#define module_param_named(a, b, c, d)
#define MODULE_PARM_DESC(a, b)
#define time_after_eq(a, b) ((long)((a) - (b)) >= 0)
#define msecs_to_jiffies(ms) (((unsigned long)(ms) * FIXTURE_HZ + 999) / 1000)
#define hweight64(x) __builtin_popcountll(x)

struct lock { bool held; };
struct device { bool pci; };
struct pci_bus { unsigned int number, domain; };
struct pci_dev {
    struct device dev;
    unsigned int vendor, device, devfn;
    struct pci_bus *bus;
};
#define dev_is_pci(d) ((d)->pci)
#define to_pci_dev(d) ((struct pci_dev *)(d))
#define pci_domain_nr(b) ((b)->domain)
struct timer_list {
    void (*function)(struct timer_list *);
    bool pending, stopped, initialized;
    unsigned long expires;
};
struct ahci_host_priv { unsigned int flags; void *mmio; };
struct ata_host { struct device *dev; void *private_data; };
struct ata_port {
    struct ata_host *host;
    struct lock *lock;
    void *private_data;
    unsigned int port_no, pflags;
    u64 qc_active;
    void *mmio;
};
struct ata_queued_cmd { struct ata_port *ap; unsigned int tag, hw_tag; };
struct ahci_port_priv {
#include "probe-fields.h"
};

#define timer_container_of(v, ptr, member) \
    ((struct ahci_port_priv *)((char *)(ptr) - __builtin_offsetof(struct ahci_port_priv, member)))
#define spin_lock_irqsave(l, f) do { assert(!(l)->held); (l)->held = true; (f) = 0; } while (0)
#define spin_unlock_irqrestore(l, f) do { (void)(f); assert((l)->held); (l)->held = false; } while (0)
#define lockdep_assert_held(l) assert((l)->held)
#define ahci_port_base(ap) ((ap)->mmio)

static struct lock guard;
static unsigned long jiffies;
static unsigned int ps4_ahci_poll_ms;
static unsigned int mmio_reads, mmio_writes, handler_runs, infos, warnings, errors;
static unsigned int timer_updates, timer_syncs;
static bool inject_callback_on_sync, freeze_in_handler;
static u32 event_after_handler;
static u64 retire_in_handler;

#define ata_port_info(ap, ...) do { (void)(ap); infos++; } while (0)
#define ata_port_warn(ap, ...) do { (void)(ap); warnings++; } while (0)
#define ata_port_err(ap, ...) do { (void)(ap); errors++; } while (0)
static u32 readl(void *p)
{
    assert(guard.held);
    mmio_reads++;
    return *(u32 *)p;
}
static void writel(u32 value, void *p)
{
    assert(guard.held);
    mmio_writes++;
    *(u32 *)p = value;
}
static void timer_setup(struct timer_list *t, void (*f)(struct timer_list *), int flags)
{
    assert(!flags);
    t->function = f;
    t->initialized = true;
}
static bool timer_pending(struct timer_list *t)
{
    assert(t->initialized && guard.held);
    return t->pending;
}
static void mod_timer(struct timer_list *t, unsigned long expires)
{
    assert(t->initialized && guard.held && !t->stopped);
    t->pending = true;
    t->expires = expires;
    timer_updates++;
}
static void timer_delete(struct timer_list *t)
{
    assert(t->initialized && guard.held);
    t->pending = false;
}
static void timer_delete_sync(struct timer_list *t)
{
    assert(t->initialized && !guard.held);
    if (inject_callback_on_sync)
        t->function(t);
    t->pending = false;
    timer_syncs++;
}
static void timer_shutdown_sync(struct timer_list *t)
{
    timer_delete_sync(t);
    t->stopped = true;
}

#include "ahci-ps4-probe.h"

/* Hardware/libata stand-in: retirement is supplied explicitly by each case. */
static void ahci_port_intr(struct ata_port *ap)
{
    u64 before = ap->qc_active;
    lockdep_assert_held(ap->lock);
    assert(*(u32 *)((char *)ap->mmio + PORT_IRQ_STAT));
    handler_runs++;
    *(u32 *)((char *)ap->mmio + PORT_IRQ_STAT) = event_after_handler;
    ap->qc_active &= ~retire_in_handler;
    ps4_ahci_probe_progress(ap, before);
    if (freeze_in_handler)
        ap->pflags |= ATA_PFLAG_FROZEN;
}

static struct pci_bus bus;
static struct pci_dev pci;
static struct ahci_host_priv hp;
static struct ata_host host;
static struct ata_port ap;
static struct ahci_port_priv pp;
static u32 host_regs[64], port_regs[64];

static void setup(void)
{
    memset(&guard, 0, sizeof guard);
    memset(&bus, 0, sizeof bus);
    memset(&pp, 0, sizeof pp);
    memset(host_regs, 0, sizeof host_regs);
    memset(port_regs, 0, sizeof port_regs);
    pci = (struct pci_dev){ .dev = { .pci = true }, .vendor = 0x104d,
          .device = 0x90de, .devfn = PCI_DEVFN(0x14, 7), .bus = &bus };
    hp = (struct ahci_host_priv){ .mmio = host_regs };
    host = (struct ata_host){ .dev = &pci.dev, .private_data = &hp };
    ap = (struct ata_port){ .host = &host, .lock = &guard,
         .private_data = &pp, .mmio = port_regs };
    jiffies = 100;
    ps4_ahci_poll_ms = 0;
    ps4_ahci_completion_probe_ms = 5;
    mmio_reads = mmio_writes = handler_runs = infos = warnings = errors = 0;
    timer_updates = timer_syncs = 0;
    inject_callback_on_sync = freeze_in_handler = false;
    event_after_handler = 0;
    retire_in_handler = 0;
}

static void start(void)
{
    assert(!ps4_ahci_probe_validate(&ap));
    ps4_ahci_probe_init(&ap);
    ps4_ahci_probe_enable(&ap);
    assert(pp.ps4_probe_enabled && !pp.ps4_probe_timer.pending);
}
static void issue(unsigned int tag)
{
    unsigned long f;
    struct ata_queued_cmd qc = { .ap = &ap, .tag = tag, .hw_tag = tag == 32 ? 0 : tag };
    spin_lock_irqsave(ap.lock, f);
    ap.qc_active |= BIT_ULL(tag);
    ps4_ahci_probe_arm(&qc);
    spin_unlock_irqrestore(ap.lock, f);
}
static void tick(void)
{
    assert(pp.ps4_probe_timer.pending);
    pp.ps4_probe_timer.pending = false;
    pp.ps4_probe_timer.function(&pp.ps4_probe_timer);
}
static void irq_retire(u64 tags)
{
    unsigned long f;
    u64 before = ap.qc_active;
    spin_lock_irqsave(ap.lock, f);
    ap.qc_active &= ~tags;
    ps4_ahci_probe_progress(&ap, before);
    spin_unlock_irqrestore(ap.lock, f);
}

static void test_identity_and_configuration(void)
{
    setup(); assert(ps4_ahci_probe_target(&ap));
    pci.device = 0x90d9; assert(!ps4_ahci_probe_target(&ap));
    pci.device = 0x90de; pci.vendor = 0x8086; assert(!ps4_ahci_probe_target(&ap));
    pci.vendor = 0x104d; pci.devfn--; assert(!ps4_ahci_probe_target(&ap));
    pci.devfn++; bus.domain++; assert(!ps4_ahci_probe_target(&ap));
    bus.domain--; bus.number++; assert(!ps4_ahci_probe_target(&ap));
    bus.number--; ap.port_no++; assert(!ps4_ahci_probe_target(&ap));
    ap.port_no--; pci.dev.pci = false; assert(!ps4_ahci_probe_target(&ap));
    ps4_ahci_probe_init(&ap); ps4_ahci_probe_enable(&ap);
    ps4_ahci_probe_disable(&ap, true); assert(!pp.ps4_probe_timer.initialized);
    setup(); ps4_ahci_completion_probe_ms = 1001;
    assert(ps4_ahci_probe_validate(&ap) == -EINVAL);
    ps4_ahci_probe_init(&ap); ps4_ahci_probe_enable(&ap);
    assert(!pp.ps4_probe_ap && !pp.ps4_probe_timer.initialized);
    ps4_ahci_completion_probe_ms = 1000; assert(!ps4_ahci_probe_validate(&ap));
    ps4_ahci_poll_ms = 1; assert(ps4_ahci_probe_validate(&ap) == -EINVAL);
    ps4_ahci_poll_ms = 0; hp.flags = AHCI_HFLAG_MULTI_MSI;
    assert(ps4_ahci_probe_validate(&ap) == -EINVAL);
    ps4_ahci_completion_probe_ms = 0; assert(!ps4_ahci_probe_validate(&ap));
    ps4_ahci_probe_init(&ap); ps4_ahci_probe_enable(&ap);
    ps4_ahci_probe_disable(&ap, true); assert(!pp.ps4_probe_timer.initialized);
    setup(); ap.private_data = NULL; ps4_ahci_probe_enable(&ap);
    ps4_ahci_probe_disable(&ap, true); assert(!timer_syncs);
}

static void test_completion_contract(void)
{
    unsigned long first_tick, first_deadline;
    setup(); start(); issue(32); /* IDENTIFY software tag 32, hardware tag 0 */
    assert(pp.ps4_probe_timer.pending);
    first_tick = pp.ps4_probe_timer.expires;
    jiffies += 1;
    issue(3); assert(pp.ps4_probe_timer.expires == first_tick);
    tick(); assert(!handler_runs && ap.qc_active == (BIT_ULL(32) | BIT_ULL(3)));
    assert(pp.ps4_probe_timer.pending); /* PxIS=0 cannot retire anything. */
    port_regs[PORT_IRQ_STAT / 4] = 1;
    retire_in_handler = BIT_ULL(32);
    tick(); assert(handler_runs == 1 && ap.qc_active == BIT_ULL(3));
    assert(pp.ps4_probe_completed == 1 && host_regs[HOST_IRQ_STAT / 4] == 1);
    first_deadline = pp.ps4_probe_deadline;
    jiffies += 5 * FIXTURE_HZ;
    issue(7); irq_retire(BIT_ULL(3));
    assert(pp.ps4_probe_deadline > first_deadline); /* Real IRQ makes progress. */
    port_regs[PORT_IRQ_STAT / 4] = 8; event_after_handler = 2;
    host_regs[HOST_IRQ_STAT / 4] = 0;
    retire_in_handler = BIT_ULL(7);
    tick(); assert(!ap.qc_active && !pp.ps4_probe_timer.pending);
    assert(host_regs[HOST_IRQ_STAT / 4] == 0); /* New port event is not acked. */
    assert(pp.ps4_probe_completed == 3);
}

static void test_lifecycle_and_errors(void)
{
    const unsigned int blocked[] = { ATA_PFLAG_FROZEN, ATA_PFLAG_RESETTING,
        ATA_PFLAG_SUSPENDED, ATA_PFLAG_UNLOADING, ATA_PFLAG_UNLOADED };
    for (unsigned int i = 0; i < sizeof blocked / sizeof blocked[0]; i++) {
        setup(); start(); issue(0); ap.pflags = blocked[i]; tick();
        assert(!mmio_reads && !mmio_writes && !handler_runs && !pp.ps4_probe_timer.pending);
    }
    setup(); start(); issue(0); port_regs[PORT_IRQ_STAT / 4] = 0x40000000;
    freeze_in_handler = true; tick();
    assert(handler_runs == 1 && !pp.ps4_probe_timer.pending && ap.qc_active == 1);
    setup(); start(); issue(0); inject_callback_on_sync = true;
    ps4_ahci_probe_disable(&ap, false);
    assert(!mmio_reads && !pp.ps4_probe_timer.pending && !pp.ps4_probe_enabled);
    issue(1); assert(!pp.ps4_probe_timer.pending);
    ap.qc_active = 0; ps4_ahci_probe_enable(&ap); issue(0);
    assert(pp.ps4_probe_timer.pending);
    ps4_ahci_probe_disable(&ap, true);
    ps4_ahci_probe_enable(&ap); issue(1);
    assert(!pp.ps4_probe_enabled && !pp.ps4_probe_timer.pending && pp.ps4_probe_timer.stopped);
}

static void test_real_deadline_and_bounds(void)
{
    setup(); start(); issue(0);
    jiffies = pp.ps4_probe_deadline; tick();
    assert(pp.ps4_probe_expired && !pp.ps4_probe_timer.pending && !mmio_reads && warnings == 1);
    issue(1); assert(!pp.ps4_probe_timer.pending); /* No new budget around a stuck qc. */
    irq_retire(3); issue(0); assert(pp.ps4_probe_timer.pending && !pp.ps4_probe_expired);
    setup(); jiffies = ULONG_MAX - 2 * FIXTURE_HZ; start(); issue(0);
    tick(); assert(pp.ps4_probe_timer.pending && !pp.ps4_probe_expired);
    jiffies += 20 * FIXTURE_HZ; tick(); assert(pp.ps4_probe_expired);
    setup(); start();
    for (unsigned int i = 0; i < 32; i++) {
        issue(0); port_regs[PORT_IRQ_STAT / 4] = 1; retire_in_handler = 1; tick();
    }
    assert(pp.ps4_probe_logs == 8 && infos == 9); /* One init + eight retirement logs. */
    for (unsigned int i = 0; i < 16; i++) {
        issue(0); jiffies = pp.ps4_probe_deadline; tick(); irq_retire(1);
    }
    assert(pp.ps4_probe_faults == 4 && warnings == 4);
    setup(); ps4_ahci_completion_probe_ms = 1; start(); issue(0);
    assert(pp.ps4_probe_interval >= 1); /* Never a zero-delay spin at low HZ. */
}

int main(void)
{
    test_identity_and_configuration();
    test_completion_contract();
    test_lifecycle_and_errors();
    test_real_deadline_and_bounds();
    printf("PASS: actual patched probe fixture, HZ=%d\n", FIXTURE_HZ);
    return 0;
}
