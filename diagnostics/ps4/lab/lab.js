"use strict";
// SPDX-License-Identifier: MIT
// Shared Firefox/Chromium baseline. Opening the page never starts a test.
const SUITE_STEPS = Object.freeze(["capabilities", "canvas2d", "webgl", "animation", "video"]);
const TEST_LIMITS = {capabilities:3000,canvas2d:4000,webgl:8000,animation:14000,video:18000};
const LABELS = {capabilities:"Capabilities",canvas2d:"Canvas",webgl:"WebGL",animation:"Frame pacing",video:"Video"};
const classify = value => /llvmpipe|softpipe|swiftshader|software|lavapipe/i.test(value) ? "software" : /AMD|Radeon|Liverpool|Gladius|NVIDIA|Intel|Apple|Adreno|Mali/i.test(value) ? "hardware" : "unknown";
const rendererUnknown = () => ({vendor:"",renderer:"",classification:"unknown"});
const base = (test,status,measurements,renderer=rendererUnknown()) => ({schema_version:1,test_id:`browser.${test}`,status,duration_ms:0,measurements,renderer,scope:"This browser only; no automatic console hardware acceptance"});
const $ = id => document.getElementById(id);
let csrf="",busy=false,cancelRequested=false,activeController=null,autoNonce=null,autoBatchId=null;
// Presentation only. Reports and their durable-save sequence remain independent.
const displayedResults=new Map();
let displayedMode=null;

