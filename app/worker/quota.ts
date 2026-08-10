/* 每人一個 Durable Object 計今日「聽譯秒數」(沿 sukemu worker/quota.ts,單位改秒)。
   重置:UTC 00:00 = 台灣早上 08:00(PRD §7)。
   額度上限由 Worker 依分級傳入,DO 只計數;計費時機在 SessionRelay
   (SM 連線中才計、SM Error 不計,見 relay.ts finish)。 */

export type Usage = { day: string; seconds: number };

export class QuotaCounter {
  constructor(private state: DurableObjectState) {}

  private async today(): Promise<Usage> {
    const day = new Date().toISOString().slice(0, 10);
    const rec = await this.state.storage.get<Usage>('usage');
    return rec?.day === day ? rec : { day, seconds: 0 };
  }

  async fetch(req: Request): Promise<Response> {
    const url = new URL(req.url);
    const cur = await this.today();
    if (url.pathname === '/usage') return Response.json(cur);
    if (url.pathname === '/add' && req.method === 'POST') {
      const { seconds = 0 } = (await req.json()) as Partial<Usage>;
      const next: Usage = { day: cur.day, seconds: Math.round(cur.seconds + Math.max(0, seconds)) };
      await this.state.storage.put('usage', next);
      return Response.json(next);
    }
    return new Response('not found', { status: 404 });
  }
}
