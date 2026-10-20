/* GMC_FAULT_TYPES_BEGIN */
#define PS4_UVD_FAULT_VMIDS 16U

/* Raw GMC handler samples; addr remains the unshifted register value. */
struct ps4_uvd_fault_record {
	u32 addr;
	u32 status;
	u32 mc_client;
};

struct ps4_uvd_fault_bucket {
	u64 arrivals;
	bool saturated;
	struct ps4_uvd_fault_record first;
	struct ps4_uvd_fault_record last;
};

struct ps4_uvd_fault_history {
	struct ps4_uvd_fault_bucket total;
	struct ps4_uvd_fault_bucket vmid[PS4_UVD_FAULT_VMIDS];
	/* Literal STATUS == U32_MAX, not proof that a register read failed. */
	struct ps4_uvd_fault_bucket unclassified_status;
};

struct ps4_uvd_fault_snapshot {
	bool active;
	u64 arm_ns;
	struct ps4_uvd_fault_history history;
};
/* GMC_FAULT_TYPES_END */
