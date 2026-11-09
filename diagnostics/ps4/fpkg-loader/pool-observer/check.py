#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Meerzulee and Linux psX contributors.
import argparse,hashlib,json,os,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent

def check(output,upstream):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    rows={}
    for name in ('core','entry'):
        exe=output/name
        cmd=['gcc','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
             '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer',
             '-DPSX_POOL_HOST_TEST','-I'+str(HERE),'-iquote'+str(Path(upstream)/'linux'),
             str(HERE/(name+'_test.c')),str(HERE/'pool_core.c'),'-o',str(exe)]
        subprocess.run(cmd,check=True,env=dict(os.environ,TMPDIR=str(output)))
        r=subprocess.run([str(exe)],capture_output=True,text=True,check=True,timeout=15)
        rows[name]={'status':'PASS','stdout':r.stdout.strip()}
    result={'status':'PASS_HOST_FIXTURES','hardware_acceptance':False,'synthetic_fingerprints_only_for_host':True,
            'production_sha256':{n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('pool_core.c','pool_core.h','observer.c')},'checks':rows}
    (output/'receipt.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);p.add_argument('--upstream',required=True,type=Path);a=p.parse_args();check(a.output,a.upstream)
