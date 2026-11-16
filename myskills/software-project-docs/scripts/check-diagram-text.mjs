/**
 * 检查图中可见文字：渲染每个图源后取 SVG 中显示的文字，找出不允许的英文词（英文标识、技术产品名等）。
 * 图源里的节点编号本身是英文，只能检查渲染后可见的文字。
 *
 * 用法：node check-diagram-text.mjs <图源目录> [文件名前缀] [--allow 词,词]
 * --allow 追加项目专有的允许词。渲染失败或含不允许英文的图逐个列出，并以非零码退出。
 * 依赖：同 render.mjs。
 */
import { resolve } from 'node:path';
import { launchDiagramPage, listDiagramHtml, openDiagram } from './diagram-page.mjs';

/** 图中允许出现的英文：UML 构造型与片段关键字、通行缩写、平台与文件格式名 */
const ALLOWED_WORD_LIST = [
  'actor', 'system', 'component', 'device', 'execution', 'environment', 'enumeration', 'abstract',
  'include', 'extend', 'interface', 'package', 'derived', 'generated', 'alt', 'opt', 'loop', 'break', 'par',
  'else', 'end', 'UDP', 'DDS', 'QoS', 'BCD', 'CRC', 'IP', 'IPv4', 'IPv6', 'C', 'C++', 'Word',
  'Excel', 'JSON', 'HTTP', 'TCP', 'LSB', 'MSB', 'Linux', 'Windows', 'x86_64', 'aarch64', 'Chrome', 'Edge', 'ID',
  'XQ', 'N', 'docx', 'xlsx', 'zip', 'ZIP', 'MB', 'Ctrl', 'Enter', 'K', 'CSV',
];

const argument_list = process.argv.slice(2);
const allow_index = argument_list.indexOf('--allow');
const extra_word_list = allow_index >= 0
  ? (argument_list.splice(allow_index, 2)[1] || '').split(',').map((w) => w.trim()).filter(Boolean)
  : [];
const [source_dir_arg, prefix = ''] = argument_list;
if (!source_dir_arg || argument_list.length > 2) {
  console.error('用法：node check-diagram-text.mjs <图源目录> [文件名前缀] [--allow 词,词]');
  process.exit(2);
}
const allowed_word_set = new Set([...ALLOWED_WORD_LIST, ...extra_word_list]);
const source_dir = resolve(source_dir_arg);
const html_list = listDiagramHtml(source_dir).filter((name) => name.startsWith(prefix));
const { browser, page } = await launchDiagramPage({ viewport: { width: 1600, height: 1200 } });
let failure_count = 0;

for (const html_name of html_list) {
  const error_text = await openDiagram(page, source_dir, html_name);
  if (error_text) {
    failure_count += 1;
    console.log(`${html_name}: 渲染失败：${error_text}`);
    continue;
  }
  const text_list = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.diagram svg text, .diagram svg foreignObject'))
      .map((element) => element.textContent || ''),
  );
  const word_set = new Set();
  for (const text of text_list) {
    for (const match of text.matchAll(/[A-Za-z][A-Za-z0-9_+]*(?:[./-][A-Za-z0-9_+]+)*/g)) {
      const word = match[0];
      if (!allowed_word_set.has(word) && !word.startsWith('XQ')) word_set.add(word);
    }
  }
  if (word_set.size > 0) {
    failure_count += 1;
    console.log(`${html_name}: ${[...word_set].join('、')}`);
  }
}

await browser.close();
console.log(failure_count === 0 ? `通过，共 ${html_list.length} 张` : `${failure_count} 张图未通过（共 ${html_list.length} 张）`);
process.exit(failure_count === 0 ? 0 : 1);
