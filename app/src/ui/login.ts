/* 登入頁(沿 sukemu src/ui/login.ts)。兩種模式由 /api/config 決定:
   - oidc:表單原生 POST /auth/login → Google 授權(Turnstile 成對設定才顯示)
   - dev:未設定 GOOGLE_CLIENT_ID 時的開發用 Email 直登(比對白名單)
   不在名單者:OIDC 回跳 /?waitlist=1、dev 回 403 waitlist → 顯示等候名單卡。 */

import { api, ApiError } from '../api';
import type { Me } from '../types';

const $ = (id: string) => document.getElementById(id)!;
const enterApp = () => {
  document.body.dataset.screen = 'app';
};

/** 是否已登入(預覽模式下所有 API 都會被伺服器擋掉,前端也不該假裝可用) */
export const isAuthed = () => document.body.dataset.auth === 'in';

export function initLogin(hooks: { onAuthed: (me: Me) => void; onPreview: () => void }) {
  const form = $('ssoForm') as HTMLFormElement;
  const emailEl = $('email') as HTMLInputElement;
  const btn = $('ssoBtn') as HTMLButtonElement;
  const waitNotice = $('waitNotice');
  const err = $('loginErr');
  let oidc = false;

  const applyMe = (me: Me) => {
    document.body.dataset.auth = 'in';
    if (me.isAdmin) $('adminBtn').classList.remove('hidden');
    enterApp();
    hooks.onAuthed(me);
  };

  const q = new URLSearchParams(location.search);
  // OIDC 回跳:不在名單 → 顯示等候名單卡
  if (q.get('waitlist') === '1') {
    waitNotice.classList.remove('hidden');
    history.replaceState(null, '', '/');
  }
  // Turnstile 沒過 → 回跳帶 err,顯示可重試的訊息(不要讓使用者停在裸 403)
  if (q.get('err') === 'challenge') {
    err.textContent = '人機驗證沒有通過,請稍候一下再按一次登入。';
    err.classList.remove('hidden');
    history.replaceState(null, '', '/');
  }
  if (q.get('err') === 'auth') {
    err.textContent = '登入流程出了點問題,請再試一次。';
    err.classList.remove('hidden');
    history.replaceState(null, '', '/');
  }

  api.config().then(cfg => {
    if (cfg.mode !== 'oidc') return; // dev 模式維持 Email 直登
    oidc = true;
    emailEl.classList.add('hidden');
    emailEl.required = false;
    form.method = 'POST';
    form.action = '/auth/login';
    btn.textContent = '使用 Google 登入';
    if (cfg.turnstileSiteKey) mountTurnstile(cfg.turnstileSiteKey, btn);
  }).catch(() => {});

  form.addEventListener('submit', async e => {
    if (oidc) return; // 原生 POST /auth/login → 302 Google
    e.preventDefault();
    btn.disabled = true;
    err.classList.add('hidden');
    waitNotice.classList.add('hidden');
    try {
      await api.login(emailEl.value);
      api.me().then(applyMe).catch(enterApp);
    } catch (ex) {
      if (ex instanceof ApiError && ex.waitlist) {
        waitNotice.classList.remove('hidden');
      } else {
        err.textContent = ex instanceof Error ? ex.message : '登入失敗,請再試一次';
        err.classList.remove('hidden');
      }
    } finally {
      btn.disabled = false;
    }
  });

  ($('demoBtn') as HTMLButtonElement).onclick = () => {
    enterApp();
    hooks.onPreview();
  };
  ($('adminBtn') as HTMLButtonElement).onclick = () => (location.href = '/admin');
  ($('loginBtn') as HTMLButtonElement).onclick = () => {
    document.body.dataset.screen = 'login';
  };

  // 已有有效 session 就直接進 App
  api.me().then(applyMe).catch(() => {});
}

/* Turnstile:token 到手前先擋住送出,免得使用者在手機上先按了按鈕
   (行動網路常拿到需要互動的挑戰,桌面多半無感通過)→ 送出空 token → 403。
   腳本載不到或挑戰卡住時,10 秒後仍放行,由伺服器決定並導回帶訊息的登入頁,
   不要讓按鈕永遠鎖死。 */
function mountTurnstile(siteKey: string, btn: HTMLButtonElement) {
  const label = btn.textContent ?? '使用 Google 登入';
  btn.disabled = true;
  btn.textContent = '驗證中…';
  const release = () => {
    btn.disabled = false;
    btn.textContent = label;
  };
  const w = window as unknown as Record<string, () => void>;
  w.__kikemuTsOk = release;
  w.__kikemuTsErr = () => {
    release();
    const e = $('loginErr');
    e.textContent = '人機驗證載入失敗,仍可嘗試登入;若持續失敗請重新整理。';
    e.classList.remove('hidden');
  };
  $('tsWidget').innerHTML =
    `<div class="cf-turnstile" data-sitekey="${siteKey}" data-theme="auto"` +
    ` data-callback="__kikemuTsOk" data-error-callback="__kikemuTsErr"` +
    ` data-expired-callback="__kikemuTsErr" data-timeout-callback="__kikemuTsErr"></div>`;
  const s = document.createElement('script');
  s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
  s.async = true;
  s.onerror = w.__kikemuTsErr;
  document.head.appendChild(s);
  setTimeout(() => btn.disabled && release(), 10_000);
}
