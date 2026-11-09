/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 Meerzulee and Linux psX contributors. */
#ifndef PSX_POOL_CORE_H
#define PSX_POOL_CORE_H

typedef unsigned char pool_u8;
typedef unsigned int pool_u32;
typedef unsigned long long pool_u64;
_Static_assert(sizeof(pool_u8)==1 && sizeof(pool_u32)==4 && sizeof(pool_u64)==8,
               "Observer requires 8/32/64-bit unsigned types");
#define POOL_MAX_SEGMENTS 31U
#define POOL_NORMAL_BASE 0x2233bf0ULL
#define POOL_SPECIAL_BASE 0x2234ab0ULL
#define POOL_SET_BYTES 1872ULL
#define POOL_MAX_OFFSET 0x2235228ULL
struct pool_segment { pool_u64 start,end,page_array; pool_u32 domain; pool_u64 free_queues; };
struct pool_snapshot {
 pool_u32 status,complete,reads,normal_segments,active_freelists,profile_mode;
 pool_u64 failed_offset,kernel_base,game_dmem_start,game_dmem_end,fmem_max;
 struct pool_segment normal[POOL_MAX_SEGMENTS],special;
 pool_u32 normal_count[2][2][3][13],special_count[2][3][13];
};
enum pool_status { POOL_OK,POOL_PROFILE,POOL_BASE,POOL_READ,POOL_FINGERPRINT,
                   POOL_SEGMENTS,POOL_FREELISTS,POOL_HEAD,POOL_SPECIAL };
typedef int (*pool_reader)(void *,pool_u64,void *,pool_u32);
int pool_base_valid(pool_u64);
int pool_read_allowed(pool_u64,pool_u64,pool_u32);
int pool_collect(pool_u32,pool_u32,pool_u64,pool_reader,void *,struct pool_snapshot *);
#endif
