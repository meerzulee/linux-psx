/* GMC_FAULT_FLOW_BEGIN */
static void ps4_uvd_fault_bucket_record(struct ps4_uvd_fault_bucket *bucket,
				       const struct ps4_uvd_fault_record *record)
{
	/* Caller holds fault_lock. Order is lock acquisition, not hardware time. */
	if (!bucket->arrivals)
		bucket->first = *record;
	bucket->last = *record;
	if (bucket->arrivals != ~0ULL)
		bucket->arrivals++;
	/* Sticky on reaching the limit. An exhausted bucket cannot establish
	 * an unchanged interval, even when its count and last tuple match.
	 */
	if (bucket->arrivals == ~0ULL)
		bucket->saturated = true;
}

/* Called only with the GMC handler's already captured tuple. This records
 * armed handler observations, not all faults or an IH/GMC one-to-one match.
 * A tuple may predate arming through latch, main-IH or soft-IH delay.
 * In particular, an empty snapshot never establishes absence of faults.
 */
void amdgpu_ps4_uvd_irq_record_fault(struct amdgpu_device *adev,
				   u32 addr, u32 status, u32 mc_client)
{
	struct ps4_uvd_irq_observer *obs = &adev->ps4_uvd_irq;
	struct ps4_uvd_fault_history *history = &obs->fault_history;
	struct ps4_uvd_fault_bucket *category;
	struct ps4_uvd_fault_record record = {
		.addr = addr,
		.status = status,
		.mc_client = mc_client,
	};
	unsigned long flags;

	if (!smp_load_acquire(&obs->active))
		return;

	/* Preserve an all-ones status as its own literal class, not VMID15.
	 * No failure or requester-cause inference is attached to this class.
	 */
	if (status == ~0U)
		category = &history->unclassified_status;
	else
		category = &history->vmid[
			REG_GET_FIELD(status, VM_CONTEXT1_PROTECTION_FAULT_STATUS, VMID)];

	spin_lock_irqsave(&obs->fault_lock, flags);
	ps4_uvd_fault_bucket_record(&history->total, &record);
	ps4_uvd_fault_bucket_record(category, &record);
	spin_unlock_irqrestore(&obs->fault_lock, flags);
}

/* Caller owns an adev lifetime reference and a non-NULL output. No GPU,
 * clock, allocation, work submission or reporting operation occurs here.
 * This cut is coherent only for the new GMC tuple history; legacy IH slots
 * keep their original independent atomic publications and report contract.
 */
void amdgpu_ps4_uvd_irq_fault_snapshot(struct amdgpu_device *adev,
				     struct ps4_uvd_fault_snapshot *snapshot)
{
	struct ps4_uvd_irq_observer *obs = &adev->ps4_uvd_irq;
	unsigned long flags;

	*snapshot = (struct ps4_uvd_fault_snapshot) { };
	if (!smp_load_acquire(&obs->active))
		return;

	spin_lock_irqsave(&obs->fault_lock, flags);
	snapshot->active = true;
	snapshot->arm_ns = obs->arm_ns;
	snapshot->history = obs->fault_history;
	spin_unlock_irqrestore(&obs->fault_lock, flags);
}
/* GMC_FAULT_FLOW_END */
