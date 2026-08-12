import type { Me, Pack } from './types';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public waitlist = false,
  ) {
    super(message);
  }
}

async function req(path: string, init?: RequestInit): Promise<any> {
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, data.error ?? `HTTP ${res.status}`, !!data.waitlist);
  }
  return data;
}

const post = (path: string, body: unknown) =>
  req(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });

export const api = {
  config: (): Promise<{ mode: 'oidc' | 'dev'; turnstileSiteKey: string | null;
  langs: { code: string; label: string }[];
  defaultLang: string;
  packLangs: { code: string; label: string }[] }> => req('/api/config'),
  me: (): Promise<Me> => req('/api/me'),
  login: (email: string): Promise<{ email: string }> => post('/api/login', { email }),
  logout: (): Promise<void> => post('/api/logout', {}),
  packs: (lang?: string): Promise<{ packs: Pack[] }> =>
    req('/api/packs' + (lang ? `?lang=${encodeURIComponent(lang)}` : '')),
};
