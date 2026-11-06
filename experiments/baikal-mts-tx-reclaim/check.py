#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Test actual patched MTS reclaim/reuse functions on CPU-only API fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess

HERE = Path(__file__).resolve().parent
SOURCE = 'drivers/net/ethernet/psx_mts_net.c'
CORE = 'drivers/net/ethernet/mts_net_core.h'
BASE_SHA = 'fa1e0181ca11e9e21fea509b14654c0e37788d537f1664f598dec6dca4bfda79'
RESULT_SHA = '8a1f0b9a351099e1dae454f7d35d84f26abc0784b89fd47c24fe77241b0d45bf'
CORE_SHA = 'd1e138130df0f7d63555704daa61846f478416a6f0e689809ab7fa3ee00a931b'
PATCH_SHA = 'b6fbbc41b295ba259b01e89aa2d7dafdfcc37441b74506cdff30087370400797'
REMOVED = '\t\tWRITE_ONCE(ring[slot].word[0], cpu_to_le32(mts_tx_idle(slot)));\n'
POLICY = {'single_change': 'omit_word0_clear_on_tx_reclaim', 'preserve_returned_word0': True,
          'word2_sentinel_unchanged': True, 'ownership_predicate_unchanged': True,
          'producer_assignment_unchanged': True, 'retained_buffers': True,
          'polling_and_lifecycle_unchanged': True, 'hypothesis_only': True,
          'hardware_acceptance': False}


def sha(raw): return hashlib.sha256(raw).hexdigest()


def whole(raw, name):
    found = re.findall(r'(?ms)^static (?:bool|void|netdev_tx_t) ' + re.escape(name) + r'\([^;{]*\)\n\{.*?^\}\n', raw)
    if len(found) != 1: raise ValueError('Nonunique actual function: ' + name)
    return found[0]


