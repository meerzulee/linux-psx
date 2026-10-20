#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate the default-off runtime delta on the frozen frame-dev1 source.

Input snapshots and worker hooks are verified/applied before parent edits.
This creates source and a patch only; it never installs or operates hardware.
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PREFIX = Path('drivers/gpu/drm/amd/amdgpu')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('nonunique anchor: ' + repr(old[:100]))
    return text.replace(old, new, 1)


def in_function(text, name, old, new):
    matches = list(re.finditer(r'\b' + re.escape(name) + r'\([^();{}]*\)\n\{', text))
    if len(matches) != 1:
        raise ValueError('nonunique function: ' + name)
    start = matches[0].end()
    depth, end = 1, start
    # Function braces in these frozen functions have balanced comments/strings.
    while depth:
        if text[end] == '{':
            depth += 1
        elif text[end] == '}':
            depth -= 1
        end += 1
    return text[:start] + once(text[start:end], old, new) + text[end:]


def transform(source):
    out = dict(source)
    def edit(name, old, new):
        out[name] = once(out[name].decode(), old, new).encode()
    def func(name, function, old, new):
        out[name] = in_function(out[name].decode(), function, old, new).encode()

    edit('amdgpu.h', '#include "amdgpu_ps4_uvd_frame.h"\n',
         '#include "amdgpu_ps4_uvd_frame.h"\n#include "amdgpu_ps4_uvd_runtime.h"\n')
    edit('amdgpu.h', '\tstruct amdgpu_ps4_uvd_frame_operation ps4_uvd_frame_operation;\n',
         '\tstruct amdgpu_ps4_uvd_frame_operation ps4_uvd_frame_operation;\n'
         '\tstruct amdgpu_ps4_uvd_runtime ps4_uvd_runtime;\n')
    edit('Makefile', 'amdgpu-y += amdgpu_ps4_uvd_irq.o\n',
         'amdgpu-y += amdgpu_ps4_uvd_irq.o\n'
         'amdgpu-y += amdgpu_ps4_uvd_runtime.o amdgpu_ps4_uvd_memory.o amdgpu_ps4_uvd_jobs.o\n')
    out['amdgpu_ps4_uvd_ready.c'] += b'\n' + (HERE / 'startup.c.inc').read_bytes()

    # Leave the old boot-only leaves dormant while the runtime owns VMID4.
    edit('amdgpu_ps4_map_canary.c',
         'bool amdgpu_ps4_map_canary_enabled(struct amdgpu_device *adev)',
         'bool amdgpu_ps4_uvd_runtime_legacy_conflict(void)\n{\n'
         '\treturn ps4_map_canary || ps4_uvd_live_prepare;\n}\n\n'
         'bool amdgpu_ps4_map_canary_enabled(struct amdgpu_device *adev)')
    func('amdgpu_ps4_map_canary.c', 'amdgpu_ps4_map_canary_enabled',
         '\n\treturn (ps4_map_canary',
         '\n\tif (amdgpu_ps4_uvd_runtime_enabled(adev))\n\t\treturn false;\n'
         '\treturn (ps4_map_canary')
    edit('amdgpu_ids.c',
         '\t\t\tif (amdgpu_ps4_map_canary_reserve(adev, i, j)) {',
         '\t\t\tif (amdgpu_ps4_uvd_runtime_reserve(adev, i, j)) {\n'
         '\t\t\t\tINIT_LIST_HEAD(&id_mgr->ids[j].list);\n'
         '\t\t\t\tcontinue;\n\t\t\t}\n'
         '\t\t\tif (amdgpu_ps4_map_canary_reserve(adev, i, j)) {')

    # Only one capability is exposed by this initial development backend.
    codecs = '''static const struct amdgpu_video_codec_info ps4_uvd_decode_array[] = {
	{ .codec_type = AMDGPU_INFO_VIDEO_CAPS_CODEC_IDX_MPEG4_AVC,
	  .max_width = 1920, .max_height = 1088,
	  .max_pixels_per_frame = 1920 * 1088, .max_level = 41 },
};
static const struct amdgpu_video_codecs ps4_uvd_decode = {
	.codec_count = ARRAY_SIZE(ps4_uvd_decode_array),
	.codec_array = ps4_uvd_decode_array,
};
static const struct amdgpu_video_codecs ps4_uvd_no_encode;

'''
    edit('cik.c', 'static int cik_query_video_codecs(', codecs + 'static int cik_query_video_codecs(')
    func('cik.c', 'cik_query_video_codecs', '\n\tswitch (adev->asic_type) {',
         '\n\tif (amdgpu_ps4_uvd_runtime_enabled(adev)) {\n'
         '\t\t*codecs = encode ? &ps4_uvd_no_encode : &ps4_uvd_decode;\n'
         '\t\treturn 0;\n\t}\n\tswitch (adev->asic_type) {')
    text = out['cik.c'].decode()
    start = text.index('\n\tcase CHIP_LIVERPOOL:', text.index('int cik_set_ip_blocks'))
    end = text.index('\n\tcase CHIP_GLADIUS:', start)
    block = once(text[start:end],
                 '\t\t/*amdgpu_device_ip_block_add(adev, &uvd_v4_2_ip_block)*/;',
                 '\t\tif (amdgpu_ps4_uvd_runtime_enabled(adev))\n'
                 '\t\t\tamdgpu_device_ip_block_add(adev, &ps4_uvd_ip_block);')
    out['cik.c'] = (text[:start] + block + text[end:]).encode()

    func('amdgpu_uvd.c', 'amdgpu_uvd_sw_init',
         '\tcase CHIP_BONAIRE:\n',
         '\tcase CHIP_LIVERPOOL:\n'
         '\t\tif (!amdgpu_ps4_uvd_runtime_enabled(adev))\n\t\t\treturn -EINVAL;\n'
         '\t\tfw_name = "amdgpu/liverpool_uvd.bin";\n\t\tbreak;\n'
         '\tcase CHIP_BONAIRE:\n')
    func('amdgpu_uvd.c', 'amdgpu_uvd_sw_init',
         '\tfor (j = 0; j < adev->uvd.num_uvd_inst; j++) {',
         '\tif (amdgpu_ps4_uvd_runtime_enabled(adev)) {\n'
         '\t\t/* Proven Liverpool transport accepts native addresses above 4GiB. */\n'
         '\t\tadev->uvd.address_64_bit = true;\n'
         '\t\tadev->uvd.max_handles = 1;\n'
         '\t\tbo_size = AMDGPU_PS4_UVD_BACKING_BYTES;\n'
         '\t\tvcpu_bo_domain = AMDGPU_GEM_DOMAIN_GTT;\n\t}\n\n'
         '\tfor (j = 0; j < adev->uvd.num_uvd_inst; j++) {')
    func('amdgpu_uvd.c', 'amdgpu_uvd_cs_msg_decode',
         '\n\t/* Reject invalid dimensions */',
         '\n\tif (amdgpu_ps4_uvd_runtime_enabled(adev) &&\n'
         '\t    (stream_type != 0 || width > 1920 || height > 1088))\n'
         '\t\treturn -EOPNOTSUPP;\n\n\t/* Reject invalid dimensions */')
    func('amdgpu_uvd.c', 'amdgpu_uvd_cs_msg',
         '\n\tswitch (msg_type) {',
         '\n\tif (amdgpu_ps4_uvd_runtime_enabled(adev) && msg_type == 0 &&\n'
         '\t    (msg[4] != 0 || msg[7] < 16 || msg[8] < 16 ||\n'
         '\t     msg[7] > 1920 || msg[8] > 1088)) {\n'
         '\t\tamdgpu_bo_kunmap(bo);\n\t\treturn -EOPNOTSUPP;\n\t}\n'
         '\n\tswitch (msg_type) {')
    func('amdgpu_uvd.c', 'amdgpu_uvd_cs_msg',
         '\n\tswitch (msg_type) {',
         '\n\tif (amdgpu_ps4_uvd_runtime_enabled(adev) && msg_type == 2 &&\n'
         '\t    (atomic_read(&adev->uvd.handles[0]) != handle ||\n'
         '\t     adev->uvd.filp[0] != ctx->parser->filp)) {\n'
         '\t\tamdgpu_bo_kunmap(bo);\n\t\treturn -EPERM;\n\t}\n'
         '\n\tswitch (msg_type) {')

    # The stock parser mutates handle ownership before submit. Serialize this
    # singleton and close the queue on any later error; no speculative handle
    # rollback or cross-file DESTROY can reach firmware in this first backend.
    func('amdgpu_cs.c', 'amdgpu_cs_ioctl', '\n\tunsigned int i;\n',
         '\n\tunsigned int i;\n\tbool ps4_uvd_session_locked = false;\n')
    func('amdgpu_cs.c', 'amdgpu_cs_ioctl', '\n\tr = amdgpu_cs_patch_jobs(&parser);',
         '\n\tif (parser.gang_size == 1 &&\n'
         '\t    amdgpu_ps4_uvd_runtime_ring(amdgpu_job_ring(parser.jobs[0]))) {\n'
         '\t\tif (!mutex_trylock(&adev->ps4_uvd_runtime.session_lock)) {\n'
         '\t\t\tr = -EBUSY;\n\t\t\tgoto error_backoff;\n\t\t}\n'
         '\t\tps4_uvd_session_locked = true;\n'
         '\t\tif (atomic_read(&adev->ps4_uvd_runtime.failed)) {\n'
         '\t\t\tr = -ESHUTDOWN;\n\t\t\tgoto error_backoff;\n\t\t}\n\t}\n'
         '\n\tr = amdgpu_cs_patch_jobs(&parser);')
    func('amdgpu_cs.c', 'amdgpu_cs_ioctl',
         '\n\tamdgpu_cs_parser_fini(&parser);\n\treturn 0;',
         '\n\tif (ps4_uvd_session_locked)\n'
         '\t\tmutex_unlock(&adev->ps4_uvd_runtime.session_lock);\n'
         '\n\tamdgpu_cs_parser_fini(&parser);\n\treturn 0;')
    func('amdgpu_cs.c', 'amdgpu_cs_ioctl', '\nerror_backoff:\n',
         '\nerror_backoff:\n\tif (ps4_uvd_session_locked)\n'
         '\t\tamdgpu_ps4_uvd_runtime_fault(adev, r);\n')
    func('amdgpu_cs.c', 'amdgpu_cs_ioctl',
         '\n\tmutex_unlock(&parser.bo_list->bo_list_mutex);',
         '\n\tif (ps4_uvd_session_locked)\n'
         '\t\tmutex_unlock(&adev->ps4_uvd_runtime.session_lock);\n'
         '\n\tmutex_unlock(&parser.bo_list->bo_list_mutex);')
    func('amdgpu_job.c', 'amdgpu_job_run',
         '\n\tif (finished->error < 0) {',
         '\n\tif (finished->error < 0) {\n'
         '\t\tif (amdgpu_ps4_uvd_runtime_ring(ring))\n'
         '\t\t\tamdgpu_ps4_uvd_runtime_fault(adev, finished->error);')
    func('amdgpu_uvd.c', 'amdgpu_uvd_free_handles', '\n\tint i, r;\n',
         '\n\tint i, r;\n\tbool ps4_runtime = amdgpu_ps4_uvd_runtime_ring(ring);\n')
    func('amdgpu_uvd.c', 'amdgpu_uvd_free_handles',
         '\n\tfor (i = 0; i < adev->uvd.max_handles; ++i) {',
         '\n\tif (ps4_runtime) {\n'
         '\t\tmutex_lock(&adev->ps4_uvd_runtime.session_lock);\n'
         '\t\tif (atomic_read(&adev->ps4_uvd_runtime.failed))\n'
         '\t\t\tgoto ps4_session_unlock;\n\t}\n'
         '\n\tfor (i = 0; i < adev->uvd.max_handles; ++i) {')
    func('amdgpu_uvd.c', 'amdgpu_uvd_free_handles',
         '\n\t\t\tdma_fence_wait(fence, false);',
         '\n\t\t\tif (ps4_runtime) {\n'
         '\t\t\t\tlong waited = dma_fence_wait_timeout(fence, false,\n'
         '\t\t\t\t\tmsecs_to_jiffies(2100));\n\n'
         '\t\t\t\tif (waited <= 0 || dma_fence_get_status(fence) < 0) {\n'
         '\t\t\t\t\tamdgpu_ps4_uvd_runtime_fault(adev, -ETIMEDOUT);\n'
         '\t\t\t\t\tdma_fence_put(fence);\n'
         '\t\t\t\t\tgoto ps4_session_unlock;\n\t\t\t\t}\n'
         '\t\t\t} else {\n\t\t\t\tdma_fence_wait(fence, false);\n\t\t\t}')
    func('amdgpu_uvd.c', 'amdgpu_uvd_free_handles',
         '\n\t\t\tr = amdgpu_uvd_get_destroy_msg(ring, handle, false,',
         '\n\t\t\tif (ps4_runtime && amdgpu_ps4_uvd_runtime_drain_jobs(adev))\n'
         '\t\t\t\tgoto ps4_session_unlock;\n'
         '\n\t\t\tr = amdgpu_uvd_get_destroy_msg(ring, handle, false,')
    func('amdgpu_uvd.c', 'amdgpu_uvd_free_handles',
         '\n\t\t\tif (r) {',
         '\n\t\t\tif (r) {\n\t\t\t\tif (ps4_runtime) {\n'
         '\t\t\t\t\tamdgpu_ps4_uvd_runtime_fault(adev, r);\n'
         '\t\t\t\t\tgoto ps4_session_unlock;\n\t\t\t\t}')
    func('amdgpu_uvd.c', 'amdgpu_uvd_free_handles',
         '\n\t\t\tatomic_set(&adev->uvd.handles[i], 0);\n\t\t}\n\t}\n',
         '\n\t\t\tatomic_set(&adev->uvd.handles[i], 0);\n\t\t}\n\t}\n'
         '\nps4_session_unlock:\n\tif (ps4_runtime)\n'
         '\t\tmutex_unlock(&adev->ps4_uvd_runtime.session_lock);\n')

    # ring->adev has not been assigned yet at this point.
    func('amdgpu_ring.c', 'amdgpu_ring_init',
         '\n\tif (ring->adev == NULL) {',
         '\n\tif (amdgpu_ps4_uvd_runtime_enabled(adev) &&\n'
         '\t    ring == &adev->uvd.inst[0].ring)\n'
         '\t\tsched_hw_submission = 1;\n\n\tif (ring->adev == NULL) {')
    func('amdgpu_fence.c', 'amdgpu_fence_driver_start_ring',
         'if (ring->funcs->type != AMDGPU_RING_TYPE_UVD) {',
         'if (ring->funcs->type != AMDGPU_RING_TYPE_UVD ||\n'
         '\t    amdgpu_ps4_uvd_runtime_ring(ring)) {')

    # Serialize actual WB sampling/monotonic observation/CAS for this ring.
    # Signal after releasing this lock; callbacks can acquire lease locks.
    old = '''	do {
		last_seq = atomic_read(&ring->fence_drv.last_seq);
		seq = amdgpu_fence_read(ring);

	} while (atomic_cmpxchg(&drv->last_seq, last_seq, seq) != last_seq);'''
    new = '''	if (amdgpu_ps4_uvd_runtime_ring(ring)) {
		struct amdgpu_ps4_uvd_runtime *s = &adev->ps4_uvd_runtime;
		unsigned long flags;

		spin_lock_irqsave(&s->observe_lock, flags);
		last_seq = atomic_read(&drv->last_seq);
		seq = le32_to_cpu(READ_ONCE(*drv->cpu_addr));
		dma_rmb();
		if (seq < last_seq || !amdgpu_ps4_uvd_runtime_observe(ring, seq)) {
			spin_unlock_irqrestore(&s->observe_lock, flags);
			amdgpu_ps4_uvd_runtime_fault(adev, -EIO);
			return false;
		}
		atomic_set(&drv->last_seq, seq);
		spin_unlock_irqrestore(&s->observe_lock, flags);
	} else {
''' + old + '\n\t}'
    func('amdgpu_fence.c', 'amdgpu_fence_process', old, new)
    func('amdgpu_fence.c', 'amdgpu_fence_driver_force_completion',
         '\n\tspin_lock_irqsave(&drv->lock, flags);',
         '\n\tif (amdgpu_ps4_uvd_runtime_ring(ring)) {\n'
         '\t\tamdgpu_ps4_uvd_runtime_fault(ring->adev, -EIO);\n'
         '\t\treturn;\n\t}\n\tspin_lock_irqsave(&drv->lock, flags);')
    func('amdgpu_ib.c', 'amdgpu_ib_schedule',
         '\n\tif (num_ibs == 0)',
         '\n\tr = amdgpu_ps4_uvd_runtime_before_submit(ring);\n'
         '\tif (r)\n\t\treturn r;\n\n\tif (num_ibs == 0)')

    # Any engine timeout must stop recovery before soft/per-ring/global reset.
    func('amdgpu_job.c', 'amdgpu_job_timedout',
         '\n\tif (!drm_dev_enter(adev_to_drm(adev), &idx)) {',
         '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev)) {\n'
         '\t\tamdgpu_ps4_uvd_runtime_fault(adev, -ETIMEDOUT);\n'
         '\t\treturn DRM_GPU_SCHED_STAT_ENODEV;\n\t}\n'
         '\n\tif (!drm_dev_enter(adev_to_drm(adev), &idx)) {')

    for function, anchor in (
        ('amdgpu_device_fini_hw', '\n\tamdgpu_ps4_live_prepare_invalidate'),
        ('amdgpu_device_fini_sw', '\n\tamdgpu_device_ip_fini(adev);'),
        ('amdgpu_device_halt', '\n\tamdgpu_xcp_dev_unplug(adev);'),
    ):
        func('amdgpu_device.c', function, anchor,
             '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n'
             f'\t\tamdgpu_ps4_uvd_runtime_park(adev, "{function}");\n' + anchor)
    for function in ('amdgpu_device_prepare', 'amdgpu_device_suspend'):
        anchor = '\n\tif (dev->switch_power_state == DRM_SWITCH_POWER_OFF)'
        func('amdgpu_device.c', function, anchor,
             '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n\t\treturn -EBUSY;\n' + anchor)
    func('amdgpu_device.c', 'amdgpu_device_resume',
         '\n\tif (amdgpu_sriov_vf(adev)) {\n\t\tr = amdgpu_virt_request_full_gpu(adev, true);',
         '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n\t\treturn -EBUSY;\n'
         '\n\tif (amdgpu_sriov_vf(adev)) {\n\t\tr = amdgpu_virt_request_full_gpu(adev, true);')
    func('amdgpu_device.c', 'amdgpu_device_gpu_recover',
         '\n\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_RESET);',
         '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev)) {\n'
         '\t\tamdgpu_ps4_uvd_runtime_fault(adev, -EIO);\n'
         '\t\treturn -EOPNOTSUPP;\n\t}\n'
         '\n\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_RESET);')

    # Stop before devres/unplug/PCI disable, not merely in the IP fini callback.
    edit('amdgpu_drv.c', '\nerr_pci:\n',
         '\nerr_pci:\n\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n'
         '\t\tamdgpu_ps4_uvd_runtime_park(adev, "probe-error");\n')
    for function, anchor in (
        ('amdgpu_pci_remove', '\n\tamdgpu_ras_eeprom_check_and_recover(adev);'),
        ('amdgpu_pci_shutdown', '\n\tif (amdgpu_ras_intr_triggered())'),
    ):
        func('amdgpu_drv.c', function, anchor,
             '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n'
             f'\t\tamdgpu_ps4_uvd_runtime_park(adev, "{function}");\n' + anchor)
    func('amdgpu_kms.c', 'amdgpu_driver_unload_kms',
         '\n\tamdgpu_unregister_gpu_instance(adev);',
         '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n'
         '\t\tamdgpu_ps4_uvd_runtime_park(adev, "unload-kms");\n'
         '\n\tamdgpu_unregister_gpu_instance(adev);')

    for function, anchor in (
        ('amdgpu_pmops_prepare', '\n\t/* device maybe not resumed here'),
        ('amdgpu_pmops_runtime_checks', '\n\tif (adev->pm.rpm_mode == AMDGPU_RUNPM_NONE) {'),
        ('amdgpu_pmops_runtime_resume', '\n\tif (adev->pm.rpm_mode == AMDGPU_RUNPM_NONE)'),
    ):
        func('amdgpu_drv.c', function, anchor,
             '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n\t\treturn -EBUSY;\n' + anchor)
    for function, anchor in (
        ('amdgpu_pci_error_detected', '\n\tdev_info(adev->dev, "PCI error: detected callback!!\\n");'),
        ('amdgpu_pci_slot_reset', '\n\tdev_info(adev->dev, "PCI error: slot reset callback!!\\n");'),
        ('amdgpu_pci_resume', '\n\tdev_info(adev->dev, "PCI error: resume callback!!\\n");'),
    ):
        func('amdgpu_device.c', function, anchor,
             '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n'
             f'\t\tamdgpu_ps4_uvd_runtime_park(adev, "{function}");\n' + anchor)
    func('cik.c', 'cik_asic_reset', '\n\t/* APUs don\'t have full asic reset */',
         '\n\tif (amdgpu_ps4_uvd_runtime_retain(adev)) {\n'
         '\t\tamdgpu_ps4_uvd_runtime_fault(adev, -EIO);\n'
         '\t\treturn -EOPNOTSUPP;\n\t}\n'
         '\n\t/* APUs don\'t have full asic reset */')
    edit('amdgpu_drv.c', 'static struct pci_error_handlers amdgpu_pci_err_handler = {',
         '''static void amdgpu_ps4_uvd_pci_reset_prepare(struct pci_dev *pdev)
{
	struct drm_device *dev = pci_get_drvdata(pdev);
	struct amdgpu_device *adev;

	if (!dev)
		return;
	adev = drm_to_adev(dev);
	if (amdgpu_ps4_uvd_runtime_retain(adev))
		amdgpu_ps4_uvd_runtime_park(adev, "pci-reset-prepare");
}

static struct pci_error_handlers amdgpu_pci_err_handler = {
	.reset_prepare = amdgpu_ps4_uvd_pci_reset_prepare,''')

    for name in ('amdgpu_ps4_uvd_runtime.c', 'amdgpu_ps4_uvd_runtime.h',
                 'amdgpu_ps4_uvd_memory.c', 'amdgpu_ps4_uvd_memory.h',
                 'amdgpu_ps4_uvd_jobs.c', 'amdgpu_ps4_uvd_jobs.h'):
        out[name] = (HERE / name).read_bytes()
    return out


