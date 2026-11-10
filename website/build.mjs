import { execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import MarkdownIt from 'markdown-it';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.dirname(here);
const manual = path.join(repo, 'manual');
export const base = process.env.SITE_BASE || '/';
if (!/^\/(?:[a-zA-Z0-9_-]+\/)*$/.test(base)) throw new Error('SITE_BASE must be a slash-delimited URL path.');
const origin = process.env.SITE_URL ? new URL(process.env.SITE_URL) : null;
if (origin && (!['https:', 'http:'].includes(origin.protocol) || origin.pathname !== '/' || origin.search || origin.hash || origin.username || origin.password)) {
  throw new Error('SITE_URL must be an HTTP(S) origin without a path or credentials.');
}
const sourceRoot = 'https://github.com/meerzulee/linux-psx';
const releases = `${sourceRoot}/releases`;
const desktopSite = 'https://omarchy.mrz.sh/';
const escape = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const slug = value => value.toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, '').trim().replace(/\s/g, '-');
// The publication allowlist. The table of contents stays last.
const manifest = [
  ['01-welcome-to-linux-psx.md', '', 'Welcome to Linux psX!'],
  ['02-compatibility.md', 'compatibility', 'Is My Console Supported?'],
  ['03-kernels.md', 'kernels', 'Kernels'],
  ['04-booting-a-kernel.md', 'booting-a-kernel', 'Booting a Kernel'],
  ['05-other-distributions.md', 'other-distributions', 'Other Distributions'],
  ['06-diagnostix.md', 'diagnostix', 'DiagnostiX'],
  ['07-results-and-reports.md', 'results-and-reports', 'Results and Reports'],
  ['08-mesa-and-video.md', 'mesa-and-video', 'Mesa and Video'],
  ['09-help-port-your-console.md', 'help-port-your-console', 'Help Port Your Console'],
  ['10-building-from-source.md', 'building-from-source', 'Building from Source'],
  ['11-contributing.md', 'contributing', 'Contributing'],
  ['12-legal-notice.md', 'legal-notice', 'Legal Notice'],
  ['README.md', 'toc', 'Table of Contents'],
];
const chapterCount = manifest.length - 1;
const filenames = (await fs.readdir(manual)).filter(name => /^\d+.*\.md$/.test(name)).sort();
if (filenames.join() !== manifest.slice(0, chapterCount).map(item => item[0]).join()) {
  throw new Error('The chapter allowlist must match the numbered manual chapters.');
}
const md = new MarkdownIt({ html: false, linkify: false, typographer: false });
const pages = await Promise.all(manifest.map(async ([file, route, title]) => {
  const source = path.join(manual, file);
  const text = (await fs.readFile(source, 'utf8')).split('\n<!-- manual-navigation -->')[0];
  const tokens = md.parse(text, {});
  const headings = new Set();
  const counts = new Map();
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i].type !== 'heading_open') continue;
    const name = slug(tokens[i + 1].content);
    const occurrence = counts.get(name) || 0;
    counts.set(name, occurrence + 1);
    const id = occurrence ? `${name}-${occurrence}` : name;
    tokens[i].attrSet('id', id);
    headings.add(id);
  }
  if (tokens.filter(token => token.type === 'heading_open' && token.tag === 'h1').length !== 1) {
    throw new Error(`${file} must have exactly one top-level heading.`);
  }
  const paragraphs = tokens.filter(token => token.type === 'inline').map(token => token.children?.map(child => child.content).join('') || token.content);
  const sections = tokens.flatMap((token, i) => token.type === 'heading_open' ? [tokens[i + 1].content] : []);
  return { file, source, route, title, text, tokens, headings, sections, body: paragraphs.join(' '), description: paragraphs[1]?.slice(0, 220) || title, url: base + (route ? route + '/' : '') };
}));
const bySource = new Map(pages.map(page => [page.source, page]));
const links = new Set();
const images = new Set();
let localLinks = 0;
async function rewrite(url, page, image = false) {
  if (/^(?:https?:|mailto:)/i.test(url)) return url;
  if (/^(?:[a-z]+:|\/\/|\/)/i.test(url)) throw new Error(`Unsupported link in ${page.file}: ${url}`);
  const [name, hash = ''] = url.split('#');
  const target = name ? path.resolve(path.dirname(page.source), decodeURIComponent(name)) : page.source;
  if (!target.startsWith(repo + path.sep)) throw new Error(`Link escapes the repository: ${url}`);
  const fragment = hash ? '#' + hash : '';
  const article = bySource.get(target);
  if (article) {
    if (hash && !article.headings.has(decodeURIComponent(hash))) throw new Error(`Missing heading in ${page.file}: ${url}`);
    localLinks++;
    return article.url + fragment;
  }
  const stat = await fs.stat(target).catch(() => null);
  if (!stat) throw new Error(`Missing target in ${page.file}: ${url}`);
  if (image) {
    if (!target.startsWith(path.join(manual, 'images') + path.sep) || !/\.(?:webp|png|jpe?g|svg)$/i.test(target)) {
      throw new Error(`Image must belong to manual/images: ${url}`);
    }
    images.add(target);
    return base + 'assets/images/' + path.basename(target);
  }
  const relative = path.relative(repo, target).split(path.sep).join('/');
  links.add(relative);
  return `${sourceRoot}/${stat.isDirectory() ? 'tree' : 'blob'}/master/${relative.split('/').map(encodeURIComponent).join('/')}${fragment}`;
}
async function visit(tokens, page) {
  for (const token of tokens) {
    if (token.type === 'link_open') token.attrSet('href', await rewrite(token.attrGet('href'), page));
    if (token.type === 'image') {
      token.attrSet('src', await rewrite(token.attrGet('src'), page, true));
      token.attrSet('loading', 'lazy');
      token.attrSet('decoding', 'async');
    }
    if (token.children) await visit(token.children, page);
  }
}
for (const page of pages) await visit(page.tokens, page);

