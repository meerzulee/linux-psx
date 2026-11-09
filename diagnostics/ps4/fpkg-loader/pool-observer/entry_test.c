// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <sys/types.h>
#include "pool_core.h"
static unsigned fw=1202,queries,invocations,mode;
static char output[1024];static size_t used;
#define main observer_entry
#define write fake_write
#include "observer.c"
#undef main
#undef write
ssize_t fake_write(int fd,const void *p,size_t n){assert(fd==1&&used+n<sizeof(output));memcpy(output+used,p,n);used+=n;output[used]=0;return n;}
u16 get_firmware(void){return fw;}
long __sysctl(const int *oid,unsigned count,void *p,size_t *n,const void *newp,size_t newlen)
{
 queries++;
 if(queries==1){assert(count==2&&oid[0]==0&&oid[1]==3&&newp&&newlen==sizeof("hw.sce_subsys_subid")-1&&!memcmp(newp,"hw.sce_subsys_subid",newlen));if(mode==1)return -1;if(mode==2){*n=1000;return 0;}if(mode==3){*n=3;return 0;}int *m=p;m[0]=6;m[1]=99;*n=2*sizeof(int);return 0;}
 assert(queries==2&&count==2&&oid[0]==6&&oid[1]==99&&!newp&&!newlen&&*n==4);
 if(mode==4)return -1;*(pool_u32*)p=mode==5?0x30200:0x30201;*n=mode==6?2:4;return 0;
}
long kexec(void (*f)(void),void *arg)
{
 assert(f==collect_callback&&!arg&&queries==2&&raw_fw==1202&&subid==0x30201);invocations++;
 /* Never execute the privileged callback on the host. Simulate only its
  * caller-visible owned result so entry gate and completion handling run. */
 if(mode!=7){callback_called=1;snapshot.complete=1;}
 return 0;
}
int main(void)
{
 for(unsigned i=0;i<9;i++){
  mode=i;fw=i==8?1200:1202;queries=invocations=used=0;output[0]=0;
  int r=observer_entry();
  if(!i)assert(r==0&&invocations==1&&strstr(output,"REPORT complete"));
  else {assert(r==1&&strstr(output,"Orbis remains running"));assert(invocations==(i==7));}
  if(i==8)assert(!queries);
 }
 puts("actual entry: exact firmware/Baikal gates; bounded read-only sysctl lookup; zero-arg callback; no privileged host execution PASS");
}
