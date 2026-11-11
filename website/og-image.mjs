// Renders assets/og-image.png, the 1200x630 card that link previews show.
// Needs Chromium or Chrome; set CHROME to its path if it isn't on PATH.
// Run it after the wordmark changes: `bun og-image.mjs`. See BRANDING.md.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const wordmark = (await fs.readFile(path.join(here, 'assets/linux-psx.svg'), 'utf8')).trim();
const output = path.join(here, 'assets/og-image.png');

// LINUX is 0-765 and PSX starts at 855 in the 1470-unit wordmark, so a clip at
// 810 colors the two words like the Omarchy psX avatar: blue, then purple.
const page = `<!doctype html><meta charset="utf-8"><style>
html, body { margin: 0; width: 1200px; height: 630px; overflow: hidden; background: #1a1b26; }
body { font-family: "JetBrainsMono Nerd Font", "JetBrains Mono", "Liberation Mono", monospace; color: #c8d0ed; }
.frame { position: absolute; inset: 34px; border: 2px solid #7aa2f7; border-radius: 12px; }
.label { position: absolute; top: 34px; left: 44px; font-size: 17px; letter-spacing: .24em; color: #9ba7cf; }
.mark { position: absolute; left: 50%; top: 170px; width: 900px; height: 156px; transform: translateX(-50%); }
.mark > div { position: absolute; inset: 0; }
.mark svg { width: 100%; height: auto; display: block; }
.mark .linux { color: #7aa2f7; clip-path: inset(0 44.9% 0 0); }
.mark .psx { color: #bb9af7; clip-path: inset(0 0 0 55.1%); }
.tagline { position: absolute; top: 372px; width: 100%; text-align: center; font-size: 33px; }
.foot { position: absolute; left: 44px; right: 44px; bottom: 30px; display: flex; justify-content: space-between; font-size: 21px; }
.domain { color: #9abaff; }
.topics { color: #9ba7cf; }
</style>
<div class="frame">
<div class="label">THE PLAYSTATION KERNEL MANUAL</div>
<div class="mark"><div class="linux">${wordmark}</div><div class="psx">${wordmark}</div></div>
<div class="tagline">Linux kernels and hardware tests for the PS4</div>
<div class="foot"><span class="domain">linux.mrz.sh</span><span class="topics">Kernels · DiagnostiX · Compatibility</span></div>
</div>`;

const browsers = [process.env.CHROME, 'chromium', 'chromium-browser', 'google-chrome-stable', 'google-chrome'].filter(Boolean);
const work = await fs.mkdtemp(path.join(os.tmpdir(), 'og-image-'));
try {
  const card = path.join(work, 'card.html');
  await fs.writeFile(card, page);
  const args = ['--headless', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', '--window-size=1200,630', `--user-data-dir=${path.join(work, 'profile')}`, `--screenshot=${output}`, `file://${card}`];
  const browser = browsers.find(name => { try { execFileSync(name, ['--version'], { stdio: 'ignore' }); return true; } catch { return false; } });
  if (!browser) throw new Error('Chromium or Chrome is required. Set CHROME to its path.');
  execFileSync(browser, args, { stdio: 'ignore' });
  console.log(`Wrote ${output}`);
} finally {
  await fs.rm(work, { recursive: true, force: true });
}
