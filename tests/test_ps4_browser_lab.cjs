"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const lab=require("../diagnostics/ps4/lab/lab.js");
const entry=(name,status="PASS",measurements={})=>({test:name,saved:true,run_id:name,result:{test_id:`browser.${name}`,status,measurements}});

test("regular 60Hz callbacks produce cadence statistics without a physical-refresh claim",()=>{
  const metrics=lab.frameStatistics(Array(600).fill(1000/60));
  assert.ok(Math.abs(metrics.estimated_raf_hz-60)<1e-9);
  assert.equal(metrics.sample_count,600);assert.equal(metrics.missed_frames_estimate,0);
  assert.equal(metrics.inconclusive,false);assert.match(metrics.scope,/not physical/);
});
test("missed-frame estimate and percentiles include a delayed frame",()=>{
  const metrics=lab.frameStatistics([10,10,10,10,30]);
  assert.equal(metrics.median_frame_ms,10);assert.equal(metrics.missed_frames_estimate,2);
  assert.ok(metrics.p95_frame_ms>10);assert.ok(metrics.p99_frame_ms>=metrics.p95_frame_ms);
});
test("hidden, throttled and empty observations remain inconclusive",()=>{
  assert.equal(lab.frameStatistics([16,16],{hidden:true}).inconclusive,true);
  assert.equal(lab.frameStatistics([1000,1000]).inconclusive,true);
  const empty=lab.frameStatistics([NaN,Infinity,-1,0]);
  assert.equal(empty.estimated_raf_hz,null);assert.equal(empty.inconclusive,true);
});
test("video counters are per-run and drops are a separate warning",()=>{
  const metrics=lab.videoStatistics({totalVideoFrames:100,droppedVideoFrames:10},{totalVideoFrames:192,droppedVideoFrames:25});
  assert.equal(metrics.total_frames,92);assert.equal(metrics.dropped_frames,15);
  assert.equal(metrics.dropped_frame_ratio,15/92);assert.equal(metrics.smoothness,"drops_observed");
  assert.match(metrics.warning,/does not establish smooth/);
  assert.equal(lab.videoStatistics({totalVideoFrames:100,droppedVideoFrames:4},{totalVideoFrames:20,droppedVideoFrames:1}).smoothness,"not_measured");
});
test("the next test waits for the previous durable save acknowledgement",async()=>{
  const calls=[];let release;
  const pending=new Promise(resolve=>{release=resolve;});
  const running=lab.runSequence(async(name,context)=>{
    calls.push(name);assert.equal(context.previous.length,context.index);
    if(context.index===0)await pending;
    return entry(name);
  });
  await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(calls,["capabilities"]);
  release();const result=await running;
  assert.deepEqual(calls,[...lab.SUITE_STEPS]);assert.equal(result.state,"completed");assert.equal(result.entries.length,5);
});
test("FAIL, context loss, timeout, and unconfirmed saves stop the sequence",async()=>{
  for(const failure of [entry("webgl","FAIL"),entry("webgl","PASS",{context_lost:true}),entry("webgl","INCONCLUSIVE",{timed_out:true}),{...entry("webgl"),saved:false}]){
    const calls=[];const result=await lab.runSequence(async name=>{calls.push(name);return name==="webgl"?failure:entry(name);});
    assert.equal(result.state,"stopped");assert.deepEqual(calls,["capabilities","canvas2d","webgl"]);
  }
  const calls=[];const result=await lab.runSequence(async name=>{calls.push(name);throw Error("report-save failure");});
  assert.equal(result.state,"stopped");assert.deepEqual(calls,["capabilities"]);
});
test("unsupported and observational results can complete a suite",async()=>{
  const result=await lab.runSequence(async name=>entry(name,name==="capabilities"?"INCONCLUSIVE":name==="webgl"?"UNSUPPORTED":"PASS"));
  assert.equal(result.state,"completed");assert.equal(result.entries.length,5);
});
test("watchdog aborts asynchronous work",async()=>{
  let aborted=false;
  await assert.rejects(lab.withLimit(signal=>new Promise(()=>{signal.addEventListener("abort",()=>{aborted=true;});}),5),lab.TestTimeout);
  assert.equal(aborted,true);
});
test("software renderer strings are never classified as hardware",()=>{
  assert.equal(lab.classify("ANGLE SwiftShader (AMD compatible)"),"software");
  assert.equal(lab.classify("AMD Radeon Liverpool"),"hardware");
  assert.equal(lab.classify("WebGL"),"unknown");
});
test("opening the page fetches identity only and never starts graphics or video",async()=>{
  const elements=new Map(),buttons=lab.SUITE_STEPS.map(name=>({dataset:{test:name},addEventListener(){}}));
  const node=id=>{
    if(!elements.has(id))elements.set(id,{id,dataset:{},addEventListener(){},replaceChildren(...items){for(const item of items)elements.set(item.id,item);}});
    return elements.get(id);
  };
  const calls=[];
  const context={console,performance,AbortController,setTimeout,clearTimeout,navigator:{userAgent:"Firefox/154",platform:"Synthetic"},
    document:{getElementById:node,createElement:()=>({dataset:{}}),querySelectorAll:selector=>selector.includes("#run-all")?[...buttons,node("run-all")]:buttons},
    fetch:async path=>{calls.push(path);return {ok:true,json:async()=>path==="/api/session"?{csrf_token:"fixture"}:{system:{hostname:"fixture",kernel_release:"fixture"}}};},
    requestAnimationFrame(){throw Error("unexpected GPU work");}};
  vm.runInNewContext(fs.readFileSync(require.resolve("../diagnostics/ps4/lab/lab.js"),"utf8"),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls,["/api/session","/api/state"]);assert.equal(node("run-all").disabled,false);
});