def build(base, extra, output):
    if output.exists():
        raise ValueError('fresh output required')
    source = {}
    for root, manifest in ((base, 'BASE.json'), (extra, 'BASE-EXTRA.json')):
        meta = json.loads((root / manifest).read_bytes())
        for name, expected in meta['files'].items():
            raw = (root / name).read_bytes()
            if sha(raw) != expected or name in source:
                raise ValueError('invalid/duplicate input: ' + name)
            source[name] = raw
    # Apply the lease worker's exact reviewed hooks before parent edits.
    with tempfile.TemporaryDirectory(prefix='ps4-uvd-runtime-') as tmp:
        root = Path(tmp)
        for name, raw in source.items():
            p = root / PREFIX / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        patch = HERE / 'ps4-uvd-jobs-hooks.patch'
        for args in (['--check'], []):
            subprocess.run(['git', 'apply', '--whitespace=nowarn', *args, str(patch)],
                           cwd=root, check=True, capture_output=True)
        hooked = {name: (root / PREFIX / name).read_bytes() for name in source}
    candidate = transform(hooked)
    output.mkdir(parents=True)
    changes = sorted(name for name in source.keys() | candidate.keys()
                     if source.get(name) != candidate.get(name))
    patch = ('From: psX Linux contributors\n'
             'Subject: [EXPERIMENT] drm/amdgpu: default-off Liverpool UVD runtime\n\n'
             'Adapt Linux UVD parser, scheduler and packet callbacks to the cold-boot\n'
             'Liverpool startup exercised by EXP-20261020-005. Native mapping and\n'
             'normal userspace runtime have no hardware acceptance. Firmware stays\n'
             'external. Retain DMA on uncertainty; no PM, reset or teardown.\n\n')
    for name in changes:
        p = output / 'candidate' / PREFIX / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(candidate[name])
        rel = str(PREFIX / name)
        patch += f'diff --git a/{rel} b/{rel}\n'
        if name not in source:
            patch += 'new file mode 100644\n'
        patch += ''.join(difflib.unified_diff(source.get(name, b'').decode().splitlines(True),
                         candidate[name].decode().splitlines(True),
                         fromfile='a/' + rel if name in source else '/dev/null', tofile='b/' + rel))
    patch_name = '0015-drm-amdgpu-ps4-uvd-runtime.patch'
    (output / patch_name).write_text(patch)
    receipt = {'schema': 1, 'status': 'SOURCE_CANDIDATE_ONLY', 'hardware_acceptance': False,
               'generator_sha256': sha(Path(__file__).read_bytes()),
               'inputs': {p.name: sha(p.read_bytes()) for p in HERE.iterdir()
                          if p.is_file() and p.suffix in ('.c', '.h', '.inc', '.patch')},
               'base_files': {n: sha(v) for n, v in source.items()},
               'candidate_files': {n: sha(candidate[n]) for n in changes},
               'patch': {'path': patch_name, 'sha256': sha(patch.encode())}}
    (output / 'SOURCE-RECEIPT.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--extra', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(build(args.base, args.extra, args.output), indent=2, sort_keys=True))
