#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Meerzulee and Linux psX contributors.
"""Build a separate read-only pool report payload. Never deploy or execute it."""
import argparse,hashlib,importlib.util,json,os,shutil,subprocess,tarfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
PIN='9acef9fbf79097a2bb39d6c9c17228198bc445cc'
ARCHIVE_SHA='28468fbc4b4d13fe2750670024002fe43fcee97192ed111f6d4426b4c988c7a4'
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def run(cmd,**kw):return subprocess.check_output(cmd,text=True,**kw).strip()
def build(a):
    ref=a.loader_tree.resolve(strict=True);out=a.output.absolute();yasm=a.yasm.resolve(strict=True)
    if out.exists() or out.is_symlink():raise ValueError('Output must be new')
    if run(['git','rev-parse','HEAD'],cwd=ref)!=PIN or run(['git','status','--porcelain=v1','--untracked-files=all'],cwd=ref):raise ValueError('Reference must be clean at pinned commit')
    inputs={p.name:sha(p) for p in sorted(HERE.iterdir()) if p.is_file()};out.mkdir(parents=True)
    env=dict(os.environ,TMPDIR=str(out));archive=out/'upstream-source.tar'
    with archive.open('xb') as f:subprocess.run(['git','archive',PIN],cwd=ref,stdout=f,check=True)
    if sha(archive)!=ARCHIVE_SHA:raise ValueError('Archive mismatch')
    src=out/'upstream';src.mkdir()
    with tarfile.open(archive) as f:f.extractall(src,filter='data')
    inv={str(p.relative_to(src)):sha(p) for p in src.rglob('*') if p.is_file()}
    own=out/'observer-source';own.mkdir()
    for p in HERE.iterdir():
        if p.is_file():shutil.copy2(p,own/p.name)
    spec=importlib.util.spec_from_file_location('pool_check',HERE/'check.py');check=importlib.util.module_from_spec(spec);spec.loader.exec_module(check)
    host=check.check(out/'host-check',src)
    commands=[]
    def execute(c):
        commands.append(c)
        with (out/'build.log').open('ab') as log:subprocess.run(c,check=True,stdout=log,stderr=subprocess.STDOUT,env=env,cwd=out)
    for n in ('crt','syscalls'):execute([str(yasm),'-f','elf64',str(src/'lib'/f'{n}.asm'),'-o',str(out/f'{n}.o')])
    flags=['gcc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-fno-stack-protector','-mno-red-zone','-mgeneral-regs-only',
           '-fPIE','-ffreestanding','-fno-builtin','-fstack-usage','-ffunction-sections','-fdata-sections','-nostdinc',
           '-isystem',str(src/'freebsd-headers'),'-iquote',str(src/'linux'),'-iquote',str(own)]
    for name,p in [('observer',own/'observer.c'),('pool_core',own/'pool_core.c'),('fw_detect',src/'linux/fw_detect.c')]:execute(flags+['-c',str(p),'-o',str(out/f'{name}.o')])
    elf=out/'pool-observer.elf';binary=out/'pool-observer.bin'
    execute(['gcc','-nostdlib','-static','-Wl,--gc-sections,-e,_start','-o',str(elf)]+[str(out/f'{x}.o') for x in ('crt','syscalls','observer','pool_core','fw_detect')])
    execute(['objcopy','--only-section','.text','--only-section','.rodata','--only-section','.data','--only-section','.bss','--set-section-flags','.bss=alloc,load,contents','-O','binary',str(elf),str(binary)])
    if run(['nm','-u',str(elf)]):raise ValueError('Undefined symbols')
    syms=run(['nm','-n',str(elf)]);(out/'symbols.txt').write_text(syms+'\n')
    forbidden=('malloc','calloc','mmap','kmem_alloc','kexec_load','kernel_main','dynlib_load_prx','mprotect','kill','reboot','nanosleep')
    if any(any(word in row.split()[-1] for word in forbidden) for row in syms.splitlines()):raise ValueError('Unexpected linked side-effect helper')
    symbols={z[2]:int(z[0],16) for row in syms.splitlines() if len(z:=row.split())==3}
    if binary.stat().st_size<symbols['_end']-symbols['_start']:raise ValueError('BIN does not include full static/BSS extent')
    dis=run(['objdump','-d',str(elf)]);(out/'disassembly.txt').write_text(dis+'\n')
    if 'wrmsr' in dis or any('\t'+x in dis for x in ('cli','sti','hlt')):raise ValueError('Unexpected privileged mutation instruction')
    max_stack=0;stack=[]
    for p in out.glob('*.su'):
        for line in p.read_text().splitlines():
            fields=line.split('\t');n=int(fields[1]);max_stack=max(max_stack,n);stack.append({'function':fields[0],'bytes':n,'kind':fields[2]})
    if max_stack>1024:raise ValueError('Unexpected per-function stack use')
    for n,pin in inv.items():
        if sha(src/n)!=pin:raise ValueError('Upstream source modified')
    for n,pin in inputs.items():
        if sha(HERE/n)!=pin or sha(own/n)!=pin:raise ValueError('Observer source changed')
    record={'status':'PASS_HOST_BUILD','hardware_acceptance':False,'upstream_commit':PIN,'upstream_archive_sha256':ARCHIVE_SHA,
       'input_sha256':inputs,'upstream_source_sha256':inv,'native_commands':commands,'host_checks':host,
       'kernel_context_flags':['-mno-red-zone','-mgeneral-regs-only','-ffreestanding','-fno-stack-protector'],
       'max_function_stack_bytes':max_stack,'stack_usage':stack,'bin_static_extent_verified':True,
       'artifacts':{p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in (elf,binary)},
       'toolchain':{n:{'path':str(p),'sha256':sha(p),'version':run([str(p),'--version']).splitlines()[0]} for n,p in [('gcc',Path(shutil.which('gcc'))),('yasm',yasm),('objcopy',Path(shutil.which('objcopy')))]}}
    (out/'BUILD.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':record['status'],'output':str(out),'max_function_stack_bytes':max_stack,'artifacts':record['artifacts']},indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--loader-tree',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--yasm',required=True,type=Path);a=p.parse_args()
    try:build(a)
    except (OSError,ValueError,subprocess.CalledProcessError) as e:p.exit(1,'Pool observer refused: '+str(e)+'\n')
