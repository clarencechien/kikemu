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
  { code: 'yue', label: '廣東話', srcName: '広東語' },
  { code: 'cmn', label: '中文(普通話)', srcName: '中国語' },
  { code: 'th', label: 'ไทย', srcName: 'タイ語' },
  { code: 'vi', label: 'Tiếng Việt', srcName: 'ベトナム語' },
  { code: 'de', label: 'Deutsch', srcName: 'ドイツ語' },
  { code: 'fr', label: 'Français', srcName: 'フランス語' },
  { code: 'it', label: 'Italiano', srcName: 'イタリア語' },
];

export const DEFAULT_LANG = 'ja';

/** 場景包目前只做日文(詞表的 sounds_like 驗證器只認全形假名) */
export const PACK_LANG = 'ja';

export const resolveLang = (code: string | null | undefined): Lang =>
  LANGS.find(l => l.code === code) ?? LANGS[0];
