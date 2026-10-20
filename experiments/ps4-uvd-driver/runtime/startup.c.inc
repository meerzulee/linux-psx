/* Appended beside the existing static readiness core by build_candidate.py. */
bool amdgpu_ps4_uvd_runtime_options_valid(struct amdgpu_device *adev)
{
	return amdgpu_ps4_uvd_runtime_enabled(adev) && ps4_uvd_ready &&
		ps4_uvd_poll_state && !ps4_uvd_token && !ps4_uvd_data_control &&
		!ps4_uvd_status_observation && !ps4_uvd_ib_data &&
		!ps4_uvd_message_create && !ps4_uvd_decode_frame &&
		!amdgpu_ps4_uvd_runtime_legacy_conflict() &&
		!amdgpu_ps4_live_prepare_enabled(adev);
}

int amdgpu_ps4_uvd_runtime_startup(struct amdgpu_device *adev,
	void *backing, size_t bytes, u64 pd_addr)
{
	struct amdgpu_ps4_uvd_ready_operation *op = &adev->ps4_uvd_ready_operation;
	struct ps4_ready_core_result result;
	struct ps4_token_sample sample;

	if (!amdgpu_ps4_uvd_runtime_options_valid(adev) ||
	    atomic_xchg(&ps4_ready_attempted, 1))
		return -EPERM;

	op->boot_claimed = true;
	result = ps4_ready_core_run(adev, backing, bytes, pd_addr);
	if (result.terminal != PS4_READY_CORE_READY_CONVENTION_OBSERVED || result.error)
		return result.error ? result.error : -EIO;

	if (!ps4_token_sample(adev, &sample, PS4_TOKEN_HELD, pd_addr,
		op->cache_vmid_expected, op->baseline_fault, op->reset_count) ||
	    !ps4_stobs_ctx_sweep(adev, &sample))
		return -EIO;

	/* The caller has initialized and published the entire owned ring. No
	 * command or WPTR is emitted here; subsequent jobs use the normal ring.
	 */
	mb();
	amdgpu_device_flush_hdp(adev, NULL);
	WREG32(mmUVD_RBC_RB_CNTL, PS4_TOKEN_RBC_FETCH);
	if (!ps4_token_sample(adev, &sample, PS4_TOKEN_EMPTY, pd_addr,
		op->cache_vmid_expected, op->baseline_fault, op->reset_count))
		return -EIO;

	dev_info(adev->dev,
		"PS4_UVD_RUNTIME STARTED vmid=4 native_alias=1 polled_fences=1; normal producers follow, no PM/reset/rearm\n");
	return 0;
}
