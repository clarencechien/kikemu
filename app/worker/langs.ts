/* 聽譯來源語言(單一事實來源:/api/config 把清單送給前端,加語言只改這裡)。
   全部經探針驗證可接受本 app 的完整 config
   (operating_point=enhanced + max_delay + additional_vocab)。

   注意 Speechmatics 的兩個限制:
   - 語言與詞表一樣隨 StartRecognition 送出,**中途不可換**(換語言 = 重連)
   - 中英雙語包只有 cmn_en;ja_en / en_ja 明確回 "lang pack is not supported"

   srcName 是填進口譯 prompt 的來源語名稱(日文寫法)。ja 走凍結字串、
   一個字都不動——exp1 的 adequacy 4.71 與台灣用語 0 失誤是在那份 prompt 上量到的。 */

export type Lang = { code: string; label: string; srcName: string };

export const LANGS: Lang[] = [
  { code: 'ja', label: '日本語', srcName: '日本語' },
  { code: 'ko', label: '한국어', srcName: '韓国語' },
  { code: 'en', label: 'English', srcName: '英語' },
  // 中英夾雜用雙語包,不用單語 cmn:exp2 實測術語召回 0.521 → 0.813(+0.23~0.29)。
  // 單語包的病徵是把英文聽成別的英文(COVID-19 → CoffeeNight),詞表補不回來。
  // cmn_en 是 Speechmatics 唯一存在的雙語包(ja_en / en_ja 皆 not supported)。
  { code: 'cmn_en', label: '中文・English(夾雜)', srcName: '中国語' },
];

export const DEFAULT_LANG = 'ja';

/** 場景包支援的語言。每種語言的 sounds_like 字集不同,驗證器要分流——
    日文只吃全形假名、韓文吃諺文(皆經探針驗證 Speechmatics 接受)。
    英文與中英夾雜暫不做包:英文拼寫本身即讀音,幫助有限。 */
export const PACK_LANGS = ['ja', 'ko'] as const;
export const packLangLabel = (code: string) => (code === 'ko' ? '韓文' : '日文');

/** 讀音字集:日文全形假名(同 scripts/make_dict.py)、韓文諺文 */
export const SOUNDS_LIKE_RE: Record<string, RegExp> = {
  ja: /^[ぁ-ゖァ-ヺー]+$/,
  ko: /^[가-힣ㄱ-ㅎㅏ-ㅣ]+$/,
};

export const resolveLang = (code: string | null | undefined): Lang =>
  LANGS.find(l => l.code === code) ?? LANGS[0];
