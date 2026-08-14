/* 每人一個 Durable Object 計今日用量(沿 sukemu worker/quota.ts,單位改秒)。
   重置:UTC 00:00 = 台灣早上 08:00(PRD §7)。
   額度上限由 Worker 依分級傳入,DO 只計數;計費時機在 SessionRelay
   (SM 連線中才計、SM Error 不計,見 relay.ts finish)。

   **兩種計量單位**:
   - seconds:對使用者的計量(牆鐘秒),決定分級額度
   - tokens:對 Gemini 的計量(含 thoughts),決定花費
   兩者刻意錯開:秒數擋不住 token 花費(manemu 的反面教訓——配額全用秒計,
   token 計價的呼叫等於沒有保險絲)。翻譯 hop 是按句付費的,一場很吵、
   句子被切得很碎的導覽,秒數沒超標但呼叫數會暴增。 */

export type Usage = { day: string; seconds: number; tokens: number; calls: number };

export class QuotaCounter {
  constructor(private state: DurableObjectState) {}

  private async today(): Promise<Usage> {
    const day = new Date().toISOString().slice(0, 10);
    const rec = await this.state.storage.get<Usage>('usage');
    // 舊紀錄沒有 tokens/calls 欄位,補 0(不要讓 undefined 汙染累加)
    if (rec?.day !== day) return { day, seconds: 0, tokens: 0, calls: 0 };
    return { day: rec.day, seconds: rec.seconds ?? 0, tokens: rec.tokens ?? 0, calls: rec.calls ?? 0 };
  }

  async fetch(req: Request): Promise<Response> {
    const url = new URL(req.url);
    const cur = await this.today();
    if (url.pathname === '/usage') return Response.json(cur);
    if (url.pathname === '/add' && req.method === 'POST') {
      const { seconds = 0, tokens = 0, calls = 0 } = (await req.json()) as Partial<Usage>;
      const next: Usage = {
        day: cur.day,
        seconds: Math.round(cur.seconds + Math.max(0, seconds)),
        tokens: cur.tokens + Math.max(0, tokens),
        calls: cur.calls + Math.max(0, calls),
      };
      await this.state.storage.put('usage', next);
      return Response.json(next);
    }
    return new Response('not found', { status: 404 });
  }
}
