// 麥克風 → 16kHz PCM16 100ms 框(沿 manemu public/pcm-worklet.js)。
// 瀏覽器原生取樣率(44.1k/48k)→ 線性內插降到 16k,累積 1600 samples 送一框。
class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = [];
    this.ratio = sampleRate / 16000; // AudioWorklet 全域 sampleRate
    this.acc = 0;
  }
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    // 線性內插降取樣
    for (let i = 0; i < ch.length; i += this.ratio) {
      const i0 = Math.floor(i), i1 = Math.min(i0 + 1, ch.length - 1), frac = i - i0;
      const v = ch[i0] + (ch[i1] - ch[i0]) * frac;
      this.buf.push(Math.max(-32768, Math.min(32767, Math.round(v * 32767))));
    }
    while (this.buf.length >= 1600) { // 100ms @16k
      const frame = new Int16Array(this.buf.splice(0, 1600));
      // 音量先算(transfer 之後 buffer 就取不到了)。這是「到底有沒有收到音」
      // 唯一的可信來源——UI 靠它區分「沒收到音」與「收到了但引擎沒回話」。
      let sum = 0;
      for (let k = 0; k < frame.length; k++) sum += frame[k] * frame[k];
      const rms = Math.sqrt(sum / frame.length); // 0..32767
      this.port.postMessage({ rms });
      this.port.postMessage(frame.buffer, [frame.buffer]);
    }
    return true;
  }
}
registerProcessor("pcm-downsampler", PcmDownsampler);
