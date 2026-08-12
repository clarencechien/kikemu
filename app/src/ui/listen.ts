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
  const langSel = $('langSel') as HTMLSelectElement;

  let state: State = 'idle';
  let ws: WebSocket | null = null;
  let audioCtx: AudioContext | null = null;
  let mediaStream: MediaStream | null = null;
  let smReady = false;
  let firstFrameAt = 0; // 第一框送出時刻:端到端延遲的原點
  let lats: number[] = [];
  let curPack: Pack | null = null;
  let curLang = 'ja';
  let packLangs: { code: string; label: string }[] = [{ code: 'ja', label: '日文' }];
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
  let srvFrames = 0; // 伺服器回報的實收框數與音量(與本地對照用)
  let srvRms = 0;
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
      // 分辨「context 沒跑」與「跑了但沒聲音」——前者是瀏覽器政策,後者是麥克風
      const st = audioCtx?.state;
      setStat(
        'warn',
        st && st !== 'running'
          ? `音訊被瀏覽器暫停(${st})——請再按一次開始`
          : '麥克風沒有送出訊號——請檢查權限或改用其他瀏覽器',
      );
    } else if (!peakRms) {
      setStat('warn', '收到的音量是零——麥克風可能被靜音或被其他 App 占用');
    } else if (!lastVoiceAt || sinceVoice > 6) {
      setStat('warn', '目前很安靜——靠近音源,或確認導覽員正在說話');
    } else if (!lastPartialAt || sincePartial > 8) {
      // 有聲音進去、引擎不回話。先確認伺服器實收的到底是不是語音——
      // 客戶端有音量但伺服器沒有 = 傳輸壞掉,那跟「引擎聽不出來」是兩回事
      setStat(
        'warn',
        srvFrames && srvRms < 50
          ? `伺服器收到 ${srvFrames} 框但音量近乎零——音訊在傳輸中損壞`
          : `有收到聲音(伺服器 RMS ${srvRms}),但引擎還沒認出字——可能太吵、或講的不是${langSel.selectedOptions[0]?.textContent ?? '所選語言'}`,
      );
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
    // 語言與詞表都隨 StartRecognition 送出,中途不可換(Speechmatics 限制)= 聽譯中鎖住
    packSel.disabled = s !== 'idle' || !packLangs.some(l => l.code === curLang);
    langSel.disabled = s !== 'idle';
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

  /** 語言清單由 /api/config 給(加語言只改 worker/langs.ts);記住上次選擇 */
  async function loadLangs() {
    try {
      const cfg = await api.config();
      packLangs = cfg.packLangs?.length ? cfg.packLangs : packLangs;
      curLang = localStorage.getItem('kk_lang') || cfg.defaultLang;
      if (!cfg.langs.some(l => l.code === curLang)) curLang = cfg.defaultLang;
      langSel.innerHTML = '';
      for (const l of cfg.langs) {
        const o = document.createElement('option');
        o.value = l.code;
        o.textContent = l.label;
        langSel.appendChild(o);
      }
      langSel.value = curLang;
      langSel.onchange = () => {
        curLang = langSel.value;
        localStorage.setItem('kk_lang', curLang);
        void loadPacks(); // 換語言 = 換一組包
      };
      syncPackAvailability();
    } catch {
      /* 拿不到就留預設日文 */
    }
  }

  const packLangLabel = (c: string) => packLangs.find(l => l.code === c)?.label ?? c;
  /** 只有做了場景包的語言才顯示包選單——不要讓人以為詞表有生效 */
  function syncPackAvailability() {
    const ok = packLangs.some(l => l.code === curLang);
    packSel.disabled = !ok || state !== 'idle';
    packSel.title = ok ? '場景包(詞表)' : `場景包目前只支援 ${packLangs.map(l => l.label).join('、')}`;
    packSel.style.opacity = ok ? '' : '.5';
  }

  async function loadPacks() {
    curPack = null;
    try {
      // 只拿本場語言的包(把日文假名詞條掛到韓文 session 只會添亂,伺服器也會擋)
      const { packs } = await api.packs(curLang);
      packSel.innerHTML = '<option value="">(不用場景包)</option>';
      for (const pk of packs) {
        const opt = document.createElement('option');
        opt.value = pk.id;
        // 顯示「中文別名 - 語言」:原文名對台灣使用者不好認
        opt.textContent = `${pk.alias || pk.name}・${packLangLabel(pk.lang)}(${pk.count} 詞)`;
        packSel.appendChild(opt);
      }
      packSel.onchange = () => {
        curPack = packs.find(pk => pk.id === packSel.value) ?? null;
      };
      /* 預設不選包。場景包只在「講的就是這個地點」時有價值(exp1:+0.12~0.19),
         自動掛上清單第一個包等於替使用者猜地點——猜錯就是把一堆不相干的專名
         推給引擎。要用哪個包由使用者明確選。 */
      packSel.value = '';
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
        lang: curLang,
        pack: curPack?.id ?? null,
        packName: curPack ? curPack.alias || curPack.name : null, // 別名優先:匯出檔頭給人看的
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
      case 'stat':
        srvFrames = msg.frames;
        srvRms = msg.rms;
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

    // AudioContext 必須在**使用者手勢的同步階段**建立並 resume。
    // 放到 await getUserMedia 之後才建,手勢已經失效 → context 停在 suspended,
    // AudioWorklet 的 process() 從頭到尾不會被呼叫:沒有錯誤、沒有權限提示、
    // 音量恆為零。這是實際踩到的 bug,不是理論風險。
    let resumeP: Promise<void> | undefined;
    try {
      audioCtx = new AudioContext();
      resumeP = audioCtx.resume();
    } catch {
      addNote('error', '這台裝置不支援 Web Audio');
      setState('idle');
      return;
    }

    try {
      // exp3 實證:瀏覽器降噪鏈(EC/NS/AGC)對兩引擎都是零到負——全關。
      // iOS constraint 支援不完整是已知風險(PRD §8),真機驗證前先如實送出。
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
    } catch (e) {
      // 分辨原因:被拒 / 沒有裝置 / 被別的 App 占用——三種的處置完全不同
      const name = (e as DOMException)?.name ?? '';
      addNote(
        'error',
        name === 'NotAllowedError'
          ? '麥克風權限被拒——請在網址列的鎖頭圖示裡改成「允許」,再按一次開始'
          : name === 'NotFoundError'
            ? '找不到麥克風裝置'
            : name === 'NotReadableError'
              ? '麥克風被其他 App 占用,請關掉其他錄音/通話程式'
              : `拿不到麥克風(${name || '未知錯誤'})`,
      );
      cleanupAudio();
      setState('idle');
      return;
    }

    try {
      await resumeP;
      if (audioCtx.state !== 'running') await audioCtx.resume();
      if (audioCtx.state !== 'running') {
        addNote('error', `音訊被瀏覽器暫停(${audioCtx.state})——請再按一次開始`);
        cleanupAudio();
        setState('idle');
        return;
      }
      const track = mediaStream.getAudioTracks()[0];
      if (!track || track.readyState !== 'live') {
        addNote('error', '麥克風軌道沒有啟動');
        cleanupAudio();
        setState('idle');
        return;
      }
      // track.muted 是「來源端被靜音」——會完全沒聲音卻不報錯,一定要講出來
      if (track.muted) addNote('error', '麥克風目前是靜音狀態(系統層),請解除靜音');
      addNote('info', `使用麥克風:${track.label || '預設裝置'}・${audioCtx.sampleRate} Hz`);
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
    const q = new URLSearchParams({ lang: curLang });
    if (curPack) q.set('pack', curPack.id);
    ws = new WebSocket(`${proto}://${location.host}/ws?${q}`);
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
      void loadLangs().then(loadPacks);
    },
    isBusy: () => state !== 'idle',
  };
}
