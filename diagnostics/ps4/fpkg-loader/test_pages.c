// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#undef NULL
#undef offsetof
#define TESTING
#include "kernel.h"
struct ksym_t kern;
static void *map;
static unsigned calls, frees, fault, starts, ends, reads;
static vm_offset_t vm, freed;
static size_t wanted, free_bytes;
static char reason[256];
static char rejected_page[512];
static unsigned safe_calls, safe_fault, safe_reads;
static int safe_mode;
static u64 safe_pa = 0x30000000ULL;
static int capture(const char *fmt, ...)
{
    char row[512]; va_list a; va_start(a,fmt); int n=vsnprintf(row,sizeof(row),fmt,a); va_end(a);
    assert(n>0 && (size_t)n<sizeof(row));
    if(strstr(row,"begin requested=")) starts++;
    if(strstr(row,"end requested=")) ends++;
    if(strstr(row,"refused reason=")) strcpy(reason,row);
    if(strstr(row,"rejected_page vm=")) strcpy(rejected_page,row);
    return n;
}
static vm_offset_t allocate(vm_map_t m, vm_size_t size)
{
    assert(m==map && starts==calls+1); calls++;wanted=size;
    vm=0xffffa00000000000ULL + calls*0x1000000ULL;
    if(fault==1)return 0;
    if(fault==2)vm++;
    if(fault==3)vm=0x4000;
    if(fault==4)vm=~(vm_offset_t)PAGE_MASK;
    return vm;
}
static vm_offset_t allocate_safe(vm_map_t m, vm_size_t size, int flags,
    vm_paddr_t low, vm_paddr_t high, unsigned long alignment,
    unsigned long boundary, vm_memattr_t attr)
{
    /* Compile against the real native kernel.h, not a substitute signature. */
    assert(m == map && size == 0x4000 && flags == 0x102);
    assert(low == 0x10000000ULL && high == 0x70000000ULL);
    assert(alignment == 0x4000 && boundary == 0 && attr == 0x06);
    ++safe_calls;
    vm = 0xffffa10000000000ULL + safe_calls * 0x4000ULL;
    if (safe_fault == 1) return 0;
    if (safe_fault == 2) ++vm;
    if (safe_fault == 3) vm = 0x4000;
    if (safe_fault == 4) vm = ~(vm_offset_t)PAGE_MASK;
    return vm;
}
static vm_paddr_t extract(pmap_t p, vm_offset_t address)
{
    assert(p==kern.kernel_pmap_store && address>=vm);
    if (safe_mode) {
        u64 sub = address - vm;
        assert(sub < PAGE_SIZE && !(sub & 0xfff));
        ++safe_reads;
        if (safe_fault == 5) return 0;
        if (safe_fault == 6) return safe_pa + sub + 1;
        if (safe_fault == 7) return 0x0fffc000ULL + sub;
        if (safe_fault == 8) return 0x70000000ULL + sub;
        if (safe_fault == 9) return 0x100000000ULL + sub;
        if (safe_fault >= 10 && safe_fault <= 12 && sub == (safe_fault - 9) * 0x1000ULL)
            return safe_pa + sub + PAGE_SIZE;
        return safe_pa + sub;
    }
    reads++;
    u64 off=address-vm;
    /* Native pages scatter, while each page's four 4KiB mappings agree. */
    u64 pa=0x40000000ULL+(off/PAGE_SIZE)*2*PAGE_SIZE+(off&PAGE_MASK);
    if(fault==5)return 0;
    if(fault==6)return pa+1;
    if(fault==7)return 0x100000000ULL+(off&PAGE_MASK); /* VRAM */
    if(fault==8 && (off&PAGE_MASK)==0x2000)return pa+0x4000;
    if(fault==9)return 0x28000ULL+(off&PAGE_MASK);
    if(fault==10)return 0x400000ULL+(off&PAGE_MASK);
    return pa;
}
static void release(vm_map_t m, vm_offset_t a, vm_size_t n)
{assert(m==map); frees++;freed=a;free_bytes=n;}
#include "pages.inc"
static void reset(void)
{fault=0;reason[0]=0;rejected_page[0]=0;memset(boot_records,0,sizeof(boot_records));}
int main(void)
{
    map=(void*)1;kern=(struct ksym_t){.kern_base=0xffffffff82200000ULL,
       .kernel_map=&map,.kmem_alloc=allocate,.kmem_free=release,
       .pmap_extract=extract,.printf=capture,.kernel_pmap_store=(void*)2};
    reset(); assert(!kernel_alloc_boot(0));assert(!calls);
    assert(!kernel_alloc_boot(~(size_t)0));assert(!calls);
    assert(!kernel_alloc_boot(1024ULL*1024*1024+1));assert(!calls);
    kern.kmem_alloc=NULL;assert(!kernel_alloc_boot(1)&&!calls);kern.kmem_alloc=allocate;
    for(unsigned f=1;f<=8;f++){
        reset();fault=f;unsigned before=calls,old=frees;
        assert(!kernel_alloc_boot(PAGE_SIZE+1));assert(calls==before+1&&starts==calls&&ends==calls);
        assert(wanted==PAGE_SIZE+1&&strstr(reason,"refused reason="));
        if(f>=5 && f<=7)assert(strstr(rejected_page,"offset=0x0")&&strstr(rejected_page,"page_bytes=0x4000"));
        if(f==7)assert(strstr(rejected_page,"pa=0x100000000")&&strstr(rejected_page,"ram_span=0"));
        if(f==5)assert(strstr(reason,"page-unmapped"));
        if(f==6)assert(strstr(reason,"page-alignment"));
        if(f==7)assert(strstr(reason,"page-not-source-ram")&&strstr(rejected_page,"sub3=0x100003000"));
        assert(frees==old+(f!=1));
        if(f!=1)assert(freed==vm&&free_bytes==2*PAGE_SIZE);
    }
    for(unsigned f=9;f<=10;f++){
        reset();fault=f;unsigned before=frees;
        void *owned=kernel_alloc_boot(PAGE_SIZE);assert(owned&&!reason[0]&&!rejected_page[0]);
        kernel_free_boot(owned,PAGE_SIZE);assert(frees==before+1&&freed==vm&&free_bytes==PAGE_SIZE);
    }
    reset();unsigned old=reads;
    void *p=kernel_alloc_boot(3*PAGE_SIZE-7);assert(p==(void*)vm&&reads==old+12);
    assert(wanted==3*PAGE_SIZE-7);vm_offset_t original=vm;old=frees;
    kernel_free_boot(p,3*PAGE_SIZE-6);assert(frees==old);
    kernel_free_boot(p,3*PAGE_SIZE-7);assert(frees==old+1&&freed==original&&free_bytes==3*PAGE_SIZE);
    kernel_free_boot(p,3*PAGE_SIZE-7);assert(frees==old+1);
    reset();void *live[5];for(unsigned i=0;i<5;i++){live[i]=kernel_alloc_boot(1);assert(live[i]);}
    old=calls;assert(!kernel_alloc_boot(1)&&calls==old&&strstr(reason,"ledger-full"));
    for(unsigned i=0;i<5;i++)kernel_free_boot(live[i],1);
    /* A full ordinary ledger must not prevent separately-owned bounce pages. */
    reset();for(unsigned i=0;i<5;i++){live[i]=kernel_alloc_boot(1);assert(live[i]);}
    struct boot_record ledger[5];memcpy(ledger,boot_records,sizeof(ledger));
    kern.kmem_alloc_contig=allocate_safe;safe_mode=1;
    for(unsigned f=1;f<=12;f++){
        safe_fault=f;unsigned before=safe_calls,old_frees=frees;safe_reads=0;
        assert(!kernel_alloc_boot_safe_page());
        assert(safe_calls==before+1); /* no retries */
        assert(frees==old_frees+(f!=1));
        if(f!=1)assert(freed==vm&&free_bytes==PAGE_SIZE);
        if(f<=4)assert(!safe_reads);
        if(f>=10)assert(safe_reads==f-8); /* first bad 4KiB translation */
        assert(!memcmp(ledger,boot_records,sizeof(ledger)));
    }
    safe_fault=0;
    const u64 edges[]={0x10000000ULL,0x30000000ULL,0x6fffc000ULL};
    for(unsigned i=0;i<sizeof(edges)/sizeof(edges[0]);i++){
        safe_pa=edges[i];safe_reads=0;unsigned before=safe_calls,old_frees=frees;
        void *page=kernel_alloc_boot_safe_page();assert(page==(void*)vm);
        assert(safe_calls==before+1&&safe_reads==4&&frees==old_frees);
        vm_offset_t original_safe=vm;
        kernel_free_boot_safe_page(page);
        assert(frees==old_frees+1&&freed==original_safe&&free_bytes==PAGE_SIZE);
        assert(!memcmp(ledger,boot_records,sizeof(ledger)));
    }
    old=frees;
    kernel_free_boot_safe_page(NULL);kernel_free_boot_safe_page((void*)0x4000);
    kernel_free_boot_safe_page((void*)(vm+1));
    kernel_free_boot_safe_page((void*)~(vm_offset_t)PAGE_MASK);
    assert(frees==old);
    old=safe_calls;
    kern.kmem_alloc_contig=NULL;assert(!kernel_alloc_boot_safe_page()&&safe_calls==old);
    kern.kmem_alloc_contig=allocate_safe;
    kern.kmem_free=NULL;assert(!kernel_alloc_boot_safe_page()&&safe_calls==old);
    unsigned before_missing_free=frees;
    kernel_free_boot_safe_page((void*)vm);assert(frees==before_missing_free);
    kern.kmem_free=release;
    struct ksym_t native=kern;
    for(unsigned f=0;f<5;f++){
        kern=native;
        if(f==0)kern.kernel_map=NULL;
        if(f==1)kern.kernel_pmap_store=NULL;
        if(f==2)kern.pmap_extract=NULL;
        if(f==3)kern.kern_base=0;
        if(f==4)map=NULL;
        assert(!kernel_alloc_boot_safe_page()&&safe_calls==old&&frees==before_missing_free);
        map=(void*)1;
    }
    kern=native;
    safe_mode=0;for(unsigned i=0;i<5;i++)kernel_free_boot(live[i],1);
    puts("actual wired allocator: one noncontiguous native call, every4KiB translation, five-slot ownership and exact original-VA cleanup PASS");
    puts("actual safe-page ABI: eight native arguments, fixed physical range, four translations, one call and original-VA cleanup PASS");
}
