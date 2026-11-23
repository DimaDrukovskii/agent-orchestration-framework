class LiveMicrophone extends AudioWorkletProcessor {
  constructor() { super(); this.frame = new Int16Array(1024); this.offset = 0; this.phase = 0; this.sum = 0; this.count = 0; }
  process(inputs) {
    for (const sample of inputs[0]?.[0] || []) {
      this.sum += sample;
      this.count++;
      this.phase += 24000;
      if (this.phase < sampleRate) continue;
      this.phase -= sampleRate;
      this.frame[this.offset++] = Math.max(-1, Math.min(1, this.sum / this.count)) * 32767;
      this.sum = this.count = 0;
      if (this.offset === this.frame.length) {
        this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
        this.frame = new Int16Array(1024); this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('live-microphone', LiveMicrophone);
