/* Gemini generateContent(REST;CF Workers 無 Node SDK)。
   兩個用途:
   1. translateSentence — 聽譯 hop:定稿句 → 台灣正體(relay.ts 逐句呼叫)。
      systemInstruction 與 user template 一字不改自 scripts/prompts.py
      (exp1 凍結口譯 prompt:台灣用語 0 失誤、adequacy 4.71,不准改)。
   2. extractVocab — 場景包生成:來源文字 → 詞條 + 假名讀音(JSON mode),
      即 scripts/make_dict.py 的產品化(admin.ts pack-generate 呼叫)。 */

import type { Env } from './index';
import type { VocabEntry } from './vocab';
import { resolveLang } from './langs';

/** 凍結口譯 systemInstruction(scripts/prompts.py INTERPRETER_SYSTEM,逐字複製) */
export const INTERPRETER_SYSTEM =
  'あなたは観光ガイド音声の同時通訳者です。聞こえてくる日本語の解説を、' +
  '台湾で使われる繁體中文(台灣正體)に翻訳して出力してください。' +
  '専有名詞(神名、人名、地名、神社名、施設名)は漢字表記をそのまま使い、' +
  'カタカナの固有名詞は台湾で一般的な訳語、なければカタカナのままにしてください。' +
  '用語は台湾の習慣に従うこと(例:資訊、品質、影片、軟體、網路;' +
  '信息、质量、视频、软件、网络は使わない)。' +
  '訳文のみを出力し、説明・注釈・ふりがなは加えないこと。';

/** scripts/prompts.py TRANSLATE_USER_TEMPLATE(逐字複製,{transcript} 置換) */
const TRANSLATE_USER_TEMPLATE =
  '以下は音声認識による日本語の書き起こしです。上記の方針で台灣正體中文に翻訳してください。\n\n{transcript}';

/* 多語言:只把來源語名稱換掉,其餘一字不動。
   lang='ja' 時回傳的字串與 exp1 量測用的**完全相同**(下方 assert 式的寫法保證這件事),
   所以既有的 adequacy 4.71 / 台灣用語 0 失誤仍然適用;其他語言沿用同一套
   語域與在地化規則,但屬於未量測範圍。 */
const interpreterSystem = (lang: string) => {
  const { srcName } = resolveLang(lang);
  return srcName === '日本語'
    ? INTERPRETER_SYSTEM
    : INTERPRETER_SYSTEM.replace('聞こえてくる日本語の解説', `聞こえてくる${srcName}の解説`);
};
/** 中英夾雜:來源已是中文(SM 輸出簡體),要的是繁體化+台灣在地化+英文術語保留,
    不是「翻譯」。措辭沿用 exp2 translate_x.py 實測版本。 */
const CODEMIX_USER =
  '以下是語音辨識的書き起こし(中文夾雜英文術語)。請整理成通順的台灣正體中文,' +
  '英文術語維持英文原文不要翻譯。只輸出整理後的文字。\n\n{transcript}';

const translateUser = (lang: string, sentence: string) => {
  const { srcName } = resolveLang(lang);
  const tpl =
    lang === 'cmn_en'
      ? CODEMIX_USER
      : srcName === '日本語'
        ? TRANSLATE_USER_TEMPLATE
        : TRANSLATE_USER_TEMPLATE.replace('音声認識による日本語の', `音声認識による${srcName}の`);
  return tpl.replace('{transcript}', sentence);
};

const model = (env: Env) => env.TRANSLATE_MODEL || 'gemini-3.5-flash';