FIXTURE = r'''
#include "mts_net_core.h"
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
typedef int netdev_tx_t;
#define U32_MAX UINT32_MAX
#define ETH_HLEN 14U
#define ETH_FRAME_LEN 1514U
#define ETH_ZLEN 60U
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88a8
#define CHECKSUM_PARTIAL 1
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define MTS_TX_PROGRESS_TIMEOUT 1000U
#define READ_ONCE(x) (x)
#define cpu_to_le32(x) ((u32)(x))
#define le32_to_cpu(x) ((u32)(x))
#define max_t(type, a, b) ((type)(a) > (type)(b) ? (type)(a) : (type)(b))
#define time_after(a,b) ((long)((b) - (a)) < 0)
enum { TX_RING, RX_RING, TX_POOL, RX_POOL, MEMORY_COUNT };
struct mts_desc { u32 word[4]; };
struct mts_memory { void *cpu; uint64_t dma; size_t size; };
struct net_device { bool running, stopped; };
struct sk_buff {
    unsigned int len;
    uint16_t protocol;
    int ip_summed;
    bool vlan, consumed, freed;
    unsigned char data[ETH_FRAME_LEN];
};
struct mts_net {
    struct mts_memory memory[MEMORY_COUNT];
    struct mts_tx_ledger tx;
    struct net_device *ndev;
    bool engine_live, admin_up, link_up, terminal_fault;
    int error, tx_lock, stats_lock;
    unsigned long tx_progress;
    uint64_t tx_completed;
    struct { uint64_t tx_packets, tx_bytes, tx_dropped; } stats;
    unsigned char *mac;
    u32 last_txd0, last_txd2, tx_after_kick;
};
static struct mts_net mts;
static struct net_device device;
static struct mts_desc ring[MTS_SLOTS];
static unsigned char pool[MTS_POOL_BYTES], saved_pool[MTS_POOL_BYTES], mac[0x58];
static unsigned long jiffies = 100;
static unsigned int phase, current_slot, current_length, barriers, status_writes, stop_calls, publish_count;
static uint64_t sent_bytes, completed_bytes, completed_packets;
enum { OTHER, XMIT, RECLAIM };

static void lock(int *value, unsigned long *flags) { assert(!*value); *value = 1; *flags = 0; }
static void unlock(int *value, unsigned long flags) { assert(*value && !flags); *value = 0; }
#define spin_lock_irqsave(value, flags) lock((value), &(flags))
#define spin_unlock_irqrestore(value, flags) unlock((value), (flags))
static bool netif_running(struct net_device *d) { return d->running; }
static bool netif_queue_stopped(struct net_device *d) { return d->stopped; }
static void netif_stop_queue(struct net_device *d) { d->stopped = true; }
static void netif_wake_queue(struct net_device *d) { d->stopped = false; }
static void netif_trans_update(struct net_device *d) { assert(d == &device); }
static void netdev_sent_queue(struct net_device *d, unsigned int bytes)
{ assert(d == &device); sent_bytes += bytes; }
static void netdev_completed_queue(struct net_device *d, unsigned int packets, unsigned int bytes)
{ assert(d == &device); completed_packets += packets; completed_bytes += bytes; }
static void mts_request_stop(struct mts_net *s, int error, const char *why)
{ (void)why; s->error = error; s->terminal_fault = true; ++stop_calls; }
static bool skb_vlan_tag_present(struct sk_buff *skb) { return skb->vlan; }
static int skb_copy_bits(struct sk_buff *skb, unsigned int offset, void *dst, unsigned int length)
{ assert(!offset && length == skb->len); memcpy(dst, skb->data, length); return 0; }
static void dev_consume_skb_any(struct sk_buff *skb) { skb->consumed = true; }
static void dev_kfree_skb_any(struct sk_buff *skb) { skb->freed = true; }
static void dma_wmb(void) { ++barriers; }
static void dma_rmb(void) { assert(phase == RECLAIM); }
static u32 readl(unsigned char *address) { assert(address == mac + 0x34); return 8; }
static void writel(u32 value, unsigned char *address)
{ assert(address == mac + 0x34 && value == 12); }
static void write_once(u32 *address, u32 value)
{
    unsigned int i;
    assert(mts.tx_lock);
    for (i = 0; i < MTS_SLOTS; ++i) {
        if (address != &ring[i].word[0]) continue;
        ++status_writes;
        if (phase == XMIT) {
            assert(i == current_slot);
            if (value & MTS_COWN) {
                /* Assignment discards every old length/status bit. */
                assert(value == mts_tx_ready(i, current_length));
                assert(!barriers);
            } else {
                assert(value == (mts_tx_ready(i, current_length) & ~MTS_COWN));
                assert(*address == mts_tx_ready(i, current_length) && barriers == 1);
                assert(ring[i].word[1] == mts.memory[TX_POOL].dma + i * MTS_BUFFER_BYTES);
                assert(!ring[i].word[2] && !ring[i].word[3]);
                assert(mts.tx.submitted[i] && sent_bytes >= current_length);
                ++publish_count;
            }
        }
    }
    *address = value;
}
#define WRITE_ONCE(address, value) write_once(&(address), (value))
@FUNCTIONS@

static void reset(void)
{
    unsigned int i;
    memset(&mts, 0, sizeof(mts)); memset(&device, 0, sizeof(device));
    memset(ring, 0, sizeof(ring)); memset(pool, 0xa5, sizeof(pool));
    mts.memory[TX_RING].cpu = (unsigned char *)ring;
    mts.memory[TX_RING].dma = 0x100000;
    mts.memory[TX_RING].size = sizeof(ring);
    mts.memory[TX_POOL].cpu = pool;
    mts.memory[TX_POOL].dma = 0x200000;
    mts.memory[TX_POOL].size = sizeof(pool);
    mts.ndev = &device; mts.mac = mac;
    mts.engine_live = mts.admin_up = mts.link_up = device.running = true;
    for (i = 0; i < MTS_SLOTS; ++i) {
        ring[i].word[0] = mts_tx_idle(i);
        ring[i].word[2] = MTS_TX_SENTINEL;
    }
    phase = OTHER; barriers = status_writes = stop_calls = publish_count = 0;
    sent_bytes = completed_bytes = completed_packets = 0;
}

static void submit(unsigned int length, unsigned char pattern)
{
    struct sk_buff skb = { .len = length, .protocol = htons(0x0800) };
    unsigned int i;
    memset(skb.data, pattern, length);
    current_slot = mts.tx.head; current_length = length < ETH_ZLEN ? ETH_ZLEN : length;
    phase = XMIT; barriers = status_writes = 0;
    assert(mts_xmit(&skb, &device) == NETDEV_TX_OK);
    assert(skb.consumed && !skb.freed && !stop_calls);
    assert(status_writes == 2 && barriers == 1);
    assert(!memcmp(pool + current_slot * MTS_BUFFER_BYTES, skb.data, length));
    for (i = length; i < current_length; ++i) assert(pool[current_slot * MTS_BUFFER_BYTES + i] == 0);
}

static void reclaim(void)
{
    unsigned char *old_pool = mts.memory[TX_POOL].cpu;
    unsigned char *old_ring = mts.memory[TX_RING].cpu;
    memcpy(saved_pool, pool, sizeof(pool));
    phase = RECLAIM; status_writes = 0;
    mts_tx_reclaim(&mts);
    assert(status_writes == 0); /* Old unpatched source must fail here. */
    assert(!memcmp(saved_pool, pool, sizeof(pool)));
    assert(mts.memory[TX_POOL].cpu == old_pool && mts.memory[TX_RING].cpu == old_ring);
    assert(!stop_calls);
}

static void one_reclaim_and_reuse(unsigned int slot)
{
    u32 completed;
    reset(); mts.tx.head = mts.tx.tail = slot;
    submit(1514, 0x31);
    completed = mts_tx_ready(slot, 1514) | 0x00400000U;
    ring[slot].word[0] = completed; /* Synthetic device ownership return. */
    ring[slot].word[2] = 0x1234; ring[slot].word[3] = 0x5678;
    reclaim();
    assert(ring[slot].word[0] == completed);
    assert(ring[slot].word[1] == mts.memory[TX_POOL].dma + slot * MTS_BUFFER_BYTES);
    assert(ring[slot].word[2] == MTS_TX_SENTINEL && ring[slot].word[3] == 0x5678);
    assert(!mts.tx.count && !mts.tx.submitted[slot] && mts.tx.tail == mts_next(slot));
    assert(completed_packets == 1 && completed_bytes == 1514 && sent_bytes == 1514);
    reclaim(); assert(completed_packets == 1); /* No double retirement. */
    /* Steer an empty fixture queue to that stale slot; producer is unmodified. */
    mts.tx.head = mts.tx.tail = slot;
    submit(42, 0x72);
    assert(ring[slot].word[0] == (mts_tx_ready(slot, 60) & ~MTS_COWN));
    assert(!(ring[slot].word[0] & 0x00400000U));
    ring[slot].word[0] |= MTS_COWN;
    reclaim(); assert(sent_bytes == completed_bytes && completed_packets == 2);
}

static void refusals_and_full_ring(void)
{
    struct sk_buff extra = { .len = 60, .protocol = htons(0x0800) };
    unsigned int i;
    reset(); submit(74, 0x52);
    reclaim(); assert(mts.tx.count == 1 && !completed_packets); /* Hardware still owns it. */
    ring[0].word[0] |= MTS_COWN; ring[0].word[2] = MTS_TX_SENTINEL;
    reclaim(); assert(mts.tx.count == 1 && !completed_packets); /* Idle sentinel refuses retirement. */
    ring[0].word[2] = 0; reclaim(); assert(completed_packets == 1);
    reset();
    for (i = 0; i < MTS_SLOTS; ++i) submit(60 + i, (unsigned char)i);
    assert(mts.tx.count == MTS_SLOTS && device.stopped);
    phase = XMIT; status_writes = 0;
    assert(mts_xmit(&extra, &device) == NETDEV_TX_BUSY && !extra.consumed && !extra.freed);
    assert(!status_writes);
    for (i = 0; i < MTS_SLOTS; ++i) ring[i].word[0] |= MTS_COWN;
    reclaim();
    assert(!mts.tx.count && !device.stopped && completed_packets == MTS_SLOTS);
    assert(sent_bytes == completed_bytes);
    for (i = 0; i < MTS_SLOTS; ++i) assert(ring[i].word[0] == mts_tx_ready(i, 60 + i));
}

static void repeated_wraps(void)
{
    unsigned int i;
    reset();
    for (i = 0; i < MTS_SLOTS * 3 + 3; ++i) {
        unsigned int slot = mts.tx.head;
        unsigned int length = 14 + (i * 37) % (ETH_FRAME_LEN - 13);
        submit(length, (unsigned char)i);
        ring[slot].word[0] |= MTS_COWN;
        reclaim();
        assert(ring[slot].word[0] == mts_tx_ready(slot, length < 60 ? 60 : length));
        assert(mts.tx.head == mts.tx.tail && !mts.tx.count);
        assert(sent_bytes == completed_bytes);
    }
    assert(completed_packets == MTS_SLOTS * 3 + 3 && publish_count == completed_packets);
}

int main(void)
{
    one_reclaim_and_reuse(0); one_reclaim_and_reuse(255);
    refusals_and_full_ring(); repeated_wraps();
    puts("PASS actual MTS reclaim/reuse: words retained, complete OWN-last reassignment, sentinel/full-ring guards, three wraps");
    return 0;
}
'''


