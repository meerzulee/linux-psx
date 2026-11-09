/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 Meerzulee and Linux psX contributors.
 * Fixed name-to-OID query follows FreeBSD's sysctlnametomib interface. */
#include <sys/types.h>
#include <stddef.h>
#include <unistd.h>
#include "fw_detect.h"
#include "pool_core.h"
extern long __sysctl(const int *,unsigned,void *,size_t *,const void *,size_t);
extern long kexec(void (*)(void),void *);
static struct pool_snapshot snapshot;
static pool_u32 raw_fw,subid,callback_called;
static pool_u64 active_base;
static int (*kernel_printf)(const char *,...);
static void say(const char *s,size_t n){write(1,s,n);}
#define SAY(s) say((s),sizeof(s)-1)
static pool_u32 southbridge(void)
{
 const char name[]="hw.sce_subsys_subid";
 int lookup[2]={0,3},oid[24];size_t n=sizeof(oid);pool_u32 value=0;
 if(__sysctl(lookup,2,oid,&n,name,sizeof(name)-1)||!n||n>sizeof(oid)||n%sizeof(int))return 0;
 unsigned count=n/sizeof(int);n=sizeof(value);
 if(__sysctl(oid,count,&value,&n,NULL,0)||n!=sizeof(value))return 0;
 return value;
}
static int native_read(void *ctx,pool_u64 address,void *destination,pool_u32 n)
{
 (void)ctx;if(!pool_read_allowed(active_base,address,n))return 0;
 pool_u8 *d=destination;
 /* Aligned counters are one native u32 load, still an unlocked snapshot. */
 if(n==4){pool_u32 v=*(volatile const pool_u32 *)(uintptr_t)address;for(unsigned i=0;i<4;i++)d[i]=(pool_u8)(v>>(8*i));}
 else if(n==8){pool_u64 v=*(volatile const pool_u64 *)(uintptr_t)address;for(unsigned i=0;i<8;i++)d[i]=(pool_u8)(v>>(8*i));}
 else {volatile const pool_u8 *s=(void *)(uintptr_t)address;for(unsigned i=0;i<n;i++)d[i]=s[i];}
 return 1;
}
static void print_segment(const char *group,unsigned i,const struct pool_segment *s)
{
 kernel_printf("PSX_POOL_V1 segment=%s index=%u start=0x%llx end=0x%llx pages=0x%llx domain=%u free_queues=0x%llx\n",
  group,i,s->start,s->end,s->page_array,s->domain,s->free_queues);
}
static void print_counts(const char *group,unsigned f,unsigned sel,unsigned p,const pool_u32 *v)
{
 kernel_printf("PSX_POOL_V1 counts=%s freelist=%u selector=%u pool=%u orders0to12=%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u\n",
 group,f,sel,p,v[0],v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8],v[9],v[10],v[11],v[12]);
}
static void collect_callback(void)
{
 unsigned lo,hi;callback_called=1;
 if(raw_fw!=1202||subid!=0x30201)return;
 asm volatile("rdmsr":"=a"(lo),"=d"(hi):"c"(0xc0000082));
 pool_u64 lstar=((pool_u64)hi<<32)|lo;if(lstar<0x1c0)return;
 active_base=lstar-0x1c0;if(!pool_base_valid(active_base))return;
 int status=pool_collect(raw_fw,subid,active_base,native_read,NULL,&snapshot);
 /* Fingerprints precede calling the fixed-profile printf entry too. */
 if(status==POOL_FINGERPRINT||status==POOL_READ||status==POOL_BASE||status==POOL_PROFILE)return;
 kernel_printf=(void *)(uintptr_t)(active_base+0x2e03e0);
 kernel_printf("PSX_POOL_V1 memory_report fw=%u subid=0x%x status=%u reads=%u unlocked=1 availability_proof=0 orbis_remains_running=1\n",raw_fw,subid,snapshot.status,snapshot.reads);
 if(status){kernel_printf("PSX_POOL_V1 refused offset=0x%llx\n",snapshot.failed_offset);return;}
 kernel_printf("PSX_POOL_V1 profile_mode=%u normal_segments=%u active_freelists=%u game_dmem_start=0x%llx game_dmem_end=0x%llx fmem_max=0x%llx\n",snapshot.profile_mode,snapshot.normal_segments,snapshot.active_freelists,snapshot.game_dmem_start,snapshot.game_dmem_end,snapshot.fmem_max);
 for(unsigned i=0;i<snapshot.normal_segments;i++)print_segment("NORMAL",i,&snapshot.normal[i]);
 print_segment("SPECIAL",0,&snapshot.special);
 for(unsigned f=0;f<snapshot.active_freelists;f++)for(unsigned sel=0;sel<2;sel++)for(unsigned p=0;p<3;p++)print_counts("NORMAL",f,sel,p,snapshot.normal_count[f][sel][p]);
 for(unsigned sel=0;sel<2;sel++)for(unsigned p=0;p<3;p++)print_counts("SPECIAL",0,sel,p,snapshot.special_count[sel][p]);
 kernel_printf("PSX_POOL_V1 complete=1 no_allocation=1 no_boot=1 orbis_remains_running=1\n");
}
int main(void)
{
 callback_called=0;raw_fw=get_firmware();subid=0;active_base=0;kernel_printf=NULL;
 if(raw_fw!=1202){SAY("PSX memory report refused: exact FW12.02 required. Orbis remains running.\n");return 1;}
 subid=southbridge();if(subid!=0x30201){SAY("PSX memory report refused: Baikal B1 required. Orbis remains running.\n");return 1;}
 SAY("PSX memory REPORT begins: UART only; no Linux boot. Orbis remains running.\n");
 snapshot.complete=0;snapshot.status=POOL_BASE;
 (void)kexec(collect_callback,NULL);
 if(!callback_called||!snapshot.complete){SAY("PSX memory REPORT refused/incomplete. Orbis remains running.\n");return 1;}
 SAY("PSX memory REPORT complete on UART. Orbis remains running.\n");return 0;
}