function percentile(sorted,fraction) {
  if(!sorted.length)return null;
  const index=(sorted.length-1)*fraction,lower=Math.floor(index),upper=Math.ceil(index);
  return sorted[lower]+(sorted[upper]-sorted[lower])*(index-lower);
}
function frameStatistics(intervals,context={}) {
  const samples=intervals.filter(value=>Number.isFinite(value)&&value>0),ordered=[...samples].sort((a,b)=>a-b);
  const median=percentile(ordered,.5),elapsed=samples.reduce((sum,value)=>sum+value,0);
  const hidden=context.hidden===true,lowCadence=median!==null&&median>=100,longPauses=samples.some(value=>value>=1000);
  return {sample_count:samples.length,elapsed_ms:elapsed,median_frame_ms:median,p95_frame_ms:percentile(ordered,.95),p99_frame_ms:percentile(ordered,.99),
    maximum_frame_ms:ordered.length?ordered[ordered.length-1]:null,estimated_raf_hz:median?1000/median:null,
    average_fps:elapsed?samples.length*1000/elapsed:null,
    missed_frames_estimate:median?samples.reduce((sum,value)=>sum+Math.max(0,Math.round(value/median)-1),0):null,
    frames_over_50ms:samples.filter(value=>value>50).length,hidden_during_test:hidden,suspected_throttling_or_stalls:lowCadence||longPauses,
    timed_out:context.timed_out===true,inconclusive:hidden||lowCadence||longPauses||context.timed_out===true||samples.length<2,
    scope:"Observed requestAnimationFrame cadence, not physical display refresh rate or hardware vsync counters.",
    missed_frame_scope:"Estimate relative to the observed median interval; variable cadence can affect it."};
}
function videoStatistics(before,after) {
  const delta=key=>Number.isFinite(before?.[key])&&Number.isFinite(after?.[key])&&after[key]>=before[key]?after[key]-before[key]:null;
  const total=delta("totalVideoFrames"),dropped=delta("droppedVideoFrames"),valid=total!==null&&dropped!==null&&total>0&&dropped<=total;
  return {total_frames:valid?total:null,dropped_frames:valid?dropped:null,dropped_frame_ratio:valid?dropped/total:null,
    smoothness:!valid?"not_measured":dropped?"drops_observed":"no_drops_observed",
    warning:!valid?"Per-run playback quality counters were unavailable or reset.":dropped?`${dropped} of ${total} frames dropped (${(100*dropped/total).toFixed(2)}%). Playback completion does not establish smooth playback.`:"",
    counter_scope:"Differences between counters immediately before and after this playback only."};
}
function stopReason(result) {
  if(result.measurements?.context_lost)return "WebGL context was lost";
  if(result.measurements?.timed_out)return "Test timed out";
  if(result.measurements?.cancelled)return "Cancelled by the operator";
  return result.status==="FAIL"?"Test failed":"";
}
async function runSequence(execute,onProgress=()=>{},cancelled=()=>false) {
  const entries=[];
  for(let index=0;index<SUITE_STEPS.length;index++) {
    const test=SUITE_STEPS[index];
    if(cancelled())return {state:"stopped",entries,reason:"Cancelled by the operator"};
    onProgress({test,index,state:"running",entries});
    try {
      const entry=await execute(test,{index,total:SUITE_STEPS.length,previous:[...entries]});
      if(!entry.saved)throw Error("Report save was not confirmed; inspect its start marker before retrying");
      entries.push(entry);
      const reason=stopReason(entry.result);
      onProgress({test,index,state:reason?"stopped":"saved",entry,entries});
      if(reason)return {state:"stopped",entries,reason};
    } catch(error) {
      onProgress({test,index,state:"stopped",entries,error:String(error.message||error)});
      return {state:"stopped",entries,reason:String(error.message||error)};
    }
  }
  return {state:"completed",entries,reason:""};
}
class TestTimeout extends Error {}
async function withLimit(action,milliseconds,externalSignal) {
  const controller=new AbortController(),started=performance.now();
  const abort=()=>controller.abort(externalSignal?.reason||Error("Cancelled by the operator"));
  if(externalSignal?.aborted)abort();
  externalSignal?.addEventListener("abort",abort,{once:true});
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(()=>{
        if(controller.signal.aborted)throw controller.signal.reason;
        return action(controller.signal);
      }).then(result=>{
        if(performance.now()-started>milliseconds)throw new TestTimeout("Test exceeded its time limit");
        return result;
      }),
      new Promise((_,reject)=>{timer=setTimeout(()=>{
        const error=new TestTimeout("Test exceeded its time limit");controller.abort(error);reject(error);
      },milliseconds);})
    ]);
  } finally {clearTimeout(timer);externalSignal?.removeEventListener("abort",abort);}
}
function clientIdentity() {
  const ua=navigator.userAgent;
  return {user_agent:ua,browser_family:/Firefox\//.test(ua)?"Firefox":/Chromium\/|Chrome\//.test(ua)?"Chromium family":"Other",
    platform:navigator.platform,measurement_scope:"The client submitting this report; remote clients do not measure the console GPU."};
}
function capabilities() {
  return base("capabilities","INCONCLUSIVE",{user_agent:navigator.userAgent,platform:navigator.platform,logical_processors:navigator.hardwareConcurrency||null,
    screen:{width:screen.width,height:screen.height,device_pixel_ratio:devicePixelRatio},
    apis:{webgl:!!window.WebGLRenderingContext,webgl2:!!window.WebGL2RenderingContext,web_audio:!!window.AudioContext,wasm:!!window.WebAssembly,gamepad:!!navigator.getGamepads},
    video:{webm:document.createElement("video").canPlayType('video/webm; codecs="vp8"'),h264:document.createElement("video").canPlayType('video/mp4; codecs="avc1.42E01E"')}});
}
function canvas2d() {
  const canvas=$("canvas");canvas.width=320;canvas.height=180;
  const context=canvas.getContext("2d");if(!context)return base("canvas2d","UNSUPPORTED",{});
  const colors=[[255,0,0],[0,255,0],[0,0,255],[255,255,255]],samples=[];let pass=true;
  colors.forEach((color,index)=>{
    context.fillStyle=`rgb(${color.join(",")})`;context.fillRect(index*80,0,80,180);
    const pixel=[...context.getImageData(index*80+40,90,1,1).data];samples.push(pixel);
    if(pixel.some((value,channel)=>value!==(channel===3?255:color[channel])))pass=false;
  });
  return base("canvas2d",pass?"PASS":"FAIL",{expected:colors,samples,oracle:"Exact RGBA values at four known interior points"});
}
function webgl() {
  const canvas=$("canvas");canvas.width=canvas.height=64;
  const gl=canvas.getContext("webgl",{antialias:false,preserveDrawingBuffer:true});
  if(!gl)return base("webgl","UNSUPPORTED",{reason:"No WebGL context"});
  let program,buffer,vs,fs,lost=false,renderer=rendererUnknown();
  const onLost=()=>{lost=true;};canvas.addEventListener("webglcontextlost",onLost);
  try {
    const extension=gl.getExtension("WEBGL_debug_renderer_info");
    renderer={vendor:String(gl.getParameter(extension?extension.UNMASKED_VENDOR_WEBGL:gl.VENDOR)),renderer:String(gl.getParameter(extension?extension.UNMASKED_RENDERER_WEBGL:gl.RENDERER)),classification:"unknown"};
    renderer.classification=classify(renderer.renderer);
    function shader(type,source) {
      const item=gl.createShader(type);gl.shaderSource(item,source);gl.compileShader(item);
      if(!gl.getShaderParameter(item,gl.COMPILE_STATUS)){gl.deleteShader(item);throw Error("Shader compilation failed");}return item;
    }
    vs=shader(gl.VERTEX_SHADER,"attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}");
    fs=shader(gl.FRAGMENT_SHADER,"precision mediump float;void main(){gl_FragColor=vec4(0.,1.,0.,1.);}");
    program=gl.createProgram();gl.attachShader(program,vs);gl.attachShader(program,fs);gl.bindAttribLocation(program,0,"p");gl.linkProgram(program);
    if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error("Shader link failed");
    gl.useProgram(program);buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,2,gl.FLOAT,false,0,0);gl.viewport(0,0,64,64);gl.clearColor(1,0,0,1);gl.clear(gl.COLOR_BUFFER_BIT);gl.drawArrays(gl.TRIANGLES,0,3);
    const pixels=new Uint8Array(64*64*4);gl.readPixels(0,0,64,64,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    const error=gl.getError();let mismatches=0;
    for(let index=0;index<pixels.length;index+=4)if(pixels[index]!==0||pixels[index+1]!==255||pixels[index+2]!==0||pixels[index+3]!==255)mismatches++;
    lost ||= gl.isContextLost();
    return base("webgl",!lost&&!mismatches&&error===gl.NO_ERROR?"PASS":"FAIL",{pixels:4096,mismatches,gl_error:error,context_lost:lost,hardware_path:renderer.classification==="hardware",oracle:"Full green triangle readback; red clear must be overwritten"},renderer);
  } catch(error) {return base("webgl","FAIL",{error:String(error.message||error),context_lost:lost||gl.isContextLost()},renderer);}
  finally {canvas.removeEventListener("webglcontextlost",onLost);if(buffer)gl.deleteBuffer(buffer);if(program)gl.deleteProgram(program);if(vs)gl.deleteShader(vs);if(fs)gl.deleteShader(fs);}
}
async function animation(signal) {
  const canvas=$("canvas");canvas.width=320;canvas.height=180;
  const context=canvas.getContext("2d");if(!context)return base("animation","UNSUPPORTED",{reason:"No 2D drawing context"});
  const intervals=[];let previous,start,frame,hidden=document.hidden;
  const visibility=()=>{hidden ||= document.hidden;};document.addEventListener("visibilitychange",visibility);
  try {
    await new Promise((resolve,reject)=>{
      const abort=()=>{cancelAnimationFrame(frame);reject(signal.reason||Error("Cancelled"));};
      if(signal.aborted)return abort();signal.addEventListener("abort",abort,{once:true});
      function draw(now) {
        if(start===undefined)start=now;if(previous!==undefined)intervals.push(now-previous);previous=now;hidden ||= document.hidden;
        context.fillStyle="#20271b";context.fillRect(0,0,320,180);context.fillStyle="#a3b56a";context.fillRect(130+Math.sin((now-start)/350)*110,65,45,45);
        if(now-start<10000)frame=requestAnimationFrame(draw);else{signal.removeEventListener("abort",abort);resolve();}
      }
      frame=requestAnimationFrame(draw);
    });
    const statistics=frameStatistics(intervals,{hidden});return base("animation",statistics.inconclusive?"INCONCLUSIVE":"PASS",statistics);
  } catch(error) {return base("animation","INCONCLUSIVE",{...frameStatistics(intervals,{hidden,timed_out:error instanceof TestTimeout}),cancelled:cancelRequested,error:String(error.message||error)});}
  finally {cancelAnimationFrame(frame);document.removeEventListener("visibilitychange",visibility);}
}
async function video(signal) {
  // Fresh element plus counter deltas excludes all prior playback counts.
  const old=$("video"),element=old.cloneNode(false);old.replaceWith(element);$("canvas").hidden=true;element.hidden=false;element.muted=true;
  if(!element.canPlayType('video/webm; codecs="vp8"'))return base("video","UNSUPPORTED",{reason:"VP8/WebM playback unavailable"});
  const before=element.getVideoPlaybackQuality?.();let hidden=document.hidden;
  const visibility=()=>{hidden ||= document.hidden;};document.addEventListener("visibilitychange",visibility);
  try {
    await new Promise((resolve,reject)=>{
      function finish(error){element.onended=element.onerror=null;signal.removeEventListener("abort",abort);error?reject(error):resolve();}
      const abort=()=>finish(signal.reason||Error("Cancelled"));if(signal.aborted)return abort();
      element.onended=()=>finish();element.onerror=()=>finish(Error("Video decoding/playback failed"));
      signal.addEventListener("abort",abort,{once:true});element.play().catch(finish);
    });
    return base("video",hidden?"INCONCLUSIVE":"PASS",{playback_completed:true,duration_seconds:Number.isFinite(element.duration)?element.duration:null,
      ...videoStatistics(before,element.getVideoPlaybackQuality?.()),hidden_during_test:hidden,muted:true,
      pass_scope:"Playback completed only; dropped-frame metrics describe smoothness separately.",decoder:"Browser selected; hardware decoding is not established"});
  } finally {element.pause();document.removeEventListener("visibilitychange",visibility);}
}
async function jsonRequest(path,payload,timeout=5000) {
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),Math.max(1,Math.min(5000,timeout)));
  try {
    const response=await fetch(path,{method:payload?"POST":"GET",signal:controller.signal,headers:payload?{"Content-Type":"application/json","X-PSX-CSRF":csrf}:{},...(payload?{body:JSON.stringify(payload)}:{})});
    const data=await response.json();if(!response.ok)throw Error(data.error||`Report service failed (${response.status})`);return data;
  } finally {clearTimeout(timer);}
}
function resultLabel(test,result) {
  if(result.status==="INCONCLUSIVE")return test==="capabilities"?"Recorded":"Review";
  return {PASS:"Passed",FAIL:"Failed",UNSUPPORTED:"Unavailable"}[result.status]||result.status;
}
function updateStep(test,state,result=null) {
  const row=$(`step-${test}`);row.dataset.state=state;
  $(`status-${test}`).textContent=result?resultLabel(test,result):{pending:"Ready",running:"Running…",stopped:"Stopped"}[state]||state;
  row.title=result?result.status:"";
}
function renderConfiguredRefresh() {
  const hz=displayedMode?.configured_refresh_hz;
  $("metric-refresh").textContent=displayedMode?.available===true&&Number.isFinite(hz)&&hz>0&&hz<=1000?`${Number(hz.toFixed(2))} Hz`:"Unavailable";
}
function renderMeasurements() {
  const measurements=test=>displayedResults.get(test)?.result.measurements||{};
  const pacing=measurements("animation"),playback=measurements("video"),canvas=measurements("canvas2d"),gl=measurements("webgl"),screen=measurements("capabilities").screen;
  const number=(value,digits=1,suffix="")=>Number.isFinite(value)?value.toFixed(digits)+suffix:"—";
  $("metric-fps").textContent=number(pacing.average_fps);
  $("metric-drops").textContent=number(Number.isFinite(playback.dropped_frame_ratio)?playback.dropped_frame_ratio*100:null,2);
  $("metric-video-frames").textContent=Number.isFinite(playback.total_frames)?`${playback.dropped_frames} of ${playback.total_frames} frames`:displayedResults.has("video")?"Counters unavailable":"Awaiting playback";
  $("metric-median").textContent=number(pacing.median_frame_ms,2," ms");
  $("metric-p95").textContent=number(pacing.p95_frame_ms,2," ms");
  $("metric-missed").textContent=number(pacing.missed_frames_estimate,0);
  $("metric-canvas").textContent=canvas.samples?`${canvas.samples.filter((pixel,index)=>canvas.expected?.[index]?.every((channel,i)=>pixel[i]===channel)&&pixel[3]===255).length} / ${canvas.samples.length} matched`:"—";
  $("metric-webgl").textContent=Number.isFinite(gl.pixels)&&Number.isFinite(gl.mismatches)?`${gl.pixels-gl.mismatches} / ${gl.pixels}`:"—";
  $("metric-screen").textContent=screen?`${screen.width} × ${screen.height}`:"—";
  renderConfiguredRefresh();
  const renderer=displayedResults.get("webgl")?.result.renderer;
  $("renderer").textContent=renderer?.renderer||"Awaiting WebGL test";$("renderer").title=renderer?.renderer||"";
  $("renderer-kind").textContent=renderer?{hardware:"Hardware reported",software:"Software reported",unknown:"Unclassified"}[renderer.classification]||"Unclassified":"—";
  const notes=[];
  if(displayedResults.has("animation"))notes.push(pacing.inconclusive?"Frame pacing needs review.":"Browser timing, not display refresh.");
  if(playback.dropped_frames>0)notes.push("Video frame drops observed.");
  else if(displayedResults.has("video"))notes.push("Video decoder type unverified.");
  $("measurement-note").textContent=notes.join(" ")||"Measurements appear as each test completes.";
  $("saved-count").textContent=`${displayedResults.size} / ${SUITE_STEPS.length} saved`;
  $("progress").value=displayedResults.size;
}
function preparePreview(test) {
  const old=$("canvas");old.replaceWith(old.cloneNode(false));$("canvas").hidden=false;$("video").hidden=true;
  $("canvas").hidden=test==="capabilities";$("preview-idle").hidden=test!=="capabilities";$("preview-label").textContent="Checking capabilities";
  updateStep(test,"running");
  $("title").textContent=LABELS[test];$("summary").textContent="Running…";
}
async function executeTest(test,suite=null) {
  preparePreview(test);const marker=await jsonRequest("/api/browser/start",{test_id:`browser.${test}`,...(autoNonce&&autoBatchId?{nonce:autoNonce,batch_id:autoBatchId}:{})});
  if(typeof marker.run_id!=="string")throw Error("The report service did not confirm a durable start marker");
  displayedMode=marker.display_mode||null;renderConfiguredRefresh();
  const started=performance.now();let result;activeController=new AbortController();
  if(cancelRequested)activeController.abort(Error("Cancelled by the operator"));
  try {result=await withLimit(signal=>({capabilities,canvas2d,webgl,animation,video}[test])(signal),TEST_LIMITS[test],activeController.signal);}
  catch(error){result=base(test,error instanceof TestTimeout||cancelRequested?"INCONCLUSIVE":"FAIL",{error:String(error.message||error),timed_out:error instanceof TestTimeout,cancelled:cancelRequested});}
  finally {activeController=null;}
  result.duration_ms=Math.round(performance.now()-started);result.run_id=marker.run_id;result.measurements.client=clientIdentity();
  if(suite)result.measurements.suite={id:"baseline",version:1,run_id:suite.id,launch_nonce:autoNonce,step:suite.index+1,total:SUITE_STEPS.length,order:[...SUITE_STEPS],
    previous_steps:suite.previous.map(entry=>({test_id:entry.result.test_id,status:entry.result.status,run_id:entry.run_id})),complete:suite.index===SUITE_STEPS.length-1&&!stopReason(result),stop_reason:stopReason(result)};
  // A save failure must never trigger a second, misleading FAIL submission.
  const saved=await jsonRequest("/api/browser/results",result);
  if(saved.run_id!==marker.run_id||saved.test_id!==result.test_id||saved.phase!=="completed")throw Error("Report acknowledgement incomplete; inspect saved reports before retrying");
  const entry={test,result,run_id:saved.run_id,display_mode:saved.display_mode||marker.display_mode||null,saved:true};
  displayedMode=entry.display_mode;
  displayedResults.set(test,entry);renderMeasurements();updateStep(test,stopReason(result)?"stopped":"saved",result);
  if(test==="capabilities")$("preview-label").textContent="Capabilities recorded";
  $("summary").textContent=`${resultLabel(test,result)} · saved${result.measurements.warning?" · review playback":""}`;
  $("output").textContent=JSON.stringify({measurements:result.measurements,renderer:result.renderer,configured_display_mode:entry.display_mode,run_id:entry.run_id},null,2);return entry;
}
function controls(disabled){document.querySelectorAll("button[data-test],#run-all").forEach(button=>{button.disabled=disabled;});$("cancel").disabled=!disabled;}
function progressRows(){SUITE_STEPS.forEach(test=>updateStep(test,"pending"));$("progress").value=0;}
async function run(test) {
  if(busy||!csrf)return;busy=true;cancelRequested=false;controls(true);$("output").textContent="";
  try {
    displayedMode=null;
    if(test==="all")displayedResults.clear();else displayedResults.delete(test);
    renderMeasurements();
    if(test!=="all"){await executeTest(test);return;}
    progressRows();const id=globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(16).slice(2)}`,deadline=performance.now()+90000;
    const summary=await runSequence((step,context)=>{if(performance.now()>=deadline)throw Error("Suite time limit reached; remaining tests were not started");return executeTest(step,{...context,id});},event=>{
      updateStep(event.test,event.state,event.entry?.result);$("progress").value=event.entries.length;
    },()=>cancelRequested);
    $("title").textContent=summary.state==="completed"?"Suite finished":"Suite stopped";
    $("summary").textContent=`${summary.entries.length} / ${SUITE_STEPS.length} saved${summary.reason?" · "+summary.reason:""}`;
    $("summary").title=$("summary").textContent;
    $("output").textContent=JSON.stringify({suite_id:id,state:summary.state,reason:summary.reason,client:clientIdentity(),reports:summary.entries.map(entry=>({test:entry.test,status:entry.result.status,run_id:entry.run_id,measurements:entry.result.measurements,configured_display_mode:entry.display_mode}))},null,2);
    return summary;
  } catch(error){if(test!=="all")updateStep(test,"stopped");$("summary").textContent="Stopped · save may be incomplete";$("output").textContent=String(error.message||error);return {state:"stopped",entries:[...displayedResults.values()],reason:String(error.message||error)};}
  finally {busy=false;activeController=null;controls(false);$("cancel").disabled=true;}
}
async function finishAutomaticSuite(grant) {
  autoNonce=grant.nonce;autoBatchId=grant.batch_id;
  let outcome;
  try {outcome=await run("all");}
  catch(error){outcome={entries:[...displayedResults.values()],reason:String(error.message||error)};}
  controls(true);$("cancel").disabled=true;$("summary").textContent="Saving batch…";
  try {
    // One completion attempt only. The controller owns closure and its watchdog.
    const finished=await jsonRequest("/api/browser/finish",{batch_id:grant.batch_id,nonce:grant.nonce,
      run_ids:(outcome?.entries||[]).map(entry=>entry.run_id),error:String(outcome?.reason||(outcome?"":"Suite did not produce a summary")).slice(0,512)});
    if(finished.saved!==true||typeof finished.closed!=="boolean")throw Error("Batch completion was not confirmed");
    $("summary").textContent=finished.closed?"Saved · returning to diagnostics":"Saved · close browser to return";
  } catch(error){$("summary").textContent="Finish unavailable · return to diagnostics";$("output").textContent+="\n"+String(error.message||error);}
  finally {autoNonce=null;autoBatchId=null;}
}
function initialize() {
  controls(true);$("cancel").disabled=true;progressRows();
  document.querySelectorAll("button[data-test]").forEach(button=>button.addEventListener("click",()=>run(button.dataset.test)));
  $("run-all").addEventListener("click",()=>run("all"));$("cancel").addEventListener("click",()=>{cancelRequested=true;activeController?.abort(Error("Cancelled by the operator"));});
  $("details-toggle").addEventListener("click",()=>{const show=$("output").hidden;$("output").hidden=!show;$("readings").hidden=show;$("details-toggle").setAttribute("aria-expanded",String(show));$("details-toggle").textContent=show?"Metrics":"Details";});
  (async()=>{try{let session=await jsonRequest("/api/session");if(typeof session.csrf_token!=="string")throw Error("Invalid report session");csrf=session.csrf_token;
    const state=await jsonRequest("/api/state");$("connection").textContent="Connected";$("browser").textContent=clientIdentity().browser_family;$("system").textContent=`${state.system.hostname} · ${state.system.kernel_release}`;
    const pendingDeadline=performance.now()+10000;
    while(session.auto_pending===true&&performance.now()<pendingDeadline){
      $("summary").textContent="Preparing suite…";
      await new Promise(resolve=>setTimeout(resolve,250));
      if(performance.now()>=pendingDeadline)break;
      session=await jsonRequest("/api/session",undefined,pendingDeadline-performance.now());
    }
    controls(false);$("cancel").disabled=true;
    const grant=session.auto_run;
    if(grant?.suite==="baseline"&&typeof grant.nonce==="string"&&/^[A-Za-z0-9_-]{32,128}$/.test(grant.nonce)
        &&typeof grant.batch_id==="string"&&/^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}$/.test(grant.batch_id)
        &&Number.isFinite(grant.expires_in_seconds)&&grant.expires_in_seconds>0&&grant.expires_in_seconds<=60){
      await finishAutomaticSuite(grant);
    }
  }catch(error){$("connection").textContent="Report service unavailable";$("system").textContent=String(error.message||error);}})();
}
if(typeof module!=="undefined"&&module.exports)module.exports={SUITE_STEPS,frameStatistics,videoStatistics,stopReason,runSequence,withLimit,TestTimeout,classify};
if(typeof document!=="undefined")initialize();
