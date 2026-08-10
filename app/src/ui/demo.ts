/* 登入前預覽(看看介面 →):播一段錄好的 demo 字幕流。
   素材是評測語料的真實輸出(exp1 sakai06:Speechmatics 定稿 + Gemini 譯文),
   來自靜態 /demo/demo.json——結構上碰不到引擎:這個模組只 import cards.ts 的
   DOM helpers,永遠不開 WS、不碰麥克風;伺服器端 /ws 另有 401(兩層保險)。 */

import { addFinal, addNote, clearStream, setPartial, setZh } from './cards';

type DemoLine = { ja: string; zh: string };

let cache: DemoLine[] | null = null;
let running = false;

const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms));
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

export async function playDemo() {
  if (running) return;
  running = true;
  try {
    cache ??= (await (await fetch('/demo/demo.json')).json()) as DemoLine[]; // 靜態檔,非 API
    clearStream();
    addNote('info', '介面預覽:這是評測語料的真實聽譯輸出(堺・宿院),非即時');
    let seq = 0;
    for (const line of cache) {
      seq++;
      // partial 逐段浮現(ghost 灰字),模擬 0.4~0.8 秒暫定字節奏
      if (!reduced()) {
        for (let i = 2; i < line.ja.length; i += 2) {
          setPartial(line.ja.slice(0, i));
          await sleep(140);
        }
      }
      setPartial('');
      addFinal(seq, line.ja);
      await sleep(reduced() ? 200 : 700); // 定稿 → 譯文浮現的節奏(~2.3 秒定稿感)
      setZh(seq, line.zh);
      await sleep(reduced() ? 200 : 500);
    }
    addNote('info', '預覽結束——登入後就能即時聽譯現場導覽');
  } catch {
    addNote('error', 'demo 載入失敗,請重新整理再試');
  } finally {
    running = false;
  }
}
