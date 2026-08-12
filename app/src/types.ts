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

/** 一句字幕:ja 定稿原文 + zh 譯文(null = 暫缺) */
export type Line = { ja: string; zh: string | null };

/** relay 下行訊息(worker/relay.ts 協定) */
export type RelayMsg =
  | { type: 'ready'; pack: string | null; packName: string | null; vocabCount: number }
  | { type: 'partial'; t: number; text: string }
  | { type: 'final'; seq: number; t: number; text: string }
  | { type: 'zh'; forSeq: number; text: string }
  | { type: 'zhError'; forSeq: number }
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
