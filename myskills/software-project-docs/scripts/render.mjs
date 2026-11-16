/**
 * 把图源目录下的 HTML 逐个用本机 Chrome 打开，等 Mermaid 渲染完成后截取 .diagram 中 Mermaid 输出的 svg 元素
 * （与 SVG 视框、即 Visio 页面同大，Word 中双击编辑时图不变形），以同名 PNG（2 倍像素密度）写入图片目录，
 * 并输出每张图的 CSS 宽高。
 * 宽度超过 1500 或宽高比大于 2.2 时打印警告（不影响退出码）；任一图渲染失败时列出出错文件并以非零码退出。
 *
 * 用法：node render.mjs <图源目录> <图片目录> [文件名关键字]
 * 依赖：playwright-core 与本机 Chrome，见 diagram-page.mjs（环境变量 PLAYWRIGHT_CORE、CHROME_PATH）。
 */
import { mkdirSync } from 'node:fs';
import { join, resolve, basename } from 'node:path';
import { launchDiagramPage, listDiagramHtml, openDiagram } from './diagram-page.mjs';

const MAX_WIDTH = 1500;
const MAX_ASPECT = 2.2;

const [, , source_dir_arg, image_dir_arg, filter_keyword] = process.argv;
if (!source_dir_arg || !image_dir_arg) {
  console.error('用法：node render.mjs <图源目录> <图片目录> [文件名关键字]');
  process.exit(2);
}
const source_dir = resolve(source_dir_arg);
const image_dir = resolve(image_dir_arg);
mkdirSync(image_dir, { recursive: true });
const html_list = listDiagramHtml(source_dir, filter_keyword);

const { browser, page } = await launchDiagramPage({ deviceScaleFactor: 2, viewport: { width: 1600, height: 1200 } });
const failure_list = [];
const warning_list = [];

for (const html_name of html_list) {
  const error_text = await openDiagram(page, source_dir, html_name);
  if (error_text) {
    failure_list.push(`${html_name}: ${error_text}`);
    continue;
  }
  const png_path = join(image_dir, basename(html_name, '.html') + '.png');
  const diagram = page.locator('.diagram pre.mermaid > svg');
  await diagram.screenshot({ path: png_path });
  const box = await diagram.boundingBox();
  const width = Math.round(box.width);
  const height = Math.round(box.height);
  console.log(`已截图 ${basename(png_path)}\t${width}×${height}`);
  if (width > MAX_WIDTH) warning_list.push(`${html_name}: 宽 ${width}，超过 ${MAX_WIDTH}`);
  if (width / height > MAX_ASPECT) warning_list.push(`${html_name}: 宽高比 ${(width / height).toFixed(2)}，大于 ${MAX_ASPECT}`);
}

await browser.close();
if (warning_list.length > 0) {
  console.log('警告（换方向或拆图）：\n' + warning_list.join('\n'));
}
if (failure_list.length > 0) {
  console.error('渲染失败：\n' + failure_list.join('\n'));
  process.exit(1);
}
console.log(`完成，共 ${html_list.length} 张`);
