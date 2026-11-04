# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Meerzulee
"""Synthetic host reference/oracle and sandboxed one-play page checks; no GPU."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('psx_browser_uvd_fixture_tests',str(ROOT/'scripts/prepare-ps4-browser-uvd-fixture'))
spec=importlib.util.spec_from_loader(loader.name,loader)
APP=importlib.util.module_from_spec(spec);loader.exec_module(APP)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir='/var/tmp',prefix='psx-browser-uvd-')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.clip=self.root/'input.h264';self.clip.write_bytes(bytes(i%251 for i in range(APP.CLIP_BYTES)))
        self.raw=b''.join(bytes([index])*APP.FRAME_BYTES for index in range(APP.FRAMES))
        self.fixture={'schema':'psx-uvd-playback-fixture-v1','width':128,'height':96,'frame_count':120,
            'frame_rate':'30/1','frame_bytes':APP.FRAME_BYTES,'pixel_format':'nv12','codec':'h264','container':'h264-annex-b',
            'clip':{'bytes':APP.CLIP_BYTES,'sha256':hashlib.sha256(self.clip.read_bytes()).hexdigest()},
            'reference':{'bytes':len(self.raw),'sha256':hashlib.sha256(self.raw).hexdigest()},
            'frames':[{'index':index,'bytes':APP.FRAME_BYTES,'sha256':hashlib.sha256(self.raw[index*APP.FRAME_BYTES:(index+1)*APP.FRAME_BYTES]).hexdigest()} for index in range(APP.FRAMES)]}
        self.manifest=self.root/'FIXTURE.json';self.manifest.write_text(json.dumps(self.fixture))
        self.tool=self.root/'synthetic-ffmpeg';self.tool.write_bytes(b'synthetic explicit host tool');self.tool.chmod(0o755)
        self.output=self.root/'new-bundle';self.calls=[];self.corrupt_frame=None;self.target_frames=120
        for name,value in [('FIXTURE_SHA256',APP.digest(self.manifest)),('CLIP_SHA256',APP.digest(self.clip)),
                           ('REFERENCE_SHA256',hashlib.sha256(self.raw).hexdigest())]:
            patcher=mock.patch.object(APP,name,value);patcher.start();self.addCleanup(patcher.stop)

    def command(self,argv,**kwargs):
        self.calls.append(argv)
        stdout=b'ffmpeg version synthetic\n' if '-version' in argv else b''
        if 'copy' in argv:
            self.target_frames=int(argv[argv.index('-frames:v')+1])
            Path(argv[-1]).write_bytes(b'\0\0\0\x18ftypisom synthetic MP4 fixture')
        if '-show_frames' in argv:
            frames=self.target_frames;duration=frames//30
            stdout=json.dumps({'streams':[{'codec_name':'h264','width':128,'height':96,
                'r_frame_rate':'30/1','avg_frame_rate':'30/1','nb_frames':str(frames),
                'time_base':'1/15360','duration_ts':frames*512,'duration':str(duration)}],
                'format':{'duration':str(duration)},'frames':[{'pts':i*512}for i in range(frames)]}).encode()
        if kwargs.get('sink') is not None:
            frames=kwargs['stdout_limit']//APP.FRAME_BYTES
            for index in range(frames):
                value=self.raw[(index%120)*APP.FRAME_BYTES:(index%120+1)*APP.FRAME_BYTES]
                if index==self.corrupt_frame:value=value[:-1]+bytes([value[-1]^1])
                kwargs['sink'].write(value)
        return {'returncode':0,'stdout':stdout,'stderr':b'',
                'stdout_bytes':kwargs['stdout_limit'] if kwargs.get('sink') is not None else len(stdout),'stderr_bytes':0}

    def prepare(self,duration=4):
        with mock.patch.object(APP,'bounded_command',side_effect=self.command):
            return APP.prepare(self.manifest,self.clip,self.tool,self.output,ffprobe=self.tool,duration_seconds=duration)

    def test_stream_copy_and_software_decode_verify_every_frame_and_whole_oracle(self):
        result=self.prepare()
        self.assertEqual(result['status'],'PASS');self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(result['software_oracle']['frames'],120)
        self.assertEqual(result['software_oracle']['frame_sha256'],[frame['sha256'] for frame in self.fixture['frames']])
        self.assertEqual(result['software_oracle']['sha256'],APP.REFERENCE_SHA256)
        self.assertEqual(result['software_oracle']['mismatched_frames'],[])
        self.assertEqual(len(self.calls),5)
        mux=next(argv for argv in self.calls if 'copy' in argv)
        decode=next(argv for argv in self.calls if '-hwaccel' in argv)
        self.assertEqual(mux[mux.index('-c:v')+1],'copy')
        self.assertIn('-an',mux);self.assertNotIn('-stream_loop',mux)
        self.assertEqual(decode[decode.index('-hwaccel')+1],'none')
        self.assertEqual(decode[decode.index('-threads')+1],'1')
        self.assertFalse((self.output/'decoded.nv12').exists())
        self.assertEqual((self.output/'FIXTURE.json').read_bytes(),self.manifest.read_bytes())
        self.assertEqual((self.output/'LICENSE').read_bytes(),(ROOT/'diagnostics/LICENSE').read_bytes())

    def test_changed_manifest_clip_and_existing_output_refuse_before_execution(self):
        for target in (self.manifest,self.clip):
            previous=target.read_bytes();target.write_bytes(previous+b'changed')
            with mock.patch.object(APP,'bounded_command',side_effect=AssertionError('must not execute')):
                with self.assertRaises(ValueError):APP.prepare(self.manifest,self.clip,self.tool,self.output,ffprobe=self.tool)
            target.write_bytes(previous);self.assertFalse(self.output.exists())
        self.output.mkdir()
        with mock.patch.object(APP,'bounded_command',side_effect=AssertionError('must not execute')):
            with self.assertRaises(ValueError):APP.prepare(self.manifest,self.clip,self.tool,self.output,ffprobe=self.tool)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_last_frame_mismatch_is_saved_as_failure_not_a_hardware_pass(self):
        self.corrupt_frame=119
        with self.assertRaisesRegex(ValueError,'oracle mismatch'):self.prepare()
        receipt=json.loads((self.output/'build-receipt.json').read_text())
        self.assertEqual(receipt['status'],'FAIL')
        self.assertEqual(receipt['software_oracle']['frames'],120)
        self.assertEqual(receipt['software_oracle']['mismatched_frames'],[119])
        self.assertFalse(receipt['hardware_acceptance'])

    def test_bounded_child_output_and_timeout_are_enforced_during_execution(self):
        with self.assertRaisesRegex(ValueError,'output exceeded'):
            APP.bounded_command([sys.executable,'-c','import os;os.write(1,b"x"*8192)'],stdout_limit=1024)
        with self.assertRaisesRegex(ValueError,'timed out'):
            APP.bounded_command([sys.executable,'-c','import time;time.sleep(3)'],timeout=.1)

    @unittest.skipUnless(shutil.which('node'),'Node required for sandboxed page behavior')
    def test_page_plays_and_reports_once_without_navigator_or_hardware_claims(self):
        html=(ROOT/'diagnostics/ps4/lab/uvd.html').read_text()
        self.assertEqual(len(re.findall(r'<video\b',html)),1)
        script=re.search(r'<script>([\s\S]*?)</script>',html).group(1)
        harness=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const source=require('node:fs').readFileSync(0,'utf8');
const config=JSON.parse(process.argv[1]);
const events={},calls=[],timers=[],elements={status:{},detail:{}};
let played=0,frame=null;
const video={muted:false,ended:false,currentTime:0,duration:config.duration_seconds,
  addEventListener:(name,fn)=>events[name]=fn,
  play:async()=>{played++},pause:()=>{},
  requestVideoFrameCallback:fn=>{frame=fn;return 1},cancelVideoFrameCallback:()=>{},
  getVideoPlaybackQuality:()=>({totalVideoFrames:config.frame_count,droppedVideoFrames:2,corruptedVideoFrames:0})};
elements.clip=video;
const context={document:{getElementById:id=>elements[id]},performance:{now:()=>100},
  setTimeout:(fn,ms)=>{timers.push({fn,ms});return 1},clearTimeout:()=>{},
  fetch:async(url,options)=>{calls.push({url,options});return{ok:true,json:async()=>({token:'a'.repeat(32),...config})}}};
vm.runInNewContext(source,context);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(played,1);assert.equal(video.src,'/clip.mp4');assert.equal(video.muted,true);
  assert.equal(calls.filter(call=>call.url==='/session').length,1);
  assert.ok(timers.every(timer=>timer.ms<=45000));
  frame(0,{presentedFrames:config.frame_count-1});video.ended=true;video.currentTime=config.duration_seconds;events.ended();events.error();
  await new Promise(resolve=>setImmediate(resolve));
  const reports=calls.filter(call=>call.url==='/result');assert.equal(reports.length,1);
  const body=JSON.parse(reports[0].options.body);assert.equal(body.outcome,'ended');assert.equal(body.ended,true);
  assert.equal(body.quality.totalVideoFrames,config.frame_count);assert.equal(body.quality.droppedVideoFrames,2);
  assert.equal(body.frameCallbacks,1);assert.equal(body.presentedFrames,config.frame_count-1);
  assert.equal(body.currentTime,config.duration_seconds);assert.equal(body.duration,config.duration_seconds);assert.ok(reports[0].options.body.length<=4096);
  assert.ok(!('status' in body));assert.equal(played,1);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        for profile,frames,duration in [('canonical-120',120,4),('continuous-900',900,30)]:
            config=json.dumps({'fixture_profile':profile,'frame_count':frames,'duration_seconds':duration})
            subprocess.run([shutil.which('node'),'-e',harness,config],input=script,text=True,capture_output=True,check=True,timeout=5)

    def test_continuous900_matches_the_canonical_cycle_and_exact_pts_without_playback_loop(self):
        result=self.prepare(30)
        self.assertEqual(result['fixture_profile'],'continuous-900')
        self.assertEqual(result['frames'],result['frame_count'])
        self.assertEqual((result['frames'],result['duration_seconds']),(900,30))
        self.assertEqual(result['canonical_software_oracle']['sha256'],APP.REFERENCE_SHA256)
        self.assertEqual(result['software_oracle']['frame_sha256'],[self.fixture['frames'][i%120]['sha256']for i in range(900)])
        self.assertEqual(result['software_oracle']['sha256'],result['reference_nv12_sha256'])
        self.assertEqual(len(result['frame_pts_us']),900)
        self.assertEqual(result['frame_pts_us'][:3],[0,33333,66667])
        self.assertEqual(result['frame_pts_us'][-1],29966667)
        self.assertEqual(result['construction']['canonical_repeat_count'],8)
        self.assertIs(result['construction']['browser_loop'],False)
        self.assertEqual((self.output/'continuous.h264').read_bytes(),self.clip.read_bytes()*8)
        self.assertFalse(any('-stream_loop'in argv for argv in self.calls))

    def test_long_last_frame_mismatch_and_nonincreasing_pts_refuse_success(self):
        self.corrupt_frame=899
        with self.assertRaisesRegex(ValueError,'oracle mismatch'):self.prepare(30)
        result=json.loads((self.output/'build-receipt.json').read_text())
        self.assertEqual(result['software_oracle']['mismatched_frames'],[899])
        self.assertEqual(result['status'],'FAIL')
        good={'streams':[{'codec_name':'h264','width':128,'height':96,'r_frame_rate':'30/1','avg_frame_rate':'30/1',
            'nb_frames':'120','time_base':'1/15360','duration_ts':61440,'duration':'4'}],
            'format':{'duration':'4'},'frames':[{'pts':i*512}for i in range(120)]}
        good['frames'][1]['pts']=0
        with self.assertRaisesRegex(ValueError,'increasing frame PTS'):APP.timeline(good,120,4)

    def test_unlisted_duration_refused_before_output_or_commands(self):
        for duration in (0,5,60,30.0,True):
            with self.assertRaises(ValueError):self.prepare(duration)
        self.assertFalse(self.calls)
        self.assertFalse(self.output.exists())


if __name__=='__main__':unittest.main()