async function generate(env: Env, body: unknown): Promise<any> {
  const r = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${model(env)}:generateContent`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-goog-api-key': env.GEMINI_API_KEY },
      body: JSON.stringify(body),
    },
  );
  if (!r.ok) throw new Error(`gemini ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

const firstText = (resp: any): string | null =>
  resp?.candidates?.[0]?.content?.parts?.map((p: any) => p.text ?? '').join('').trim() || null;

/** 定稿句 → 台灣正體譯文(exp1 同款呼叫:temperature 0.2) */
export async function translateSentence(env: Env, sentence: string, lang = 'ja'): Promise<string> {
  const resp = await generate(env, {
    systemInstruction: { parts: [{ text: interpreterSystem(lang) }] },
    contents: [{ parts: [{ text: translateUser(lang, sentence) }] }],
    generationConfig: { temperature: 0.2 },
  });
  const zh = firstText(resp);
  if (!zh) throw new Error('gemini 沒有回譯文');
  return zh;
}

/** 場景包詞條抽取 prompt(scripts/make_dict.py 的產品化;來源不限維基百科) */
const VOCAB_PROMPT = `あなたは音声認識用のカスタム語彙(custom dictionary)を作るアシスタントです。
以下は、ある観光ルートの訪問先に関する紹介文です。
観光ガイドの音声認識で誤認識されやすい固有名詞・専門語を抽出し、
音声認識エンジンに登録する語彙リストを作ってください。

対象: 神名、人名、神社・施設名、神事・祭事名、地名、駅名、社名、時代・年号、茶道等の専門用語。
各項目:
- "content": 正式表記(記事中の表記)
- "sounds_like": 読みの配列(全角ひらがな。読みが複数あれば複数)

必ず含めるもの:
- 紹介文の主題そのもの(神社名・公園名・駅名・人名など)
- 文中に登場する境内施設名(〜殿、〜宮など)、神名、神事・祭事名、関連人物名

一般語は含めない。最大150項目。JSONの配列のみ出力:
[{"content": "...", "sounds_like": ["..."]}]

## 紹介文
`;

/** 來源文字 → 原始詞條(格式驗證在 vocab.ts validateEntries) */
/* 關鍵字產包(pass A):用 Google 搜尋接地蒐集該地點的固有名詞與讀音。
   為什麼要接地:exp1 §2.4 記錄了「只用維基百科」的天花板——對正解專名的字面
   覆蓋只有 1/6 ~ 6/7,在地小眾詞(いとや百貨店、旧池田電報電話局)維基沒有。
   搜尋能碰到官方頁與在地資料,正是報告裡說「產品端可用官方頁面補充」那條路。

   實測行為:模型自己決定要不要查——冷門題目觸發 2 次搜尋回 5~7 筆來源;
   把主題釘死在最前面的這版 prompt,連大阪城這種它本來就熟的題目也會查
   (正式站那包:10 次搜尋、10 筆來源,含官方頁與文化廳)。早期版本回過 0 筆,
   所以來源筆數仍要回報給管理者看——0 筆代表沒有外部佐證,讀音錯了會反而傷辨識。

   搜尋工具不與 responseMimeType=application/json 併用(分兩趟比較穩,
   也沿用 sukemu P1/P2 的分趟省錢模式:pass B 只吃 pass A 的文字)。 */
/* 韓文版:用韓語下指令,讀音要諺文(Speechmatics ko 包已探針驗證接受)。
   兩個實測教訓照搬:主題本身要釘死在最前面、漢字與諺文表記各列一條。 */
const RESEARCH_KO = (keyword: string) =>
  `「${keyword}」에 대해 한국어 공식 홈페이지·관광 안내·백과사전을 검색하세요.\n` +
  `이 장소/주제의 오디오 가이드에서 실제로 읽히는 고유명사를 최대한 폭넓게 모으세요.\n\n` +
  `【최우선】먼저 「${keyword}」 자체의 정식 명칭을 맨 앞에 반드시 넣으세요.\n` +
  `【중요】한자 표기와 한글 표기가 모두 쓰이는 이름은 두 가지를 각각 별도 항목으로 넣으세요.\n\n` +
  `대상: 건물·시설명, 경내 각처 이름, 신·불 이름, 인명, 제례·행사명, 지명, 역명,\n` +
  `연호·시대명, 전문 용어, 주변 상점·거리 이름.\n\n` +
  `각 항목을 「정식표기(한글 읽기)」 형식으로 나열하세요. 읽기를 모르면 추측하지 말고 생략하세요.\n` +
  `설명은 불필요, 목록만.`;

const RESEARCH_JA = (keyword: string) =>
  `「${keyword}」について、日本語の公式サイト・観光案内・百科事典を検索してください。
` +
  `この場所/テーマの音声ガイドで実際に読み上げられる固有名詞を、できるだけ網羅的に集めてください。

` +
  `対象: 建物・施設名、境内の各所名、神名・仏名、人名、神事・祭事名、地名、駅名、
` +
  `年号・時代名、専門用語(茶道・建築・信仰など)、周辺の店舗・商店街名。

` +
  `各項目を「正式表記(ふりがな)」の形式で列挙してください。読みが不明なものは推測せず省くこと。
` +
  `解説は不要、一覧のみ。`;

const RESEARCH_PROMPT = (keyword: string, lang: string) =>
  (lang === 'ko' ? RESEARCH_KO : RESEARCH_JA)(keyword);

export type Research = { text: string; sources: { title: string; uri: string }[]; queries: string[] };

export async function researchTerms(env: Env, keyword: string, lang = 'ja'): Promise<Research> {
  const resp = await generate(env, {
    contents: [{ parts: [{ text: RESEARCH_PROMPT(keyword, lang) }] }],
    tools: [{ google_search: {} }],
    generationConfig: { temperature: 0.0 },
  });
  const text = firstText(resp) ?? '';
  if (!text) throw new Error('搜尋沒有回結果');
  const gm = resp?.candidates?.[0]?.groundingMetadata ?? {};
  const sources = ((gm.groundingChunks ?? []) as any[])
    .map(c => ({ title: String(c?.web?.title ?? ''), uri: String(c?.web?.uri ?? '') }))
    .filter(s => s.uri);
  return { text, sources, queries: (gm.webSearchQueries ?? []) as string[] };
}

/* 容錯解析:輸出被截斷時 JSON.parse 會整份失敗,但前面幾百個詞條其實是好的。
   先直接 parse,失敗就逐一撈出完整的 {…} 物件——scripts/make_dict.py 踩過同一個坑,
   當時也是加了容錯才把包生出來。寧可少幾條,不要整包掉。 */
function parseEntries(raw: string): VocabEntry[] {
  try {
    const items = JSON.parse(raw);
    if (Array.isArray(items)) return items as VocabEntry[];
  } catch {
    /* 往下走容錯路徑 */
  }
  const out: VocabEntry[] = [];
  for (const m of raw.matchAll(/\{[^{}]*\}/g)) {
    try {
      const o = JSON.parse(m[0]);
      if (o && typeof o.content === 'string') out.push(o as VocabEntry);
    } catch {
      /* 半截的物件跳過 */
    }
  }
  if (!out.length) throw new Error('詞條 JSON 解析失敗');
  return out;
}

export async function extractVocab(env: Env, sourceText: string, lang = 'ja'): Promise<VocabEntry[]> {
  // 讀音字集依語言:日文全形假名、韓文諺文(Speechmatics 各自接受,已探針驗證)
  const prompt =
    lang === 'ko'
      ? VOCAB_PROMPT.replace('全角ひらがな', 'ハングル').replace(
          '観光ガイドの音声認識',
          '観光ガイド(韓国語)の音声認識',
        )
      : VOCAB_PROMPT;
  const resp = await generate(env, {
    contents: [{ parts: [{ text: prompt + sourceText }] }],
    // 詞條多的地點(大阪城 147 條)輸出很長,不給額度會被截斷成壞掉的 JSON
    generationConfig: { temperature: 0.0, responseMimeType: 'application/json', maxOutputTokens: 16384 },
  });
  const raw = firstText(resp);
  if (!raw) throw new Error('gemini 沒有回詞條');
  return parseEntries(raw);
}
