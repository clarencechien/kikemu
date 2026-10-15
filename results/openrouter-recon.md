# OpenRouter STT 偵察(Phase 0,2026-10-15)

任務書:[`handoff-v16.md`](../handoff-v16.md)(= 使用者的 kikemu-openrouter-stt-handoff)。
呼叫程式:`scripts/openrouter_stt.py`(實際送出的 request body 去掉音訊後原樣記錄)。偵察花費 ≈ $0.2(OpenRouter)。

## 0. 前提

| 項目 | 狀態 |
|---|---|
| OpenRouter key | 環境變數 `or_key`(任務書寫 `OPENROUTER_API_KEY`,本環境名稱不同)。`/auth/key` 200,額度 $10 |
| xAI key | **沒有**(`XAI_API_KEY` / `xai_key` 都未設定)→ 無法直連 `api.x.ai/v1/stt` |
| OpenAI key | 401(與 exp1 時相同) |
| OpenRouter 上的 STT 模型 | `GET /api/v1/models?output_modalities=transcription` → **26 個**。比任務書多一個 `assemblyai/universal-3-6-pro`(canonical slug 日期 2026-10-14,3.5 Pro 的後繼) |

## 1. Grok slug 背後是 1.0 還是 2.0 → **無法判定**

- 回應本體只有 `text` 與 `usage`,header 只有 `X-Generation-Id`
- `GET /api/v1/generation?id=…` 回 `model: x-ai/grok-stt-20260723`、`provider_name: xAI`——那是 OpenRouter 上架日期的 permaslug,
  **不是**上游 xAI 的型號
- 任務書 §1 引 xAI release notes:「1.0 於 2026-10-02 EOL,請求自動轉 2.0」。若屬實,10-02 之後經 OpenRouter 的呼叫在 xAI 端
  就是 2.0——但這是**推論**,本專案沒有 xAI key 可以直連比對
- 依任務書 §2-1 的規則:無法判定 → 應直連 xAI 當正式 arm;**直連做不到** → Grok 只有「經 OpenRouter、版本未定」這一格,
  **Gate A 的結論只能當參考**

## 2. 輸出字形(T1 300 秒、`language` 不設,原樣不轉)

| 模型 | 簡體字 / 繁體字 | 字形 |
|---|---|---|
| x-ai/grok-stt-1.0 | 3 / 385 | **繁** |
| openai/gpt-transcribe | 3 / 394 | **繁** |
| openai/whisper-large-v3-turbo | 17 / 321 | **繁**(混少量簡) |
| elevenlabs/scribe-v2 | 445 / 0 | 簡 |
| qwen/qwen3-asr-1.7b、0.6b | 421 / 0、433 / 0 | 簡 |
| microsoft/mai-transcribe-2 | 432 / 0 | 簡 |
| meta/muse-voice-transcribe-1.0 | 428 / 0 | 簡 |
| nvidia/nemotron-3.5-asr-…-0.6b | 425 / 0 | 簡 |
| assemblyai/universal-3-5-pro、3-6-pro | 420 / 0、423 / 0 | 簡 |

計數方式:OpenCC `s2t` 會改動的字算簡體、`t2s` 會改動的字算繁體。計分前一律走既有流程(exp5 `score.py` 的 OpenCC `s2t`),
原始字形另列。

## 3. 詞表(keyterm)經 OpenRouter 傳不傳得過去

**判別法**:送「違反上游規格」的值。被轉送 → 上游拒收;被丟掉 → 照樣成功。(單純比有無詞表的輸出分不出來:
連送不存在的參數,輸出長度都會變——模型本身有隨機性,OpenRouter 也不會對不認得的參數報錯。)

| 送出 | 結果 | 判讀 |
|---|---|---|
| `provider.options.xai.keyterm` 150 條(xAI 上限 100) | **400** | **有轉送到 xAI** |
| `provider.options.xai.keyterm` 單條 80 字(上限 50) | **400** | 同上 |
| `provider.options.elevenlabs.diarize: "字串"` | 400,OpenRouter 自己的 schema 錯誤訊息 | OpenRouter 對 elevenlabs 選項有白名單檢查 |
| `provider.options.elevenlabs.num_speakers: 999` | 400「expected number to be <=32」 | 同上 |
| `provider.options.elevenlabs.keyterms: 12345`(型別錯) | **200** | 不在白名單 → **被丟掉** |
| `provider.options.elevenlabs.keyterms` 1001 條 | **200** | 同上 |

