/**
 * 图源页面的共用逻辑：启动本机 Chrome、列出图源、打开图源并等待 Mermaid 渲染完成。
 * render.mjs（截图）、check-diagram-text.mjs（查图中文字）与 visio/export-svg.mjs（导出静态 SVG）共用。
 *
 * 依赖：playwright-core 与本机 Chrome。
 * - PLAYWRIGHT_CORE：playwright-core 的 index.mjs 路径；未设置时按包名 playwright-core 导入。
 * - CHROME_PATH：Chrome 可执行文件路径；未设置时在 macOS 上用默认安装路径，其他平台必须设置。
 */
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const MAC_CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const RENDER_TIMEOUT = 30000;

/** 加载 playwright-core 的 chromium；找不到时提示安装命令并退出 */
async function loadChromium() {
  if (process.env.PLAYWRIGHT_CORE) {
    return (await import(pathToFileURL(process.env.PLAYWRIGHT_CORE).href)).chromium;
  }
  try {
    return (await import('playwright-core')).chromium;
  } catch (error) {
    console.error('找不到 playwright-core。请在本地磁盘的工具目录执行 npm install mermaid@12.3.0 playwright-core@1.57.0，'
      + '再设置 PLAYWRIGHT_CORE=<工具目录>/node_modules/playwright-core/index.mjs');
    process.exit(2);
  }
}

/** Chrome 路径：CHROME_PATH 优先，macOS 用默认安装路径 */
function chromePath() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === 'darwin') return MAC_CHROME_PATH;
  console.error('请用环境变量 CHROME_PATH 指定 Chrome 可执行文件路径');
  process.exit(2);
}

/**
 * 列出图源目录下的 HTML 文件名（按名称排序），可按文件名关键字过滤。
 * @param {string} source_dir 图源目录
 * @param {string} [filter_keyword] 文件名关键字
 * @returns {string[]}
 */
export function listDiagramHtml(source_dir, filter_keyword) {
  return readdirSync(source_dir)
    .filter((name) => name.endsWith('.html'))
    .filter((name) => !filter_keyword || name.includes(filter_keyword))
    .sort();
}

/**
 * 启动无头 Chrome 并打开一个标签页。
 * @param {object} page_options 传给 browser.newPage 的选项（像素密度、视口）
 * @returns {Promise<{ browser: object, page: object }>}
 */
export async function launchDiagramPage(page_options) {
  const chromium = await loadChromium();
  const browser = await chromium.launch({ executablePath: chromePath(), headless: true });
  const page = await browser.newPage(page_options);
  return { browser, page };
}

/**
 * 打开一个图源并等待渲染完成；渲染失败或页面脚本报错时返回错误说明。
 * @param {object} page playwright 页面
 * @param {string} source_dir 图源目录
 * @param {string} html_name 图源文件名
 * @returns {Promise<string>} 空字符串表示成功，否则为错误说明
 */
export async function openDiagram(page, source_dir, html_name) {
  const page_error_list = [];
  const on_error = (error) => page_error_list.push(String(error));
  page.on('pageerror', on_error);
  await page.goto(pathToFileURL(join(source_dir, html_name)).href);
  try {
    await page.waitForFunction(() => document.body.dataset.rendered, null, { timeout: RENDER_TIMEOUT });
  } catch (error) {
    page_error_list.push('等待渲染超时');
  }
  const state = await page.evaluate(() => ({
    rendered: document.body.dataset.rendered,
    error: document.body.dataset.error || '',
  }));
  page.off('pageerror', on_error);
  if (state.rendered !== '1' || page_error_list.length > 0) {
    return state.error || page_error_list.join('; ') || '渲染失败';
  }
  return '';
}
