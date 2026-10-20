/* READY_CORE_STATE_BEGIN */
struct firmware;

/* The deciding guard's saved sequential reads; not an atomic HW snapshot. */
struct ps4_ready_guard_sample {
	u32 sampled, failed;
	u32 rbc, mastint, ib_size, wptr, rptr;
};

enum ps4_ready_core_terminal {
	PS4_READY_CORE_NONE,
	PS4_READY_CORE_NOT_AUTHORIZED,
	PS4_READY_CORE_PREFLIGHT,
	PS4_READY_CORE_VCPU_CACHE_VMID_PREFLIGHT,
	PS4_READY_CORE_ATOM_PREFLIGHT,
	PS4_READY_CORE_FIRMWARE_REQUEST,
	PS4_READY_CORE_FIRMWARE_HEADER_CRC,
	PS4_READY_CORE_BASELINE_NOT_CLEAN,
	PS4_READY_CORE_INITIAL_SETUP_PARK,
	PS4_READY_CORE_HELD_BASELINE,
	PS4_READY_CORE_RBC_RELEASE_GUARD,
	PS4_READY_CORE_FAULT_OR_RESET,
	PS4_READY_CORE_VCPU_CACHE_VMID_CHANGED,
	PS4_READY_CORE_FETCH_GUARD_CHANGED,
	PS4_READY_CORE_POLL_STATE_REGRESSION,
	PS4_READY_CORE_READY_TIMEOUT,
	PS4_READY_CORE_READY_CONVENTION_OBSERVED,
	PS4_READY_CORE_READY_OBSERVED,
};

struct ps4_ready_core_result {
	enum ps4_ready_core_terminal terminal;
	int error;
};

/*
 * Embedded in the zero-initialized amdgpu_device; exclusively written by the
 * existing boot wrapper/core. No external reader, reset, clear or rearm API.
 * The global cold-boot latch remains the admission policy across devices.
 *
 * Firmware is retained here after successful request, even on later failure.
 * CPU/PD/backing describe BORROWED mappings from the retained canary owner:
 * they are not BO references and do not extend allocation/device lifetime.
 * There is no stack-owned or replaceable-module-owned retained state.
 *
 * Entry flags are conservative software witnesses, set BEFORE an operation:
 * not confirmation that its MMIO completed, that DMA occurred, or that DMA
 * is quiescent. No cleanup follows a returning result. The only production
 * caller dispatches the original boot diagnostic or parks without returning.
 */
struct amdgpu_ps4_uvd_ready_operation {
	bool boot_claimed;
	bool entered;
	bool completed;
	bool first_mmio_entered;
	bool backing_write_entered;
	bool hdp_flush_entered;
	bool irq_arm_entered;
	bool indexed_snapshot_entered;
	bool setup_entered;
	bool release_entered;
	bool vcpu_seen;
	const struct firmware *firmware;
	void *retained_cpu;
	size_t backing_size;
	u64 retained_pd;
	u64 started, elapsed;
	u32 crc, baseline_fault, fault, status;
	u32 cache_vmid_original, cache_vmid_expected;
	int reset_count;
	struct ps4_ready_guard_sample guard_sample;
	struct ps4_ready_core_result result;
};
/* READY_CORE_STATE_END */