- **Grok**:詞表經 OpenRouter **傳得過去**(`provider.options.xai.keyterm`,單數)
- **Scribe**:**經 OpenRouter 無法開詞表**(至少 `keyterms` 這個名稱不行)。**不是 Scribe 不支援**——直連 API 有 `keyterms`,
  exp1 的 `Sbat_ja_full` 就是直連帶 166 條
- 附帶發現:**舊紀錄的施測錯誤**——exp5 的 Scribe 直連 arm `Sbat_bi` 的 `_meta.json` 寫 `keyterms: true, keyterm_cap: 50`,
  但 `run_scribe_batch.py` 對 exp5 的詞典是 `None`,**實際送出 0 條**(逐檔的 `meta.n_keyterms` 是 0)。
  正是 CLAUDE.md「施測紀錄 ≠ 能力宣稱」的那一型,`_meta.json` 已更正(見 Phase 1 commit)

## 4. 價格單位(`usage.cost ÷ usage.seconds`,300 秒實測)

| 模型 | cost / 300 秒 | 換算 $/hr | 任務書假設 |
|---|---|---|---|
| x-ai/grok-stt-1.0 | 0.00833 | **0.10** | 0.10 ✓ |
| elevenlabs/scribe-v2 | 0.00917 | **0.11** | 0.11 ✓(與筆記的 ~$0.22 不符的原因仍未查——經 OpenRouter 沒開 diarize、也開不了詞表) |
| qwen/qwen3-asr-1.7b | 0.00225 | 0.027 | ✓ |
| qwen/qwen3-asr-0.6b | 0.00100 | 0.012 | ✓ |
| microsoft/mai-transcribe-2 | 0.00833 | **0.10** | 「單位不明(標 0.1)」→ 實為 $0.10/hr |
| openai/whisper-large-v3-turbo | 0.00100 | 0.012 | ✓ |
| openai/gpt-transcribe | 0.0225 | 0.27 | ✓ |
| meta/muse-voice-transcribe-1.0 | 0.01500 | 0.18 | ✓ |
| nvidia/nemotron-3.5-asr-…-0.6b | 0.00100 | 0.012 | ✓ |
| assemblyai/universal-3-5-pro / 3-6-pro | 0.0250 | **0.30** | 任務書寫 0.45 → 實為 0.30 |

## 5. 長度與 60 秒逾時 → **不用切段**

- exp5 每段 **300 秒**(T1–T3 × M0–M3);exp1 最長 157.9 秒,6 段裡 4 段超過 60 秒
- 300 秒整檔送出,**所有可用模型都在 60 秒內回來**(最慢 qwen3-asr-1.7b 28.6 秒、muse 26.8 秒、scribe 20.2 秒)
- 「60 秒逾時」是上游處理時間,不是音訊長度——本輪**不切段**,也就沒有「切段」這個新變因
- `deepgram/nova-3`:300 秒回「does not support large audio inputs」(本來就在跳過名單)

## 6. ZDR 與失敗的模型

**ZDR**(`provider.zdr: true`)以 1 秒靜音掃描:grok-stt、gemini-3.5-transcribe、gpt-transcribe、muse-voice → **404 沒有 ZDR 端點**;
其餘 9 個可用。使用者 2026-10-15 決定「**先不算 ZDR**」→ 本輪所有呼叫**都不送** `zdr`(記在每檔 request 紀錄)。

**google/gemini-3.5-transcribe 經 OpenRouter 一律 400**(「Provider returned 400」,28 秒 / 30 秒 / 300 秒、
`language=zh`、`verbose_json` 都一樣)。**直連 Gemini API 可用**:`models/gemini-3.5-transcribe:generateContent`,
轉寫在 `candidates[].content.parts[].audioTranscription.text`(不是一般的 `text`),
語言與詞表用 `generationConfig.audioTranscriptionConfig.{languageCodes, customVocabulary}`
([官方文件](https://ai.google.dev/gemini-api/docs/generate-content/transcribe),2026-10-15 讀)→ Phase 1 用**直連**跑這一格,標「直連」。
