#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Meerzulee and Linux psX contributors.
"""Compile and run actual modified loader functions with injected host-only APIs."""
import argparse, hashlib, importlib.util, json, os
from pathlib import Path
import subprocess
HERE=Path(__file__).resolve().parent

def uvd_fixture(firmware):
    pin='#define UVD_RAW_CRC32 0xa465ba07U'
    if pin not in firmware:raise ValueError('UVD checksum pin differs')
    header=firmware[firmware.index('struct firmware_header {'):firmware.index('static inline char hex')]
    body=firmware[firmware.index('#define UVD_KERNEL_OFFSET'):firmware.index('struct fw_info_t *get_fw_info()')]
    return header+body.replace(pin,'#define UVD_RAW_CRC32 uvd_test_crc')

def check(source, output):
    source=Path(source).resolve();output=Path(output).absolute();output.mkdir(exist_ok=False,parents=True)
    main=(source/'linux/main-aio.c').read_text()
    kernel=(source/'linux/ps4-kexec-common/kernel.c').read_text()
    kexec=(source/'linux/ps4-kexec-common/kexec.c').read_text()
    boot=(source/'linux/ps4-kexec-common/linux_boot.c').read_text()
    production={
      'entry':main[main.index('int main(void)'):],
      'io':main[main.index('#define DIAG_KERNEL_MAX'):main.index('// evf_open')],
      'pages':(source/'linux/ps4-kexec-common/diagnostix_pages.inc').read_text(),
      'kexec':kexec[kexec.index('u8 sb_id = 0;'):kexec.index('int kexec_init(')],
      'address':boot[boot.index('static u64 vram_base'):boot.index('#define WR32')],
      'uvd':uvd_fixture((source/'linux/ps4-kexec-common/firmware.c').read_text()),
    }
    rows={}
    for name in ('entry','io','pages','kexec','address','uvd','entry_stage','kexec_stage'):
        fixture=name.removesuffix('_stage');body=production[fixture]
        inc=output/(name+'.inc');inc.write_text(body)
        exe=output/name
        cmd=['gcc','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-parameter',
             '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer',
             '-I'+str(output),'-iquote'+str(source/'linux/ps4-kexec-common'),
             str(HERE/('test_'+fixture+'.c'))]
        if name.endswith('_stage'):cmd+=['-DDIAGNOSTIX_STAGE_ONLY']
        if fixture=='kexec':cmd+=[str(source/'linux/ps4-kexec-common/diagnostix_scatter.c')]
        if fixture=='uvd':cmd+=[str(source/'linux/ps4-kexec-common/crc32.c')]
        cmd+=['-o',str(exe)]
        subprocess.run(cmd,check=True,env=dict(os.environ,TMPDIR=str(output)))
        r=subprocess.run([str(exe)],check=True,capture_output=True,text=True,timeout=15)
        rows[name]={'status':'PASS','stdout':r.stdout.strip(),'production_sha256':hashlib.sha256(body.encode()).hexdigest(),
                    'kernel_header_sha256':hashlib.sha256((source/'linux/ps4-kexec-common/kernel.h').read_bytes()).hexdigest()}
    spec=importlib.util.spec_from_file_location('diagnostix_scatter_check',HERE/'staging/check.py')
    scatter=importlib.util.module_from_spec(spec);spec.loader.exec_module(scatter)
    rows['scatter']=scatter.check(source,output/'scatter')
    thunk=output/'thunk'
    cmd=['gcc','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror',
         '-fsanitize=address,undefined','-fno-sanitize-recover=all',
         '-DTESTING','-DDIAGNOSTIX_HOST_COPY','-iquote'+str(source/'linux/ps4-kexec-common'),
         str(HERE/'staging/test_thunk.c'),
         str(source/'linux/ps4-kexec-common/diagnostix_scatter.c'),
         str(source/'linux/ps4-kexec-common/linux_thunk.S'),'-o',str(thunk)]
    subprocess.run(cmd,check=True,env=dict(os.environ,TMPDIR=str(output)))
    r=subprocess.run([str(thunk)],check=True,capture_output=True,text=True,timeout=15)
    rows['thunk']={'status':'PASS','stdout':r.stdout.strip(),
      'production_sha256':hashlib.sha256((source/'linux/ps4-kexec-common/linux_thunk.S').read_bytes()).hexdigest()}
    # Six GiB is ASan's reserved shadow gap. Exercise the exact physical
    # addresses through MAP_FIXED_NOREPLACE in a separate UBSan-only process.
    fixed=output/'thunk-fixed-physical'
    fixed_cmd=[('-fsanitize=undefined' if item=='-fsanitize=address,undefined' else item)
               for item in cmd]
    fixed_cmd[fixed_cmd.index('-o')+1]=str(fixed)
    fixed_cmd.insert(1,'-DDIAGNOSTIX_FIXED_PHYSICAL_TEST')
    subprocess.run(fixed_cmd,check=True,env=dict(os.environ,TMPDIR=str(output)))
    r=subprocess.run([str(fixed)],check=True,capture_output=True,text=True,timeout=15)
    rows['fixed_physical_collision']={'status':'PASS','stdout':r.stdout.strip(),
      'planner_sha256':hashlib.sha256((source/'linux/ps4-kexec-common/diagnostix_scatter.c').read_bytes()).hexdigest(),
      'assembly_sha256':rows['thunk']['production_sha256'],
      'fixture_sha256':hashlib.sha256((HERE/'staging/test_thunk.c').read_bytes()).hexdigest(),
      'sanitizer':'UBSan; fixed physical addresses conflict with ASan shadow gap',
      'mapping_policy':'MAP_FIXED_NOREPLACE only'}
    record={'status':'PASS_HOST_FIXTURES','hardware_acceptance':False,'source':str(source),'checks':rows}
    (output/'receipt.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));return record
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();check(a.source,a.output)
