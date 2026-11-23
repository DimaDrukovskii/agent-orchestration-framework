const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../demo/call.html'),'utf8');
function extract(start,end){return html.slice(html.indexOf(start),html.indexOf(end,html.indexOf(start)));}
const micCode=extract('async function ensureMic(', 'async function transcribe(');
const recordCode=extract('function listenServer(', '// --- распознавание речи');
const openingCode=extract('async function startCall(', 'function teardownCall(');
function base(){
 let now=1000, id=0; const timers=new Map(),pending=[],recorders=[];
 const ctx={console,Float32Array,Blob,AbortController,performance:{now:()=>now},
  setInterval:fn=>{timers.set(++id,fn);return id},clearInterval:id=>timers.delete(id),
  isActive:c=>!c.ended,setPhase:()=>{},showCaption:()=>{},showNotice:()=>{},stopPlayback:()=>{},
  fallbackToBrowserStt:()=>{throw Error('Unexpected fallback')},
  handleUserTurn:()=>{},transcribe:async()=>'',serverListener:null,
  VAD_MIN_LEVEL:0.012,VAD_FACTOR:2.5,VAD_POLL_MS:30,VAD_IDLE_RESTART_MS:15000,
  VAD_END_SILENCE_MS:700,VAD_MIN_SPEECH_MS:300,VAD_MAX_UTTERANCE_MS:20000,STT_MIN_CHARS:2,
  MediaRecorder:class{constructor(){this.mimeType='audio/webm';recorders.push(this)}start(){this.started=true}stop(){this.ondataavailable({data:new Blob(['first-syllable'])});pending.push(Promise.resolve(this.onstop?.()))}},
 };
 vm.createContext(ctx);
 return {ctx,recorders,timers,pending,tick:t=>{now=t;for(const fn of [...timers.values()])fn()}};
}
test('microphone detects speech in the first polling frame and does not gate its audio',async()=>{
 const h=base(),connections=[];let stopped=false;
 const node=name=>({name,connect:other=>connections.push([name,other.name])});
 const analyser={...node('analyser'),fftSize:1024,getFloatTimeDomainData:b=>b.fill(0.05)};
 const audio={createMediaStreamSource:()=>node('source'),createBiquadFilter:()=>({...node('highpass'),frequency:{value:0}}),createAnalyser:()=>analyser,createMediaStreamDestination:()=>({...node('recording'),stream:{}}),createGain:()=>{throw Error('Audio gate must not clip the opening')}};
 h.ctx.navigator={mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop:()=>{stopped=true}}]})}};
 vm.runInContext(micCode,h.ctx);
 const call={audio}; const mic=await h.ctx.ensureMic(call);
 h.tick(1030);assert.equal(mic.lastLoud,1030);assert.ok(connections.some(([a,b])=>a==='highpass'&&b==='recording'));
 mic.destroy();assert.equal(stopped,true);assert.equal(h.timers.size,0);
});
test('server barge-in stops playback, keeps recording, and submits the beginning of the phrase',async()=>{
 const h=base(),heard=[],stops=[];
 h.ctx.stopPlayback=()=>stops.push(true);
 h.ctx.transcribe=async(call,blob)=>{assert.equal(await blob.text(),'first-syllable');return 'Меня интересует Камчатка'};
 h.ctx.handleUserTurn=(call,text)=>heard.push(text);
 vm.runInContext(recordCode,h.ctx);
 const call={mic:{stream:{},lastLoud:0},busy:true};
 h.ctx.listenServer(call,{bargeText:'Меня зовут Алина. Я менеджер по путешествиям на Дальний Восток.'});
 assert.equal(h.recorders[0].started,true);
 for(let t=1030;t<=1480;t+=30){call.mic.lastLoud=t;h.tick(t)}
 assert.equal(stops.length,1);assert.equal(call.interrupted,true);
 h.tick(2200);await Promise.all(h.pending);
 assert.deepEqual(heard,['Меня интересует Камчатка']);
});
function opening(userSpeaks){
 const h=base(),events=[];
 Object.assign(h.ctx,{call:null,widgetSessionId:'test',document:{getElementById:()=>({})},widget:{dataset:{}},modelSelect:{},timerEl:{},
 checkEnvironment:()=>true,AudioContextImpl:class{resume(){}createAnalyser(){return {}}},
 showNotice:()=>{},setMuted:()=>{},checkServerVoice:async()=>true,checkServerStt:async()=>true,
 checkModels:async()=>{},modelConfigured:true,modelProvider:'polza',window:{MediaRecorder:true},navigator:{mediaDevices:{}},
 ensureMic:async c=>{c.mic={lastLoud:0};return c.mic},startTimer:()=>{},
 listen:()=>events.push('listen'),sleep:async ms=>{events.push('wait:'+ms);if(userSpeaks&&ms===1000)h.ctx.call.mic.lastLoud=1500},
 speak:async()=>events.push('intro'),teardownRecognition:()=>events.push('stop-listening'),
 agentName:'Алина',GREETING:'Меня зовут Алина.',OPENING_LISTEN_MS:1000,LISTEN_RESUME_DELAY_MS:100,failCall:()=>{throw Error('Failed')},
 });
 vm.runInContext(openingCode,h.ctx);return {h,events};
}
test('opening listens before speaking, then gives a short intro if the caller stays silent',async()=>{
 const {h,events}=opening(false);await h.ctx.startCall();
 assert.deepEqual(events,['listen','wait:1000','stop-listening','intro','wait:100','listen']);
});
test('opening skips the intro if the caller has already started speaking',async()=>{
 const {h,events}=opening(true);await h.ctx.startCall();
 assert.deepEqual(events,['listen','wait:1000']);
});

test('502 retries the original recording once and returns its transcript',async()=>{
 const h=base(), sent=[];
 Object.assign(h.ctx,{STT_URL:'/api/stt',keyInput:{value:'test'},widgetSessionId:'call',sleep:async()=>{},
 fetch:async(url,opts)=>{sent.push(opts.body);return sent.length===1?{ok:false,status:502}:{ok:true,json:async()=>({text:'на двоих'})}}});
 vm.runInContext(extract('async function transcribe(', 'function listenServer('),h.ctx);
 const recording=new Blob(['speech']);
 assert.equal(await h.ctx.transcribe({abort:new AbortController()},recording),'на двоих');
 assert.deepEqual(sent,[recording,recording]);
});
