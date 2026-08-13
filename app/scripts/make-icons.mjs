// kikemu PWA icon 產生器(執行:node scripts/make-icons.mjs;需 chromium 與 python3+PIL)
// 家族設計語言(manemu 紅・說 / sukemu 藍・看 / kikemu 琥珀・聽):
//   白底基形 + 扁平物件疊放 + 主色配一個兄弟色。
//   kikemu = 耳機(聽)戴著字幕卡(輸出),耳罩用 manemu 紅補完色三角。
// 產出:icon-512 / icon-192(圓角方、透明角)、icon-maskable-512(滿版白底,
//   glyph 縮在 76% 安全區內)、apple-touch-icon(180,滿版白底,iOS 自己裁圓角)。
// headless chromium 的視窗高會被 UI 吃掉 ~88px,所以開大視窗、截完用 PIL 裁切。
import { writeFileSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const AMBER = '#B8860B';
const RED = '#C0392B'; // manemu 紅,家族色三角:紅(說)藍(看)琥珀(聽)
const WHITE = '#FFFFFF';

// glyph 本體(viewBox 512,置中設計;交給外層決定底形與縮放)
// 音源點在右上,三道弧朝左下傳;字幕卡在左下前景,微傾 -4° 是家族的玩心
const glyph = `
  <path d="M 128 236 A 128 128 0 0 1 384 236" fill="none"
        stroke="${AMBER}" stroke-width="38" stroke-linecap="round"/>
  <rect x="96" y="210" width="64" height="102" rx="32" fill="${RED}"/>
  <rect x="352" y="210" width="64" height="102" rx="32" fill="${RED}"/>
  <rect x="86" y="300" width="340" height="146" rx="40" fill="${AMBER}"/>
  <line x1="136" y1="348" x2="336" y2="348" stroke="${WHITE}" stroke-width="30" stroke-linecap="round"/>
  <line x1="136" y1="402" x2="262" y2="402" stroke="${WHITE}" stroke-width="30" stroke-linecap="round"/>
`;

// 弧線畫法說明:圓心 = 音源點(370,152),朝左下開口(角度以「向左為 0°」計)。
// 上面用參數式算端點,rotate 微調指向,肉眼校準過:弧要「指向」字幕卡。

function svg({ size, bg, pad }) {
  // pad:glyph 縮到中央的比例(maskable 安全區要 <80%)
  const s = 512 * (1 - pad * 2);
  const inner = `<g transform="translate(${512 * pad} ${512 * pad}) scale(${s / 512})">${glyph}</g>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 512 512">${bg}${inner}</svg>`;
}

const roundedBg = `<rect width="512" height="512" rx="115" fill="${WHITE}"/>`; // sukemu 同款 22.5% 圓角
const fullBg = `<rect width="512" height="512" fill="${WHITE}"/>`;

const out = resolve(import.meta.dirname, '../public/icons');
mkdirSync(out, { recursive: true });

const variants = [
  { file: 'icon-512.png', size: 512, bg: roundedBg, pad: 0.06, alpha: true },
  { file: 'icon-192.png', size: 192, bg: roundedBg, pad: 0.06, alpha: true },
  { file: 'icon-maskable-512.png', size: 512, bg: fullBg, pad: 0.12, alpha: false },
  { file: 'apple-touch-icon.png', size: 180, bg: fullBg, pad: 0.1, alpha: false },
];

for (const v of variants) {
  const html = `<!doctype html><meta charset="utf-8"><style>*{margin:0}body{background:transparent}</style>${svg(v)}`;
  const page = resolve(out, v.file + '.html');
  writeFileSync(page, html);
  execFileSync('/opt/pw-browsers/chromium', [
    '--headless', '--no-sandbox', '--disable-gpu', '--hide-scrollbars',
    `--screenshot=${resolve(out, v.file)}`,
    `--window-size=${v.size + 200},${v.size + 200}`, // UI 會吃高度,開大再裁
    ...(v.alpha ? ['--default-background-color=00000000'] : []),
    `file://${page}`,
  ], { stdio: 'pipe' });
  execFileSync('python3', ['-c',
    `from PIL import Image; p='${resolve(out, v.file)}'; Image.open(p).convert('RGBA').crop((0,0,${v.size},${v.size})).save(p)`]);
  console.log(v.file, 'ok');
}
// 截圖用的暫存 html 不留下
import { unlinkSync } from 'node:fs';
for (const v of variants) unlinkSync(resolve(out, v.file + '.html'));
