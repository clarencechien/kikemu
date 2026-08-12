/* 字幕紀錄(PRD §2 決策):裝置端 IndexedDB,伺服器零內容留存。
   每筆 = 一場聽譯 session 的全部字幕(ja + zh)。模式沿 sukemu src/db.ts。 */

import type { Line } from './types';

export type SessionRecord = {
  id?: number;
  /** ISO 時間(session 結束時刻) */
  at: string;
  /** 來源語言(worker/langs.ts 的 code);舊紀錄沒有這欄,匯出時只寫「原文」 */
  lang?: string;
  pack: string | null;
  packName: string | null;
  /** 聽譯秒數(顯示用) */
  seconds: number;
  lines: Line[];
};

const DB_NAME = 'kikemu';
const STORE = 'sessions';

function open(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
    };
    req.onsuccess = () => res(req.result);
    req.onerror = () => rej(req.error);
  });
}

function tx<T>(dbMode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest): Promise<T> {
  return open().then(
    db =>
      new Promise<T>((res, rej) => {
        const r = fn(db.transaction(STORE, dbMode).objectStore(STORE));
        r.onsuccess = () => res(r.result as T);
        r.onerror = () => rej(r.error);
      }),
  );
}

export const sessionStore = {
  save: (rec: Omit<SessionRecord, 'id'>) => tx<number>('readwrite', s => s.add(rec)),
  async list() {
    const all = await tx<SessionRecord[]>('readonly', s => s.getAll());
    return all.sort((a, b) => b.at.localeCompare(a.at));
  },
  remove: (id: number) => tx<void>('readwrite', s => s.delete(id)),
};
