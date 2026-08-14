"use strict";
// kikemu 名單/額度/場景包管理(仿 sukemu public/admin.js;額度單位:秒/日)
// 成本估算(PRD §7):SM $0.24/hr(按秒)+ Gemini 譯(按 token,含 thoughts)。
// 兩種計量單位分開顯示——秒數擋不住 token 花費,合成一個數字就看不出是哪邊在燒。
const $ = (id) => document.getElementById(id);
const USD_TWD = 32;
const SM_USD_PER_MIN = 0.24 / 60;               // Speechmatics 牌價 $0.24/hr
// gemini-3.5-flash $1.50/M in、$9.00/M out(2026-08-14 官方 pricing 頁;thinking 計輸出價)。
// 這裡用輸出價當上限估(relay 回報的是 prompt+output+thoughts 總和),寧可高估不要低估。
const GEMINI_USD_PER_MTOK = 9.00;
let DATA = null;

function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2600);
}
const api = async (path, body) => {
  const r = await fetch(path, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const mins = (n) => (Number(n) === 0 ? "無上限" : `${Math.round(Number(n) / 60)} 分/日`);

function tierLabel(value) {
  const isNum = /^\d+$/.test(String(value));
  const cls = isNum ? "custom" : (value === "admin" ? "admin" : "");
  const limit = isNum ? Number(value) : DATA.tiers[value];
  const detail = limit === undefined ? "級別未定義" : mins(limit);
  return `<span class="tier ${cls}">${esc(isNum ? "自訂" : value)}</span> <span class="ts">${detail}</span>`;
}
function usageLabel(email) {
  const u = DATA.usage[email];
  if (!u || (!u.seconds && !u.tokens)) return `<span class="ts">—</span>`;
  const m = (u.seconds || 0) / 60;
  const tok = u.tokens || 0;
  const usd = m * SM_USD_PER_MIN + (tok / 1e6) * GEMINI_USD_PER_MTOK;
  const tokStr = tok ? ` · ${(tok / 1000).toFixed(1)}k tok/${u.calls || 0} 句` : "";
  return `<span class="ts">${m.toFixed(1)} 分${tokStr} · <b class="twd">NT$${(usd * USD_TWD).toFixed(2)}</b></span>`;
}
function tierOptions(selected) {
  return Object.keys(DATA.tiers).map((t) =>
    `<option value="${esc(t)}"${t === selected ? " selected" : ""}>${esc(t)} · ${mins(DATA.tiers[t])}</option>`).join("");
}

function render() {
  // 等候名單
  $("waitCount").textContent = DATA.waitlist.length ? `(${DATA.waitlist.length})` : "";
  $("waitBox").innerHTML = DATA.waitlist.length === 0
    ? `<p class="empty">目前沒有人在等候。有人用不在名單內的帳號登入,就會自動出現在這裡。</p>`
    : `<table><thead><tr><th>EMAIL</th><th>申請時間</th><th>動作</th></tr></thead><tbody>${
        DATA.waitlist.map((w) => `<tr>
          <td class="email">${esc(w.email)}</td>
          <td class="ts">${esc(String(w.at).replace("T", " ").slice(0, 16))}</td>
          <td><div class="row-actions">
            <select data-approve-tier="${esc(w.email)}">${tierOptions(DATA.defaultTier)}</select>
            <button class="primary" data-approve="${esc(w.email)}">核准</button>
            <button class="danger" data-wait-remove="${esc(w.email)}">忽略</button>
          </div></td></tr>`).join("")}</tbody></table>`;

  // 已核准
  const entries = Object.entries(DATA.allowlist).sort((a, b) => a[0].localeCompare(b[0]));
  $("allowCount").textContent = entries.length ? `(${entries.length})` : "";
  $("allowBox").innerHTML = entries.length === 0
    ? `<p class="empty">名單是空的。</p>`
    : `<table><thead><tr><th>EMAIL</th><th>額度</th><th>今日</th><th>動作</th></tr></thead><tbody>${
        entries.map(([email, tier]) => {
          const isAdminVar = DATA.admins.includes(email);
          return `<tr>
            <td class="email">${esc(email)}${isAdminVar ? ' <span class="tier admin">ADMIN</span>' : ""}</td>
            <td>${tierLabel(tier)}</td>
            <td>${usageLabel(email)}</td>
            <td><div class="row-actions">
              <select data-change-tier="${esc(email)}">${tierOptions(String(tier))}</select>
              <button data-change="${esc(email)}">改額度</button>
              ${isAdminVar ? "" : `<button class="danger" data-remove="${esc(email)}">移除</button>`}
            </div></td></tr>`;
        }).join("")}</tbody></table>`;

  $("addTier").innerHTML = tierOptions(DATA.defaultTier);
  $("tierHint").textContent = `級別定義在 QUOTA_TIERS 變數:` +
    Object.entries(DATA.tiers).map(([t, n]) => `${t}=${mins(n)}`).join("、") +
    `。填「自訂秒數」會覆蓋級別選單(例:1200 = 20 分/日)。名單改動立即生效,不用重部署。`;

  // 場景包(kikemu 專屬)
  const packs = DATA.packs || [];
  $("packCount").textContent = packs.length ? `(${packs.length})` : "";
  $("packBox").innerHTML = packs.length === 0
    ? `<p class="empty">還沒有場景包。用下方表單生成,或 wrangler r2 object put 上傳種子包(見 app/README.md)。</p>`
    : `<table><thead><tr><th>ID</th><th>名稱</th><th>詞條數</th><th>更新時間</th><th>動作</th></tr></thead><tbody>${
        packs.map((p) => `<tr>
          <td class="ts">${esc(p.id)}</td>
          <td>${esc(p.alias || p.name)}<small style="color:var(--ink-2)"> ・${esc(p.lang === "ko" ? "韓文" : "日文")}</small></td>
          <td class="ts">${Number(p.count)}</td>
          <td class="ts">${esc(String(p.updated || "").replace("T", " ").slice(0, 16))}</td>
          <td><div class="row-actions">
            <button data-pack-revalidate="${esc(p.id)}" title="重跑驗證 pipeline(規則後來補強過的話,舊包不會自動套用)">重驗</button>
            <button class="danger" data-pack-delete="${esc(p.id)}">刪除</button>
          </div></td></tr>`).join("")}</tbody></table>`;
}

async function reload() { DATA = await api("/api/admin/data"); render(); }

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  try {
    if (b.dataset.approve) {
      const tier = document.querySelector(`[data-approve-tier="${CSS.escape(b.dataset.approve)}"]`).value;
      await api("/api/admin/allow", { email: b.dataset.approve, tier });
      toast(`已核准 ${b.dataset.approve}(${tier})`);
      await reload();
    } else if (b.dataset.waitRemove) {
      await api("/api/admin/waitlist-remove", { email: b.dataset.waitRemove });
      toast("已從等候名單移除"); await reload();
    } else if (b.dataset.change) {
      const tier = document.querySelector(`[data-change-tier="${CSS.escape(b.dataset.change)}"]`).value;
      await api("/api/admin/allow", { email: b.dataset.change, tier });
      toast(`${b.dataset.change} → ${tier}`); await reload();
    } else if (b.dataset.remove) {
      if (!confirm(`確定把 ${b.dataset.remove} 移出名單?他下一次操作就會被擋下。`)) return;
      await api("/api/admin/remove", { email: b.dataset.remove });
      toast("已移除"); await reload();
    } else if (b.dataset.packRevalidate) {
      b.disabled = true; // 會寫回 R2,不能連點
      try {
        const d = await api("/api/admin/pack-revalidate", { id: b.dataset.packRevalidate });
        toast(d.unchanged ? `「${d.id}」重驗:沒有需要修正的詞條`
                          : `「${d.id}」已重驗:${d.before} → ${d.count} 詞(✎${d.stats.fix} ✂${d.stats.drop})`);
        $("packWarn").innerHTML = issueSummary(d);
      } finally { b.disabled = false; }
      await reload();
    } else if (b.dataset.packDelete) {
      if (!confirm(`確定刪除場景包「${b.dataset.packDelete}」?正在使用它的 session 不受影響,下一場起消失。`)) return;
      await api("/api/admin/pack-delete", { id: b.dataset.packDelete });
      toast("場景包已刪除"); await reload();
    }
  } catch (err) { toast("失敗:" + err.message); }
});

