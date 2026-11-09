// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "pool_core.h"
static const pool_u64 base=0xffffffff82200000ULL;
static unsigned calls,data_calls,fail_at,bad_hash,nsegments=31,freelist_last=1,bad_head,bad_special;
static unsigned seen_normal[156],seen_special[78],seen_segments[31];
static void put(pool_u8 *b,pool_u64 v,unsigned n){for(unsigned i=0;i<n;i++)b[i]=(pool_u8)(v>>(8*i));}
static int reader(void *ctx,pool_u64 addr,void *dst,pool_u32 n)
{
 assert(!ctx&&pool_read_allowed(base,addr,n));pool_u64 o=addr-base;pool_u8 *b=dst;
 calls++;if(calls==fail_at)return 0;memset(b,0,n);
 if(o==0x2d6570||o==0x2d56a0){assert(n==32);memset(b,o==0x2d6570?0x11:0x22,n);if(bad_hash==(o==0x2d6570?1U:2U))b[0]^=1;return 1;}
 data_calls++;
 if(o==0x2233704){assert(n==4);put(b,nsegments,4);}
 else if(o==0x2233be8){assert(n==1);b[0]=freelist_last;}
 else if(o==0x1a50358){assert(n==4);put(b,0x12345678,4);}
 else if(o==0x21f54d8||o==0x21f5528||o==0x21f5568){assert(n==8);put(b,o*17,8);}
 else if(o==0x2234a90||o==0x2234a98){assert(n==8);unsigned f=(o-0x2234a90)/8;assert(f<=freelist_last);put(b,base+POOL_NORMAL_BASE+f*POOL_SET_BYTES+(bad_head?24:0),8);}
 else if(o>=0x2233710&&o<0x2233be8){assert(n==40&&(o-0x2233710)%40==0);unsigned i=(o-0x2233710)/40;assert(i<nsegments&&i<31&&!seen_segments[i]++);put(b,0x1000000ULL+i*0x100000,8);put(b+8,0x1100000ULL+i*0x100000,8);put(b+16,base+i*0x4000,8);put(b+24,0x87654321,4);put(b+32,base+POOL_NORMAL_BASE+(i%2)*POOL_SET_BYTES,8);}
 else if(o==0x2235200){assert(n==40);put(b,0x170000000ULL,8);put(b+8,0x180000000ULL,8);put(b+16,base+0x200000,8);put(b+24,2,4);put(b+32,base+POOL_SPECIAL_BASE+(bad_special?8:0),8);}
 else if(o>=POOL_NORMAL_BASE&&o<POOL_NORMAL_BASE+2*POOL_SET_BYTES){assert(n==4&&o>=POOL_NORMAL_BASE+16&&(o-POOL_NORMAL_BASE-16)%24==0);unsigned i=(o-POOL_NORMAL_BASE-16)/24;assert(i<156&&i<(freelist_last+1)*78&&!seen_normal[i]++);put(b,0x80000000U+i,4);}
 else if(o>=POOL_SPECIAL_BASE&&o<POOL_SPECIAL_BASE+POOL_SET_BYTES){assert(n==4&&o>=POOL_SPECIAL_BASE+16&&(o-POOL_SPECIAL_BASE-16)%24==0);unsigned i=(o-POOL_SPECIAL_BASE-16)/24;assert(i<78&&!seen_special[i]++);put(b,0xf0000000U+i,4);}
 else assert(0);
 return 1;
}
static void reset(void){calls=data_calls=fail_at=bad_hash=bad_head=bad_special=0;nsegments=31;freelist_last=1;memset(seen_normal,0,sizeof(seen_normal));memset(seen_special,0,sizeof(seen_special));memset(seen_segments,0,sizeof(seen_segments));}
static struct pool_snapshot s;
static void rejected(unsigned expected)
{assert(pool_collect(1202,0x30201,base,reader,NULL,&s)==(int)expected);assert(!s.complete&&!s.kernel_base&&!s.normal_segments&&!s.normal[0].start&&!s.special.end&&!s.normal_count[0][0][0][0]&&!s.special_count[1][2][12]);}
int main(void)
{
 reset();memset(&s,0x55,sizeof(s));assert(pool_collect(1200,0x30201,base,reader,NULL,&s)==POOL_PROFILE&&!calls&&!s.complete&&!s.special.end);
 assert(pool_collect(1202,3,base,reader,NULL,&s)==POOL_PROFILE&&!calls);
 const pool_u64 bad[]={0,base+1,~(pool_u64)0-0x3fff};
 for(unsigned i=0;i<3;i++)assert(pool_collect(1202,0x30201,bad[i],reader,NULL,&s)==POOL_BASE&&!calls);
 assert(!pool_read_allowed(base,base+POOL_NORMAL_BASE,8));
 assert(!pool_read_allowed(base,base+POOL_NORMAL_BASE+8,8));
 assert(!pool_read_allowed(base,base+POOL_NORMAL_BASE+20,4));
 assert(!pool_read_allowed(base,base+0x2233710+31*40,40));
 assert(!pool_read_allowed(base,base+0x2235200,41));
 assert(!pool_read_allowed(base,base+0x2233704,8));
 for(unsigned i=1;i<=2;i++){reset();bad_hash=i;rejected(POOL_FINGERPRINT);assert(calls==i&&!data_calls);}
 reset();nsegments=32;rejected(POOL_SEGMENTS);assert(calls==3);
 reset();nsegments=0;rejected(POOL_SEGMENTS);assert(calls==3);
 reset();freelist_last=2;rejected(POOL_FREELISTS);assert(calls==4);
 reset();bad_head=1;rejected(POOL_HEAD);assert(!seen_normal[0]);
 reset();bad_special=1;rejected(POOL_SPECIAL);assert(!seen_normal[0]&&!seen_special[0]);
 reset();assert(pool_collect(1202,0x30201,base,reader,NULL,&s)==POOL_OK&&s.complete);
 assert(calls==276&&s.reads==276&&s.normal_segments==31&&s.active_freelists==2&&s.profile_mode==0x12345678);
 assert(s.normal[30].start==0x2e00000&&s.normal[30].domain==0x87654321);
 assert(s.special.start==0x170000000ULL&&s.special.free_queues==base+POOL_SPECIAL_BASE);
 assert(s.normal_count[1][1][2][12]==0x80000000U+155&&s.special_count[1][2][12]==0xf0000000U+77);
 for(unsigned i=0;i<156;i++)assert(seen_normal[i]==1);
 for(unsigned i=0;i<78;i++)assert(seen_special[i]==1);
 for(unsigned f=1;f<=276;f++){reset();fail_at=f;rejected(POOL_READ);assert(calls==f&&s.reads==f);}
 reset();nsegments=1;freelist_last=0;assert(pool_collect(1202,0x30201,base,reader,NULL,&s)==POOL_OK&&s.active_freelists==1);
 for(unsigned i=78;i<156;i++)assert(!seen_normal[i]);
 puts("actual pool collector: exact fixed read allowlist/counts; profile/fingerprints; all276 failure boundaries; reset/endian/inactive-list bounds PASS");
}
