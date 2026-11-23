"""Paid local Chrome test with synthetic microphone input. Run against an isolated test database."""
import argparse
import asyncio,json,urllib.request
import websockets
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--chrome-port', type=int, required=True)
parser.add_argument('--database', required=True, help='SQLite path of the isolated local test app')
args = parser.parse_args() if __name__ == "__main__" else None
origin = f'http://127.0.0.1:{args.port}' if args else None

async def main():
 tabs=json.load(urllib.request.urlopen(f'http://127.0.0.1:{args.chrome_port}/json/list'))
 tab=next(t for t in tabs if t['url'].startswith(origin + '/demo/call.html'))
 async with websockets.connect(tab['webSocketDebuggerUrl']) as ws:
  n=0
  async def js(expr):
   nonlocal n
   n+=1
   await ws.send(json.dumps({'id':n,'method':'Runtime.evaluate','params':{'expression':expr,'returnByValue':True,'awaitPromise':True}}))
   while True:
    v=json.loads(await ws.recv())
    if v.get('id')==n:break
   r=v['result']
   if 'exceptionDetails' in r: raise RuntimeError(str(r['exceptionDetails'])[:650])
   return r['result'].get('value')
  await js('location.reload()');await asyncio.sleep(2)
  print(await js("""(async()=>{
    window.qaAudio=new AudioContext();await qaAudio.resume();
    window.qaMic=qaAudio.createMediaStreamDestination();
    navigator.mediaDevices.getUserMedia=async()=>qaMic.stream.clone();
    window.qaSay=b=>{const s=qaAudio.createBufferSource();s.buffer=b;s.connect(qaMic);s.start()};
    window.qaBuffers=[];
    for(const text of ['Нас двое. Хотим в августе в Сикачи-Алян. Бюджет пятьдесят тысяч рублей на человека. Что входит в экскурсию?', 'Поправка. Пятьдесят тысяч рублей на двоих, это общий бюджет.']){
      const r=await fetch('/api/tts',{method:'POST',headers:apiHeaders(),body:JSON.stringify({text})});
      if(!r.ok)throw Error('Fixture TTS '+r.status);
      qaBuffers.push(await qaAudio.decodeAudioData(await r.arrayBuffer()));
    }
    startCall(); return 'started';
  })()"""),flush=True)
  async def wait(expr):
   for _ in range(450):
    if await js(expr):return
    await asyncio.sleep(.15)
   raise RuntimeError('timeout '+expr+' '+str(await js('[!!call, call?.stt, call?.ended, widget.dataset.phase, window.voiceAgentLastLatencyMs]')))
  await wait('call?.mic && serverListener && !call.bargeActive')
  await js('qaSay(qaBuffers[0])')
  await wait('call?.source && window.voiceAgentLastLatencyMs>0 && call.bargeActive')
  print('first_voice_latency_ms',await js('window.voiceAgentLastLatencyMs'),flush=True)
  await js('window.qaOldSource=call.source;qaSay(qaBuffers[1]);')
  await wait('call?.interrupted && call.source !== qaOldSource')
  print('barge_in_stopped_before_correction_finished',True,flush=True)
  await wait("call && !call.busy && widget.dataset.phase === 'listening' && window.voiceAgentLastLatencyMs > 0")
  print('second_latency_ms',await js('window.voiceAgentLastLatencyMs'),'session',await js('widgetSessionId'),flush=True)
  print((await js('document.body.innerText'))[-1200:],flush=True)
  import httpx,sqlite3
  sid=await js('widgetSessionId')
  db=sqlite3.connect(args.database)
  for msg in ['Готов оформить поездку. Подтверждаю все исправленные параметры и контакт +79005550100. Сохраните заявку менеджеру.', 'Да, всё верно, заявку подтверждаю.']:
   r=httpx.post(origin + '/api/hermes/dialog', headers={'X-API-Key':'test-local-key'},json={'session_id':sid,'caller_phone':'','user_message':msg},timeout=45)
   r.raise_for_status()
   card=db.execute('select budget,urgency,comment from handoff_cards where call_id=?',(sid,)).fetchone()
   if card:break
  print('saved_card',card,flush=True)
  assert card and '50 000' in card[0] and 'группу' in card[0]
  await js('finishCall(call, "Тест завершён")')
if __name__ == "__main__":
 asyncio.run(main())
