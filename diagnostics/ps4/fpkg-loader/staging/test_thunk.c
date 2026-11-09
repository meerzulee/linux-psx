// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#ifdef DIAGNOSTIX_FIXED_PHYSICAL_TEST
#include <sys/mman.h>
#undef NULL
#undef offsetof
#include "diagnostix_scatter.h"
#endif
struct record {uint64_t src,dst,bytes;};
struct node {uint64_t next,count;struct record records[682];};
extern int diag_scatter_copy_host(uint64_t head,uint64_t count);
#ifdef DIAGNOSTIX_FIXED_PHYSICAL_TEST
/* This composition runs with UBSan, separately from the ASan fixture: 6 GiB is
 * in ASan's reserved shadow gap. Addresses model physical memory in this host
 * process only. NOREPLACE always refuses an existing mapping; never clobber it. */
#define P DIAG_SCATTER_PAGE
struct fixed_fixture {
    void *metadata, *safe[8];
    unsigned safe_live, safe_frees, metadata_frees;
};
static struct diag_scatter_workspace fixed_workspace;
static struct diag_scatter_plan fixed_plan;
static void *map_exact(u64 pa, u64 bytes)
{
    void *p=mmap((void *)(uintptr_t)pa,bytes,PROT_READ|PROT_WRITE,
                 MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0);
    assert(p!=MAP_FAILED && "fixed physical fixture address unavailable; no mapping replaced");
    assert((u64)p==pa);return p;
}
static int contains(u64 pa,u64 bytes,u64 base,u64 span)
{return pa>=base&&pa-base<span&&bytes<=span-(pa-base);}
static int fixed_source(void *ctx,u64 pa,u64 bytes)
{
    struct fixed_fixture *f=ctx;
    if(bytes!=P)return 0;
    if(contains(pa,bytes,DIAG_SCATTER_KERNEL,2*P)|| /* original initrd */
       contains(pa,bytes,DIAG_SCATTER_INITRD+P,3*P)|| /* original image */
       contains(pa,bytes,DIAG_SCATTER_INITRD,P)|| /* pre-consumed aux */
       (f->metadata&&contains(pa,bytes,(u64)f->metadata,P)))return 1;
    for(unsigned i=0;i<f->safe_live;++i)
        if(contains(pa,bytes,(u64)f->safe[i],P))return 1;
    return 0;
}
static u64 fixed_physical(void *ctx,u64 va)
{
    return fixed_source(ctx,va&~(P-1),P)?va:0;
}
static int fixed_destination(void *ctx,u64 pa,u64 bytes)
{
    (void)ctx;
    return (pa==DIAG_SCATTER_KERNEL&&bytes==7*P)||
           (pa==DIAG_SCATTER_INITRD&&bytes==2*P);
}
static void *fixed_allocate(void *ctx,u64 bytes)
{
    struct fixed_fixture *f=ctx;assert(bytes==P&&!f->metadata);
    f->metadata=map_exact(DIAG_SCATTER_KERNEL+2*P,P);
    return f->metadata;
}
static void fixed_free(void *ctx,void *p,u64 bytes)
{
    struct fixed_fixture *f=ctx;assert(p==f->metadata&&bytes==P);
    assert(!munmap(p,bytes));f->metadata=NULL;++f->metadata_frees;
}
static void *fixed_allocate_safe(void *ctx)
{
    struct fixed_fixture *f=ctx;assert(f->safe_live<8);
    void *p=map_exact(0x40000000+f->safe_live*P,P);
    f->safe[f->safe_live++]=p;return p;
}
static void fixed_free_safe(void *ctx,void *p)
{
    struct fixed_fixture *f=ctx;assert(f->safe_live&&p==f->safe[f->safe_live-1]);
    assert(!munmap(p,P));--f->safe_live;++f->safe_frees;
}
static void assert_canary(const u8 *p,u64 bytes)
{for(u64 i=0;i<bytes;++i)assert(p[i]==0xa5);}
static void planned_collision_copy(void)
{
    struct fixed_fixture f={0};
    u8 *kernel_region=map_exact(DIAG_SCATTER_KERNEL-P,3*P);
    u8 *kernel_after=map_exact(DIAG_SCATTER_KERNEL+3*P,P);
    u8 *initrd_region=map_exact(DIAG_SCATTER_INITRD-P,6*P);
    memset(kernel_region,0xa5,3*P);memset(kernel_after,0xa5,P);memset(initrd_region,0xa5,6*P);
    u8 *original_initrd=(void *)(uintptr_t)DIAG_SCATTER_KERNEL;
    u8 *aux=(void *)(uintptr_t)DIAG_SCATTER_INITRD;
    u8 *image=aux+P;
    for(u64 i=0;i<2*P;++i)original_initrd[i]=(u8)(i*29+i/P*13+3);
    for(u64 i=0;i<3*P;++i)image[i]=(u8)(i*37+i/P*11+5);
    memset(aux,0x7b,P);
    u8 *before_image=malloc(3*P),*before_initrd=malloc(2*P),*low_aux=malloc(P);
    assert(before_image&&before_initrd&&low_aux);
    memcpy(before_image,image,3*P);memcpy(before_initrd,original_initrd,2*P);
    struct diag_scatter_buffer auxiliary={aux,P};
    struct diag_scatter_input in={
        .image={image,3*P},.image_bytes=3*P,.setup_bytes=0x1230,
        .protected_bytes=2*P+57,.kernel_init_size=7*P,
        .initrd={original_initrd,2*P},.initrd_bytes=P+17,.aux=&auxiliary,.aux_count=1};
    struct diag_scatter_ops o={&f,fixed_allocate,fixed_free,fixed_physical,
        fixed_destination,fixed_source,fixed_allocate_safe,fixed_free_safe};
    assert(!diag_scatter_prepare(&o,&in,&fixed_workspace,&fixed_plan));
    assert(fixed_plan.safe_count==4&&fixed_plan.node_count==1&&fixed_plan.total_records==7);
    assert(fixed_plan.physical_head!=DIAG_SCATTER_KERNEL+2*P);
    assert(!memcmp(image,before_image,3*P)&&!memcmp(original_initrd,before_initrd,2*P));
    for(u64 i=0;i<P;++i)assert(aux[i]==0x7b);
    /* Model the real phase order: aux is consumed before the terminal thunk. */
    memcpy(low_aux,aux,P);
    assert(diag_scatter_copy_host(fixed_plan.physical_head,fixed_plan.total_records)==0);
    assert(!memcmp((void *)(uintptr_t)DIAG_SCATTER_KERNEL,before_image+in.setup_bytes,in.protected_bytes));
    assert(!memcmp((void *)(uintptr_t)DIAG_SCATTER_INITRD,before_initrd,in.initrd_bytes));
    for(u64 i=in.protected_bytes;i<3*P;++i)assert(((u8 *)(uintptr_t)DIAG_SCATTER_KERNEL)[i]==0);
    for(u64 i=in.initrd_bytes;i<2*P;++i)assert(((u8 *)(uintptr_t)DIAG_SCATTER_INITRD)[i]==0);
    assert_canary(kernel_region,P);assert_canary(kernel_after,P);
    assert_canary(initrd_region,P);assert_canary(aux+4*P,P);
    assert(!memcmp(image+P,before_image+P,2*P));
    for(u64 i=0;i<P;++i)assert(low_aux[i]==0x7b);
    diag_scatter_release(&o,&fixed_plan);diag_scatter_release(&o,&fixed_plan);
    assert(f.safe_frees==4&&f.metadata_frees==1&&!f.safe_live);
    assert(!munmap(kernel_region,3*P));assert(!munmap(kernel_after,P));assert(!munmap(initrd_region,6*P));
    free(before_image);free(before_initrd);free(low_aux);
    puts("planner plus actual assembly: physical two-way cycle, destination metadata bounce, consumed aux, exact payloads/tails, canaries and cleanup PASS");
}
#endif
int main(void)
{
 struct node *a=calloc(1,sizeof(*a)),*b=calloc(1,sizeof(*b));assert(a&&b);
 unsigned char *src=malloc(32768),*dst=malloc(32770);assert(src&&dst);
 for(unsigned i=0;i<32768;i++)src[i]=(unsigned char)(i*31+7);
 memset(dst,0xa5,32770);
 a->next=(uintptr_t)b;a->count=1;a->records[0]=(struct record){(uintptr_t)src,(uintptr_t)(dst+1),16384};
 b->count=2;b->records[0]=(struct record){(uintptr_t)(src+16384),(uintptr_t)(dst+16385),47};
 b->records[1]=(struct record){0,(uintptr_t)(dst+16432),16337};
 assert(diag_scatter_copy_host((uintptr_t)a,3)==0);
 assert(!memcmp(src,dst+1,16431));for(unsigned i=16432;i<=32768;i++)assert(dst[i]==0);
 assert(dst[0]==0xa5&&dst[32769]==0xa5);
 assert(diag_scatter_copy_host((uintptr_t)a,0)==1);
 assert(diag_scatter_copy_host((uintptr_t)a,40001)==1);
 assert(diag_scatter_copy_host((uintptr_t)a,4)==1);
 a->count=683;assert(diag_scatter_copy_host((uintptr_t)a,1)==1);
 a->count=0;assert(diag_scatter_copy_host((uintptr_t)a,1)==1);
 a->count=1;a->next=0;a->records[0].bytes=0;assert(diag_scatter_copy_host((uintptr_t)a,1)==1);
 a->records[0].bytes=16385;assert(diag_scatter_copy_host((uintptr_t)a,1)==1);
 a->records[0].bytes=1;a->next=(uintptr_t)a;assert(diag_scatter_copy_host((uintptr_t)a,1)==1);
 free(a);free(b);free(src);free(dst);
#ifdef DIAGNOSTIX_FIXED_PHYSICAL_TEST
 planned_collision_copy();
#endif
 puts("actual assembly copy loop: chained scattered descriptors, partial input/zero padding, canaries, counts and cycle bound PASS");
}