// Source links resolve to GitHub, so a file that exists only in this working
// tree would pass the check above and still be a dead link on the site.
let published = null;
try {
  published = new Set(execFileSync('git', ['-C', repo, 'ls-tree', '-r', '-t', '--name-only', 'origin/master'], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }).split('\n'));
} catch {
  console.warn('WARNING: origin/master is unavailable, so source links were not checked against the published tree.');
}
if (published) {
  const unpublished = [...links].filter(link => !published.has(link));
  if (unpublished.length) throw new Error(`Not on origin/master, so the link would be dead: ${unpublished.join(', ')}`);
}

md.renderer.rules.table_open = () => '<div class="table-scroll" tabindex="0" role="region" aria-label="Reference table"><table>\n';
md.renderer.rules.table_close = () => '</table></div>\n';
// A status word in a code span renders as a labelled result; it stays readable as Markdown on GitHub.
const statusLabels = { working: 'Working', beta: 'Beta', untested: 'Untested', unavailable: 'Not available' };
const codeInline = md.renderer.rules.code_inline;
md.renderer.rules.code_inline = (tokens, i, options, env, self) => {
  const label = statusLabels[tokens[i].content];
  return label ? `<span class="status status-${tokens[i].content}">${label}</span>` : codeInline(tokens, i, options, env, self);
};

