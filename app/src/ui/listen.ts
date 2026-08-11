/* 聽譯主流程:mic → AudioWorklet(16kHz PCM16 100ms 框)→ WS /ws?pack=…
   → SessionRelay DO → Speechmatics;下行 partial/final/zh 進字幕流。
   - 開始/停止是 toggle,不是 PTT(導覽是連續旁白)
   - getUserMedia 三關閉:exp3 實證瀏覽器降噪鏈是負資產
   - 未登入(看看介面預覽)一律走 demo.ts,這裡直接擋掉——雙層保險的前端層,
     伺服器端 /ws 另有 401 */

import { api } from '../api';
import { sessionStore } from '../db';
import type { Me, Pack, RelayMsg } from '../types';
import { addFinal, addNote, clearStream, collectLines, setPartial, setZh, setZhError, toast } from './cards';

const $ = (id: string) => document.getElementById(id)!;

type State = 'idle' | 'starting' | 'live' | 'stopping';

export type Listen = {
  onAuthed: (me: Me) => void;
  isBusy: () => boolean;
};

export function initListen(onPreviewStart: () => void): Listen {
  const toggleBtn = $('toggleBtn') as HTMLButtonElement;
  const latChip = $('latChip');
  const quotaChip = $('quotaChip');
  const packSel = $('packSel') as HTMLSelectElement;

  let state: State = 'idle';
  let ws: WebSocket | null = null;
  let audioCtx: AudioContext | null = null;
  let mediaStream: MediaStream | null = null;
  let smReady = false;
  let firstFrameAt = 0; // 第一框送出時刻:端到端延遲的原點
  let lats: number[] = [];
  let curPack: Pack | null = null;
  let guidanceShown = false; // 拾音指引一次就好
  let endFuse: ReturnType<typeof setTimeout> | undefined;

  // ── 管線觀測:四段(麥克風→連線→聽寫→翻譯)各自的最後活動時刻。
  // 沒有這些,「收不到音」與「翻不出來」在畫面上長得一模一樣。
  const statusBar = $('statusBar');
  const statText = $('statText');
  const meterFill = $('meterFill');
  const timeChip = $('timeChip');
  const VOICE_RMS = 220; // 16-bit RMS;低於此視為靜音(冷氣房底噪約 50~150)
  let startedAt = 0;
  let frames = 0; // 已送出的音框數:證明音訊真的在流
  let lastVoiceAt = 0; // 最後一次「有人在說話」的時刻
  let lastPartialAt = 0;
  let lastFinalAt = 0;
  let peakRms = 0; // 本場最大音量:全程為 0 = 麥克風根本沒訊號
  let tick: ReturnType<typeof setInterval> | undefined;

  const setStat = (kind: 'idle' | 'ok' | 'warn', text: string) => {
    statusBar.dataset.stat = kind;
    statText.textContent = text;
  };

  const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

  /** 每秒重算一次「現在到底卡在哪一段」。順序由前段往後段判斷,先斷的先講。 */
  function diagnose() {
    if (state !== 'live') return;
    const now = performance.now();
    timeChip.textContent = mmss((now - startedAt) / 1000);
    const sinceVoice = (now - lastVoiceAt) / 1000;
    const sincePartial = (now - lastPartialAt) / 1000;

    if (!frames) {
      setStat('warn', '麥克風沒有送出訊號——請檢查權限或改用其他瀏覽器');
    } else if (!peakRms) {
      setStat('warn', '收到的音量是零——麥克風可能被靜音或被其他 App 占用');
    } else if (!lastVoiceAt || sinceVoice > 6) {
      setStat('warn', '目前很安靜——靠近音源,或確認導覽員正在說話');
    } else if (!lastPartialAt || sincePartial > 8) {
      // 有聲音進去、引擎不回話:這是「聽不出來」而不是「收不到」
      setStat('warn', '有收到聲音,但引擎還沒認出字——可能太吵或不是日文');
    } else if (lastFinalAt && lastFinalAt > lastPartialAt) {
      setStat('ok', '聽寫中・翻譯中');
    } else {
      setStat('ok', '聽寫中');
    }
  }

  const setState = (s: State) => {
    state = s;
    document.body.dataset.listen = s;
    toggleBtn.textContent = s === 'idle' ? '● 開始聽' : s === 'starting' ? '連線中…' : s === 'stopping' ? '收尾中…' : '■ 停止';
    toggleBtn.disabled = s === 'starting' || s === 'stopping';
    packSel.disabled = s !== 'idle'; // 詞表中途不換,換場景包 = 重連(Speechmatics 限制)
  };

  const p50 = () => {
    if (!lats.length) return null;
    const a = [...lats].sort((x, y) => x - y);
    return a[Math.floor((a.length - 1) / 2)];
  };
  const showLat = () => {
    const m = p50();
    latChip.textContent = m == null ? '⚡–' : `⚡${m.toFixed(1)}s`;
  };

  const showQuota = (usedSeconds: number, limitSeconds: number) => {
    quotaChip.textContent =
      limitSeconds > 0 ? `剩 ${Math.max(0, Math.floor((limitSeconds - usedSeconds) / 60))} 分` : '無上限';
  };
  const refreshQuota = () => api.me().then(me => showQuota(me.usedSeconds, me.limitSeconds)).catch(() => {});

  async function loadPacks() {
    try {
      const { packs } = await api.packs();
      packSel.innerHTML = '<option value="">(不用場景包)</option>';
      for (const pk of packs) {
        const opt = document.createElement('option');
        opt.value = pk.id;
        opt.textContent = `${pk.name}(${pk.count} 詞)`;
        packSel.appendChild(opt);
      }
      packSel.onchange = () => {
        curPack = packs.find(pk => pk.id === packSel.value) ?? null;
      };
      // 預設選第一個包(場景包是核心資產,exp1:C+−C 全條件 +0.12~0.19)
      if (packs.length) {
        packSel.value = packs[0].id;
        curPack = packs[0];
      }
    } catch {
      /* 拿不到包清單就先不用包 */
    }
  }

  function cleanupAudio() {
    mediaStream?.getTracks().forEach(t => t.stop());
    mediaStream = null;
    audioCtx?.close().catch(() => {});
    audioCtx = null;
  }

  async function saveHistory(seconds: number) {
    const lines = collectLines();
    if (!lines.length) return;
    await sessionStore
      .save({
        at: new Date().toISOString(),
        pack: curPack?.id ?? null,
        packName: curPack?.name ?? null,
        seconds,
        lines,
      })
      .catch(() => {});
  }

  const DONE_REASONS: Record<string, string> = {
    'hard-cap': '單場已達 60 分鐘上限,自動停止',
    idle: '長時間沒有聲音,已自動停止',
    quota: '今日聽譯額度已用完,台灣時間早上 8 點重置',
    'sm-error': '語音引擎回報錯誤,本場已結束',
    'upstream-closed': '語音引擎連線中斷',
    'upstream-error': '語音引擎連線錯誤',
  };

  function onMsg(msg: RelayMsg) {
    switch (msg.type) {
      case 'ready':
        smReady = true;
        setState('live');
        setStat('ok', '已連上引擎・等待聲音');
        if (!guidanceShown) {
          guidanceShown = true; // exp4 結論的 UI 化
          toast('離音源越近越清楚——貼近導覽員或喇叭');
        }
        if (msg.packName) addNote('info', `場景包「${msg.packName}」已載入(${msg.vocabCount} 詞)`);
        return;
      case 'partial':
        lastPartialAt = performance.now();
        setPartial(msg.text);
        return;
      case 'final': {
        lastPartialAt = lastFinalAt = performance.now();
        addFinal(msg.seq, msg.text);
        // 端到端延遲:牆鐘經過 −(該句在音訊時間軸的位置)
        if (firstFrameAt && msg.t > 0) {
          const lat = (performance.now() - firstFrameAt) / 1000 - msg.t;
          if (lat > 0 && lat < 30) {
            lats.push(lat);
            showLat();
          }
        }
        return;
      }
      case 'zh':
        setZh(msg.forSeq, msg.text);
        return;
      case 'zhError':
        setZhError(msg.forSeq, seq => {
          if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'retryZh', seq }));
        });
        return;
      case 'error':
        addNote('error', msg.message);
        return;
      case 'done': {
        const note = DONE_REASONS[msg.reason];
        if (note) addNote(msg.reason === 'quota' ? 'error' : 'info', note);
        showQuota(msg.usedSeconds, msg.limitSeconds);
        void saveHistory(msg.seconds);
        finishLocal();
        return;
      }
    }
  }

  function finishLocal() {
    clearTimeout(endFuse);
    clearInterval(tick);
    meterFill.style.width = '0';
    // 收尾時把本場最關鍵的一句話留在狀態列:沒出字時要看得出是哪一段沒過
    if (startedAt) {
      const secs = Math.round((performance.now() - startedAt) / 1000);
      timeChip.textContent = mmss(secs);
      if (!frames) setStat('warn', '本場沒有送出任何音訊');
      else if (!peakRms) setStat('warn', '本場音量全程為零——麥克風沒有真的在收音');
      else if (!lastPartialAt) setStat('warn', `有收到音(${secs} 秒),但引擎一個字都沒認出來`);
      else setStat('idle', `已結束・${secs} 秒`);
    } else {
      setStat('idle', '尚未開始');
    }
    setPartial('');
    cleanupAudio();
    try {
      ws?.close();
    } catch {}
    ws = null;
    smReady = false;
    setState('idle');
    refreshQuota();
  }

  async function start() {
    // 預覽模式(未登入)不會走到這裡——toggle 的分流在 main.ts;這是保險
    if (document.body.dataset.auth !== 'in') return;
    setState('starting');
    clearStream();
    lats = [];
    showLat();
    firstFrameAt = 0;
    frames = peakRms = lastVoiceAt = lastPartialAt = lastFinalAt = 0;
    startedAt = performance.now();
    timeChip.textContent = '00:00';
    meterFill.style.width = '0';
    setStat('idle', '連線中…');
    clearInterval(tick);
    tick = setInterval(diagnose, 1000);

    try {
      // exp3 實證:瀏覽器降噪鏈(EC/NS/AGC)對兩引擎都是零到負——全關。
      // iOS constraint 支援不完整是已知風險(PRD §8),真機驗證前先如實送出。
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
    } catch {
      addNote('error', '拿不到麥克風權限——請允許麥克風,或檢查瀏覽器設定');
      setState('idle');
      return;
    }

    try {
      audioCtx = new AudioContext();
      await audioCtx.audioWorklet.addModule('/pcm-worklet.js');
      const src = audioCtx.createMediaStreamSource(mediaStream);
      const node = new AudioWorkletNode(audioCtx, 'pcm-downsampler');
      src.connect(node);
      node.port.onmessage = e => {
        // worklet 送兩種:{rms} 音量(給狀態列)與 ArrayBuffer 音框(給 SM)
        if (!(e.data instanceof ArrayBuffer)) {
          const rms = (e.data as { rms: number }).rms;
          if (rms > peakRms) peakRms = rms;
          if (rms >= VOICE_RMS) lastVoiceAt = performance.now();
          // 條長用對數:講話 (~1000) 到滿格,底噪不會撐滿
          const pct = Math.min(100, Math.max(0, (Math.log10(Math.max(rms, 1)) / Math.log10(6000)) * 100));
          meterFill.style.width = `${pct}%`;
          return;
        }
        // SM 收到 StartRecognition 確認前的框直接丟(暖機幾百毫秒,不影響內容)
        if (smReady && ws?.readyState === WebSocket.OPEN) {
          if (!firstFrameAt) firstFrameAt = performance.now();
          frames++;
          ws.send(e.data);
        }
      };
    } catch {
      addNote('error', '音訊初始化失敗——這台裝置可能不支援 AudioWorklet');
      cleanupAudio();
      setState('idle');
      return;
    }

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const packQ = curPack ? `?pack=${encodeURIComponent(curPack.id)}` : '';
    ws = new WebSocket(`${proto}://${location.host}/ws${packQ}`);
    ws.binaryType = 'arraybuffer';
    ws.onmessage = ev => {
      try {
        onMsg(JSON.parse(String(ev.data)) as RelayMsg);
      } catch {}
    };
    ws.onclose = () => {
      if (state === 'idle') return;
      // 沒收到 done 就斷:明講,不假裝成功
      if (state !== 'stopping') addNote('error', '連線中斷——可能是額度用盡或網路不穩,請再按一次開始');
      void saveHistory(0);
      finishLocal();
    };
    ws.onerror = () => {
      if (state === 'starting') {
        addNote('error', '連不上伺服器,請稍候再試');
      }
    };
  }

  function stop() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      finishLocal();
      return;
    }
    setState('stopping');
    cleanupAudio(); // 立刻停麥克風;WS 留著收尾(SM 吐完最後的定稿與譯文)
    ws.send(JSON.stringify({ type: 'end' }));
    // 保險絲:8 秒沒等到 done 就強制收
    endFuse = setTimeout(() => {
      addNote('info', '收尾逾時,已強制結束');
      void saveHistory(0);
      finishLocal();
    }, 8000);
  }

  toggleBtn.onclick = () => {
    // 未登入(看看介面)→ 重播 demo 字幕流,不碰引擎;伺服器端 /ws 另有 401
    if (document.body.dataset.auth !== 'in') return onPreviewStart();
    if (state === 'idle') void start();
    else if (state === 'live') stop();
  };

  // 離開頁面時收乾淨(iOS 背景回收是已知風險,PRD §8)
  addEventListener('pagehide', () => {
    if (state !== 'idle') stop();
  });

  setState('idle');

  return {
    onAuthed(me: Me) {
      showQuota(me.usedSeconds, me.limitSeconds);
      void loadPacks();
    },
    isBusy: () => state !== 'idle',
  };
}
