#!/usr/bin/env python3
"""Build an offline, non-installable preparation-only boot bundle from pinned inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile

TARGET='7.2-ps4-baikal-uvd-live-prepare-dev1'
RELEASE='7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1'
IB_RELEASE='7.2.11-psx-ps4-baikal-uvd-ib-data-dev1'
PATCH='9cc51da26d81342d2fd3cc674a4495cbdc4298350059cdeb0eeba9d744693456'
INITRAMFS='cf2102f52b8b5d048538e1dd410b2c9b43ec81d2a567294d6204578aa83103f7'
INIT_RECEIPT='28150c8b1fb43addcf0bff20770b59583b46ea9df3afe09d1e4c92b56eacbb9a'
IB_BOOTARGS='4d08ab790b3cd656e2bd3797070351005f6b42f47843425810ac9ede2fb6142f'
PREFIX='drivers/gpu/drm/amd/amdgpu/'
OBJECTS={'amdgpu_debugfs','amdgpu_device','amdgpu_drv','amdgpu_fence','amdgpu_ids','amdgpu_kms','amdgpu_ps4_map_canary','amdgpu_ps4_uvd_ready','gmc_v7_0'}
STATUS='OFFLINE_BUNDLE_PENDING_OPERATOR_PLAN'
RECEIPTS=('identity.json','review.json','prepare-base.json','prepare.json','build.json','package.json','modules.json','module-tree.json','compiler.json','built-source.json','project-inputs.json')
OUTPUTS={'bzImage','config','System.map','version.txt','manifest.json','artifacts.json','inputs.tar.zst','modules-'+RELEASE+'.tar.zst','source-'+RELEASE+'.tar.zst'}
IB_GATES={b'amdgpu.ps4_uvd_ready',b'amdgpu.ps4_uvd_poll_state',b'amdgpu.ps4_uvd_token',b'amdgpu.ps4_uvd_ib_data'}
KNOWN_GATES=IB_GATES|{b'amdgpu.ps4_uvd_data_control',b'amdgpu.ps4_uvd_status_observation',b'amdgpu.ps4_map_canary',b'amdgpu.ps4_uvd_writeback',b'amdgpu.ps4_uvd_ib_fetch',b'amdgpu.ps4_uvd_live_prepare'}


def require(ok,message):
    if not ok:raise RuntimeError(message)


def digest(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def safe_path(value,kind='file'):
    p=Path(os.path.abspath(value))
    require(not any(x.is_symlink() for x in (p,*p.parents)), 'Symlink input/output path: '+str(p))
    if kind=='file':require(p.is_file() and stat.S_ISREG(p.stat().st_mode),'Not a regular input: '+str(p))
    elif kind=='dir':require(p.is_dir(),'Not an input directory: '+str(p))
    return p


def child(root,name):
    require(isinstance(name,str) and name and PurePosixPath(name).as_posix()==name and not name.startswith('/') and all(x not in ('','.','..') for x in name.split('/')),'Unsafe relative path: '+str(name))
    return safe_path(root/name)


def read(path,limit=64*1024*1024):
    p=safe_path(path);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        before=os.fstat(f.fileno());require(stat.S_ISREG(before.st_mode) and before.st_size<=limit,'Input bound exceeded: '+str(p))
        data=f.read(limit+1);after=os.fstat(f.fileno())
        require(len(data)==before.st_size and (before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_size,after.st_mtime_ns,after.st_ctime_ns),'Input changed while reading: '+str(p))
        return data


def measured(path):
    p=safe_path(path);h=hashlib.sha256();fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        before=os.fstat(f.fileno());require(stat.S_ISREG(before.st_mode),'Not a regular opened input')
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        after=os.fstat(f.fileno())
        require((before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_size,after.st_mtime_ns,after.st_ctime_ns),'Input changed while hashing: '+str(p))
    return {'sha256':h.hexdigest(),'bytes':before.st_size}


def sha(path):return measured(path)['sha256']


def load(path):
    def unique(pairs):
        out={}
        for k,v in pairs:
            require(k not in out,'Duplicate JSON key: '+k);out[k]=v
        return out
    return json.loads(read(path).decode('utf-8'),object_pairs_hook=unique)


def bootargs(original):
    require(original.endswith(b'\n') and original.count(b'\n')==1 and all(32<=x<=126 for x in original[:-1]),'Malformed saved bootargs')
    tokens=original[:-1].split(b' ')
    require(all(tokens) and all(b'"' not in x and b"'" not in x and b'\\' not in x for x in tokens),'Ambiguous bootargs whitespace/quoting')
    require(b'--' not in tokens,'Kernel argument terminator would hide the preparation gate')
    removed=[];kept=[];seen=set()
    for token in tokens:
        key=token.split(b'=',1)[0];normalized=key.replace(b'-',b'_')
        require(key!=b'debugfs','Saved arguments already select debugfs; explicit review required')
        experimental=normalized.startswith((b'amdgpu.ps4_uvd_',b'ps4_uvd_')) or b'.ps4_uvd_' in normalized or normalized in (b'amdgpu.ps4_map_canary',b'ps4_map_canary')
        if experimental:
            require(key==normalized and key in KNOWN_GATES,'Unknown/noncanonical experimental gate: '+key.decode('ascii'))
            require(key not in seen,'Duplicate experimental gate: '+key.decode('ascii'));seen.add(key)
            require(token==key+b'=1','Malformed/disabled/conflicting experimental gate')
            require(key in IB_GATES,'Saved IB gate conflict: '+key.decode('ascii'));removed.append(token)
        else:kept.append(token)
    require(seen==IB_GATES,'Saved IB arguments must contain the four exact expected gates')
    return b' '.join([*kept,b'amdgpu.ps4_uvd_live_prepare=1',b'debugfs=on'])+b'\n',removed


def configuration(raw):
    values={}
    for line in raw.decode('ascii').splitlines():
        match=re.fullmatch(r'(CONFIG_[A-Z0-9_]+)=(.*)',line)
        disabled=re.fullmatch(r'# (CONFIG_[A-Z0-9_]+) is not set',line)
        if match or disabled:
            key=(match or disabled).group(1);require(key not in values,'Duplicate config option');values[key]=match.group(2) if match else 'n'
    expected={'CONFIG_LOCALVERSION':'"-psx-ps4-baikal-uvd-live-prepare-dev1"','CONFIG_DRM_AMDGPU':'y','CONFIG_X86_PS4':'y','CONFIG_DEBUG_FS':'y','CONFIG_DEBUG_FS_ALLOW_ALL':'n','CONFIG_DEBUG_FS_ALLOW_NONE':'y','CONFIG_PM':'n','CONFIG_EXTRA_FIRMWARE':'""'}
    require(all(values.get(k)==v for k,v in expected.items()) and values.get('CONFIG_INITRAMFS_SOURCE','""')=='""','Preparation-only configuration mismatch')


def module_identity(data,record):
    require(len(data)>=64 and data[:6]==b'\x7fELF\x02\x01','Module is not ELF64 little-endian')
    h=struct.unpack_from('<HHIQQQIHHHHHH',data,16)
    require(h[0:3]==(1,62,1) and record.get('elf_header')=={'class':2,'data':1,'machine':62,'type':1},'Module ELF machine/type mismatch')
    shoff,shsize,count,names=h[5],h[10],h[11],h[12]
    require(shsize==64 and 0<count<=4096 and 0<names<count and shoff+shsize*count<=len(data),'Invalid module section table')
    sections=[struct.unpack_from('<IIQQQQIIQQ',data,shoff+i*shsize) for i in range(count)]
    def bytes_for(s):
        require(s[4]<=len(data) and s[5]<=len(data)-s[4],'Module section outside bytes');return data[s[4]:s[4]+s[5]]
    names_raw=bytes_for(sections[names]);modinfo=[]
    for s in sections:
        require(s[0]<len(names_raw),'Invalid section-name index');end=names_raw.find(b'\0',s[0]);require(end>=0,'Unterminated section name')
        if names_raw[s[0]:end]==b'.modinfo':require(s[1]==1,'Wrong modinfo section type');modinfo.append(bytes_for(s))
    require(len(modinfo)==1,'One module modinfo section required')
    values=[x[9:] for x in modinfo[0].split(b'\0') if x.startswith(b'vermagic=')]
    require(len(values)==1 and values[0].decode('ascii').strip()==record.get('vermagic') and values[0].split()[0].decode()==RELEASE,'Module vermagic differs')


def validate_modules(archive,tree,modules):
    prefix='lib/modules/'+RELEASE+'/'
    require(len(tree)==55 and len(modules)==41 and set(modules)=={n for n in tree if n.endswith('.ko')},'Whole 55-file/41-module manifest required')
    for name,row in tree.items():
        require(name.startswith(prefix) and all(x not in ('','.','..') for x in name.split('/')) and row.get('kind')=='file' and digest(row.get('sha256')) and type(row.get('bytes')) is int and 0<=row['bytes']<=32*1024*1024 and row.get('mode')==0o644,'Unsafe module-tree record')
        if name in modules:require(all(modules[name].get(k)==v for k,v in row.items()),'Module/tree receipt differs')
    decoder=shutil.which('zstd');require(decoder is not None,'zstd is required only to decode the module archive')
    seen=set()
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen([decoder,'-d','-c','-M256MB','--',str(archive)],stdout=subprocess.PIPE,stderr=errors)
        try:
            with tarfile.open(fileobj=process.stdout,mode='r|') as tar:
                for member in tar:
                    name=member.name
                    require(len(seen)<55 and name not in seen,'Duplicate/extra module archive member');seen.add(name)
                    require(member.isfile() and name in tree and not name.startswith('/') and all(x not in ('','.','..') for x in name.split('/')),'Unsafe module archive member')
                    require(not member.pax_headers or member.pax_headers=={'path':name},'Unknown PAX override')
                    row=tree[name];require(member.size==row['bytes'] and member.mode==row['mode'] and member.uid==member.gid==0,'Module tar size/mode/owner differs')
                    data=tar.extractfile(member).read();require(len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'],'Module archive member content differs')
                    if name in modules:module_identity(data,modules[name])
            process.stdout.close();code=process.wait();errors.seek(0)
            require(code==0,'zstd decoder failed: '+errors.read(4096).decode(errors='replace'))
        except BaseException:
            process.stdout.close()
            if process.poll() is None:process.kill()
            process.wait();raise
    require(seen==set(tree),'Module archive file set incomplete')
    return {'regular_files':55,'modules':41,'content_sha256_and_mode_verified':True,'elf_and_vermagic_verified':True,'extracted_to_filesystem':False}


def validate(native_run,compiled_path,compiled_sha,ib_stage,destination):
    require(digest(compiled_sha),'Caller must pin the compiled review SHA256')
    destination=safe_path(destination,'new');require(not os.path.lexists(destination),'Destination already exists')
    safe_path(destination.parent,'dir')
    run=safe_path(native_run,'dir');stage=safe_path(ib_stage,'dir');compiled_path=safe_path(compiled_path)
    require(not destination.is_relative_to(run) and not destination.is_relative_to(stage),'Destination must be outside preserved input trees')
    require(sha(compiled_path)==compiled_sha,'Pinned compiled review changed')
    compiled=load(compiled_path)
    require(compiled.get('status')=='PASS_SCOPED_COMPILED_PREPARATION_REVIEW' and compiled.get('release')==RELEASE and compiled.get('patch_sha256')==PATCH and compiled.get('must_fix')==[],'Compiled preparation review incomplete/wrong scope')
    require(all(compiled.get(k) is False for k in ('hardware_acceptance','hardware_tested','usb_staged','uvd_arm_implemented')),'Compiled review scope changed')
    receipts=safe_path(run/'receipts','dir');output=safe_path(run/'project/output'/TARGET,'dir');source=safe_path(run/'project/src'/TARGET,'dir')
    paths={n:child(receipts,n) for n in RECEIPTS};hashes={n:sha(p) for n,p in paths.items()};r={n:load(p) for n,p in paths.items()}
    require(compiled.get('build_receipt_sha256')==hashes['build.json'] and compiled.get('package_receipt_sha256')==hashes['package.json'] and compiled.get('source_review_sha256')==hashes['review.json'],'Compiled review/native receipt binding differs')
    review=r['review.json'];identity=r['identity.json'];build=r['build.json'];package=r['package.json'];prepared=r['prepare.json']
    require(review.get('status')=='PASS_SCOPED_SOURCE_REVIEW_FOR_NATIVE_BUILD' and review.get('must_fix')==[] and review.get('patch_sha256')==PATCH and review.get('uvd_arm_implemented') is False,'Source review identity/scope differs')
    require(identity.get('target')==TARGET and identity.get('release')==RELEASE and identity.get('review_sha256')==hashes['review.json'],'Native identity differs')
    require(identity['input_pins']['patch']['sha256']==PATCH and identity['input_pins']['source_review']['sha256']==hashes['review.json'] and identity.get('review',{}).get('approved_for_native_build_only') is True,'Native input approval/pins differ')
    previous=None
    for name,status in (('prepare-base.json','PASS_PREPARE_BASE'),('prepare.json','PASS_PREPARE'),('build.json','PASS_BUILD'),('package.json','PASS_PACKAGE')):
        item=r[name]
        require(item.get('status')==status and item.get('baseline_preserved') is True and item.get('hardware_acceptance') is False and item.get('target')==TARGET and item.get('release')==RELEASE,'Native phase incomplete: '+name)
        require(item.get('review_sha256')==hashes['review.json'] and item.get('identity_sha256')==hashes['identity.json'] and item.get('previous_phase_receipt_sha256')==(hashes[previous] if previous else None),'Native phase chain differs: '+name)
        previous=name
    require(len(prepared.get('patches',[]))==73 and prepared['patches'][-1].get('sha256')==PATCH,'Patch stack differs')
    require(prepared.get('project_inputs_sha256')==hashes['project-inputs.json'] and build.get('compiler_sha256')==hashes['compiler.json'] and build.get('module_tree_sha256')==hashes['module-tree.json'] and build.get('modules_sha256')==hashes['modules.json'] and build.get('source_inventory_sha256')==hashes['built-source.json'],'Native inventories changed')
    require(build.get('module_count')==41 and build.get('vmlinux_sha256')==compiled.get('vmlinux_sha256'),'Linked kernel/module identity differs')
    require(set(package.get('artifact_sha256',{}))==OUTPUTS,'Packaged output set differs')
    outputs={n:child(output,n) for n in OUTPUTS};measurements={n:measured(p) for n,p in outputs.items()}
    require(all(measurements[n]['sha256']==h for n,h in package['artifact_sha256'].items()),'Packaged artifact bytes differ')
    require(set(build.get('artifact_sha256',{}))=={'bzImage','config','System.map','manifest.json','version.txt'} and all(package['artifact_sha256'][n]==h for n,h in build['artifact_sha256'].items()),'Build/package artifact chain differs')
    require(measurements['bzImage']['sha256']==compiled.get('kernel_sha256') and measurements['config']['sha256']==compiled.get('config_sha256'),'Compiled image/config identity differs')
    require(read(outputs['bzImage'],64*1024*1024)[0x202:0x206]==b'HdrS','Invalid x86 boot image header')
    require(read(outputs['version.txt'])==(RELEASE+'\n').encode(),'Kernel release file differs');configuration(read(outputs['config']))
    artifacts=load(outputs['artifacts.json']);manifest=load(outputs['manifest.json'])
    require(artifacts==package.get('artifacts') and artifacts.get('target')==TARGET and artifacts.get('kernel_release')==RELEASE and artifacts.get('active_patch_count')==73 and artifacts.get('module_count')==41 and artifacts.get('embedded_firmware') is False and artifacts.get('hardware_acceptance')=='none' and artifacts.get('review_sha256')==hashes['review.json'],'Artifact metadata differs')
    require(artifacts.get('kernel_sha256')==compiled['kernel_sha256'] and artifacts.get('kernel_config_sha256')==compiled['config_sha256'],'Artifact image/config metadata differs')
    for key,name in (('modules_archive','modules-'+RELEASE+'.tar.zst'),('source_archive','source-'+RELEASE+'.tar.zst'),('inputs_archive','inputs.tar.zst')):
        require(artifacts.get(key)=={'path':name,'sha256':measurements[name]['sha256']},'Archive identity/path differs: '+key)
    require(manifest.get('target')==TARGET and manifest.get('release')==RELEASE and manifest.get('active_patch_count')==73 and manifest.get('patch_sha256')==PATCH and manifest.get('review_sha256')==hashes['review.json'] and manifest.get('hardware_acceptance') is False,'Build manifest differs')
    require(manifest.get('project_inputs_sha256')==hashes['project-inputs.json'] and manifest.get('built_source_inventory_sha256')==hashes['built-source.json'] and manifest.get('native_tool_sha256')==identity.get('tool_sha256') and manifest.get('compiler')==r['compiler.json'],'Manifest provenance differs')
    checks=package.get('archive_verification',{});require(set(checks)=={'modules','inputs','source'},'Package readback missing')
    require(checks['modules']=={'members_verified':55,'regular_files':55},'Module package readback incomplete')
    require(checks['inputs']['members_verified']==checks['inputs']['regular_files']==len(r['project-inputs.json'])+1,'Input package readback count differs')
    inventory=r['built-source.json'];require(checks['source']['members_verified']==len(inventory)+4 and checks['source']['regular_files']==sum(x['kind']=='file' for x in inventory.values())+4,'Source package readback count differs')
    source_pins=review.get('source_sha256',{});require(len(source_pins)==16,'Selected-source scope differs')
    for name,h in source_pins.items():
        require(Path(name).name==name and inventory.get(PREFIX+name,{}).get('sha256')==h and sha(child(source,PREFIX+name))==h,'Selected source changed: '+name)
    require(set(compiled.get('objects',{}))==OBJECTS,'Compiled object scope differs')
    for name,row in compiled['objects'].items():
        obj=child(source,PREFIX+name+'.o');require(measured(obj)=={'sha256':row['sha256'],'bytes':row['bytes']} and sha(child(source,PREFIX+'.'+name+'.o.cmd'))==row['compile_command_sha256'] and row['source_sha256']==source_pins[name+'.c'],'Compiled object/command/source changed: '+name)
    require(sha(child(source,'vmlinux'))==compiled['vmlinux_sha256'],'Linked kernel changed after review')
    module_check=validate_modules(outputs['modules-'+RELEASE+'.tar.zst'],r['module-tree.json'],r['modules.json'])
    ib_plan_path=child(stage,'plan.json');ib_plan_sha=sha(ib_plan_path);ib_plan=load(ib_plan_path);ib_args=child(stage,'bootargs.new');initramfs=child(stage,'initramfs.cpio.gz');init_receipt=child(stage,'initramfs-preparation.json')
    require(ib_plan.get('release')==IB_RELEASE and ib_plan.get('files',{}).get('bootargs.new')==ib_plan.get('new_bootargs_sha256')==sha(ib_args)==IB_BOOTARGS,'Retained IB arguments identity differs')
    require(sha(initramfs)==INITRAMFS==ib_plan.get('initramfs_sha256')==ib_plan.get('files',{}).get('initramfs.cpio.gz') and sha(init_receipt)==INIT_RECEIPT==ib_plan.get('initramfs_preparation_receipt_sha256')==ib_plan.get('files',{}).get('initramfs-preparation.json'),'Retained opaque initramfs identity differs')
    init_meta=load(init_receipt);require(init_meta.get('output_sha256')==INITRAMFS and init_meta.get('output_bytes')==initramfs.stat().st_size and init_meta.get('member_metadata_preserved_except_size') is True and init_meta.get('non_uvd_entries_preserved')==init_meta.get('entries',0)-1,'Initramfs preparation receipt differs')
    new_args,removed=bootargs(read(ib_args))
    copies={'bzImage':outputs['bzImage'],'config':outputs['config'],'modules.tar.zst':outputs['modules-'+RELEASE+'.tar.zst'],'initramfs.cpio.gz':initramfs,'provenance/compiled-review.json':compiled_path,'provenance/retained-ib-plan.json':stage/'plan.json','provenance/retained-ib-bootargs.txt':ib_args,'provenance/initramfs-preparation.json':init_receipt,**{'provenance/native/'+n:p for n,p in paths.items()},'provenance/native/artifacts.json':outputs['artifacts.json'],'provenance/native/manifest.json':outputs['manifest.json']}
    expected_copies={'bzImage':compiled['kernel_sha256'],'config':compiled['config_sha256'],'modules.tar.zst':measurements['modules-'+RELEASE+'.tar.zst']['sha256'],'initramfs.cpio.gz':INITRAMFS,'provenance/compiled-review.json':compiled_sha,'provenance/retained-ib-plan.json':ib_plan_sha,'provenance/retained-ib-bootargs.txt':ib_plan['new_bootargs_sha256'],'provenance/initramfs-preparation.json':INIT_RECEIPT,**{'provenance/native/'+n:h for n,h in hashes.items()},'provenance/native/artifacts.json':measurements['artifacts.json']['sha256'],'provenance/native/manifest.json':measurements['manifest.json']['sha256']}
    copy_records={}
    for n,p in copies.items():
        info=measured(p);require(info['sha256']==expected_copies[n], 'Input changed after validation: '+n)
        copy_records[n]={**info,'source_path':str(p)}
    external={n:{**measurements[n],'path':str(outputs[n]),'copied':False,'requires_same_bytes_if_relocated':True} for n in ('inputs.tar.zst','source-'+RELEASE+'.tar.zst')}
    return destination,copies,copy_records,new_args,{'schema':1,'status':STATUS,'target':TARGET,'release':RELEASE,'patch_sha256':PATCH,'hardware_acceptance':False,'executable_install':False,'uvd_arm_implemented':False,'compiled_review_sha256':compiled_sha,'source_review_sha256':hashes['review.json'],'build_receipt_sha256':hashes['build.json'],'package_receipt_sha256':hashes['package.json'],'copied_files':copy_records,'bootargs_sha256':hashlib.sha256(new_args).hexdigest(),'removed_gates':[x.decode() for x in removed],'added_gate':'amdgpu.ps4_uvd_live_prepare=1','added_observation_prerequisite':'debugfs=on','configuration_changes':['Select preparation-only mode instead of the four prior IB experiment gates.','Enable debugfs registration/API for cached observation because the reviewed configuration defaults to DEBUG_FS_ALLOW_NONE.'],'module_validation':module_check,'external_archives':external,'opaque_initramfs':{'sha256':INITRAMFS,'preparation_receipt_sha256':INIT_RECEIPT,'unpacked':False,'executed':False},'historical_pending_fields':'Original native receipts remain byte-identical; the separate completed compiled review is bound above.','portability':'Boot bundle carries matching modules and private opaque initramfs. Full corresponding-source and input archives remain external hash-pinned references; copy/rebind them with exact hashes before moving/rebuilding on another host.','pending':'Separate boot-compatibility assessment and operator/hardware plan. This bundle provides no install command, experiment or session.'}


def write_new(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())


def save(path,value):write_new(path,(json.dumps(value,indent=2,sort_keys=True)+'\n').encode())


def copy_checked(source,destination,expected):
    fd=os.open(safe_path(source),os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as src:
        out=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(out,'wb') as dst:
            shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
    require(measured(destination)=={k:expected[k] for k in ('sha256','bytes')},'Copy/input changed: '+str(source))


def bundle(native_run,compiled_review,compiled_sha,retained_ib_stage,destination):
    destination,copies,copy_records,args,manifest=validate(native_run,compiled_review,compiled_sha,retained_ib_stage,destination)
    destination.mkdir(mode=0o700)
    incomplete={'status':'OFFLINE_BUNDLE_INCOMPLETE','hardware_acceptance':False,'executable_install':False,'compiled_review_sha256':compiled_sha}
    try:
        save(destination/'STATUS.json',incomplete)
        for name,path in copies.items():
            output=destination/name;output.parent.mkdir(parents=True,exist_ok=True,mode=0o700);copy_checked(path,output,copy_records[name])
        write_new(destination/'bootargs.new',args)
        # Final reference rehash detects concurrent change without executing or
        # unpacking either source/input archive. No external file is modified.
        for row in manifest['external_archives'].values():require(measured(Path(row['path']))=={k:row[k] for k in ('sha256','bytes')},'External archive changed during bundle copy')
        save(destination/'MANIFEST.json',manifest)
        for folder,_,_ in os.walk(destination,topdown=False):
            fd=os.open(folder,os.O_RDONLY);os.fsync(fd);os.close(fd)
        save(destination/'.STATUS-complete.json',{'status':STATUS,'manifest_sha256':sha(destination/'MANIFEST.json'),'hardware_acceptance':False,'executable_install':False})
        os.replace(destination/'.STATUS-complete.json',destination/'STATUS.json')
        fd=os.open(destination,os.O_RDONLY);os.fsync(fd);os.close(fd)
    except BaseException as error:
        try:
            save(destination/'FAILURE.json',{'status':'OFFLINE_BUNDLE_INCOMPLETE','error':str(error),'hardware_acceptance':False,'executable_install':False})
            if (destination/'STATUS.json').exists():
                save(destination/'.STATUS-failed.json',incomplete);os.replace(destination/'.STATUS-failed.json',destination/'STATUS.json')
        except OSError:pass
        raise
    return {'status':STATUS,'destination':str(destination),'manifest_sha256':sha(destination/'MANIFEST.json'),'hardware_acceptance':False,'executable_install':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-run',type=Path,required=True);p.add_argument('--compiled-review',type=Path,required=True)
    p.add_argument('--compiled-review-sha256',required=True);p.add_argument('--retained-ib-stage',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args();print(json.dumps(bundle(a.native_run,a.compiled_review,a.compiled_review_sha256,a.retained_ib_stage,a.destination),indent=2,sort_keys=True))
