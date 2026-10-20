#!/usr/bin/env python3
"""Validate saved IB-DATA evidence and actual stage pins. No UART/device commands."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('ibdata_common',HERE/'common.py')
c=importlib.util.module_from_spec(s);s.loader.exec_module(c)
LIMIT=250000000
BASE=0x43504442
VALUE=0x55564442
CONTEXT=0x43504342
HEX=set('status int rbc ib wp rp ctx data wpc rpa fault pd final_ib reg value mask expected rb_va ib_va data_baseline data_value context_baseline ring_crc ib_crc'.split())
TEXT={'result','class','end_reason','sampled'}
SAMPLE=set('status int rbc ib wp rp ctx data wpc rpa fault reset in_reset pd valid'.split())
REC_KEYS=SAMPLE|set('i elapsed_ns t_end_ns predeadline bit16'.split())
EVIDENCE_KEYS=set('class valid_samples data_seen transport_seen consecutive joint_samples confirmed first_data_ns first_data_end_ns first_transport_ns first_joint_ns confirmed_ns'.split())
END_KEYS=set('result error end_reason enabled submitted elapsed_ns publish_ns records predeadline_records observed_data_seen last_rp bit16_seen final_sampled final_ib final_valid sweep_ran sweep_valid marker_observed accepted'.split())
PREPARED={'ring_bytes':4096,'ib_bytes':4096,'submit_dw':16,'ib_length_dw':16,'rb_va':0x300308000,'ib_va':0x30030a000,'data_baseline':BASE,'data_value':VALUE,'context_baseline':CONTEXT,'ring_crc':0xe5b6afa6,'ib_crc':0xe847443a}
EARLY_ERRORS={'IBDATA_DESCRIPTOR_OR_GATES':-22,'IBDATA_PRECONDITION':-5,'IBDATA_STALE_RESULT':-16,'IBDATA_PLAN_REJECTED':-22,'IBDATA_BASELINE_READBACK':-5,'IBDATA_CONTEXT_BASELINE_READBACK':-5,'IBDATA_BASELINE_OR_PUBLICATION_GUARD':-5,'IBDATA_ENABLE_TIMEOUT':-110,'IBDATA_ENABLE_GUARD':-5,'IBDATA_BEFORE_SUBMIT_TIMEOUT':-110,'IBDATA_PREPUBLICATION_GUARD':-5}


def fields(line):
    pairs=re.findall(r'([A-Za-z0-9_]+)=([^\s;]+)',line.split(';',1)[0])
    c.require(len(pairs)==len({k for k,v in pairs}),'Duplicate field')
    return {k:v if k in TEXT else int(v,16 if k in HEX else 10) for k,v in pairs}


def evidence_class(p):
    if p['confirmed']:return 'IBDATA_MARKER_OBSERVED'
    if p['data_seen']:return 'IBDATA_MARKER_UNCONFIRMED'
    if p['bit16_seen']:return 'IBDATA_STATUS_WITHOUT_MARKER'
    if p['transport_seen']:return 'IBDATA_DRAINED_NO_MARKER'
    return 'IBDATA_NO_TRANSPORT'


def visible_valid(r):
    """Only predicates whose values are printed; hidden reads remain unverified."""
    return (r['status'] in (2,3,0x10002,0x10003) and r['int']==2 and r['rbc']==0x1100080c and
            r['wp']==16 and 0<=r['rp']<=16 and r['wpc']==r['rpa']==r['in_reset']==0 and
            r['data'] in (BASE,VALUE) and r['ctx']==CONTEXT)


def replay(records):
    p=dict(valid_samples=0,consecutive=0,joint_samples=0,last_rp=0,data_seen=0,transport_seen=0,
           confirmed=0,observed_data_seen=0,bit16_seen=0,first_data_ns=0,first_data_end_ns=0,
           first_transport_ns=0,first_joint_ns=0,confirmed_ns=0)
    terminal=None
    for index,r in enumerate(records):
        c.require(terminal is None,'Record follows a terminal sample')
        c.require(r['i']==index and set(r)==REC_KEYS,'Record schema/index differs')
        c.require(r['valid'] in (0,1) and r['predeadline'] in (0,1) and r['bit16'] in (0,1),'Nonboolean record flag')
        c.require(0<=r['elapsed_ns']<LIMIT and r['t_end_ns']>=r['elapsed_ns'],'Record times invalid')
        c.require(r['predeadline']==int(r['t_end_ns']<LIMIT),'Late sample flag differs')
        c.require(r['bit16']==int(bool(r['status'] & 0x10000)),'Recorded bit16 differs')
        if index:c.require(records[index-1]['t_end_ns']<=r['elapsed_ns'],'Sequential record timestamps regress')
        if not r['valid']:
            terminal='guard';continue
        c.require(visible_valid(r),'Valid record fails a printed predicate')
        if r['rp']<p['last_rp']:
            terminal='IBDATA_POINTER_REGRESSION';continue
        if p['observed_data_seen'] and r['data']==BASE:
            terminal='IBDATA_MARKER_REGRESSION';continue
        p['last_rp']=r['rp'];p['observed_data_seen']|=int(r['data']==VALUE)
        if not r['predeadline']:
            terminal='late';continue
        p['valid_samples']+=1;p['bit16_seen']|=r['bit16']
        if not p['data_seen'] and r['data']==VALUE:
            p.update(data_seen=1,first_data_ns=r['elapsed_ns'],first_data_end_ns=r['t_end_ns'])
        drained=r['wp']==r['rp']==16
        if not p['transport_seen'] and drained:p.update(transport_seen=1,first_transport_ns=r['elapsed_ns'])
        if r['data']==VALUE and drained:
            if not p['joint_samples']:p['first_joint_ns']=r['elapsed_ns']
            p['joint_samples']+=1;p['consecutive']+=1
            if p['consecutive']>=2 and not p['confirmed']:p.update(confirmed=1,confirmed_ns=r['t_end_ns'])
        else:p['consecutive']=0
    return p,terminal


def stage_evidence(stage,plan_sha,review_sha):
    c.validate_reviewed_handoff(stage,review_sha,plan_sha)
    plan=c.validate_plan(c.load_json(stage/'plan.json'))
    for name,digest in plan['files'].items():c.require(c.file_sha(stage/name)==digest,'Staged evidence changed: '+name)
    c.validate_chain(plan,stage)
    installed_path=stage.parent/'runtime/install-receipt.json'
    installed=c.load_json(installed_path)
    after={**plan['install_boot_hashes'],'bzImage':plan['kernel_sha256'],'initramfs.cpio.gz':plan['initramfs_sha256'],'bootargs.txt':plan['new_bootargs_sha256']}
    c.require(installed.get('committed') is True and installed.get('unmounted') is True and installed.get('boot_after')==after and installed.get('boot_before')==plan['install_boot_hashes'],'Exact C-to-IB installation receipt absent')
    c.require(installed.get('experiment')==plan['experiment'] and installed.get('release')==c.RELEASE and installed.get('reviewed_handoff_sha256')==review_sha,'Installation receipt review/experiment differs')
    return {'verified':True,'experiment':plan['experiment'],'plan_sha256':plan_sha,'handoff_review_sha256':review_sha,
            'install_receipt_sha256':c.file_sha(installed_path),'candidate_sha256':c.CANDIDATE,'patch_sha256':c.PATCH,
            'source_sha256':c.SOURCE_PINS,'kernel_sha256':plan['kernel_sha256'],'build_receipt_sha256':plan['files']['build.json'],
            'compiled_review_sha256':plan['files']['compiled-review.json'],'bootargs_sha256':plan['new_bootargs_sha256']}


def analyze(raw,session,exp,binding=None):
    exp=c.experiment(exp);row=session.get('index_record',session)
    text=raw.decode('utf-8',errors='replace').replace('\r','')
    errors=[]
    if row.get('label')!=exp:errors.append('Session label differs')
    if row.get('state')!='completed' or row.get('continuity_errors'):errors.append('Continuity is not completed without errors')
    if row.get('bytes')!=len(raw) or row.get('end_offset',0)-row.get('start_offset',0)!=len(raw):errors.append('Bounded raw length differs')
    if not row.get('capture_generation') or type(row.get('connection_epoch')) is not int:errors.append('Capture identity missing')
    if 'artifacts' in session and session['artifacts']['raw']['sha256']!=c.sha(raw):errors.append('Saved raw digest differs')
    boots=re.findall(r'Linux version ([^\s]+)',text)
    if boots!=[c.RELEASE]:errors.append('Expected exactly one matching Linux boot')
    if re.search(r'Kernel panic|(?:^|\n)[^\n]*(?:Oops:|BUG: unable to handle|soft lockup)',text):errors.append('Kernel fault marker present')
    tags={}
    for tag,payload in re.findall(r'PS4_UVD_IBDATA ([A-Z_]+) ([^\n]+)',text):tags.setdefault(tag,[]).append(payload)
    parks=re.findall(r'PS4_UVD_READY RESULT=([^\s]+) error=(-?\d+) PARKED',text)
    result={'schema':1,'experiment':exp,'release':c.RELEASE,'raw_sha256':c.sha(raw),'raw_bytes':len(raw),'session':row,
            'bindings':binding,'records':[],'end':None,'evidence':None,'final':None,'saved':None,
            'park':[{'result':r,'error':int(v)} for r,v in parks],'evidence_errors':errors,
            'log_supports_marker':False,'qualified_marker_observation':False,'hardware_acceptance':False,'decode_demonstrated':False,
            'limitations':['Sequential recorded values only; no atomic onset or causation.',
                          'Hidden direct guard reads and indexed state during the window are not reconstructible from REC fields.',
                          'Endpoint values cannot create predeadline marker evidence.',
                          'No firmware message, decoded frame, general coherency, containment, reset or teardown claim.']}
    try:
        c.require(all(len(v)==1 for k,v in tags.items() if k not in ('REC',)),'Repeated non-record IB-DATA tag')
        c.require(len(parks)==1,'Expected exactly one terminal PARKED result')
        if 'END' not in tags:
            c.require(not tags,'Observation began without its END report')
            result['status']='EARLY_TERMINAL_NO_OBSERVATION' if not errors else 'INCOMPLETE_OR_INVALID_EVIDENCE'
            return result
        end=fields(tags['END'][0]);evidence=fields(tags['EVIDENCE'][0]);records=[fields(x) for x in tags.get('REC',[])]
        # Preserve parsed samples before semantic checks so failures/late values
        # remain reviewable even when the summary is inconsistent.
        result.update(end=end,evidence=evidence,records=records)
        c.require(set(end)==END_KEYS and set(evidence)==EVIDENCE_KEYS,'END/EVIDENCE schema differs')
        c.require(end['records']==len(records)<=40 and end['accepted']==0,'Record/acceptance count differs')
        for k in ('enabled','submitted','observed_data_seen','bit16_seen','final_sampled','final_valid','sweep_ran','sweep_valid','marker_observed'):
            c.require(end[k] in (0,1),'Nonboolean END field: '+k)
        c.require(parks[0]==(end['result'],str(end['error'])),'PARK and END result/error differ')
        c.require(end['predeadline_records']==sum(r['valid'] and r['predeadline'] for r in records),'Predeadline-record count differs')
        p,terminal=replay(records)
        expected={k:p[k] for k in EVIDENCE_KEYS-{'class'}};expected['class']=evidence_class(p)
        c.require(evidence==expected,'EVIDENCE differs from independent record accounting')
        for k in ('observed_data_seen','last_rp','bit16_seen'):c.require(end[k]==p[k],'END progress differs: '+k)
        if records:c.require(end['publish_ns']<=records[0]['elapsed_ns'],'First record precedes publication stamp')
        c.require(end['elapsed_ns']>=(records[-1]['t_end_ns'] if records else 0),'END timestamp precedes final record')
        prepared=fields(tags['PREPARED'][0]) if 'PREPARED' in tags else None
        if prepared:c.require(prepared==PREPARED,'Packet CRC/identity differs')
        c.require(not end['submitted'] or (prepared and 'PUBLICATION_BEGIN' in tags and end['enabled']),'Submitted identity/intention evidence absent')
        if not end['submitted']:
            c.require(not records and not end['final_sampled'] and end['publish_ns']==0,'Prepublication stop contains submitted evidence')
            c.require(EARLY_ERRORS.get(end['result'])==end['error'] and end['end_reason']=='prepublication','Unrecognized prepublication result')
        elif end['final_sampled']:
            final=fields(tags['FINAL_CORE'][0]);result['final']=final
            c.require(set(final)==SAMPLE|{'sampled'} and final['sampled']=='all_then_data0','FINAL_CORE schema differs')
            c.require(final['valid']==end['final_valid'] and final['ib']==end['final_ib'],'Final summary differs')
            c.require(end['end_reason'] in ('deadline','record_budget'),'Final sample outside normal window completion')
            c.require(terminal in (None,'late'),'Endpoint followed a terminal record fault')
            if end['end_reason']=='record_budget':c.require(len(records)==40 and terminal is None and end['elapsed_ns']==records[-1]['t_end_ns']<LIMIT,'Record-budget end differs')
            else:c.require(end['elapsed_ns']>=LIMIT,'Deadline end occurred before deadline')
            expected_error=-5
            if not final['valid']:expected_result='IBDATA_FINAL_GUARD'
            else:
                c.require(visible_valid(final),'Valid endpoint fails printed predicates')
                if final['rp']<p['last_rp']:expected_result='IBDATA_POINTER_REGRESSION'
                elif p['observed_data_seen'] and final['data']==BASE:expected_result='IBDATA_MARKER_REGRESSION'
                else:
                    c.require(end['sweep_ran']==1 and 'CTX_SWEEP' in tags,'Endpoint sweep missing')
                    sweep=fields(tags['CTX_SWEEP'][0]);c.require(sweep['valid']==end['sweep_valid'],'Sweep summary differs')
                    if not end['sweep_valid']:expected_result='IBDATA_CTX_SWEEP_GUARD'
                    else:
                        expected_result=evidence_class(p)
                        expected_error=0 if p['confirmed'] else -5 if not p['data_seen'] and p['bit16_seen'] else -110
            if expected_result in ('IBDATA_FINAL_GUARD','IBDATA_POINTER_REGRESSION','IBDATA_MARKER_REGRESSION'):
                c.require(end['sweep_ran']==end['sweep_valid']==0 and 'CTX_SWEEP' not in tags,'Forbidden sweep after endpoint failure')
            c.require((end['result'],end['error'])==(expected_result,expected_error),'Endpoint outcome differs')
        else:
            c.require(end['end_reason']=='fault' and end['error']!=0,'Submitted non-final stop is not a fault')
            if terminal in ('IBDATA_POINTER_REGRESSION','IBDATA_MARKER_REGRESSION'):c.require(end['result']==terminal,'Regression outcome differs')
            elif terminal=='guard':c.require(end['result'] in ('IBDATA_UNEXPECTED_VALUE','IBDATA_UNEXPECTED_CONTEXT','IBDATA_STATUS_UNEXPECTED','IBDATA_CORE_GUARD'),'Guard outcome differs')
            else:c.require(end['result']=='IBDATA_RECORD_OVERFLOW' and end['error']==-22,'Unexplained submitted fault')
            c.require(not end['sweep_ran'] and not end['sweep_valid'] and 'FINAL_CORE' not in tags and 'CTX_SWEEP' not in tags,'Fault path took endpoint reads')
        if terminal is not None:
            c.require('SAVED' in tags, 'Terminal failing/late sample report missing')
        if 'SAVED' in tags:
            saved=fields(tags['SAVED'][0]);result['saved']=saved
            c.require(set(saved)==SAMPLE|{'sampled'},'SAVED schema differs')
            c.require(saved.get('sampled')=='all_then_data0', 'SAVED sampling label differs')
            if records:c.require(all(saved[k]==records[-1][k] for k in SAMPLE),'SAVED differs from terminal REC')
        c.require(end['marker_observed']==int(bool(end['final_sampled'] and end['error']==0 and p['confirmed'])),'Marker-observed field differs')
        supports=bool(end['marker_observed'] and end['result']=='IBDATA_MARKER_OBSERVED' and not errors)
        result['log_supports_marker']=supports
        result['qualified_marker_observation']=supports and bool(binding and binding.get('verified') and binding.get('experiment')==exp)
        result['status']='MARKER_OBSERVATION_BOUND_NO_ACCEPTANCE' if result['qualified_marker_observation'] else 'LOG_OBSERVATION_ONLY' if supports else 'TERMINAL_OBSERVATION_NO_MARKER_CLAIM'
    except (RuntimeError,ValueError,KeyError,IndexError) as error:
        errors.append(str(error));result['status']='INCOMPLETE_OR_INVALID_EVIDENCE'
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('raw','session-json','stage','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--plan-sha256',required=True);p.add_argument('--review-sha256',required=True)
    a=p.parse_args();binding=stage_evidence(a.stage,a.plan_sha256,a.review_sha256)
    result=analyze(c.raw(a.raw),c.load_json(a.session_json),binding['experiment'],binding)
    c.save(a.output,result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('records','session')},indent=2,sort_keys=True))