test("only a valid one-shot server grant auto-starts; early pending claims wait",async()=>{
  for(const allowed of [false,true]){
    const elements=new Map(),calls=[],reports=[],starts=[],finishes=[];let sessions=0;
    function make(id){return {id,dataset:{},addEventListener(){},replaceChildren(...items){for(const item of items)elements.set(item.id,item);},
      cloneNode(){return make(id);},replaceWith(replacement){elements.set(id,replacement);},
      getContext(){throw Error("A blocked next-step marker must never draw");}};}
    const node=id=>{if(!elements.has(id))elements.set(id,make(id));return elements.get(id);};
    const buttons=lab.SUITE_STEPS.map(name=>({...make(name),dataset:{test:name}}));
    const nonce="A".repeat(32),batch_id="20261104T120000Z-0123456789ab";
    const context={console,performance,AbortController,Uint8Array,setTimeout:(fn,ms)=>setTimeout(fn,ms===250?1:ms),clearTimeout,
      navigator:{userAgent:"Firefox/154",platform:"Synthetic",hardwareConcurrency:2},screen:{width:1920,height:1080},devicePixelRatio:1,window:{},
      document:{getElementById:node,createElement:kind=>kind==="video"?{canPlayType:()=>"probably"}:make(""),querySelectorAll:selector=>selector.includes("#run-all")?[...buttons,node("run-all")]:buttons},
      fetch:async(path,options)=>{
        calls.push(path);let value,ok=true;
        if(path==="/api/session"){
          sessions++;value={csrf_token:"fixture",auto_pending:allowed&&sessions===1,
            auto_run:allowed&&sessions>1?{suite:"baseline",nonce,batch_id,expires_in_seconds:60}:allowed?null:{suite:"baseline",nonce:"invalid",expires_in_seconds:0}};
        }else if(path==="/api/state")value={system:{hostname:"fixture",kernel_release:"fixture"}};
        else if(path==="/api/browser/start"){
          const body=JSON.parse(options.body);starts.push(body);
          if(body.test_id==="browser.capabilities")value={run_id:"fixture-run"};
          else{ok=false;value={error:"Synthetic stop before any drawing"};}
        }else if(path==="/api/browser/results"){
          const body=JSON.parse(options.body);reports.push(body);value={run_id:body.run_id,test_id:body.test_id,phase:"completed"};
        }else if(path==="/api/browser/finish"){
          finishes.push(JSON.parse(options.body));assert.equal(options.headers["X-PSX-CSRF"],"fixture");value={saved:true,closed:true,status:"INCONCLUSIVE"};
        }else throw Error("Unexpected request "+path);
        return {ok,json:async()=>value};
      },requestAnimationFrame(){throw Error("Unexpected animation");}};
    vm.runInNewContext(fs.readFileSync(require.resolve("../diagnostics/ps4/lab/lab.js"),"utf8"),context);
    await new Promise(resolve=>setTimeout(resolve,15));
    if(allowed){
      assert.equal(sessions,2);assert.equal(reports.length,1);assert.equal(reports[0].measurements.suite.launch_nonce,nonce);
      assert.equal(starts[0].batch_id,batch_id);assert.equal(starts[0].nonce,nonce);
      assert.equal(finishes.length,1);assert.deepEqual(finishes[0],{batch_id,nonce,run_ids:["fixture-run"],error:"Synthetic stop before any drawing"});
      assert.ok(calls.indexOf("/api/browser/results")<calls.indexOf("/api/browser/finish"));
      assert.equal(node("summary").textContent,"Saved · returning to diagnostics");
    }else{assert.equal(reports.length,0);assert.equal(finishes.length,0);assert.deepEqual(calls,["/api/session","/api/state"]);}
  }
});

