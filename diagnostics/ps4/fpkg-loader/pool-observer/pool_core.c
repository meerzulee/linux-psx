/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 Meerzulee and Linux psX contributors. */
#include "pool_core.h"
static void zero(void *p,pool_u64 n){volatile pool_u8 *b=p;while(n--)*b++=0;}
static pool_u64 le(const pool_u8 *b,unsigned n){pool_u64 v=0;for(unsigned i=0;i<n;i++)v|=(pool_u64)b[i]<<(8*i);return v;}
static pool_u64 fnv(const pool_u8 *b,unsigned n){pool_u64 h=0xcbf29ce484222325ULL;for(unsigned i=0;i<n;i++)h=(h^b[i])*0x100000001b3ULL;return h;}
int pool_base_valid(pool_u64 b){return b>=0xffff800000000000ULL && !(b&0x3fff) && b<=~(pool_u64)0-POOL_MAX_OFFSET;}
int pool_read_allowed(pool_u64 base,pool_u64 addr,pool_u32 n)
{
 if(!pool_base_valid(base)||addr<base)return 0;
 pool_u64 o=addr-base;
 if(o>=POOL_MAX_OFFSET||!n||n>40||n>POOL_MAX_OFFSET-o)return 0;
 if(n==32&&(o==0x2d6570||o==0x2d56a0))return 1;
 if(n==4&&(o==0x2233704||o==0x1a50358))return 1;
 if(n==1&&o==0x2233be8)return 1;
 if(n==8&&(o==0x21f54d8||o==0x21f5528||o==0x21f5568||o==0x2234a90||o==0x2234a98))return 1;
 if(n==40&&o==0x2235200)return 1;
 if(n==40&&o>=0x2233710&&o<0x2233be8&&(o-0x2233710)%40==0&&n<=0x2233be8-o)return 1;
 if(n==4&&o>=POOL_NORMAL_BASE+16&&o<POOL_NORMAL_BASE+2*POOL_SET_BYTES&&(o-POOL_NORMAL_BASE-16)%24==0)return 1;
 if(n==4&&o>=POOL_SPECIAL_BASE+16&&o<POOL_SPECIAL_BASE+POOL_SET_BYTES&&(o-POOL_SPECIAL_BASE-16)%24==0)return 1;
 return 0;
}
static int fail(struct pool_snapshot *s,unsigned status,pool_u64 offset)
{unsigned reads=s->reads;zero(s,sizeof(*s));s->status=status;s->reads=reads;s->failed_offset=offset;return status;}
static int get(pool_reader r,void *ctx,pool_u64 base,pool_u64 off,void *p,unsigned n,struct pool_snapshot *s)
{
 s->failed_offset=off;
 if(!pool_read_allowed(base,base+off,n))return 0;
 s->reads++;return r(ctx,base+off,p,n);
}
static void segment(struct pool_segment *s,const pool_u8 *b)
{s->start=le(b,8);s->end=le(b+8,8);s->page_array=le(b+16,8);s->domain=(pool_u32)le(b+24,4);s->free_queues=le(b+32,8);}
int pool_collect(pool_u32 fw,pool_u32 sb,pool_u64 base,pool_reader r,void *ctx,struct pool_snapshot *s)
{
 pool_u8 b[40];pool_u64 off;
 zero(s,sizeof(*s));
 if(fw!=1202||sb!=0x30201)return fail(s,POOL_PROFILE,0);
 if(!r||!pool_base_valid(base))return fail(s,POOL_BASE,0);
 static const pool_u64 fingerprints[2][2]={
#ifdef PSX_POOL_HOST_TEST
  /* Synthetic fixtures only; production build cannot override its profile. */
  {0x2d6570,0x1d552c65238459c5ULL},{0x2d56a0,0xc4cfc9895fb01325ULL}
#else
  {0x2d6570,0x9b6ecd5dac970aacULL},{0x2d56a0,0xd00589007330840aULL}
#endif
 };
 for(unsigned i=0;i<2;i++){
  off=fingerprints[i][0];if(!get(r,ctx,base,off,b,32,s))return fail(s,POOL_READ,off);
  if(fnv(b,32)!=fingerprints[i][1])return fail(s,POOL_FINGERPRINT,off);
 }
#define GET(o,n) do {off=(o);if(!get(r,ctx,base,off,b,(n),s))return fail(s,POOL_READ,off);} while(0)
 GET(0x2233704,4);s->normal_segments=(pool_u32)le(b,4);
 if(!s->normal_segments||s->normal_segments>POOL_MAX_SEGMENTS||0x2233710ULL+s->normal_segments*40>0x2233be8ULL)return fail(s,POOL_SEGMENTS,off);
 GET(0x2233be8,1);if(b[0]>1)return fail(s,POOL_FREELISTS,off);s->active_freelists=b[0]+1;
 GET(0x1a50358,4);s->profile_mode=(pool_u32)le(b,4);
 GET(0x21f54d8,8);s->game_dmem_start=le(b,8);
 GET(0x21f5528,8);s->game_dmem_end=le(b,8);
 GET(0x21f5568,8);s->fmem_max=le(b,8);
 for(unsigned f=0;f<s->active_freelists;f++){
  GET(0x2234a90ULL+f*8,8);
  if(le(b,8)!=base+POOL_NORMAL_BASE+f*POOL_SET_BYTES)return fail(s,POOL_HEAD,off);
 }
 for(unsigned i=0;i<s->normal_segments;i++){GET(0x2233710ULL+i*40,40);segment(&s->normal[i],b);}
 GET(0x2235200,40);segment(&s->special,b);
 if(s->special.free_queues!=base+POOL_SPECIAL_BASE)return fail(s,POOL_SPECIAL,off);
 for(unsigned f=0;f<s->active_freelists;f++)for(unsigned sel=0;sel<2;sel++)for(unsigned p=0;p<3;p++)for(unsigned order=0;order<13;order++){
  GET(POOL_NORMAL_BASE+((((f*2+sel)*3+p)*13+order)*24ULL)+16,4);
  s->normal_count[f][sel][p][order]=(pool_u32)le(b,4);
 }
 for(unsigned sel=0;sel<2;sel++)for(unsigned p=0;p<3;p++)for(unsigned order=0;order<13;order++){
  GET(POOL_SPECIAL_BASE+((sel*3+p)*13+order)*24ULL+16,4);
  s->special_count[sel][p][order]=(pool_u32)le(b,4);
 }
#undef GET
 s->kernel_base=base;s->failed_offset=0;s->complete=1;s->status=POOL_OK;return POOL_OK;
}
