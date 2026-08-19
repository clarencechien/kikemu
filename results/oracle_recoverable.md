# Oracle 可救回清單(tolerant)

「oracle 命中,但該條件下最佳單一 arm 沒命中」的項目。
**要人眼看:救回來的是不是真的重要的詞。比率會騙人,清單不會。**

## exp1 — 全 arm

- `N0` **御祭神** ×1(hig01_A1) — 最佳單一 arm `Abat` 沒中,救回者:A, C, Cplus, Cb, Cbplus
- `N0` **饒速日尊** ×1(hig01_A1) — 最佳單一 arm `Abat` 沒中,救回者:A
- `N0` **椿の井戸** ×1(sakai05) — 最佳單一 arm `Abat` 沒中,救回者:A, C, Cplus, Cb, Cbplus
- `N1` **主祭神** ×1(hig02_B12) — 最佳單一 arm `Cplus` 沒中,救回者:A
- `N1` **呉服神社** ×1(ikeda02) — 最佳單一 arm `Cplus` 沒中,救回者:A
- `N1` **池田駅** ×1(ikeda02) — 最佳單一 arm `Cplus` 沒中,救回者:A
- `N1` **いとや百貨店** ×1(ikeda03) — 最佳單一 arm `Cplus` 沒中,救回者:Cbplus
- `N2` **あげまき結び** ×1(hig02_B12) — 最佳單一 arm `Cbplus` 沒中,救回者:A
- `N3` **物部氏** ×1(hig01_A1) — 最佳單一 arm `Cplus` 沒中,救回者:Abat
- `N3` **石切神社** ×1(hig01_A1) — 最佳單一 arm `Cplus` 沒中,救回者:Abat
- `N3` **神武天皇** ×1(hig01_A1) — 最佳單一 arm `Cplus` 沒中,救回者:Abat
- `N3` **主祭神** ×1(hig02_B12) — 最佳單一 arm `Cplus` 沒中,救回者:Abat
- `N3` **宮司** ×1(hig02_B12) — 最佳單一 arm `Cplus` 沒中,救回者:Abat
- `N3` **拝殿** ×1(hig02_B12) — 最佳單一 arm `Cplus` 沒中,救回者:A, Abat, C, Cb, Cbplus
- `N3` **池田新市街** ×1(ikeda02) — 最佳單一 arm `Cplus` 沒中,救回者:Abat
- `N3` **旧田中町商店街** ×1(ikeda03) — 最佳單一 arm `Cplus` 沒中,救回者:Abat
- `N3` **宿院** ×1(sakai06) — 最佳單一 arm `Cplus` 沒中,救回者:Abat
- `N4` **御祭神** ×1(hig01_A1) — 最佳單一 arm `Abat` 沒中,救回者:C, Cplus, Cb, Cbplus
- `N4` **記紀神話時代** ×1(hig01_A1) — 最佳單一 arm `Abat` 沒中,救回者:C, Cplus, Cb, Cbplus
- `N4` **枚岡神社** ×1(hig02_B12) — 最佳單一 arm `Abat` 沒中,救回者:Cplus, Cbplus
- `N4` **椿の井戸** ×1(sakai05) — 最佳單一 arm `Abat` 沒中,救回者:A, C, Cplus, Cb, Cbplus
- `N4` **紀州街道** ×1(sakai05) — 最佳單一 arm `Abat` 沒中,救回者:Cplus, Cb, Cbplus

## exp2 — 全 arm

- `M0` **I saw a saw** ×1(S1) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:G
- `M0` **POS tagging POS tagging** ×1(S1) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:Xsli_bi
- `M0` **sentiment analysis sentiment analysis** ×1(S1) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:G, X_bi, Xdom_bi
- `M0` **Midjourney** ×1(S3) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:G, X_bi, Xsli_bi, Xdom_bi
- `M1` **case** ×1(S1) — 最佳單一 arm `Xsli_bi` 沒中,救回者:X_cmn
- `M1` **drug discovery** ×2(S1) — 最佳單一 arm `Xsli_bi` 沒中,救回者:G, X_bi, Xdom_bi
- `M1` **ChatGPT** ×8(S3) — 最佳單一 arm `Xsli_bi` 沒中,救回者:G
- `M2` **ChatGPT** ×8(S3) — 最佳單一 arm `Xsli_bi` 沒中,救回者:G
- `M3` **POS tagging POS tagging** ×1(S1) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:Xsli_bi
- `M3` **case** ×1(S1) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:X_cmn
- `M3` **sentiment analysis sentiment analysis** ×1(S1) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:X_bi, Xsli_bi, Xdom_bi

## exp5 — 全 arm(含 Gemini,有主場偏差)

- `M0` **load** ×1(T1) — 最佳單一 arm `Gbat` 沒中,救回者:Gbat37, Xbrz_auto
- `M3` **image** ×1(T1) — 最佳單一 arm `Gbat37` 沒中,救回者:G, Gbat, Xbrz_auto
- `M3` **load** ×1(T1) — 最佳單一 arm `Gbat37` 沒中,救回者:Gbat, Xbrz_auto
- `M3` **open source library** ×6(T1) — 最佳單一 arm `Gbat37` 沒中,救回者:G, Gbat, Xbat_bi, Xbrz_auto
- `M3` **team own** ×1(T1) — 最佳單一 arm `Gbat37` 沒中,救回者:Gbat
- `M3` **ChatGPT** ×1(T3) — 最佳單一 arm `Gbat37` 沒中,救回者:Gbat
- `M3` **Mario** ×1(T3) — 最佳單一 arm `Gbat37` 沒中,救回者:Gbat, Xbat_bi, Xbrz_auto
- `M3` **XX** ×2(T3) — 最佳單一 arm `Gbat37` 沒中,救回者:G, Gbat
- `M3` **change** ×1(T3) — 最佳單一 arm `Gbat37` 沒中,救回者:Xbrz_auto

## exp5 — 乾淨組(Breeze + SM,排除與參考同源的 Gemini)

- `M0` **startup** ×1(T1) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:Xbat_bi
- `M0` **wallet wallet** ×1(T2) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:Xbat_bi
- `M3` **CDN** ×1(T1) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:Xbat_bi
- `M3` **wallet wallet** ×1(T2) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:Xbat_bi
- `M3` **APP** ×1(T3) — 最佳單一 arm `Xbrz_auto` 沒中,救回者:Xbat_bi