// LINUX PSX in Delta Corps Priest 1, as a static SVG; see BRANDING.md.
const wordmarkSvg = (await fs.readFile(path.join(here, 'assets/linux-psx.svg'), 'utf8')).trim();
const wordmark = `<div class="wordmark">${wordmarkSvg}</div><p class="manual-label">THE PLAYSTATION KERNEL MANUAL</p>`;
function template(page, index) {
  const chapters = pages.slice(0, chapterCount);
  const toc = pages[chapterCount];
  const previous = index > 0 && index < chapterCount ? chapters[index - 1] : null;
  const next = index < chapterCount - 1 ? chapters[index + 1] : null;
  const navigation = chapters.map((item, n) => `<li><a href="${item.url}"${item === page ? ' aria-current="page"' : ''}><span class="chapter-number">${String(n + 1).padStart(2, '0')}</span>${escape(item.title.replace(/!$/, ''))}</a></li>`).join('\n');
  const canonical = origin ? `<link rel="canonical" href="${escape(new URL(page.url, origin).href)}">` : '';
  return `<!doctype html>
<html lang="en" data-site-base="${base}">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escape(page.title)} — Linux psX</title>
<meta name="description" content="${escape(page.description)}">
<meta name="theme-color" content="#1a1b26">
${canonical}
<link rel="icon" href="${base}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="${base}assets/manual.css">
<script src="${base}assets/manual.js" defer></script>
</head>
<body>
<a class="skip-link" href="#content">Skip to content</a>
<header class="site-header"><a class="site-name" href="${base}" aria-label="Linux psX manual home">${wordmarkSvg}</a><nav aria-label="Project links"><a class="manual-link" href="${toc.url}">The manual</a><a href="${releases}">Releases <span aria-hidden="true">↗</span></a><a class="desktop-link" href="${desktopSite}">Omarchy psX <span aria-hidden="true">↗</span></a><a class="source-link" href="${sourceRoot}">GitHub <span aria-hidden="true">↗</span></a><button class="theme-toggle" type="button" hidden aria-label="Switch to light theme">◐</button></nav></header>
<div class="manual-layout">
<aside class="sidebar" aria-label="Manual navigation">
<button class="search-open" type="button" hidden><span>Search the manual</span><kbd>/</kbd></button>
<details class="chapter-panel" open><summary>Chapters <span>${chapterCount}</span></summary><nav aria-label="Chapters"><ol>${navigation}</ol><a class="contents-link" href="${toc.url}"${page === toc ? ' aria-current="page"' : ''}>Table of Contents</a></nav></details>
<p class="sidebar-note">Kernels and diagnostics for PlayStation.<br>Want a desktop? See <a href="${desktopSite}">Omarchy psX</a>.</p>
</aside>
<main id="content" tabindex="-1">
<div class="masthead">${wordmark}</div>
<article>${md.renderer.render(page.tokens, md.options, {})}</article>
<nav class="pagination" aria-label="Chapter pagination">${previous ? `<a href="${previous.url}" rel="prev"><span>Previous</span>← ${escape(previous.title)}</a>` : '<span></span>'}<a class="pagination-contents" href="${toc.url}">Contents</a>${next ? `<a class="pagination-next" href="${next.url}" rel="next"><span>Next</span>${escape(next.title)} →</a>` : '<span></span>'}</nav>
<footer class="page-footer"><p>An independent community project. <a href="${chapters[chapterCount - 1].url}">Licenses &amp; credits</a>.</p><p><a href="${releases}">Releases on GitHub ↗</a> · <a href="${sourceRoot}">Source on GitHub ↗</a></p></footer>
</main>
</div>
<dialog class="search-dialog" aria-labelledby="search-title"><form method="dialog"><div class="search-heading"><h2 id="search-title">Search the manual</h2><button aria-label="Close search">Esc</button></div></form><label class="sr-only" for="manual-search">Search words</label><input type="search" id="manual-search" placeholder="Try ethernet, firmware, report…" autocomplete="off" spellcheck="false"><p id="search-status" role="status">Search all chapters.</p><ul id="search-results" aria-label="Search results"></ul></dialog>
</body></html>\n`;
}
const html = pages.map(template);
const report = `${pages.length} pages, ${localLinks} internal links/anchors, ${images.size} manual images; ${links.size} source references remain external.`;
if (process.argv.includes('--check')) {
  console.log(`PASS: ${report}`);
} else {
  const output = path.join(here, 'dist');
  const temporary = path.join(here, 'dist-next');
  await fs.rm(temporary, { recursive: true, force: true });
  await fs.mkdir(path.join(temporary, 'assets/images'), { recursive: true });
  for (let i = 0; i < pages.length; i++) {
    const folder = path.join(temporary, pages[i].route);
    await fs.mkdir(folder, { recursive: true });
    await fs.writeFile(path.join(folder, 'index.html'), html[i]);
  }
  for (const name of ['manual.css', 'manual.js', 'favicon.svg', 'linux-psx.svg', 'LICENSE-omarchy.txt']) await fs.copyFile(path.join(here, 'assets', name), path.join(temporary, 'assets', name));
  await fs.copyFile(path.join(here, '_headers'), path.join(temporary, '_headers'));
  await fs.copyFile(path.join(here, 'LICENSE'), path.join(temporary, 'LICENSE.txt'));
  for (const image of images) await fs.copyFile(image, path.join(temporary, 'assets/images', path.basename(image)));
  await fs.writeFile(path.join(temporary, 'search-index.json'), JSON.stringify(pages.map(({ title, url, body, sections }) => ({ title, url, body, sections }))) + '\n');
  await fs.writeFile(path.join(temporary, '.nojekyll'), '');
  if (origin) {
    await fs.writeFile(path.join(temporary, 'sitemap.xml'), `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${pages.map(page => `<url><loc>${escape(new URL(page.url, origin).href)}</loc></url>`).join('')}</urlset>\n`);
    await fs.writeFile(path.join(temporary, 'robots.txt'), `User-agent: *\nAllow: /\nSitemap: ${new URL(base + 'sitemap.xml', origin).href}\n`);
  }
  await fs.rm(output, { recursive: true, force: true });
  await fs.rename(temporary, output);
  console.log(`Built ${report}\nOutput: ${output}`);
}