test("metric presentation keeps unavailable counters distinct from measured zero",()=>{
  const context=vm.createContext({});
  vm.runInContext(fs.readFileSync(require.resolve("../diagnostics/ps4/lab/lab.js"),"utf8"),context);
  const elements=new Map();context.document={getElementById:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);}};
  vm.runInContext("renderMeasurements()",context);
  assert.equal(elements.get("metric-fps").textContent,"—");assert.equal(elements.get("metric-drops").textContent,"—");
  vm.runInContext('displayedResults.set("video",{result:{measurements:{total_frames:null,dropped_frames:null,dropped_frame_ratio:null}}});renderMeasurements()',context);
  assert.equal(elements.get("metric-drops").textContent,"—");assert.equal(elements.get("metric-video-frames").textContent,"Counters unavailable");
  vm.runInContext('displayedResults.set("video",{result:{measurements:{total_frames:300,dropped_frames:0,dropped_frame_ratio:0}}});renderMeasurements()',context);
  assert.equal(elements.get("metric-drops").textContent,"0.00");assert.equal(elements.get("metric-video-frames").textContent,"0 of 300 frames");
});

test("metric presentation retains pacing and renderer context across later tests",()=>{
  const context=vm.createContext({});
  vm.runInContext(fs.readFileSync(require.resolve("../diagnostics/ps4/lab/lab.js"),"utf8"),context);
  const elements=new Map();context.document={getElementById:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);}};
  vm.runInContext('displayedResults.set("animation",{result:{measurements:{average_fps:59.94,median_frame_ms:16.68,p95_frame_ms:20.5,missed_frames_estimate:2,inconclusive:true}}});displayedResults.set("webgl",{result:{measurements:{pixels:4096,mismatches:1},renderer:{renderer:"SwiftShader",classification:"software"}}});displayedResults.set("video",{result:{measurements:{total_frames:100,dropped_frames:3,dropped_frame_ratio:0.03}}});renderMeasurements()',context);
  assert.equal(elements.get("metric-fps").textContent,"59.9");assert.equal(elements.get("metric-p95").textContent,"20.50 ms");
  assert.equal(elements.get("metric-webgl").textContent,"4095 / 4096");assert.equal(elements.get("renderer-kind").textContent,"Software reported");
  assert.match(elements.get("measurement-note").textContent,/needs review/);assert.match(elements.get("measurement-note").textContent,/drops observed/);
  assert.equal(elements.get("saved-count").textContent,"3 / 5 saved");
});

test("automatic finish is attempted once after completion or an exception, without retrying an unconfirmed finish",async()=>{
  for(const failed of [false,true]){
    const context=vm.createContext({}),elements=new Map(),finishes=[];
    vm.runInContext(fs.readFileSync(require.resolve("../diagnostics/ps4/lab/lab.js"),"utf8"),context);
    context.document={getElementById:id=>{if(!elements.has(id))elements.set(id,{textContent:""});return elements.get(id);},querySelectorAll:()=>[]};
    context.finish=async(path,body)=>{finishes.push({path,body});if(failed)throw Error("Synthetic finish response lost");return {saved:true,closed:true,status:"PASS"};};
    context.perform=async()=>{if(failed)throw Error("Synthetic save failure");return {entries:[{run_id:"first"},{run_id:"second"}],reason:""};};
    await vm.runInContext('run=perform;jsonRequest=finish;finishAutomaticSuite({nonce:"A".repeat(32),batch_id:"20261104T120000Z-0123456789ab"})',context);
    assert.equal(finishes.length,1);assert.equal(finishes[0].path,"/api/browser/finish");
    assert.deepEqual(Array.from(finishes[0].body.run_ids),failed?[]:["first","second"]);
    assert.equal(finishes[0].body.error,failed?"Synthetic save failure":"");
    assert.equal(elements.get("summary").textContent,failed?"Finish unavailable · return to diagnostics":"Saved · returning to diagnostics");
    assert.equal(vm.runInContext("autoNonce===null&&autoBatchId===null",context),true);
  }
});

test("configured refresh comes only from an available display mode, independently of browser cadence",()=>{
  const context=vm.createContext({}),elements=new Map();
  vm.runInContext(fs.readFileSync(require.resolve("../diagnostics/ps4/lab/lab.js"),"utf8"),context);
  context.document={getElementById:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);}};
  vm.runInContext('displayedResults.set("animation",{result:{measurements:{average_fps:119.9,estimated_raf_hz:120}}});renderMeasurements()',context);
  assert.equal(elements.get("metric-fps").textContent,"119.9");assert.equal(elements.get("metric-refresh").textContent,"Unavailable");
  for(const [mode,expected] of [[{available:true,configured_refresh_hz:60},"60 Hz"],[{available:true,configured_refresh_hz:59.94},"59.94 Hz"],[null,"Unavailable"],[{available:false,configured_refresh_hz:60},"Unavailable"],[{available:true,configured_refresh_hz:0},"Unavailable"],[{available:true,configured_refresh_hz:"60"},"Unavailable"]]){
    context.mode=mode;vm.runInContext('displayedMode=mode;renderMeasurements()',context);
    assert.equal(elements.get("metric-refresh").textContent,expected);assert.equal(elements.get("metric-fps").textContent,"119.9");
  }
});
