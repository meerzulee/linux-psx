// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
static off_t length=3;
static int fail_open,fail_map,fail_seek,fail_read,fail_close,extra,closed,unmapped,reads;
static int lazy,changed_length,null_map,seeks,maps,map_active;
static void *memory;static size_t mapped_bytes,offset;
static int fake_open(const char *p,int flags)
{
 assert(strncmp(p,"/data/diagnostix/versions/0.16/boot/",strlen("/data/diagnostix/versions/0.16/boot/"))==0);
 if(lazy)assert(!strcmp(p,"/data/diagnostix/versions/0.16/boot/initramfs.cpio.gz"));
 assert(flags==(O_RDONLY|O_NOFOLLOW));return fail_open?-1:7;
}
static off_t fake_seek(int fd,off_t off,int whence)
{
 assert(fd==7&&off==0);assert(whence==SEEK_END||(!lazy&&whence==SEEK_SET));seeks++;
 if(seeks==fail_seek)return -1;
 return whence==SEEK_END?length+(changed_length&&seeks==2):0;
}
static void *fake_map(void *a,size_t n,int prot,int flags,int fd,off_t off)
{
 assert(!a&&off==0&&!map_active);maps++;mapped_bytes=n;
 if(lazy)assert(prot==PROT_READ&&flags==MAP_PRIVATE&&fd==7&&n==(size_t)length);
 else assert(prot==(PROT_READ|PROT_WRITE)&&flags==(MAP_PRIVATE|MAP_ANONYMOUS)&&fd==-1&&n==(size_t)length+1);
 if(fail_map)return MAP_FAILED;
 map_active=1;
 if(null_map)return memory=NULL;
 /* Any eager read, write or sentinel access by the production helper faults.
  * This private host mapping reserves virtual space only, including at max. */
 if(lazy)memory=mmap(NULL,n,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
 else memory=calloc(1,n);
 assert(memory&&memory!=MAP_FAILED);return memory;
}
static ssize_t fake_read(int fd,void *p,size_t n)
{assert(fd==7&&!lazy);reads++;if(fail_read&&reads==2)return -1;if(offset==(size_t)length)return extra?1:0;if(n>2)n=2;memset(p,'a',n);offset+=n;return n;}
static int fake_close(int fd){assert(fd==7);closed++;return fail_close?-1:0;}
static int fake_unmap(void *p,size_t n)
{
 assert(map_active&&p==memory&&n==mapped_bytes);
 if(lazy&&!null_map)assert(munmap(p,n)==0);else if(!null_map)free(p);
 memory=NULL;map_active=0;unmapped++;return 0;
}
#define open fake_open
#define lseek fake_seek
#define mmap fake_map
#define read fake_read
#define close fake_close
#define munmap fake_unmap
#include "io.inc"
static void reset(void)
{
 assert(!memory&&!map_active);length=3;offset=mapped_bytes=0;
 fail_open=fail_map=fail_seek=fail_read=fail_close=extra=closed=unmapped=reads=0;
 lazy=changed_length=null_map=seeks=maps=0;
}
int main(void)
{
 char *p=(char*)1;unsigned long long n=99;
 assert(read_file("/data/diagnostix/versions/0.16/boot/bzImage",&p,&n,64)==0);assert(n==3&&!memcmp(p,"aaa\0",4)&&closed==1);fake_unmap(p,n+1);
 for(int kind=0;kind<9;kind++){
  reset();p=(char*)1;n=99;
  if(kind==0)fail_open=1;
  if(kind==1)fail_map=1;
  if(kind==2)fail_seek=1;
  if(kind==3)fail_read=1;
  if(kind==4)extra=1;
  if(kind==5)length=0;
  if(kind==6)length=-1;
  if(kind==7)fail_close=1;
  if(kind==8)fail_seek=2;
  assert(read_file("/data/diagnostix/versions/0.16/boot/bzImage",&p,&n,64)==-1);assert(!p&&!n&&!memory);assert(closed==(kind!=0));
 }
 /* The actual lazy helper cannot touch even one input byte. */
 const off_t sizes[]={18,4097,DIAG_INITRD_MAX};
 for(size_t i=0;i<sizeof(sizes)/sizeof(sizes[0]);i++){
  reset();lazy=1;length=sizes[i];p=(char*)1;n=99;
  assert(map_initramfs(&p,&n)==0&&p==memory&&n==(unsigned long long)length);
  assert(reads==0&&maps==1&&closed==1&&seeks==2&&!unmapped);
  fake_unmap(p,n);assert(unmapped==1);
 }
 for(int kind=0;kind<11;kind++){
  reset();lazy=1;length=18;p=(char*)1;n=99;
  if(kind==0)fail_open=1;
  if(kind==1)fail_map=1;
  if(kind==2)fail_seek=1;
  if(kind==3)length=-1;
  if(kind==4)length=0;
  if(kind==5)length=17;
  if(kind==6)length=DIAG_INITRD_MAX+1;
  if(kind==7)fail_seek=2;
  if(kind==8)changed_length=1;
  if(kind==9)fail_close=1;
  if(kind==10)null_map=1;
  assert(map_initramfs(&p,&n)==-1&&!p&&!n&&!memory&&!map_active);
  assert(!reads&&closed==(kind!=0)&&maps<=1);
  assert(unmapped==(kind>=7));
 }
 char cmd[513];memset(cmd,'x',sizeof(cmd));cmd[511]=0;assert(valid_cmdline(cmd,511));assert(!valid_cmdline(cmd,512));cmd[5]=0;assert(!valid_cmdline(cmd,511));assert(!valid_cmdline(cmd,0));
 char line[]="panic=0\n";assert(valid_cmdline(line,sizeof(line)-1));assert(!strcmp(line,"panic=0"));
 assert(valid_vram("1024",4)&&valid_vram("1024\n",5));
 assert(!valid_vram("2048",4)&&!valid_vram("1024\r\n",6)&&!valid_vram("1024x",5)&&!valid_vram("102",3)&&!valid_vram("",0));
 puts("actual readers: partial/signed/EOF cleanup; lazy fixed-file mapping, zero data access, exact bounds/cleanup and cmdline bounds PASS");
}
