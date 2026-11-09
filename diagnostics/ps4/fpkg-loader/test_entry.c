// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define SIG_IGN ((void (*)(int))1)
#define SIGSTOP 19
#define SIGTERM 15
#define SIGKILL 9
struct sigaction { void (*sa_handler)(int); int sa_flags; };
typedef uint16_t u16;
static void *g_fw;static char blob[2],*g_kexec_s,*g_kexec_e;
static int g_kexec_error,fw=1202,sb=0x30201,reads,unmaps,fail_read,load_error,bootstrap_error,rebooted,loads,bad_line,bad_vram;
static void *buffers[4];
#define DIAG_KERNEL_MAX (64ULL*1024*1024)
#define DIAG_INITRD_MAX (512ULL*1024*1024)
#define DIAG_CMDLINE_MAX 511ULL
static int sigaction(int signal,const struct sigaction *a,struct sigaction *b){(void)signal;(void)a;(void)b;return 0;}
static u16 get_firmware(void){return fw;}
static u16 normalize_fw_ver(u16 v){return v==1202?1200:v;}
static void *find_offsets_by_fw(u16 v){return v==1200?(void*)1:NULL;}
static void get_kexec_blob(u16 v,char **s,char **e){assert(v==1200);*s=blob;*e=blob+2;}
static int get_sb_id(void){return sb;}
static int fake_input(const char *path,char **p,unsigned long long *n,unsigned long long max,int lazy)
{
 static const char *names[]={"/data/diagnostix/versions/0.16/boot/vram.txt","/data/diagnostix/versions/0.16/boot/bzImage","/data/diagnostix/versions/0.16/boot/initramfs.cpio.gz","/data/diagnostix/versions/0.16/boot/bootargs.txt"};
 static const unsigned long long limits[]={16,DIAG_KERNEL_MAX,DIAG_INITRD_MAX,DIAG_CMDLINE_MAX};
 assert(reads<4&&!strcmp(path,names[reads])&&max==limits[reads]&&lazy==(reads==2));reads++;if(reads==fail_read){*p=NULL;*n=0;return -1;}
 *n=reads==1?5:7;*p=calloc(1,*n+1);assert(*p);memcpy(*p,reads==1?"1024\n":"panic=0",*n);buffers[reads-1]=*p;return 0;
}
static int read_file(const char *path,char **p,unsigned long long *n,unsigned long long max)
{return fake_input(path,p,n,max,0);}
static int map_initramfs(char **p,unsigned long long *n)
{return fake_input("/data/diagnostix/versions/0.16/boot/initramfs.cpio.gz",p,n,DIAG_INITRD_MAX,1);}
static int valid_vram(const char *p,unsigned long long n){assert(p&&n==5);return !bad_vram;}
static int valid_cmdline(char *p,unsigned long long n){assert(p&&n);return !bad_line;}
static void alert(const char *s){assert(s);}
static void log_msg(const char *s){assert(s);}
static void kernel_main(void){assert(0);}
static void kexec(void (*fn)(void),void *arg){assert(fn==kernel_main&&!arg&&reads==4);g_kexec_error=bootstrap_error;}
static int pack_kexec_args(int v,u16 f,int b){assert(v==1024&&f==1202&&b==0x30201);return 1;}
static int kexec_load(char *a,unsigned long long b,char *c,unsigned long long d,char *e,int v)
{assert(a&&b&&c&&d&&e&&v==1&&reads==4&&!rebooted);loads++;return load_error;}
static void __attribute__((unused)) reboot_thread(void *p){assert(!p&&loads==1&&!load_error&&!bootstrap_error);rebooted++;}
static int munmap(void *p,size_t n){for(int i=0;i<4;i++)if(buffers[i]==p){assert(n==(i==0?6:i==2?7:8));buffers[i]=NULL;free(p);unmaps++;return 0;}assert(0);return 0;}
#define main diag_entry
#include "entry.inc"
#undef main
int main(void)
{
 for(int kind=0;kind<10;kind++){
  fw=1202;sb=0x30201;reads=unmaps=fail_read=load_error=bootstrap_error=rebooted=loads=bad_line=bad_vram=0;
  if(kind<4)fail_read=kind+1;
  if(kind==4)load_error=1;
  if(kind==5)bootstrap_error=1;
  if(kind==6)bad_line=1;
  if(kind==7)fw=1200;
  if(kind==8)sb=0x30200;
  if(kind==9)bad_vram=1;
  assert(diag_entry()!=0&&!rebooted);for(int i=0;i<4;i++)assert(!buffers[i]);
 }
 fw=1202;sb=0x30201;reads=unmaps=fail_read=load_error=bootstrap_error=rebooted=loads=bad_line=bad_vram=0;
 assert(diag_entry()==0&&unmaps==4);
#ifdef DIAGNOSTIX_STAGE_ONLY
 assert(rebooted==0);puts("actual stage-only entry: real paths, exact cleanup and no reboot on success or failure PASS");
#else
 assert(rebooted==1);puts("actual entry: lazy initrd, exact cleanup, fixed paths; failures never request reboot PASS");
#endif
}
