"use strict";
// kikemu 名單/額度/場景包管理(仿 sukemu public/admin.js;額度單位:秒/日,
// 今日用量顯示分鐘 + 估算 NT$ 0.25/分,PRD §7)
const $ = (id) => document.getElementById(id);
const TWD_PER_MIN = 0.25;
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
  if (!u || !u.seconds) return `<span class="ts">—</span>`;
  const m = u.seconds / 60;
  return `<span class="ts">${m.toFixed(1)} 分 · <b class="twd">NT$${(m * TWD_PER_MIN).toFixed(2)}</b></span>`;
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
          <td>${esc(p.name)}</td>
          <td class="ts">${Number(p.count)}</td>
          <td class="ts">${esc(String(p.updated || "").replace("T", " ").slice(0, 16))}</td>
          <td><button class="danger" data-pack-delete="${esc(p.id)}">刪除</button></td></tr>`).join("")}</tbody></table>`;
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
    if (d.warnings && d.warnings.length) {
      $("packWarn").textContent = "格式警告:\n" + d.warnings.join("\n");
    }
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

/* 警告彙總:長讀音動輒佔一半以上詞條,逐條列出沒有可讀性。
   分類計數,並把 exp1 的實測狀態講清楚——文件說 >6 字會被忽略,
   但 exp1 的 C+ 確實救回了「石切劔箭神社」(讀音 12 字),
   所以就算讀音被丟,表記本身仍有加成。不阻擋、也不假裝沒事。 */
function warnSummary(warnings) {
  if (!warnings?.length) return "";
  const long = warnings.filter(w => w.includes("超過 6 字")).length;
  const other = warnings.filter(w => !w.includes("超過 6 字"));
  const bits = [];
  if (long) bits.push(`${long} 個詞條的假名讀音超過 6 字——Speechmatics 文件稱會忽略,` +
    `但 exp1 實測「石切劔箭神社」(12 字讀音)仍被救回,表記本身也有加成,故保留。`);
  if (other.length) bits.push(other.slice(0, 5).join("\n") + (other.length > 5 ? `\n…另 ${other.length - 5} 則` : ""));
  return `<p class="warnList">${esc(bits.join("\n"))}</p>`;
}

$("packSearchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("skwBtn");
  const keyword = $("skwKeyword").value.trim();
  const id = $("skwId").value.trim().toLowerCase();
  const name = $("skwName").value.trim();
  btn.disabled = true;
  btn.textContent = "搜尋中…";
  $("skwPreview").innerHTML = '<p class="hint">正在搜尋並抽取詞條,約 10~30 秒…</p>';
  try {
    const d = await api("/api/admin/pack-search", { id, name, keyword, preview: true });
    SKW = { id, name, keyword };
    const src = (d.sources || []).map(s =>
      `<li><a href="${esc(s.uri)}" target="_blank" rel="noopener">${esc(s.title || s.uri)}</a></li>`).join("");
    const terms = (d.entries || []).map(en =>
      `<code>${esc(en.content)}</code>${en.sounds_like?.length ? `<small>(${esc(en.sounds_like[0])})</small>` : ""}`
    ).join("、");
    $("skwPreview").innerHTML = `
      <div class="card" style="border:1px solid var(--line); border-radius:2px; padding:10px; margin-top:8px">
        <b>「${esc(d.keyword)}」抽出 ${d.count} 個詞條</b>
        <p class="hint">${d.sources?.length
            ? `搜尋詞:${esc((d.queries || []).join(" / "))}`
            : "⚠ 這次沒有引用外部來源(模型憑既有知識回答)——冷門地點請改用貼上官方頁內文,讀音比較可靠。"}</p>
        <p style="font-size:13px; line-height:2; margin:8px 0">${terms}</p>
        ${src ? `<details><summary class="hint" style="cursor:pointer">來源 ${d.sources.length} 筆</summary><ul class="hint">${src}</ul></details>` : ""}
        ${warnSummary(d.warnings)}
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
      // 再跑一次(含搜尋)後落地:兩趟成本很低,換到的是「存的就是剛才看到的來源」
      const d = await api("/api/admin/pack-search", { ...SKW, preview: false });
      $("skwPreview").innerHTML = `<p class="hint">✓ 已存「${esc(d.name)}」(${d.count} 詞)</p>`;
      $("skwKeyword").value = "";
      $("skwId").value = "";
      $("skwName").value = "";
      await reload();
    } catch (err) {
      $("skwPreview").innerHTML = `<p class="warnList">${esc(String(err.message || err))}</p>`;
    }
  }
});