$("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = $("addEmail").value.trim();
  const custom = $("addCustom").value.trim();
  const tier = custom || $("addTier").value;
  try {
    await api("/api/admin/allow", { email, tier });
    toast(`已加入 ${email}`);
    $("addEmail").value = ""; $("addCustom").value = "";
    await reload();
  } catch (err) { toast("失敗:" + err.message); }
});

// 場景包生成:make_dict.py 的產品化(Gemini 抽詞條 → 全形假名驗證 → 存 R2)
$("packForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("packGen");
  btn.disabled = true; btn.textContent = "生成中…(Gemini 抽詞條)";
  $("packWarn").textContent = "";
  try {
    const d = await api("/api/admin/pack-generate", {
      id: $("packId").value.trim().toLowerCase(),
      name: $("packName").value.trim(),
      source_text: $("packSrc").value,
    });
    toast(`「${d.name}」已存檔:${d.count} 詞條`);
    $("packWarn").innerHTML = issueSummary(d);
    $("packSrc").value = "";
    await reload();
  } catch (err) { toast("失敗:" + err.message); }
  finally { btn.disabled = false; btn.textContent = "生成場景包"; }
});

(async function boot() {
  let me = null;
  try { const r = await fetch("/api/me"); if (r.ok) me = await r.json(); } catch {}
  if (!me) { $("gate").innerHTML = `請先回 <a href="/">首頁登入</a>。`; return; }
  if (!me.isAdmin) { $("gate").textContent = `${me.email} 沒有管理權限。`; return; }
  $("whoami").textContent = me.email;
  try {
    await reload();
    $("gate").hidden = true; $("main").hidden = false;
  } catch (err) { $("gate").textContent = "讀取失敗:" + err.message; }
})();

