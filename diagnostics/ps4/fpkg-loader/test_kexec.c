// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#undef NULL
#undef offsetof
#define TESTING
#include "kernel.h"
#include "linux_boot.h"
#include "firmware.h"
#include "kexec.h"
#include "diagnostix_scatter.h"
#include "../sb_detect.h"
struct ksym_t kern;
static unsigned allocated,freed,copies,strings,gpu,prepared,hooked,fail_alloc,fail_copy;
static int fw_result,hook_fail,overlap,cmd_fail,handoff_fail,native_reserved;
static unsigned safe_calls,safe_allocated,safe_freed,fail_safe,safe_bad;
static void *safe_ptrs[16];
static void *ptrs[5];static size_t sizes[5],rounded[5];static u8 pstate;
static u64 physical(pmap_t map,u64 va)
{
 (void)map;
 for(unsigned i=0;i<allocated;i++)if(ptrs[i]&&va>=(u64)ptrs[i]&&va-(u64)ptrs[i]<rounded[i]){
  u64 off=va-(u64)ptrs[i];
  if(native_reserved&&i==1&&off<PAGE_SIZE)return 0x28000ULL+off;
  u64 base=0x40000000ULL+i*0x100000;
  if(overlap==1&&i==1)base=DIAG_SCATTER_KERNEL;
  if(overlap==2&&(i==0||i==1))base=i==0?DIAG_SCATTER_INITRD:DIAG_SCATTER_KERNEL;
  if(overlap==3&&i==4)base=DIAG_SCATTER_KERNEL;
  if(overlap==4&&i==2)base=DIAG_SCATTER_KERNEL;
  return base+2*(off/PAGE_SIZE)*PAGE_SIZE+(off&PAGE_MASK);
 }
 for(unsigned i=0;i<safe_allocated;i++)if(safe_ptrs[i]&&va>=(u64)safe_ptrs[i]&&va-(u64)safe_ptrs[i]<PAGE_SIZE){
  u64 base=safe_bad==1?DIAG_SCATTER_KERNEL:safe_bad==2?0x40000000ULL:0x50000000ULL+i*2*PAGE_SIZE;
  return base+va-(u64)safe_ptrs[i];
 }
 assert(0);return 0;
}
static u64 __attribute__((unused)) write_protect_disable(void){assert(hooked&&allocated==5);return 0;}
static void __attribute__((unused)) write_protect_restore(u64 v){(void)v;}
static int quiet(const char *f,...){(void)f;return 0;}
void *kernel_alloc_boot(size_t n)
{
 if(allocated+1==fail_alloc)return NULL;
 assert(allocated<5&&n<1024*1024);
 sizes[allocated]=n;rounded[allocated]=(n+PAGE_MASK)&~(size_t)PAGE_MASK;
 ptrs[allocated]=aligned_alloc(PAGE_SIZE,rounded[allocated]);assert(ptrs[allocated]);
 memset(ptrs[allocated],0,rounded[allocated]);return ptrs[allocated++];
}
void kernel_free_boot(void *p,size_t n)
{if(!p)return;for(unsigned i=0;i<allocated;i++)if(ptrs[i]==p){assert(n==sizes[i]);free(p);ptrs[i]=NULL;freed++;return;}assert(0);}
void *kernel_alloc_boot_safe_page(void)
{
 safe_calls++;if(safe_calls==fail_safe)return NULL;
 assert(safe_allocated<16);void *p=aligned_alloc(PAGE_SIZE,PAGE_SIZE);assert(p);
 memset(p,0,PAGE_SIZE);safe_ptrs[safe_allocated++]=p;return p;
}
void kernel_free_boot_safe_page(void *p)
{assert(p);for(unsigned i=0;i<safe_allocated;i++)if(safe_ptrs[i]==p){free(p);safe_ptrs[i]=NULL;safe_freed++;return;}assert(0);}
static int copy(const void *s,void *d,size_t n){copies++;if(copies==fail_copy)return EFAULT;memcpy(d,s,n);return 0;}
static int copystr(const void *s,void *d,size_t n,size_t *done){strings++;if(cmd_fail||strlen(s)>=n)return ENAMETOOLONG;strcpy(d,s);if(done)*done=strlen(s)+1;return 0;}
ssize_t firmware_extract(void *d){if(fw_result>0&&fw_result<=FW_CPIO_SIZE)memset(d,0,fw_result);return fw_result;}
void set_nix_info(void *a,struct boot_params *b,void *c,size_t n,char *d,int v,u64 head,u64 records)
{(void)a;(void)b;(void)c;(void)d;assert(v==1024&&n==18+(size_t)fw_result&&head&&records);}
void prepare_boot_params(struct boot_params *bp,u8 *image){memcpy(bp,image,sizeof(*bp));prepared++;}
int diagnostix_prepare_handoff(void){assert(prepared==1&&!gpu&&!hooked);return !handoff_fail;}
int hook_icc_query_nowait(u8 *m){(void)m;return 0;}
int kernel_hook_install(void *a,void *b){(void)a;(void)b;assert(prepared==1&&copies==2&&strings==1&&allocated==5&&!gpu);if(hook_fail)return 0;hooked=1;return 1;}
static int pro(void){assert(hooked);return 0;}
static void one(unsigned v){(void)v;assert(hooked);gpu++;}
static void two(unsigned a,unsigned b){(void)a;(void)b;assert(hooked);gpu++;}
#include "kexec.inc"
static unsigned char image[8192],ramdisk[18];static char cmd[513];
static struct sys_kexec_args args;
static void reset(void)
{
 if(scatter_plan.metadata_kva||scatter_plan.safe_count)diag_scatter_release(&scatter_ops,&scatter_plan);
 for(unsigned i=0;i<safe_allocated;i++)assert(!safe_ptrs[i]);
 for(unsigned i=0;i<allocated;i++)free(ptrs[i]);
 memset(ptrs,0,sizeof(ptrs));
 memset(&scatter_plan,0,sizeof(scatter_plan));
 allocated=freed=copies=strings=gpu=prepared=hooked=fail_alloc=fail_copy=0;
 safe_calls=safe_allocated=safe_freed=fail_safe=safe_bad=0;
 memset(safe_ptrs,0,sizeof(safe_ptrs));
 fw_result=64;hook_fail=overlap=cmd_fail=handoff_fail=native_reserved=0;pstate=0;
 memset(image,0,sizeof(image));struct setup_header *h=&((struct boot_params*)image)->hdr;
 h->boot_flag=0xaa55;h->header=0x53726448;h->version=0x20f;h->setup_sects=1;
 h->syssize=(sizeof(image)-1024)/16;h->init_size=4*1024*1024;h->cmdline_size=2047;h->xloadflags=3;h->relocatable_kernel=1;
 memset(ramdisk,0,sizeof(ramdisk));ramdisk[0]=0x1f;ramdisk[1]=0x8b;ramdisk[2]=8;strcpy(cmd,"panic=0");
 args=(struct sys_kexec_args){.image=image,.image_size=sizeof(image),.initramfs=ramdisk,.initramfs_size=sizeof(ramdisk),.cmd_line=cmd,.vram_gb=(3U<<28)|(1202U<<16)|1024};
 kern=(struct ksym_t){.printf=quiet,.copyin=copy,.copyinstr=copystr,.copyout=copy,.icc_query_nowait=(void*)1,
  .pstate_before_shutdown=&pstate,.gpu_devid_is_9924=pro,.set_gpu_freq=two,.set_pstate=one,
  .update_vddnp=one,.set_cu_power_gate=one,.pmap_extract=physical};
}
static void refused(void){assert(sys_kexec((void*)1,&args)!=0);assert(freed==allocated&&safe_freed==safe_allocated&&!gpu&&!hooked&&pstate==0&&!scatter_plan.metadata_kva&&!scatter_plan.safe_count);}
int main(void)
{
 for(unsigned i=1;i<=5;i++){reset();fail_alloc=i;refused();}
 for(unsigned i=1;i<=2;i++){reset();fail_copy=i;refused();}
 for(int kind=0;kind<12;kind++){
  reset();switch(kind){case 0:args.image_size=(size_t)-1;break;case 1:args.initramfs_size=512ULL*1024*1024+1;break;
   case 2:args.initramfs_size=0;break;case 3:((struct boot_params*)image)->hdr.header=0;break;
   case 4:((struct boot_params*)image)->hdr.init_size=0;break;case 5:ramdisk[0]=0;break;
   case 6:fw_result=-1;break;case 7:fw_result=FW_CPIO_SIZE+1;break;case 8:cmd_fail=1;break;case 9:overlap=1;fail_safe=1;break;
   case 10:kern.set_gpu_freq=NULL;break;case 11:hook_fail=1;break;}
#ifdef DIAGNOSTIX_STAGE_ONLY
  if(kind>=10)continue;
#endif
  refused();
 }
 reset();args.image_size=64ULL*1024*1024;args.initramfs_size=512ULL*1024*1024;fail_alloc=1;refused();assert(copies==0);
 reset();handoff_fail=1;refused();assert(prepared==1);
 reset();overlap=2;fail_safe=2;refused();assert(safe_allocated==1&&safe_freed==1);
 for(unsigned bad=1;bad<=2;bad++){reset();overlap=1;safe_bad=bad;refused();}
 for(unsigned collision=1;collision<=4;collision++){
  reset();overlap=collision;assert(sys_kexec((void*)1,&args)==0);
  unsigned expected=collision==2?2:collision==4?0:1;
#ifdef DIAGNOSTIX_STAGE_ONLY
  assert(safe_allocated==expected+1&&safe_freed==safe_allocated&&freed==allocated&&!gpu&&!hooked);
#else
  assert(safe_allocated==expected&&!safe_freed&&scatter_plan.safe_count==expected&&hooked&&gpu);
#endif
 }
#ifdef DIAGNOSTIX_STAGE_ONLY
 reset();fail_safe=1;refused();assert(safe_calls==1&&!safe_allocated);
 reset();safe_bad=2;refused();assert(safe_allocated==1&&safe_freed==1);
#endif
 reset();native_reserved=1;memset(cmd,'x',511);cmd[511]=0;assert(sys_kexec((void*)1,&args)==0);assert(allocated==5);
#ifdef DIAGNOSTIX_STAGE_ONLY
 assert(freed==5&&!hooked&&!gpu&&pstate==0&&!scatter_plan.metadata_kva);
 assert(safe_allocated==1&&safe_freed==1);
 puts("actual stage-only sys_kexec: collision replacements and constrained-page probe verified; all five ordinary and every safe allocation freed; no hook/GPU/pstate changes PASS");
#else
 assert(freed==0&&hooked&&gpu&&pstate==3&&scatter_plan.metadata_kva);
 puts("actual sys_kexec: real scatter planner handles direct/cyclic/metadata collisions; all allocation/copy/header/plan/hook failures clean up before arm; success retains exact owned backing PASS");
#endif
 reset();
}
