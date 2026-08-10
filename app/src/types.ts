/* 共用型別:relay 下行協定與本機紀錄。 */

export type Pack = { id: string; name: string; count: number; updated: string | null };

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
  | {
      type: 'done';
      reason: string;
      seconds: number;
      usedSeconds: number;
      limitSeconds: number;
      charged: boolean;
    };
