// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#undef NULL
#undef offsetof
#define TESTING
#include "kernel.h"
struct ksym_t kern;
static void *map;
static unsigned calls,frees,fault,records,frees_at_record,begins,ends,accepted;
static unsigned original_reads,alias_reads,zero_returns,attempt_expected;
static char begin_record[1024],end_record[1024],message[1024];
static vm_offset_t vm,freed;
static size_t actual_bytes,mapped_bytes,free_bytes;
static vm_paddr_t returned_high,physical_start=0x140000000ULL;
static int capture(const char *fmt,...)
{
 char row[1024],expected[96];va_list ap;va_start(ap,fmt);int n=vsnprintf(row,sizeof(row),fmt,ap);va_end(ap);
 assert(n>0&&(size_t)n<sizeof(row));
 if(strstr(row,"DIAGNOSTIX_ALLOC_V2 event=begin ")) {
  assert(begins==ends);begins++;attempt_expected++;strcpy(begin_record,row);
  snprintf(expected,sizeof(expected),"policy=UPSTREAM_BOUNDED3 attempt=%u of=3 ",attempt_expected);
  assert(strstr(row,expected)&&strstr(row,"flags=0x102 low=0 "));
 } else if(strstr(row,"DIAGNOSTIX_ALLOC_V2 event=end ")) {
  assert(begins==ends+1&&calls==begins);ends++;strcpy(end_record,row);
  snprintf(expected,sizeof(expected),"policy=UPSTREAM_BOUNDED3 attempt=%u of=3 ",attempt_expected);
  assert(strstr(row,expected)&&strstr(row,"flags=0x102 high="));
 } else if(strstr(row,"DIAGNOSTIX_ALLOC_V3 event=accepted ")) {
  assert(original_reads==mapped_bytes/PAGE_SIZE+1&&alias_reads==mapped_bytes/PAGE_SIZE);
  accepted++;attempt_expected=0;
 } else {
  assert(begins==ends);strcpy(message,row);records++;frees_at_record=frees;attempt_expected=0;
 }
 return n;
}
static vm_offset_t allocate(vm_map_t m,vm_size_t size,int flags,vm_paddr_t lo,
 vm_paddr_t hi,unsigned long alignment,unsigned long boundary,vm_memattr_t attr)
{
 (void)m;assert(lo==0&&boundary==0&&attr==6);
 assert(begins==ends+1&&begins==calls+1);
 calls++;assert(flags==0x102&&alignment==1);actual_bytes=size;
 mapped_bytes=(size+PAGE_MASK)&~(size_t)PAGE_MASK;returned_high=hi;
 vm=0xffffa00000000000ULL+calls*0x1000000ULL;
 if(fault==2)vm++;
 if(fault==7)vm=~(vm_offset_t)PAGE_MASK;
 if(zero_returns){--zero_returns;return 0;}
 return fault==1?0:vm;
}
static vm_paddr_t extract(pmap_t m,vm_offset_t a)
{
 (void)m;
 if(a>=vm&&a-vm<mapped_bytes) {
  original_reads++;
  if(fault==3)return 0;
  if(fault==4)return physical_start+1;
  if(fault==5)return ~(vm_paddr_t)PAGE_MASK;
  if(fault==6)return DIAG_RAM_END;
  if(fault==8)return 0x100000000ULL; /* VRAM, addressable but not owned RAM */
  if(fault==9&&a!=vm)return physical_start+2*PAGE_SIZE;
  return physical_start+(a-vm);
 }
 u64 direct=kern.dmap_base+physical_start;
 assert(a>=direct&&a-direct<mapped_bytes);alias_reads++;
 if(fault==10)return 0;
 if(fault==11&&a!=direct)return physical_start+2*PAGE_SIZE;
 return physical_start+(a-direct);
}
static void release(vm_map_t m,vm_offset_t a,vm_size_t size)
{(void)m;frees++;freed=a;free_bytes=size;}
#include "alloc.inc"
static void reason(const char *s,unsigned attempts)
{
 char expected[96];snprintf(expected,sizeof(expected),"reason=%s attempts=%u ",s,attempts);
 assert(records==1&&strstr(message,"DIAGNOSTIX_ALLOC_V1 ")&&strstr(message,expected));
}
static void reset_case(void){fault=records=original_reads=alias_reads=zero_returns=0;physical_start=0x140000000ULL;kern.dmap_base=0xffff840b00000000ULL;}
int main(void)
{
 kern.kernel_map=&map;kern.kmem_alloc_contig=allocate;kern.kmem_free=release;kern.pmap_extract=extract;kern.printf=capture;
 reset_case();assert(!kernel_alloc_contig_limit(0,DIAG_RAM_END));reason("request-size",0);assert(!calls);
 reset_case();assert(!kernel_alloc_contig_limit((size_t)-1,DIAG_RAM_END));reason("request-size",0);assert(!calls);
 reset_case();assert(!kernel_alloc_contig_limit(1,~(u64)0));reason("request-high",0);assert(!calls);
 reset_case();kern.dmap_base=0x40000000;assert(!kernel_alloc_contig(1));reason("dmap-base",0);assert(!calls);
 static const char *names[]={"","kmem-returned-zero","vm-unaligned","physical-zero","physical-unaligned","physical-overflow","physical-above-high","vm-overflow","physical-not-ram","noncontiguous","dmap-translation","dmap-translation"};
 for(unsigned f=1;f<=11;f++){
  reset_case();fault=f;unsigned before=calls,old_frees=frees,old_accepted=accepted;
  unsigned expected_calls=f==1?3:1;
  assert(!kernel_alloc_contig(PAGE_SIZE+1));assert(calls==before+expected_calls&&begins==calls&&ends==calls&&accepted==old_accepted);
  reason(names[f],f==1?3:1);
  assert(strstr(message,"requested=16385 rounded=32768 flags=0x102 low=0 high=0xffffffffffffffff alignment=0x1 guard_high=0x27f000000"));
  assert(strstr(begin_record,"requested=16385 rounded=32768")&&strstr(end_record,"requested=16385 rounded=32768"));
  assert(strstr(begin_record,"high=0xffffffffffffffff alignment=0x1 guard_high=0x27f000000"));
  if(f==1)assert(strstr(end_record,"attempt=3 of=3 ")&&strstr(end_record,"vm=0x0\n"));
  assert(frees==old_frees+(f!=1)&&frees_at_record==old_frees);
  if(f!=1)assert(freed==vm&&free_bytes==2*PAGE_SIZE);
  if(f==9||f==11)assert(strstr(message,"page_offset=0x4000 observed=0x140008000"));
  if(f==10)assert(alias_reads==1&&strstr(message,"page_offset=0x0 observed=0x0"));
 }
 /* End is exclusive; complete rounded spans cannot cross RAM/reserved edges. */
 reset_case();physical_start=DIAG_RAM_END-PAGE_SIZE;
 assert(!kernel_alloc_contig(PAGE_SIZE+1));reason("physical-above-high",1);
 reset_case();physical_start=0x7efe4000ULL;
 assert(!kernel_alloc_contig(PAGE_SIZE+1));reason("physical-not-ram",1);
 const u64 addresses[]={0x40000000ULL,0x140000000ULL,0x200000000ULL,DIAG_RAM_END-PAGE_SIZE};
 for(unsigned i=0;i<sizeof(addresses)/sizeof(addresses[0]);i++){
  reset_case();physical_start=addresses[i];unsigned old_accepted=accepted;
  void *p=kernel_alloc_contig(123);assert(p&&actual_bytes==123&&mapped_bytes==PAGE_SIZE&&returned_high==~(vm_paddr_t)0&&!records&&accepted==old_accepted+1);
  assert((u64)(uintptr_t)p==kern.dmap_base+physical_start&&DM_TO_ID(p)==physical_start);
  vm_offset_t original=vm;unsigned old_frees=frees;
  kernel_free_contig(p,124);assert(frees==old_frees);kernel_free_contig(p,123);
  assert(frees==old_frees+1&&freed==original&&free_bytes==PAGE_SIZE);
  kernel_free_contig(p,123);assert(frees==old_frees+1);
 }
 reset_case();zero_returns=2;unsigned before=calls,old_accepted=accepted;
 void *retried=kernel_alloc_contig(123);assert(retried&&calls==before+3&&!zero_returns&&!records&&accepted==old_accepted+1);
 assert(strstr(begin_record,"attempt=3 of=3 ")&&strstr(end_record,"attempt=3 of=3 "));
 kernel_free_contig(retried,123);
 reset_case();for(int i=0;i<4;i++)contig_records[i]=(struct contig_record){(void*)(uintptr_t)(0x1000+i),0,0,0};
 before=calls;assert(!kernel_alloc_contig(1));reason("ledger-full",0);assert(calls==before);
 memset(contig_records,0,sizeof(contig_records));
 puts("actual allocator: bounded three-attempt zero-return retry, success stops retry, finite RAM spans, alias checks and exact original-VA cleanup PASS");
}
