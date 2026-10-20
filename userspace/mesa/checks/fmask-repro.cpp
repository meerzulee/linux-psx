// CPU-only AddrLib public-API regression using EXP-20261020-016 register tables.
// Link against the actual Mesa libaddrlib.a; no GPU access. SPDX-License-Identifier: MIT
#include "addrinterface.h"
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <unistd.h>
#include <initializer_list>
static void *ADDR_API alloc_cb(const ADDR_ALLOCSYSMEM_INPUT *i){return malloc(i->sizeInBytes);}
static ADDR_E_RETURNCODE ADDR_API free_cb(const ADDR_FREESYSMEM_INPUT *i){free(i->pVirtAddr);return ADDR_OK;}
static ADDR_E_RETURNCODE ADDR_API print_cb(const ADDR_DEBUGPRINT_INPUT *i){va_list ap;va_copy(ap,const_cast<ADDR_DEBUGPRINT_INPUT*>(i)->ap);vfprintf(stderr,i->pDebugString,ap);va_end(ap);return ADDR_OK;}
static void trap(int){_exit(101);}
static UINT_32 tile[32]={8389392,8391440,8393488,8395536,8397584,8389384,8393496,8397592,772,776,33555216,33555092,33555224,4195080,37749520,0,37749396,37749528,0,0,0,0,0,0,0,0,0,12583688,46138128,46138004,46138136,0};
static UINT_32 macro[16]={232,212,208,208,128,64,0,0,236,232,212,208,128,64,0,0};
int main(int argc,char **argv){
 signal(SIGTRAP,trap);
 int mode=argc>1?atoi(argv[1]):0;
 // Mode1 restores a synthetic valid unused3D entry, serving as a2D layout control.
 if(mode==1||mode==4)tile[15]=(tile[14]&~0x3cu)|(12u<<2);
 if(mode==3)tile[14]=0;
 ADDR_CREATE_INPUT in={};ADDR_CREATE_OUTPUT out={};
 in.size=sizeof(in);out.size=sizeof(out);in.chipEngine=0xA;
in.chipFamily=125;in.chipRevision=97;in.regValue.gbAddrConfig=33624067;in.regValue.noOfBanks=1;in.regValue.noOfRanks=0;
 in.regValue.backendDisables=15;in.regValue.pTileConfig=tile;in.regValue.noOfEntries=32;
 in.regValue.pMacroTileConfig=macro;in.regValue.noOfMacroEntries=16;
 in.createFlags.useTileIndex=1;in.createFlags.useHtileSliceAlign=1;
 in.callbacks.allocSysMem=alloc_cb;in.callbacks.freeSysMem=free_cb;in.callbacks.debugPrint=print_cb;
 auto ret=AddrCreate(&in,&out);if(ret!=ADDR_OK){fprintf(stderr,"create=%d\n",ret);return 2;}
 for(unsigned samples: {2u,4u,8u}){
  ADDR_COMPUTE_FMASK_INFO_INPUT f={};ADDR_COMPUTE_FMASK_INFO_OUTPUT o={};
  f.size=sizeof(f);o.size=sizeof(o);f.tileMode=(mode==2||mode==4)?ADDR_TM_3D_TILED_THIN1:ADDR_TM_2D_TILED_THIN1;
  f.pitch=128;f.height=16;f.numSlices=1;f.numSamples=samples;f.numFrags=samples;f.tileIndex=-1;
  ret=AddrComputeFmaskInfo(out.hLib,&f,&o);
  printf("mode=%d samples=%u status=%d tile=%d pitch=%u height=%u bytes=%llu align=%u internal_pointer=%s\n",mode,samples,ret,o.tileIndex,o.pitch,o.height,(unsigned long long)o.fmaskBytes,o.baseAlign,o.pTileInfo?"set":"null");fflush(stdout);
  if((mode==2||mode==3)?ret!=ADDR_INVALIDPARAMS:ret!=ADDR_OK)return 3;
 }
 AddrDestroy(out.hLib);return 0;
}
