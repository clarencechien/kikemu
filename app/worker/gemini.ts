/* Gemini generateContent(REST;CF Workers 無 Node SDK)。
   兩個用途:
   1. translateSentence — 聽譯 hop:定稿句 → 台灣正體(relay.ts 逐句呼叫)。
      systemInstruction 與 user template 一字不改自 scripts/prompts.py
      (exp1 凍結口譯 prompt:台灣用語 0 失誤、adequacy 4.71,不准改)。
   2. extractVocab — 場景包生成:來源文字 → 詞條 + 假名讀音(JSON mode),
      即 scripts/make_dict.py 的產品化(admin.ts pack-generate 呼叫)。 */

import type { Env } from './index';
import type { VocabEntry } from './vocab';

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
export async function translateSentence(env: Env, sentence: string): Promise<string> {
  const resp = await generate(env, {
    systemInstruction: { parts: [{ text: INTERPRETER_SYSTEM }] },
    contents: [{ parts: [{ text: TRANSLATE_USER_TEMPLATE.replace('{transcript}', sentence) }] }],
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
export async function extractVocab(env: Env, sourceText: string): Promise<VocabEntry[]> {
  const resp = await generate(env, {
    contents: [{ parts: [{ text: VOCAB_PROMPT + sourceText }] }],
    generationConfig: { temperature: 0.0, responseMimeType: 'application/json' },
  });
  const raw = firstText(resp);
  if (!raw) throw new Error('gemini 沒有回詞條');
  const items = JSON.parse(raw);
  if (!Array.isArray(items)) throw new Error('詞條格式不是陣列');
  return items as VocabEntry[];
}