/* ── 關鍵字產包(搜尋接地 → 預覽 → 確認存)──
   分兩段的理由:讀音錯的詞條會反過來害辨識(exp1 的詞表價值來自讀音正確),
   所以一定讓管理者先看過詞條與來源筆數。來源 0 筆 = 模型憑記憶答的,要更小心。 */
let SKW = null; // 上一次預覽的參數,確認存檔時重用

/* 驗證 pipeline 的結果摘要(worker/vocab.ts 四段:trim → content → reading → dedupe)。
   為什麼要把「自動修正」單獨列出來給人看:大阪城那包實測出現過
   `黄金 of 茶室`(の 被翻成 of)與 `虎石→トらいし`(混片假名),
   pipeline 現在會自己改掉——但改動過的詞條一定要讓管理者過目,
   免得程式默默把某個真的叫這個名字的詞「修」壞了。

   長讀音另外處理:動輒佔一半以上詞條,逐條列沒有可讀性,只給計數與實測註解。 */
const LEVELS = { fix: { icon: "✎", label: "自動修正" }, warn: { icon: "⚠", label: "警告" }, drop: { icon: "✂", label: "剔除" } };

function issueSummary(d) {
  const issues = d.issues || [];
  const stats = d.stats;
  if (!issues.length && !stats) return "";
  const long = issues.filter(i => i.message.includes("超過 6 字"));
  const rest = issues.filter(i => !i.message.includes("超過 6 字"));
  const head = stats
    ? `pipeline:${stats.in} → ${stats.out} 詞・✎ ${stats.fix} 修正・⚠ ${stats.warn} 警告・✂ ${stats.drop} 剔除`
    : "";

  const groups = ["fix", "warn", "drop"].map(level => {
    const list = rest.filter(i => i.level === level);
    if (!list.length) return "";
    const rows = list.slice(0, 12).map(i =>
      `<li><code>${esc(i.content)}</code> ${esc(i.message)}</li>`).join("");
    const more = list.length > 12 ? `<li class="hint">…另 ${list.length - 12} 則</li>` : "";
    return `<p style="margin:6px 0 2px"><b>${LEVELS[level].icon} ${LEVELS[level].label}(${list.length})</b></p>
            <ul class="hint" style="margin:0">${rows}${more}</ul>`;
  }).join("");

  const longNote = long.length
    ? `<p class="hint">${long.length} 個詞條的讀音超過 6 字——Speechmatics 文件稱會忽略,` +
      `但 exp1 實測「石切劔箭神社」(12 字讀音)仍被救回,表記本身也有加成,故保留。</p>`
    : "";

  const body = groups + longNote;
  if (!body) return `<p class="hint">${esc(head)}</p>`;
  return `<details ${rest.some(i => i.level !== "warn") ? "open" : ""} style="margin-top:8px">
      <summary class="hint" style="cursor:pointer">${esc(head)}</summary>${body}</details>`;
}

/* 語言選單:哪些語言有場景包由 worker/langs.ts 的 PACK_LANGS 決定,經 /api/config 送來。
   這裡先用 fallback 同步畫出來、再用伺服器的清單覆蓋——選單絕不能是空的:
   空的 <select> 在畫面上就是「不能選語言」,而原本 fetch 失敗被 .catch 吞掉,
   使用者只看到一個點不開的框,連哪裡壞了都不知道。 */
