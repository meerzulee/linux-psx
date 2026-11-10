#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Meerzulee and Linux psX contributors.
"""Build the dedicated FW12.02/Baikal B1 Diagnostix loader; never deploy it."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

HERE=Path(__file__).resolve().parent
PIN='d0f4d4c0bbc9e8b4870300781b352605ed2bc91d'
ARCHIVE_SHA='0650db2830f918625d7768a604b8f7c702339c1d716330d08d10748992e32e4a'
CHANGED=('linux/main-aio.c','linux/ps4-kexec-common/kernel.c',
         'linux/ps4-kexec-common/kernel.h','linux/ps4-kexec-common/kexec.c',
         'linux/ps4-kexec-common/acpi.h','linux/ps4-kexec-common/linux_boot.c',
         'linux/ps4-kexec-common/linux_boot.h',
         'linux/ps4-kexec-common/firmware.c','linux/ps4-kexec-common/firmware.h')
SCATTER_CHANGED=('linux/Makefile','linux/ps4-kexec-common/Makefile',
                 'linux/ps4-kexec-common/linux_thunk.S')
OVERLAYS=('diagnostix_pages.inc','diagnostix_scatter.c','diagnostix_scatter.h','linux_thunk.S')

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def run(argv,cwd=None,env=None):
    return subprocess.check_output(argv,cwd=cwd,env=env,text=True).strip()

def verify_reference(reference):
    if run(['git','rev-parse','HEAD'],reference)!=PIN or run(['git','status','--porcelain=v1','--untracked-files=all'],reference):
        raise ValueError('Loader reference must be clean at exact reviewed commit')

def build(args):
    reference=args.loader_tree.resolve(strict=True);verify_reference(reference)
    output=args.output.absolute()
    if output.exists() or output.is_symlink():raise ValueError('Output must be new')
    if not 1<=args.jobs<=16:raise ValueError('Use 1 through 16 native jobs')
    env=dict(os.environ)
    if args.yasm:
        yasm=args.yasm.resolve(strict=True)
        if yasm.name!='yasm':raise ValueError('Expected an executable named yasm')
        env['PATH']=str(yasm.parent)+os.pathsep+env.get('PATH','')
    tools={}
    for name in ('gcc','ld','objcopy','yasm'):
        path=shutil.which(name,path=env.get('PATH'))
        if not path:raise ValueError('Missing native tool: '+name)
        tools[name]={'path':path,'sha256':sha(path),'version':run([path,'--version']).splitlines()[0]}
    inputs={str(p.relative_to(HERE)):sha(p) for p in sorted(HERE.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    output.mkdir(parents=True,exist_ok=False);(output/'tmp').mkdir();env['TMPDIR']=str(output/'tmp')
    archive=output/'upstream-source.tar'
    with archive.open('xb') as f:subprocess.run(['git','archive','--format=tar',PIN],cwd=reference,stdout=f,check=True)
    if sha(archive)!=ARCHIVE_SHA:raise ValueError('Pinned upstream archive differs')
    source=output/'loader-source';source.mkdir()
    with tarfile.open(archive) as t:t.extractall(source,filter='data')
    original={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file()}
    patch=HERE/'dedicated-loader.patch'
    subprocess.run(['git','apply','--check','--whitespace=error',str(patch)],cwd=source,check=True)
    subprocess.run(['git','apply','--whitespace=error',str(patch)],cwd=source,check=True)
    scatter_patch=HERE/'staging/scatter-integration.patch'
    subprocess.run(['git','apply','--check','--whitespace=error',str(scatter_patch)],cwd=source,check=True)
    subprocess.run(['git','apply','--whitespace=error',str(scatter_patch)],cwd=source,check=True)
    slim_patch=HERE/'slim-loader.patch'
    subprocess.run(['git','apply','--check','--whitespace=error',str(slim_patch)],cwd=source,check=True)
    subprocess.run(['git','apply','--whitespace=error',str(slim_patch)],cwd=source,check=True)
    for version_patch in (HERE/'version-0.15.patch',HERE/'version-0.16.patch',HERE/'uvd-firmware.patch'):
        subprocess.run(['git','apply','--check','--whitespace=error',str(version_patch)],cwd=source,check=True)
        subprocess.run(['git','apply','--whitespace=error',str(version_patch)],cwd=source,check=True)
    for name in OVERLAYS:
        shutil.copy2(HERE/'staging'/name,source/'linux/ps4-kexec-common'/name)
    adapted={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file()}
    added={'linux/ps4-kexec-common/'+n for n in OVERLAYS if n!='linux_thunk.S'}
    if set(adapted)!=set(original)|added or {n for n in original if original[n]!=adapted[n]}!=set(CHANGED)|set(SCATTER_CHANGED):
        raise ValueError('Patch changed unexpected source inputs')
    (output/'SOURCE.json').write_text(json.dumps(adapted,indent=2,sort_keys=True)+'\n')
    spec=importlib.util.spec_from_file_location('diagnostix_loader_host_check',HERE/'check.py')
    checks=importlib.util.module_from_spec(spec);spec.loader.exec_module(checks)
    check=checks.check(source,output/'host-check')
    metadata={'schema_version':1,'status':'building','upstream_commit':PIN,'upstream_archive_sha256':ARCHIVE_SHA,
      'input_sha256':inputs,'toolchain':tools,'changed_sources':{n:{'before':original.get(n),'after':adapted[n]} for n in sorted(set(CHANGED)|set(SCATTER_CHANGED)|added)},
      'mode':'stage-only' if args.stage_only else 'boot',
      'profile':{'boot_directory':'/data/diagnostix/versions/0.16/boot/','required_vram_file':'1024 or 1024 newline; no override', 'firmware_raw':1202,'firmware_normalized':1200,
                 'southbridge_subid':0x30201,'vram_mib':1024,'kernel_max_bytes':64*1024**2,
                 'initramfs_max_bytes':512*1024**2,'cmdline_max_bytes':511,'fallback':False,
                 'allocation':'one native wired kmem_alloc per buffer, independent 16KiB physical pages; validated linked physical relocation descriptors',
                 'allocator_flags':None,'allocator_request_size':'unrounded byte size','allocator_entry_offset':'0x4659e0',
                 'post_allocation_guard_high':'0x27f000000','allocation_may_block':True,'allocator_outer_retry':False,
                 'allocator_hard_timeout':False,'allocator_begin_end_logging':True,
                 'stage_only':args.stage_only,'kernel_destination':'0x06000000','initrd_destination':'0x180000000',
                 'destinations_written_only_at_terminal_takeover':True,
                 'source_destination_overlap':'actual copy conflicts replaced by owned safe pages before arm; later decompressor workspace is phase-separated',
                 'safe_page_bytes':16384,'safe_page_limit':8192,
                 'safe_page_physical_low':'0x10000000','safe_page_physical_high_exclusive':'0x70000000',
                 'safe_page_native_flags':'0x102','safe_page_native_calls_per_page':1,
                 'safe_page_outer_retry':False,'safe_page_may_block':True,
                 'stage_only_safe_probe':args.stage_only,
                 'initramfs_input':'lazy PROT_READ|MAP_PRIVATE file mapping; exact bytes; no prefault or eager fallback',
                 'initramfs_staged_generation_must_remain_immutable':True,
                 'physical_end_exclusive':'0x27f000000','replacement_identity_bytes':16*1024**3,
                 'separate_dmap_pdpt':True,'native_alias_page_checks':True,
                 'original_live_identity_entries':4,
                 'uvd_firmware':{'source':'running FW12.00/12.02 kernel read-only segment','offset':'0x8cbff0',
                                 'raw_bytes':313912,'raw_crc32':'a465ba07','on_mismatch':'left out; boot continues'}},
      'host_checks':check,'hardware_acceptance':False,'artifacts':{}}
    receipt=output/'BUILD.json';receipt.write_text(json.dumps(metadata,indent=2)+'\n')
    command=['make',f'-j{args.jobs}','FIRMWARES=1200']
    if args.stage_only:
        command+=['EXTRA_CFLAGS=-DDIAGNOSTIX_STAGE_ONLY','EXTRA_AIO_CFLAGS=-DDIAGNOSTIX_STAGE_ONLY']
    command+=['payloads/linux-1024mb.elf','payloads/linux-1024mb.bin']
    with (output/'build.log').open('xb') as log:
        result=subprocess.run(command,cwd=source/'linux',env=env,stdout=log,stderr=subprocess.STDOUT)
    metadata.update(build_returncode=result.returncode,build_command=command)
    if result.returncode:
        metadata['status']='build_failed';receipt.write_text(json.dumps(metadata,indent=2)+'\n');raise ValueError('Build failed; see '+str(output/'build.log'))
    for n,pin in adapted.items():
        if sha(source/n)!=pin:raise ValueError('Build modified source: '+n)
    for n,pin in inputs.items():
        if sha(HERE/n)!=pin:raise ValueError('Input changed during build: '+n)
    verify_reference(reference)
    for src,name in [('linux/payloads/linux-1024mb.elf','loader.elf'),('linux/payloads/linux-1024mb.bin','loader.bin'),('linux/kexec-build/1200/kexec.bin','kexec-1200.bin'),('linux/kexec-build/1200/kexec.slim','kexec-1200.slim'),('linux/kexec-build/1200/kexec.footer','kexec-1200.footer'),('linux/kexec-build/1200/kexec-layout.h','kexec-layout.h')]:
        shutil.copy2(source/src,output/name)
        if sha(output/name)!=sha(source/src):raise ValueError('Artifact readback differs')
        metadata['artifacts'][name]={'sha256':sha(output/name),'bytes':(output/name).stat().st_size}
    # Upstream's footer intentionally materializes the complete BSS in the blob.
    symbols={row.split()[2]:int(row.split()[0],16) for row in run(['nm','-n',str(source/'linux/kexec-build/1200/kexec.elf')]).splitlines() if len(row.split())==3}
    extent=symbols['_end']-symbols['_start']
    relocations=run(['readelf','-rW',str(source/'linux/kexec-build/1200/kexec.elf')])
    if 'R_X86_64_' in relocations:
        raise ValueError('Raw kexec blob contains unresolved native relocations')
    metadata['kexec_no_relocations_verified']=True
    if (output/'kexec-1200.bin').stat().st_size != extent:
        raise ValueError('Kexec blob does not include the complete BSS/workspace extent')
    metadata['kexec_static_extent_verified']=True
    metadata['kexec_static_extent_bytes']=extent
    # The embedded image is the non-BSS prefix, zero-extended to the BSS start;
    # the kernel-side allocation zeroes the BSS before entering the blob.
    layout=(output/'kexec-layout.h').read_text()
    values={key:int(value,16) for key,value in re.findall(r'#define (KEXEC_[A-Z_]+) 0x([0-9a-f]+)ULL',layout)}
    required={'KEXEC_IMAGE_BYTES','KEXEC_BSS_OFFSET','KEXEC_BSS_BYTES','KEXEC_FOOTER_OFFSET','KEXEC_FOOTER_BYTES'}
    if set(values)!=required:
        raise ValueError('Kexec layout header is incomplete')
    if values['KEXEC_IMAGE_BYTES']!=values['KEXEC_BSS_OFFSET'] or (output/'kexec-1200.slim').stat().st_size!=values['KEXEC_IMAGE_BYTES']:
        raise ValueError('Slim kexec image does not end at the BSS offset')
    if values['KEXEC_IMAGE_BYTES']+values['KEXEC_BSS_BYTES']+values['KEXEC_FOOTER_BYTES']!=extent:
        raise ValueError('Slim image, BSS and footer do not cover the full kexec extent')
    if (output/'kexec-1200.footer').stat().st_size!=values['KEXEC_FOOTER_BYTES']:
        raise ValueError('Kexec footer size differs')
    reconstructed=((output/'kexec-1200.slim').read_bytes()+
                   b'\0'*values['KEXEC_BSS_BYTES']+
                   (output/'kexec-1200.footer').read_bytes())
    if reconstructed!=(output/'kexec-1200.bin').read_bytes():
        raise ValueError('Slim image plus zeroed BSS/footer does not reconstruct the exact blob')
    metadata['kexec_slim_reconstruction_verified']=True
    metadata['kexec_slim_extent_verified']=True
    metadata['kexec_slim_bytes']=values['KEXEC_IMAGE_BYTES']
    metadata['kexec_bss_bytes']=values['KEXEC_BSS_BYTES']
    metadata['kexec_footer_bytes']=values['KEXEC_FOOTER_BYTES']
    if (output/'loader.elf').stat().st_size >= (output/'kexec-1200.bin').stat().st_size:
        raise ValueError('Loader still embeds the complete BSS-filled kexec blob')
    metadata['loader_elf_bytes']=(output/'loader.elf').stat().st_size
    elf=(output/'loader.elf').read_bytes()
    if elf[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(elf[18:20],'little')!=62:raise ValueError('Not an x86-64 ELF')
    if run(['nm','-u',str(output/'loader.elf')]):raise ValueError('Loader has unresolved symbols')
    shutil.copy2(HERE/'LICENSE',output/'LICENSE');shutil.copy2(HERE/'provenance.json',output/'provenance.json')
    metadata.update(status='PASS_HOST_BUILD',reference_checkout_unchanged=True,all_source_inputs_read_back=True,
                    build_log_sha256=sha(output/'build.log'),source_inventory_sha256=sha(output/'SOURCE.json'))
    receipt.write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps({'status':metadata['status'],'output':str(output),'artifacts':metadata['artifacts']},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--loader-tree',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--jobs',type=int,default=16);parser.add_argument('--yasm',type=Path)
    parser.add_argument('--stage-only',action='store_true',help='Stage, validate and free the real boot files; never arm or request shutdown')
    args=parser.parse_args()
    try:build(args)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:parser.exit(1,'Dedicated loader refused: '+str(error)+'\n')
