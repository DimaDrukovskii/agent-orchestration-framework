// Both browsers connect to the same authenticated server. PCM16 mono at 24 kHz.
function connectLiveCall(role, key, room, stream, status, ended) {
  const audio = new AudioContext();
  const socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/live/connect');
  socket.binaryType = 'arraybuffer';
  let closed = false, capture, next = 0;
  const finish = () => {
    if (closed) return;
    closed = true;
    socket.close();
    capture?.disconnect();
    audio.close().catch(()=>{});
    ended();
  };
  audio.resume().catch(finish);
  socket.onopen = () => socket.send(JSON.stringify({role, key, room}));
  socket.onmessage = async event => {
    try {
      if (typeof event.data === 'string') {
        const message = JSON.parse(event.data);
        if (message.type === 'waiting') status('Ожидаем подключения менеджера…');
        if (message.type !== 'joined') return;
        await audio.audioWorklet.addModule('/demo/live-mic.js');
        if (closed) return;
        capture = new AudioWorkletNode(audio, 'live-microphone');
        audio.createMediaStreamSource(stream).connect(capture);
        capture.connect(audio.destination); // Worklet produces silent output.
        capture.port.onmessage = ({data}) => {
          if (socket.readyState === WebSocket.OPEN && socket.bufferedAmount < 48000) socket.send(data);
        };
        status('Менеджер на линии');
        return;
      }
      const samples = new Int16Array(event.data);
      const buffer = audio.createBuffer(1, samples.length, 24000);
      const channel = buffer.getChannelData(0);
      for (let i=0;i<samples.length;i++) channel[i] = samples[i]/32768;
      if (next > audio.currentTime + 0.3) return;
      const node = audio.createBufferSource();
      node.buffer = buffer; node.connect(audio.destination);
      next = Math.max(next, audio.currentTime + 0.02);
      node.start(next); next += buffer.duration;
    } catch { finish(); }
  };
  socket.onclose = socket.onerror = finish;
  return finish;
}
