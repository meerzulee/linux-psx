/* LIVE_PREPARE_DEBUGFS_BEGIN */
static int amdgpu_ps4_live_prepare_show(struct seq_file *m, void *unused)
{
	static const char * const stages[] = {
		"DISABLED", "SELECTED", "RESERVED", "PREPARING", "PREPARED",
		"INVALIDATED", "RELEASING", "RELEASED", "FAILED",
	};
	static const char * const events[] = {
		"NONE", "GART_ENABLE", "GART_DISABLE", "RESET", "SUSPEND",
		"HW_FINI", "VMID_FINI", "REINIT",
	};
	struct amdgpu_device *adev = m->private;
	struct ps4_live_prepare_snapshot s;

	amdgpu_ps4_live_prepare_snapshot(adev, &s);
	seq_printf(m, "schema=1 build=%s interface=prepare-only arm=absent\n",
		   UTS_RELEASE);
	seq_printf(m, "stage=%s error=%d selected=%u attempted=%u retained=%u\n",
		   stages[s.stage], s.error, s.selected, s.attempted, s.retained);
	seq_printf(m, "vmid=4 reservation_seen=%u allocator_alive_cached=%u early_fence_init=%u\n",
		   s.reservation_seen, s.allocator_alive,
		   READ_ONCE(adev->ps4_canary_fences_initialized));
	seq_printf(m, "observed_generation=%llu proof_generation=%llu last_event=%s\n",
		   s.observed_generation, s.proof_generation, events[s.last_event]);
	seq_printf(m, "historical_sdma_proof=%u proof_ns=%llu reset_counter_at_proof=%u copy_crc32=%08x\n",
		   s.proof_passed, s.proof_ns, s.proof_reset_counter, s.copy_crc);
	seq_printf(m, "pd_at_proof=0x%llx pd_bytes=0x%llx pt_bytes=0x%llx pages=%u\n",
		   s.pd_addr, s.pd_bytes, s.pt_bytes, s.mapped_pages);
	seq_printf(m, "r1_bytes=0x%llx rb_bytes=0x%llx ib_bytes=0x%llx\n",
		   s.r1_bytes, s.rb_bytes, s.ib_bytes);
	seq_puts(m, "observer=cached-only current_hardware_mapping=not-sampled UVD_setup=never\n");
	return 0;
}

static int amdgpu_ps4_live_prepare_open(struct inode *inode, struct file *file)
{
	struct amdgpu_device *adev = inode->i_private;
	int r;

	/* Keep scalar cached state alive even if removal overlaps an open fd.
	 * No reset-domain reference, runtime-PM get or hardware entry is needed.
	 */
	drm_dev_get(adev_to_drm(adev));
	r = single_open(file, amdgpu_ps4_live_prepare_show, adev);
	if (r)
		drm_dev_put(adev_to_drm(adev));
	return r;
}

static int amdgpu_ps4_live_prepare_release(struct inode *inode, struct file *file)
{
	struct seq_file *m = file->private_data;
	struct amdgpu_device *adev = m->private;
	int r;

	r = single_release(inode, file);
	drm_dev_put(adev_to_drm(adev));
	return r;
}

static const struct file_operations amdgpu_ps4_live_prepare_fops = {
	.owner = THIS_MODULE,
	.open = amdgpu_ps4_live_prepare_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = amdgpu_ps4_live_prepare_release,
};
/* LIVE_PREPARE_DEBUGFS_END */