def no_core_dump(): resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cc', default=os.environ.get('CC', 'cc'))
    args = parser.parse_args()
    base = args.kernel_source.resolve(strict=True); output = args.output.absolute()
    if output.parent.resolve(strict=True) != output.parent or output.is_relative_to(base) or base.is_relative_to(output):
        raise ValueError('NEW canonical output outside preserved source required')
    output.mkdir(mode=0o700, exist_ok=False)
    report = {'status': 'FAIL', 'hardware_access': False, 'hardware_acceptance': False,
              'scope': 'actual-source CPU/API control flow only, no DMA timing or physical completion proof',
              'mts_tx_reclaim_experiment': POLICY, 'checks': []}
    try:
        original = (base / SOURCE).read_bytes(); core = (base / CORE).read_bytes()
        patch = (HERE / 'terminal-reclaim.patch').read_bytes()
        if sha(original) != BASE_SHA or sha(core) != CORE_SHA or sha(patch) != PATCH_SHA:
            raise ValueError('Frozen source/core/patch differs')
        report.update(base_source_sha256=BASE_SHA, core_sha256=CORE_SHA, patch_sha256=PATCH_SHA,
                      checker_sha256=sha(Path(__file__).read_bytes()))
        staged = output / 'source'
        path = staged / SOURCE; path.parent.mkdir(parents=True); path.write_bytes(original)
        patch_path = output / 'terminal-reclaim.patch'; patch_path.write_bytes(patch)
        for flags in (['--check'], ['--whitespace=error']):
            run = subprocess.run(['git', 'apply', *flags, str(patch_path)], cwd=staged, capture_output=True, timeout=30)
            report['checks'].append({'command': ['git', 'apply', *flags], 'returncode': run.returncode,
                                     'stderr': run.stderr.decode(errors='replace')})
            if run.returncode: raise ValueError('Patch does not apply exactly')
        result = path.read_bytes()
        if original.count(REMOVED.encode()) != 1 or result != original.replace(REMOVED.encode(), b'', 1) or sha(result) != RESULT_SHA:
            raise ValueError('Delta exceeds the one allowed word0-write deletion')
        (output / 'mts_net_core.h').write_bytes(core)
        for kind, raw in (('candidate', result), ('negative-control', original)):
            functions = '\n'.join(whole(raw.decode(), name) for name in ('mts_tx_allowed', 'mts_tx_reclaim', 'mts_xmit'))
            source = output / (kind + '.c'); source.write_text(FIXTURE.replace('@FUNCTIONS@', functions))
            binary = output / kind
            command = [args.cc, '-std=gnu11', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                       '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer',
                       str(source), '-o', str(binary)]
            compiled = subprocess.run(command, capture_output=True, timeout=60)
            (output / (kind + '.compile.log')).write_bytes(compiled.stdout + compiled.stderr)
            if compiled.returncode: raise ValueError('Fixture compilation failed: ' + kind)
            ran = subprocess.run([str(binary)], capture_output=True, timeout=15, preexec_fn=no_core_dump)
            (output / (kind + '.run.log')).write_bytes(ran.stdout + ran.stderr)
            report['checks'].append({'kind': kind, 'returncode': ran.returncode, 'actual_functions_sha256': sha(functions.encode()),
                                     'stdout': ran.stdout.decode(errors='replace'), 'stderr': ran.stderr.decode(errors='replace')[:1200]})
            if kind == 'candidate' and (ran.returncode or not ran.stdout.startswith(b'PASS actual MTS reclaim/reuse:')):
                raise ValueError('Candidate control-flow regression')
            if kind == 'negative-control' and (ran.returncode == 0 or b'status_writes == 0' not in ran.stderr):
                raise ValueError('Old source did not fail the precise preservation assertion')
        if (base / SOURCE).read_bytes() != original or (base / CORE).read_bytes() != core:
            raise ValueError('Preserved input changed')
        report.update(status='PASS_CPU_MTS_RECLAIM_REUSE', result_source_sha256=RESULT_SHA)
    except Exception as error:
        report['error'] = str(error)
    (output / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS_CPU_MTS_RECLAIM_REUSE' else 1


if __name__ == '__main__': raise SystemExit(main())
