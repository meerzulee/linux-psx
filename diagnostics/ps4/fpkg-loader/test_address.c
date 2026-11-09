// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#undef NULL
#undef offsetof
#define TESTING
#include "kernel.h"
#include "acpi.h"
#include "linux_boot.h"
#include "diagnostix_scatter.h"
struct ksym_t kern;
/* Synthetic bytes only; never executed. The production symbol denotes code. */
asm(".pushsection .rodata\n.balign 8\n.globl jmp_to_linux\njmp_to_linux:\n.fill 121,1,0xa5\n.popsection\n");
size_t jmp_to_linux_size=121;
static size_t fake_strlcpy(char *d,const char *s,size_t n){size_t len=strlen(s);if(n){size_t k=len<n-1?len:n-1;memcpy(d,s,k);d[k]=0;}return len;}
#define strlcpy fake_strlcpy
#include "address.inc"
static u64 tables[3*512];
/* Independent x86 page walker consumes the actual helper's generated entries. */
static int walk(u64 va,u64 *pa)
{
 u64 pml4=tables[(va>>39)&511];if(!(pml4&1))return 0;
 u64 pdp_pa=pml4&0x000ffffffffff000ULL;
 assert(pdp_pa==DIAG_IDENT_PDP_PA||pdp_pa==DIAG_DMAP_PDP_PA);
 size_t page=(pdp_pa-DIAG_PML4_PA)/4096;
 u64 leaf=tables[page*512+((va>>30)&511)];if(!(leaf&1))return 0;
 assert((leaf&0x87)==0x87);
 *pa=(leaf&0x000fffffc0000000ULL)|(va&0x3fffffffULL);return 1;
}
static void address_tests(void)
{
 const u64 base=0xffff840b00000000ULL;
 assert(((base>>30)&511)==44&&diag_dmap_base_valid(base));kern.dmap_base=base;
 const u64 points[]={0,0x1000,0x4000,0x140000000ULL,0x200000000ULL,DIAG_RAM_END-1,DIAG_MAP_BYTES-1};
 assert(diag_build_page_tables(tables,3*512,base));
 for(unsigned i=0;i<sizeof(points)/sizeof(points[0]);i++){
  u64 direct=99,pa=99,walked=99;
  assert(diag_pa_to_dm(base,points[i],1,&direct)&&direct==base+points[i]);
  assert(diag_dm_to_pa(base,direct,1,&pa)&&pa==points[i]);
  assert(PA_TO_DM(points[i])==direct&&DM_TO_ID(direct)==points[i]);
  assert(walk(points[i],&walked)&&walked==points[i]);
  assert(walk(direct,&walked)&&walked==points[i]);
 }
 assert((base|0x140000000ULL)!=(base+0x140000000ULL));
 assert((base|0x200000000ULL)!=(base+0x200000000ULL));
 u64 x=99;assert(!walk(DIAG_MAP_BYTES,&x)&&!walk(base+DIAG_MAP_BYTES,&x));
 assert(!diag_pa_to_dm(base,DIAG_MAP_BYTES,1,&x)&&x==0);
 assert(!diag_pa_to_dm(base,DIAG_MAP_BYTES-1,2,&x)&&x==0);
 assert(!diag_pa_to_dm(base,0,0,&x));assert(!diag_dm_to_pa(base,0x4800,1,&x));
 assert(!diag_dm_to_pa(base,base-1,1,&x));assert(!diag_dm_to_pa(base,base+DIAG_MAP_BYTES-1,2,&x));
 const u64 bad[]={0,0x40000000ULL,0xffff000000000000ULL,base+1,0xffff807c40000000ULL,0xffffffffc0000000ULL};
 for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++){
  assert(!diag_dmap_base_valid(bad[i]));memset(tables,0x55,sizeof(tables));
  assert(!diag_build_page_tables(tables,3*512,bad[i])&&tables[0]==0x5555555555555555ULL);
 }
 assert(!diag_build_page_tables(tables,3*512-1,base));
 /* Separate PDPTs also work when their numeric indices overlap. */
 const u64 edges[]={0xffff800000000000ULL,0xffff807c00000000ULL};
 for(unsigned i=0;i<2;i++){
  assert(diag_build_page_tables(tables,3*512,edges[i]));
  assert(walk(edges[i]+0x200000000ULL,&x)&&x==0x200000000ULL);
  assert(walk(0x140000000ULL,&x)&&x==0x140000000ULL);
 }
}
static void ram_tests(void)
{
 struct boot_params bp,image;memset(&image,0,sizeof(image));vram_mb=1024;
 prepare_boot_params(&bp,(u8*)&image);
 for(unsigned i=0;i<bp.e820_entries;i++){
  struct e820entry *e=&bp.e820_map[i];
  int eligible=e->type==SMAP_TYPE_MEMORY&&e->addr>=DIAG_LOW_END;
  assert(diag_ram_span(e->addr,e->size)==eligible);
  if(eligible){assert(diag_ram_span(e->addr,1));assert(diag_ram_span(e->addr+e->size-1,1));assert(!diag_ram_span(e->addr+e->size-1,2));}
 }
 assert(diag_ram_span(0x140000000ULL,PAGE_SIZE));assert(diag_ram_span(0x200000000ULL,PAGE_SIZE));
 assert(diag_ram_span(DIAG_RAM_END-PAGE_SIZE,PAGE_SIZE));
 const u64 rejected[]={0,0x8000,0x400000,0x7efe8000,0x80000000,0xe4800000,0x100000000ULL,0x13ffffff0ULL,DIAG_RAM_END,~(u64)0};
 for(unsigned i=0;i<sizeof(rejected)/sizeof(rejected[0]);i++)assert(!diag_ram_span(rejected[i],PAGE_SIZE));
 assert(!diag_ram_span(0x7efe4000,2*PAGE_SIZE));assert(!diag_ram_span(DIAG_RAM_END-PAGE_SIZE,PAGE_SIZE+1));
 assert(!diag_ram_span(0x140000000ULL,0));assert(!diag_ram_span(0x140000000ULL,~(u64)0));
 /* Native-owned staging may occupy these Linux-reserved holes. Linux's map
  * remains unchanged; handoff low pages, MMIO and VRAM remain ineligible. */
 assert(diag_source_span(0x28000,PAGE_SIZE)&&!diag_ram_span(0x28000,PAGE_SIZE));
 assert(diag_source_span(0x400000,PAGE_SIZE)&&!diag_ram_span(0x400000,PAGE_SIZE));
 assert(diag_source_span(DIAG_LOW_END,PAGE_SIZE));
 assert(!diag_source_span(0x4000,PAGE_SIZE));
 assert(!diag_source_span(0x2bfff,2)&&!diag_source_span(0x47ffff,2));
 assert(!diag_source_span(0x2c000,PAGE_SIZE)&&!diag_source_span(0xe0000,PAGE_SIZE));
 assert(!diag_source_span(0x100000000ULL,PAGE_SIZE));
 assert(!diag_source_span(~(u64)0,PAGE_SIZE)&&!diag_source_span(0x28000,~(u64)0));
 assert(!diag_source_span(0x28000,0));
}
static void layout_tests(void)
{
 static u8 low[DIAG_LOW_END-DIAG_GDT_PA];struct boot_params bp;char cmd[513];
 memset(&bp,0,sizeof(bp));memset(cmd,'x',511);cmd[511]=0;
 kern.dmap_base=0xffff840b00000000ULL;
 u64 initrd_pa=DIAG_SCATTER_INITRD;
 set_nix_info((void*)(uintptr_t)(kern.dmap_base+0x140000000ULL),&bp,
              (void*)(uintptr_t)(kern.dmap_base+initrd_pa),0x12345,cmd,1024,0x40000000,4);
 assert(diagnostix_prepare_handoff());assert(low_layout.thunk==0x4800);
 assert(low_layout.boot_params>=low_layout.thunk+121+0x200&&low_layout.end<=0x8000);
 assert(low_layout.command_line==low_layout.boot_params+0x1000);
 memset(low,0x55,sizeof(low));diagnostix_copy_low_data(low);
 for(unsigned i=0;i<121;i++)assert(low[low_layout.thunk-DIAG_GDT_PA+i]==0xa5);
 struct boot_params *actual=(void*)(low+low_layout.boot_params-DIAG_GDT_PA);
 assert(actual->hdr.cmd_line_ptr==low_layout.command_line&&actual->ext_cmd_line_ptr==0);
 assert((((u64)actual->ext_ramdisk_image<<32)|actual->hdr.ramdisk_image)==initrd_pa);
 assert(actual->hdr.ramdisk_size==0x12345&&actual->hdr.hardware_subarch==X86_SUBARCH_PS4);
 assert(!memcmp(low+low_layout.command_line-DIAG_GDT_PA,cmd,512));
 assert(low[low_layout.thunk-DIAG_GDT_PA-1]==0x55&&low[low_layout.end-DIAG_GDT_PA]==0x55);
 struct diag_low_layout l={1,2,3,4};assert(!diag_plan_low_layout(0,1,&l)&&!l.end);
 assert(!diag_plan_low_layout(~(u64)0,1,&l));assert(!diag_plan_low_layout(121,513,&l));
 assert(diag_plan_low_layout(4096,512,&l)&&l.end<=0x8000);
 cmd[511]='x';cmd[512]=0;assert(!diagnostix_prepare_handoff()&&!low_layout.end);
 cmd[511]=0;jmp_to_linux_size=4097;assert(!diagnostix_prepare_handoff());jmp_to_linux_size=121;
 kern.dmap_base++;assert(!diagnostix_prepare_handoff());
 assert(halted_cpus==0);
}
int main(void){address_tests();ram_tests();layout_tests();puts("actual address/handoff helpers: arithmetic DMAP, independent page walk, E820 spans, low physical data/stack/bp/511-byte command layout PASS");}