const PACK_LANG_FALLBACK = [{ code: "ja", label: "日文" }, { code: "ko", label: "韓文" }];

function fillPackLangs(langs) {
  const sel = $("skwLang");
  const keep = sel.value;
  sel.innerHTML = "";
  for (const l of langs) {
    const o = document.createElement("option");
    o.value = l.code; o.textContent = l.label;
    sel.appendChild(o);
  }
  if (keep && langs.some(l => l.code === keep)) sel.value = keep;
}

fillPackLangs(PACK_LANG_FALLBACK);
fetch("/api/config")
  .then(r => r.json())
  .then(cfg => {
    if (cfg.packLangs?.length) fillPackLangs(cfg.packLangs);
  })
  .catch(err => {
    // 不靜默:fallback 還在,但要講明這份清單沒跟伺服器對過
    $("skwHint").textContent = `語言清單讀取失敗(${err.message || err}),先用預設值 日文 / 韓文。`;
  });

$("packSearchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("skwBtn");
  const keyword = $("skwKeyword").value.trim();
  const id = $("skwId").value.trim().toLowerCase();
  const alias = $("skwAlias").value.trim();
  btn.disabled = true;
  btn.textContent = "搜尋中…";
  $("skwPreview").innerHTML = '<p class="hint">正在搜尋並抽取詞條,約 10~30 秒…</p>';
  try {
    const d = await api("/api/admin/pack-search", { keyword, lang: $("skwLang").value });
    // 存檔時直接送回這批詞條,不重跑搜尋(省 100 秒,也保證存的就是看到的)
    SKW = { id, alias, name: keyword, lang: d.lang, keyword,
            entries: d.entries, sources: d.sources, queries: d.queries };
    const src = (d.sources || []).map(s =>
      `<li><a href="${esc(s.uri)}" target="_blank" rel="noopener">${esc(s.title || s.uri)}</a></li>`).join("");
    const terms = (d.entries || []).map(en =>
      `<code>${esc(en.content)}</code>${en.sounds_like?.length ? `<small>(${esc(en.sounds_like[0])})</small>` : ""}`
    ).join("、");
    $("skwPreview").innerHTML = `
      <div class="card" style="border:1px solid var(--line); border-radius:2px; padding:10px; margin-top:8px">
        <b>「${esc(d.keyword)}」抽出 ${d.count} 個詞條(${esc($("skwLang").selectedOptions[0]?.textContent || "")})</b>
        <p class="hint">${d.sources?.length
            ? `搜尋詞:${esc((d.queries || []).join(" / "))}`
            : "⚠ 這次沒有引用外部來源(模型憑既有知識回答)——冷門地點請改用貼上官方頁內文,讀音比較可靠。"}</p>
        <p style="font-size:13px; line-height:2; margin:8px 0">${terms}</p>
        ${src ? `<details><summary class="hint" style="cursor:pointer">來源 ${d.sources.length} 筆</summary><ul class="hint">${src}</ul></details>` : ""}
        ${issueSummary(d)}
        <div class="row-actions" style="margin-top:10px">
          <button class="primary" id="skwSave">✓ 存成場景包</button>
          <button id="skwCancel">取消</button>
        </div>
      </div>`;
  } catch (err) {
    $("skwPreview").innerHTML = `<p class="warnList">${esc(String(err.message || err))}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "🔎 搜尋生成";
  }
});

$("skwPreview").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.id === "skwCancel") { $("skwPreview").innerHTML = ""; return; }
  if (b.id === "skwSave" && SKW) {
    b.disabled = true;
    b.textContent = "存檔中…";
    try {
      const d = await api("/api/admin/pack-save", SKW);
      $("skwPreview").innerHTML =
        `<p class="hint" style="color:var(--ok)">✓ 已存「${esc(d.alias)}」(${esc(d.lang === "ko" ? "韓文" : "日文")}・${d.count} 詞,id=${esc(d.id)})——下方清單與使用者介面都會出現</p>`
        + issueSummary(d);
      $("skwKeyword").value = "";
      $("skwId").value = "";
      $("skwAlias").value = "";
      await reload();
    } catch (err) {
      $("skwPreview").innerHTML = `<p class="warnList">${esc(String(err.message || err))}</p>`;
    }
  }
});
