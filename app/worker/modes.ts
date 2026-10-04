/* 聽譯模式(單一事實來源:/api/config 把清單送給前端,加模式只改這裡)。

   三個模式、一個選單,而不是「單人/多人」×「SM/Gemini」兩個獨立開關:
   gemini-3.5-transcribe-live 在即時串流下**不支援語者分離**(官方文件
   live-api/live-transcribe 的 Limitations,2026-10-04 讀),四種組合有一種不存在。

   - guide  :導覽主線。Speechmatics 單人,行為與 2026-10 之前完全相同(exp1 量的就是它)
   - dialog :Speechmatics + diarization: speaker。換人就斷句;字數上限改在軟斷點切,
              不切在字中間。即時語者分離的準確度**未量測**(侷限 26)
   - gemini :只換耳朵——gemini-3.5-transcribe-live 聽,譯仍走凍結 prompt 的逐句 hop
              (鐵律 3 不動)。數字見 handoff-v12;**只有 admin 看得到**(費用模型未驗證) */

export type ModeCode = 'guide' | 'dialog' | 'gemini';
export type Mode = { code: ModeCode; label: string; adminOnly: boolean; hint: string };

export const MODES: Mode[] = [
  { code: 'guide', label: '導覽', adminOnly: false, hint: '一位講者・Speechmatics(實測主線)' },
  { code: 'dialog', label: '對話', adminOnly: false, hint: '多人・換人就斷句(語者分離未量測)' },
  // R2(handoff-v12,先寫死)的判定:整體 0.591 < 0.741、無詞表 0.525 < 0.639 →「僅供對照,實用不建議」。
  // hint 只陳述逐條件數字:安靜/殘響時與 SM+詞表同級(N0 0.896 vs 0.836、N1 0.851 vs 0.836),
  // 人聲背景會崩(N2 0.343 vs 0.806、N3 0.045 vs 0.627)。定稿約每 9 秒一則(SM 0.6 秒)。
  { code: 'gemini', label: 'Gemini 對照', adminOnly: true, hint: '僅供對照:安靜時與 SM 同級,有人聲背景會崩・Gemini 3.5 Transcribe Live 聽、譯不變' },
];

export const DEFAULT_MODE: ModeCode = 'guide';

export const findMode = (code: string | null | undefined): Mode | undefined =>
  MODES.find(m => m.code === code);
