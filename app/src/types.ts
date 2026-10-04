/* 共用型別:relay 下行協定與本機紀錄。 */

export type Pack = {
  id: string;
  name: string;
  /** 中文別名(使用者介面顯示用;原文名對台灣使用者不好認) */
  alias?: string;
  /** 來源語言(ja / ko) */
  lang: string;
  count: number;
  updated: string | null;
};

export type Me = { email: string; tier: string; isAdmin: boolean; usedSeconds: number; limitSeconds: number };

/** 聽譯模式(worker/modes.ts 給的清單) */
export type Mode = { code: string; label: string; adminOnly: boolean; hint: string };

/** 一句字幕:ja 定稿原文 + zh 譯文(null = 暫缺) */
/** speaker:對話模式的語者(A/B…);其他模式沒有 */
export type Line = { ja: string; zh: string | null; speaker?: string };

/** relay 下行訊息(worker/relay.ts 協定) */
export type RelayMsg =
  | { type: 'ready'; pack: string | null; packName: string | null; vocabCount: number; mode?: string }
  | { type: 'partial'; t: number; text: string }
  /** speaker:對話模式才有(SM 的 S1/S2…);前端顯示成 A/B… */
  | { type: 'final'; seq: number; t: number; text: string; speaker?: string }
  | { type: 'zh'; forSeq: number; text: string }
  /** reason:伺服器端失敗原因的前段(例如 `gemini 400: User location is not supported`),
   *  讓現場直接在手機上看得到是哪一種失敗,不用開 dashboard */
  | { type: 'zhError'; forSeq: number; reason?: string }
  | { type: 'error'; message: string }
  /** 伺服器實收音訊統計:與客戶端音量條對照,可分辨「麥克風沒收到」與「傳輸弄壞了」 */
  | { type: 'stat'; frames: number; rms: number }
  | {
      type: 'done';
      reason: string;
      seconds: number;
      usedSeconds: number;
      limitSeconds: number;
      charged: boolean;
    };
