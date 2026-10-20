#!/usr/bin/env python3
"""Read saved bounded-session bytes and classify evidence quality; no UART commands."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('stobs_common', HERE/'common.py')
c=importlib.util.module_from_spec(s);s.loader.exec_module(c)
HEX={'status','int','rbc','ib','wp','rp','ctx','data','wpc','rpa','fault','pd','last_data','last_ctx'}
END_KEYS=set('result log_class enabled submitted elapsed_ns publish_ns records fine bit16_first onset_ns sets bit16_clears first_clear markers persistent onset_with_values data_regressions context_regressions accepted last_wp last_rp last_data last_ctx final_valid sweep_valid'.split())
REC_KEYS=set('i elapsed_us t_end_us status int rbc ib wp rp ctx data wpc rpa fault reset in_reset pd bit16'.split())
CLASSES={'STOBS_BIT16_PERSISTENT','STOBS_BIT16_TRANSIENT','STOBS_BIT16_TOGGLING','STOBS_BIT16_ABSENT_MARKERS','STOBS_BIT16_ABSENT_NO_MARKERS','STOBS_NO_RECORDS'}


def fields(text):
    rows=re.findall(r'([A-Za-z0-9_]+)=([^\s;]+)',text)
    c.require(len({k for k,v in rows})==len(rows),'Duplicate log field')
    out={}
    for k,v in rows:
        if k in ('result','log_class'):
            out[k]=v
        else:
            out[k]=int(v,16 if k in HEX else 10)
    return out


def pattern(records, submitted):
    first=next((i for i,r in enumerate(records) if r['bit16']),-1)
    sets=clears=0;previous=False;first_clear=-1
    for i,r in enumerate(records):
        if r['bit16'] and not previous: sets+=1
        if not r['bit16'] and previous:
            if not clears: first_clear=i
            clears+=1
        previous=bool(r['bit16'])
    markers=any(r['data']==0x55564435 or r['ctx']==0x43545835 for r in records)
    persistent=first>=0 and not clears and sets==1
    log_class=('STOBS_NO_RECORDS' if not submitted or not records else
        ('STOBS_BIT16_ABSENT_MARKERS' if markers else 'STOBS_BIT16_ABSENT_NO_MARKERS') if first<0 else
        'STOBS_BIT16_PERSISTENT' if persistent else 'STOBS_BIT16_TRANSIENT' if sets==1 else 'STOBS_BIT16_TOGGLING')
    onset=bool(first>=0 and (records[first]['data']==0x55564435 or records[first]['ctx']==0x43545835 or records[first]['wp']==records[first]['rp']==16))
    return {'log_class':log_class,'bit16_first':first,'sets':sets,'bit16_clears':clears,'first_clear':first_clear,
            'markers':int(markers),'persistent':int(persistent),'onset_with_values':int(onset),
            'data_regressions':sum(a['data']==0x55564435 and b['data']==0x43504435 for a,b in zip(records,records[1:])),
            'context_regressions':sum(a['ctx']==0x43545835 and b['ctx']==0x43504335 for a,b in zip(records,records[1:]))}


def analyze(raw, session, exp):
    """Parsing errors reduce evidence quality; they never become hardware PASS."""
    exp=c.experiment(exp)
    row=session.get('index_record',session)
    text=raw.decode('utf-8',errors='replace').replace('\r','')
    errors=[]
    if row.get('label')!=exp: errors.append('Session label differs')
    if row.get('state')!='completed' or row.get('continuity_errors'): errors.append('Continuity is not completed without errors')
    if row.get('bytes')!=len(raw) or row.get('end_offset',0)-row.get('start_offset',0)!=len(raw): errors.append('Bounded raw length differs')
    if not row.get('capture_generation') or type(row.get('connection_epoch')) is not int: errors.append('Capture identity missing')
    if 'artifacts' in session and session['artifacts']['raw']['sha256']!=c.sha(raw): errors.append('Saved raw digest differs')
    boots=re.findall(r'Linux version ([^\s]+)',text)
    if boots!=[c.RELEASE]: errors.append('Expected exactly one matching Linux boot')
    end_lines=re.findall(r'PS4_UVD_STOBS END ([^\n]+)',text)
    rec_lines=re.findall(r'PS4_UVD_STOBS REC ([^\n]+)',text)
    park=re.findall(r'PS4_UVD_READY RESULT=([^\s]+) error=(-?\d+) PARKED',text)
    publications=text.count('PS4_UVD_STOBS PUBLICATION_BEGIN ')
    prepared=text.count('PS4_UVD_STOBS PREPARED ')
    if publications>1 or prepared>1 or len(park)>1 or len(end_lines)>1: errors.append('Repeated run markers')
    if re.search(r'Kernel panic|(?:^|\n)[^\n]*(?:Oops:|BUG: unable to handle|soft lockup)',text): errors.append('Kernel fault marker present')
    result={'schema':1,'experiment':exp,'release':c.RELEASE,'raw_sha256':c.sha(raw),'raw_bytes':len(raw),
            'session':row,'end':None,'records':[],'park':[{'result':r,'error':int(e)} for r,e in park],
            'publication_begin_lines':publications,'prepared_lines':prepared,'hardware_acceptance':False,
            'decode_demonstrated':False,'qualified_class_observation':False,'evidence_errors':errors,
            'limits':['Saved sequential register observations only; no atomicity or causation.',
                      'Indexed predicates unobserved between ENABLE and terminal sweep.',
                      'Final marker allowances are not rechecked by final_valid.',
                      'Persistence after the observation window and during terminal printing is unobserved.',
                      'Reused EXP005 marker values are not independent acceptance evidence.']}
    if len(end_lines)!=1:
        if rec_lines or prepared or publications: errors.append('Observation began without its END report')
        if len(park)!=1: errors.append('Terminal park missing or repeated')
        result['status']='EARLY_TERMINAL_NO_OBSERVATION' if len(park)==1 and not errors else 'INCOMPLETE_OR_INVALID_EVIDENCE'
        return result
    try:
        end=fields(end_lines[0].split(';',1)[0]);records=[fields(x) for x in rec_lines]
        c.require(set(end)==END_KEYS,'Missing/extra END fields')
        c.require(len(records)<=40 and end['records']==len(records),'Record count differs')
        c.require(all(set(r)==REC_KEYS and r['i']==i for i,r in enumerate(records)),'REC schema or indices differ')
        c.require(all(r['t_end_us']>=r['elapsed_us'] and r['bit16']==int(bool(r['status'] & 0x10000)) for r in records),'Record times/bit16 flag differ')
        c.require(all(a['t_end_us']<=b['elapsed_us'] for a,b in zip(records,records[1:])),'Record timestamps regress')
        c.require(end['accepted']==0 and end['fine']==min(len(records),24),'Unexpected acceptance/fine count')
        c.require(all(end[k]==v for k,v in pattern(records,end['submitted']).items()),'Recorded class/counters disagree with REC lines')
        c.require(len(park)==1 and park[0][0]==end['result'],'Park and END result differ')
        c.require(end['submitted'] in (0,1) and publications in (0,1) and (not end['submitted'] or publications==1) and prepared==1,'Publication-intent/preparation evidence differs')
        c.require(all(r['elapsed_us'] < 250000 for r in records), 'Record began at/after the deadline')
        if records:
            last=records[-1]
            c.require((end['last_wp'],end['last_rp'],end['last_data'],end['last_ctx'])==(last['wp'],last['rp'],last['data'],last['ctx']),'Last-record summary differs')
            c.require(records[0]['elapsed_us']>=end['publish_ns']//1000,'First record precedes publication stamp')
            result['first_record_delay_us_quantized']=records[0]['elapsed_us']-end['publish_ns']//1000
        first=end['bit16_first']
        c.require((end['onset_ns']//1000==records[first]['elapsed_us']) if first>=0 else end['onset_ns']==0,'Onset timestamp differs')
        result['end'],result['records']=end,records
        normal=(int(park[0][1])==0 and end['final_valid']==1 and end['sweep_valid']==1 and end['submitted']==1 and bool(records) and end['result']==end['log_class'])
        if normal:
            final=re.findall(r'PS4_UVD_STOBS FINAL_CORE [^\n]* valid=(\d+)',text)
            sweep=re.findall(r'PS4_UVD_STOBS CTX_SWEEP valid=(\d+)',text)
            c.require(final==['1'] and sweep==['1'], 'Completed core/indexed reports missing or invalid')
        result['qualified_class_observation']=normal and not errors
        result['status']='CLASS_OBSERVATION_RECORDED_NO_ACCEPTANCE' if result['qualified_class_observation'] else 'ERROR_OR_UNQUALIFIED_OBSERVATION'
    except (RuntimeError,ValueError,KeyError) as error:
        errors.append(str(error));result['status']='INCOMPLETE_OR_INVALID_EVIDENCE'
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw',type=Path,required=True);p.add_argument('--session-json',type=Path,required=True)
    p.add_argument('--experiment',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=analyze(c.raw(a.raw),c.load_json(a.session_json),a.experiment)
    c.save(a.output,result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('records','session')},indent=2,sort_keys=True))
