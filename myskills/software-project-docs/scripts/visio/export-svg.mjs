/**
 * 把图源的 Mermaid 渲染结果压平成静态 SVG，供同目录的 html_to_vsdx.py 转成 Visio。
 *
 * 每个图源输出一个同名 HTML，内含图源的 <title> 与一个 <svg>：
 * - 全部图元换算到 SVG 根坐标系，不带 transform，保留原 viewBox；
 * - 曲线路径按长度采样后判形：贴合内接椭圆的写成 ellipse，贴合外框（可带圆角）的写成 rect，
 *   贴合内接菱形的写成四点 polygon，其余写成 polygon（闭合）或 polyline（不闭合）；
 * - 相邻且几何相同的填充、描边图元合并成一个，与矩形边框重合的无箭头折线丢弃；
 * - foreignObject 与 text 中的文字逐字符取位置，一个标签一个 text，每行一个 tspan；
 *   未写对齐方式的标签按所在方框判断是否居中；
 * - marker 按登记表写成 url(#箭头类型)，未登记的报错；连线端点延长到箭头尖端，两端都能落到
 *   不同方框时，把离边界 8 像素以内的端点吸附到边界上；时序图序号圆点写成实心小圆。
 *
 * 用法：node export-svg.mjs <图源目录> <输出目录> [文件名关键字]
 * 输出页面的 body 带 data-rendered，可直接用 render.mjs 截图核对。
 * 依赖：同 render.mjs（共用上一级目录的 diagram-page.mjs）。
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { launchDiagramPage, listDiagramHtml, openDiagram } from '../diagram-page.mjs';

/** Mermaid marker id（去掉 mermaid-<数字> 前缀）按画出的形状登记的箭头类型 */
const MARKER_TYPE_MAP = {
  'flowchart-v2-pointEnd': 'filled-triangle',
  'flowchart-v2-pointStart': 'filled-triangle',
  'usecase-pointEnd': 'filled-triangle',
  arrowhead: 'filled-triangle',
  'classDiagram-dependencyStart': 'filled-triangle',
  'classDiagram-dependencyEnd': 'filled-triangle',
  'stateDiagram-barbEnd': 'filled-triangle',
  'classDiagram-extensionStart': 'hollow-triangle',
  'classDiagram-extensionEnd': 'hollow-triangle',
  'usecase-extensionEnd': 'hollow-triangle',
  'classDiagram-compositionStart': 'filled-diamond',
  'classDiagram-compositionEnd': 'filled-diamond',
  'classDiagram-aggregationStart': 'hollow-diamond',
  'classDiagram-aggregationEnd': 'hollow-diamond',
  sequencenumber: 'filled-circle',
};

/** 浏览器预览用的箭头定义（转换脚本跳过 defs，只看 marker 的 id） */
const PREVIEW_MARKER_DEFS = [
  '<marker id="filled-triangle" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#333333"/></marker>',
  '<marker id="hollow-triangle" viewBox="0 0 18 14" refX="18" refY="7" markerWidth="18" markerHeight="14" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M1 1L18 7L1 13Z" fill="#ffffff" stroke="#333333"/></marker>',
  '<marker id="filled-diamond" viewBox="0 0 18 14" refX="18" refY="7" markerWidth="18" markerHeight="14" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M1 7L9 1L18 7L9 13Z" fill="#333333"/></marker>',
  '<marker id="hollow-diamond" viewBox="0 0 18 14" refX="18" refY="7" markerWidth="18" markerHeight="14" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M1 7L9 1L18 7L9 13Z" fill="#ffffff" stroke="#333333"/></marker>',
  '<marker id="open-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0 0L10 5L0 10" fill="none" stroke="#333333"/></marker>',
  '<marker id="filled-circle" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse"><circle cx="5" cy="5" r="5" fill="#333333"/></marker>',
].join('');

const SHAPE_TOLERANCE = 1.5;
const SIMPLIFY_TOLERANCE = 0.5;
const GLUE_TOLERANCE = 4;
const SNAP_DISTANCE = 8;
const BASELINE_OFFSET = 0.31;

/**
 * 在页面中收集图元：形状给出根坐标系下的几何与样式，路径给出采样点列，文字给出逐行位置。
 * 本函数整体传入浏览器执行，只能使用函数内部定义的内容。
 * @param {Record<string, string>} marker_type_map 箭头登记表
 * @returns {{ view_box: string, width: number, height: number, record_list: object[] }}
 */
function collectDiagramInPage(marker_type_map) {
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const SKIP_TAG_LIST = ['defs', 'marker', 'clipPath', 'mask', 'pattern', 'symbol', 'style', 'title', 'desc',
    'linearGradient', 'radialGradient', 'metadata'];
  const svg = document.querySelector('.diagram svg');
  if (!svg) throw new Error('页面里没有 svg');
  const root_inverse = svg.getScreenCTM().inverse();
  const temp_path = document.createElementNS(SVG_NS, 'path');
  temp_path.setAttribute('visibility', 'hidden');
  svg.appendChild(temp_path);
  const record_list = [];

  /** 元素坐标到根坐标的矩阵 */
  function toRootMatrix(el) {
    return root_inverse.multiply(el.getScreenCTM());
  }

  /** 用矩阵变换一个点 */
  function applyMatrix(m, x, y) {
    return [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f];
  }

  /** 矩阵的平均缩放 */
  function matrixScale(m) {
    return Math.sqrt(Math.abs(m.a * m.d - m.b * m.c));
  }

  /** 元素及其祖先的不透明度连乘 */
  function effectiveOpacity(el) {
    let opacity = 1;
    for (let node = el; node && node !== svg.parentElement; node = node.parentElement) {
      opacity *= parseFloat(getComputedStyle(node).opacity);
    }
    return opacity;
  }

  /** 渐变填充取第一个色标 */
  function gradientColor(value) {
    const id = value.replace(/^url\(["']?#/, '').replace(/["']?\)$/, '');
    const stop = svg.querySelector('#' + CSS.escape(id) + ' stop');
    if (!stop) throw new Error(`无法解析的填充：${value}`);
    return getComputedStyle(stop).stopColor;
  }

  /** 计算样式颜色转十六进制；透明为 none，半透明与白色混合 */
  function parseColor(value, alpha) {
    if (!value || value === 'none') return 'none';
    if (value.startsWith('url(')) return parseColor(gradientColor(value), alpha);
    const match = value.match(/^rgba?\(([^)]*)\)$/);
    if (!match) throw new Error(`无法解析的颜色：${value}`);
    const part_list = match[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    const total_alpha = (part_list.length > 3 ? part_list[3] : 1) * alpha;
    if (total_alpha <= 0.001) return 'none';
    return '#' + part_list.slice(0, 3)
      .map((v) => Math.round(v * total_alpha + 255 * (1 - total_alpha)).toString(16).padStart(2, '0'))
      .join('');
  }

  /** 虚线数组按缩放换算；没有间隔的视为实线 */
  function parseDash(value, scale) {
    if (!value || value === 'none') return '';
    let value_list = value.split(/[\s,]+/).filter(Boolean).map((v) => parseFloat(v) * scale);
    if (value_list.length % 2 === 1) value_list = value_list.concat(value_list);
    if (value_list.every((v, i) => i % 2 === 0 || v <= 0)) return '';
    return value_list.map((v) => +v.toFixed(2)).join(' ');
  }

  /** 填充、描边、线宽与虚线 */
  function paintStyle(el, scale) {
    const cs = getComputedStyle(el);
    const opacity = effectiveOpacity(el);
    const stroke_width = parseFloat(cs.strokeWidth) * scale;
    return {
      fill: parseColor(cs.fill, opacity * parseFloat(cs.fillOpacity)),
      stroke: stroke_width > 0 ? parseColor(cs.stroke, opacity * parseFloat(cs.strokeOpacity)) : 'none',
      stroke_width: +stroke_width.toFixed(2),
      dash: parseDash(cs.strokeDasharray, scale),
    };
  }

  /** 读取 marker：返回箭头类型、尖端到端点的距离（序号圆点返回半径） */
  function resolveMarker(el, attr_name, scale) {
    const value = el.getAttribute(attr_name);
    if (!value || value === 'none') return null;
    const id = value.replace(/^url\(["']?#/, '').replace(/["']?\)$/, '');
    const type = marker_type_map[id.replace(/^mermaid-\d+[_-]/, '')];
    if (!type) throw new Error(`未登记的箭头 marker：${id}`);
    const marker = svg.querySelector('#' + CSS.escape(id));
    if (!marker || !marker.firstElementChild) throw new Error(`找不到 marker 定义：${id}`);
    const view_box = marker.viewBox.baseVal;
    const unit = marker.markerUnits.baseVal === 1 ? 1 : parseFloat(getComputedStyle(el).strokeWidth);
    const ratio = view_box && view_box.width > 0 ? marker.markerWidth.baseVal.value / view_box.width : 1;
    const factor = unit * ratio * scale;
    const box = marker.firstElementChild.getBBox();
    const ref_x = marker.refX.baseVal.value;
    const ref_y = marker.refY.baseVal.value;
    if (type === 'filled-circle') {
      if (Math.abs(box.x + box.width / 2 - ref_x) > 0.5 || Math.abs(box.y + box.height / 2 - ref_y) > 0.5) {
        throw new Error(`圆点 marker 的圆心不在参考点上：${id}`);
      }
      return { type, radius: (box.width / 2) * factor, fill: parseColor(getComputedStyle(marker.firstElementChild).fill, 1) };
    }
    const reverse = attr_name === 'marker-start' && marker.getAttribute('orient') === 'auto-start-reverse';
    const tip = attr_name === 'marker-end' || reverse ? box.x + box.width - ref_x : ref_x - box.x;
    return { type, tip_length: Math.max(0, tip) * factor };
  }

  /** 路径拆成子路径；每段改写成以绝对 M 开头的独立路径 */
  function splitSubpaths(d) {
    const ARG_COUNT = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0 };
    const token_list = d.match(/[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g) || [];
    const subpath_list = [];
    let x = 0, y = 0, start_x = 0, start_y = 0, cmd = null, current = null, i = 0;
    while (i < token_list.length) {
      const token = token_list[i];
      if (/^[A-Za-z]$/.test(token)) {
        cmd = token;
        i += 1;
        if (cmd === 'Z' || cmd === 'z') {
          if (current) {
            current.closed = true;
            subpath_list.push(current);
            current = null;
          }
          x = start_x;
          y = start_y;
        }
        continue;
      }
      if (!cmd) throw new Error(`无法解析的路径：${d.slice(0, 40)}`);
      const upper = cmd.toUpperCase();
      const count = ARG_COUNT[upper];
      const arg_list = token_list.slice(i, i + count).map(Number);
      i += count;
      const relative = cmd !== upper;
      if (upper === 'M') {
        if (current) subpath_list.push(current);
        x = relative ? x + arg_list[0] : arg_list[0];
        y = relative ? y + arg_list[1] : arg_list[1];
        start_x = x;
        start_y = y;
        current = { start: [x, y], part_list: [], closed: false };
        cmd = relative ? 'l' : 'L';
        continue;
      }
      if (!current) current = { start: [x, y], part_list: [], closed: false };
      current.part_list.push(cmd + ' ' + arg_list.join(' '));
      if (upper === 'H') x = relative ? x + arg_list[0] : arg_list[0];
      else if (upper === 'V') y = relative ? y + arg_list[0] : arg_list[0];
      else {
        x = relative ? x + arg_list[count - 2] : arg_list[count - 2];
        y = relative ? y + arg_list[count - 1] : arg_list[count - 1];
      }
    }
    if (current) subpath_list.push(current);
    return subpath_list
      .filter((s) => s.part_list.length > 0)
      .map((s) => ({ d: `M ${s.start[0]} ${s.start[1]} ${s.part_list.join(' ')}${s.closed ? ' Z' : ''}`, closed: s.closed }));
  }

  /** 按长度约每 1 像素采样一次，返回根坐标点列 */
  function samplePath(d, m) {
    temp_path.setAttribute('d', d);
    const length = temp_path.getTotalLength();
    const count = Math.min(5000, Math.max(72, Math.ceil(length)));
    const point_list = [];
    for (let k = 0; k <= count; k += 1) {
      const p = temp_path.getPointAtLength((length * k) / count);
      point_list.push(applyMatrix(m, p.x, p.y));
    }
    return point_list;
  }

  /** 文字背景色：从文字所在元素向上找到容器为止 */
  function textBackground(el, container) {
    for (let node = el; node && node !== container; node = node.parentElement) {
      const bg = parseColor(getComputedStyle(node).backgroundColor, 1);
      if (bg !== 'none') return bg;
    }
    return '';
  }

  /** 逐字符取位置，按纵向中心分行 */
  function collectText(container, is_html) {
    const scale = matrixScale(toRootMatrix(container));
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const line_list = [];
    let anchor = '';
    let bg = '';
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (!parent || !parent.checkVisibility({ visibilityProperty: true })) continue;
      const cs = getComputedStyle(parent);
      const size = parseFloat(cs.fontSize) * scale;
      for (let k = 0; k < node.data.length; k += 1) {
        const range = document.createRange();
        range.setStart(node, k);
        range.setEnd(node, k + 1);
        const rect = range.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        const corner_list = [[rect.left, rect.top], [rect.right, rect.bottom]]
          .map(([cx, cy]) => new DOMPoint(cx, cy).matrixTransform(root_inverse));
        const ch = node.data[k];
        const char = {
          ch: /\s/.test(ch) ? ' ' : ch,
          left: Math.min(corner_list[0].x, corner_list[1].x),
          right: Math.max(corner_list[0].x, corner_list[1].x),
          center: (corner_list[0].y + corner_list[1].y) / 2,
          size,
          color: parseColor(is_html ? cs.color : cs.fill, 1),
          bold: parseInt(cs.fontWeight, 10) >= 600,
        };
        if (char.ch === ' ') {
          if (line_list.length) line_list[line_list.length - 1].char_list.push(char);
          continue;
        }
        if (!anchor) {
          const align = is_html ? cs.textAlign : cs.textAnchor;
          anchor = { center: 'middle', middle: 'middle', right: 'end', end: 'end' }[align] || 'start';
          bg = is_html ? textBackground(parent, container) : '';
        }
        const last = line_list[line_list.length - 1];
        if (last && Math.abs(char.center - last.center) <= 0.5 * size) last.char_list.push(char);
        else line_list.push({ center: char.center, char_list: [char] });
      }
    }
    const result_list = line_list.map((line) => {
      const solid_list = line.char_list.filter((c) => c.ch !== ' ');
      const first = solid_list[0];
      return {
        text: line.char_list.map((c) => c.ch).join('').replace(/\s+/g, ' ').trim(),
        left: Math.min(...solid_list.map((c) => c.left)),
        right: Math.max(...solid_list.map((c) => c.right)),
        center: solid_list.reduce((sum, c) => sum + c.center, 0) / solid_list.length,
        size: Math.max(...solid_list.map((c) => c.size)),
        color: first.color,
        bold: first.bold,
      };
    }).filter((line) => line.text);
    if (!result_list.length) return null;
    // 未写对齐且容器正好包住文字时，对齐方式由标签摆放的位置决定，交给后续按所在方框判断
    const widest = Math.max(...result_list.map((line) => line.right - line.left));
    const align_free = is_html && anchor === 'start' && container.width.baseVal.value * scale - widest <= 2;
    return { kind: 'text', anchor, bg, align_free, line_list: result_list };
  }

  /** 基本图形与路径的记录 */
  function collectShape(el, tag) {
    const m = toRootMatrix(el);
    const scale = matrixScale(m);
    const style = paintStyle(el, scale);
    if (style.fill === 'none' && style.stroke === 'none') return null;
    const marker = {
      marker_start: resolveMarker(el, 'marker-start', scale),
      marker_end: resolveMarker(el, 'marker-end', scale),
    };
    if (tag === 'rect') {
      const w = el.width.baseVal.value, h = el.height.baseVal.value;
      if (w < 0.5 || h < 0.5) return null;
      const x = el.x.baseVal.value, y = el.y.baseVal.value;
      const corner_list = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]].map(([px, py]) => applyMatrix(m, px, py));
      if (Math.abs(m.b) > 1e-6 || Math.abs(m.c) > 1e-6) return { kind: 'polygon', point_list: corner_list, ...style };
      const xs = corner_list.map((p) => p[0]), ys = corner_list.map((p) => p[1]);
      const rx = (el.rx.baseVal.value || el.ry.baseVal.value) * scale;
      return { kind: 'rect', x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs),
        h: Math.max(...ys) - Math.min(...ys), rx, ...style };
    }
    if (tag === 'circle' || tag === 'ellipse') {
      const cx = el.cx.baseVal.value, cy = el.cy.baseVal.value;
      const rx = tag === 'circle' ? el.r.baseVal.value : el.rx.baseVal.value;
      const ry = tag === 'circle' ? el.r.baseVal.value : el.ry.baseVal.value;
      if (rx <= 0 || ry <= 0) return null;
      const [x, y] = applyMatrix(m, cx, cy);
      return { kind: 'ellipse', cx: x, cy: y, rx: rx * Math.hypot(m.a, m.b), ry: ry * Math.hypot(m.c, m.d), ...style };
    }
    if (tag === 'line') {
      const point_list = [applyMatrix(m, el.x1.baseVal.value, el.y1.baseVal.value),
        applyMatrix(m, el.x2.baseVal.value, el.y2.baseVal.value)];
      return { kind: 'path', subpath_list: [{ point_list, closed: false }], ...style, ...marker };
    }
    if (tag === 'polygon' || tag === 'polyline') {
      const point_list = [...el.points].map((p) => applyMatrix(m, p.x, p.y));
      if (tag === 'polygon') return { kind: 'polygon', point_list, ...style };
      return { kind: 'path', subpath_list: [{ point_list, closed: false }], ...style, ...marker };
    }
    const subpath_list = splitSubpaths(el.getAttribute('d') || '')
      .map((s) => ({ point_list: samplePath(s.d, m), closed: s.closed }));
    return subpath_list.length ? { kind: 'path', subpath_list, ...style, ...marker } : null;
  }

  /** 按文档顺序遍历 */
  function walk(el) {
    for (const child of el.children) {
      const tag = child.tagName;
      if (SKIP_TAG_LIST.includes(tag) || child === temp_path) continue;
      if (!child.checkVisibility({ visibilityProperty: true })) continue;
      if (tag === 'g' || tag === 'a' || tag === 'switch') walk(child);
      else if (tag === 'foreignObject' || tag === 'text') {
        const record = collectText(child, tag === 'foreignObject');
        if (record) record_list.push(record);
      } else if (['rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'path'].includes(tag)) {
        const record = collectShape(child, tag);
        if (record) record_list.push(record);
      } else throw new Error(`不支持的 SVG 元素：${tag}`);
    }
  }

  walk(svg);
  temp_path.remove();
  const bounds = svg.getBoundingClientRect();
  return { view_box: svg.getAttribute('viewBox'), width: bounds.width, height: bounds.height, record_list };
}

/** 两点距离 */
function distance(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

/** 点到线段的距离与参数 t */
function segmentDistance(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return { d: Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy), t };
}

/** 点列的外框 */
function boundsOf(point_list) {
  const xs = point_list.map((p) => p[0]), ys = point_list.map((p) => p[1]);
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
}

/** 区间覆盖率：位置落在 [from, to] 中的点按 1 像素分桶，返回被占用的比例；区间不足 1 像素视为覆盖 */
function coverage(position_list, from, to) {
  if (to - from < 1) return 1;
  const bucket_count = Math.floor(to - from);
  const used = new Set();
  for (const v of position_list) {
    if (v >= from && v <= to) used.add(Math.min(bucket_count - 1, Math.floor(((v - from) / (to - from)) * bucket_count)));
  }
  return used.size / bucket_count;
}

/**
 * 贴合外框四边（可带圆角，圆角半径至多为短边一半）则返回 { rx }，否则返回 null。
 * 圆角半径由各角最靠近对角线的点估算：圆弧上 45 度处的点离两边都是 r(1 - 1/√2)。
 */
function fitRect(point_list, box) {
  const w = box.x1 - box.x0, h = box.y1 - box.y0;
  const limit = Math.min(w, h) / 2;
  const side_list = [[], [], [], []];
  const inner_list = [];
  for (const [x, y] of point_list) {
    const d_list = [y - box.y0, box.x1 - x, box.y1 - y, x - box.x0];
    const side = d_list.indexOf(Math.min(...d_list));
    if (d_list[side] <= SHAPE_TOLERANCE) side_list[side].push(side % 2 === 0 ? x : y);
    else inner_list.push([x, y]);
  }
  if (side_list.some((list) => list.length === 0)) return null;
  const corner_list = [[box.x0, box.y0], [box.x1, box.y0], [box.x1, box.y1], [box.x0, box.y1]];
  const nearest_list = corner_list.map(([cx, cy]) => {
    let nearest = Infinity;
    for (const [x, y] of point_list) {
      const dx = Math.abs(x - cx), dy = Math.abs(y - cy);
      if (dx <= limit && dy <= limit) nearest = Math.min(nearest, Math.max(dx, dy));
    }
    return nearest;
  }).sort((a, b) => a - b);
  if (!Number.isFinite(nearest_list[3])) return null;
  const radius = (nearest_list[1] + nearest_list[2]) / 2 / (1 - Math.SQRT1_2);
  const rx = radius < 2 ? 0 : Math.min(limit, radius);
  for (const [x, y] of inner_list) {
    const in_corner_x = x < box.x0 + rx || x > box.x1 - rx;
    const in_corner_y = y < box.y0 + rx || y > box.y1 - rx;
    if (!in_corner_x || !in_corner_y) return null;
    const ax = Math.min(Math.max(x, box.x0 + rx), box.x1 - rx);
    const ay = Math.min(Math.max(y, box.y0 + rx), box.y1 - rx);
    if (Math.abs(Math.hypot(x - ax, y - ay) - rx) > SHAPE_TOLERANCE) return null;
  }
  const pad = rx + 1;
  const ok = coverage(side_list[0], box.x0 + pad, box.x1 - pad) >= 0.9
    && coverage(side_list[2], box.x0 + pad, box.x1 - pad) >= 0.9
    && coverage(side_list[1], box.y0 + pad, box.y1 - pad) >= 0.9
    && coverage(side_list[3], box.y0 + pad, box.y1 - pad) >= 0.9;
  return ok ? { rx } : null;
}

/** 贴合内接椭圆且整圈都有点 */
function fitEllipse(point_list, box) {
  const cx = (box.x0 + box.x1) / 2, cy = (box.y0 + box.y1) / 2;
  const a = (box.x1 - box.x0) / 2, b = (box.y1 - box.y0) / 2;
  const tolerance = Math.min(SHAPE_TOLERANCE, Math.max(0.3, 0.08 * Math.min(a, b)));
  const used = new Set();
  for (const [x, y] of point_list) {
    const r = Math.hypot((x - cx) / a, (y - cy) / b);
    if (Math.abs(r - 1) * Math.min(a, b) > tolerance) return false;
    used.add(Math.floor(((Math.atan2((y - cy) / b, (x - cx) / a) + Math.PI) / (2 * Math.PI)) * 36) % 36);
  }
  return used.size === 36;
}

/** 内接菱形的四个顶点（上、右、下、左） */
function rhombusPoints(box) {
  const cx = (box.x0 + box.x1) / 2, cy = (box.y0 + box.y1) / 2;
  return [[cx, box.y0], [box.x1, cy], [cx, box.y1], [box.x0, cy]];
}

/** 贴合内接菱形且四边都有点 */
function fitRhombus(point_list, box) {
  const vertex_list = rhombusPoints(box);
  const side_list = [[], [], [], []];
  for (const p of point_list) {
    const hit_list = vertex_list.map((a, i) => segmentDistance(p, a, vertex_list[(i + 1) % 4]));
    const best = hit_list.reduce((m, h, i) => (h.d < hit_list[m].d ? i : m), 0);
    if (hit_list[best].d > SHAPE_TOLERANCE) return false;
    side_list[best].push(hit_list[best].t * 100);
  }
  return side_list.every((s) => coverage(s, 2, 98) >= 0.9);
}

/** Douglas-Peucker 化简 */
function simplify(point_list) {
  if (point_list.length < 3) return point_list.slice();
  let index = 0, max_d = 0;
  const first = point_list[0], last = point_list[point_list.length - 1];
  for (let i = 1; i < point_list.length - 1; i += 1) {
    const d = segmentDistance(point_list[i], first, last).d;
    if (d > max_d) {
      max_d = d;
      index = i;
    }
  }
  if (max_d <= SIMPLIFY_TOLERANCE) return [first, last];
  return simplify(point_list.slice(0, index + 1)).slice(0, -1).concat(simplify(point_list.slice(index)));
}

/** 两条点列是否相同（含反向） */
function samePoints(a, b) {
  if (a.length !== b.length) return false;
  const reversed = b.slice().reverse();
  return [b, reversed].some((c) => a.every((p, i) => distance(p, c[i]) <= SIMPLIFY_TOLERANCE));
}

/** 把端点沿切线方向延长到箭头尖端 */
function extendEnd(point_list, length, at_start) {
  if (!length) return;
  const list = at_start ? point_list.slice().reverse() : point_list;
  const end = list[list.length - 1];
  let back = list[list.length - 2];
  for (let i = list.length - 2; i >= 0; i -= 1) {
    back = list[i];
    if (distance(back, end) >= 2) break;
  }
  const d = distance(back, end) || 1;
  const moved = [end[0] + ((end[0] - back[0]) / d) * length, end[1] + ((end[1] - back[1]) / d) * length];
  if (at_start) point_list[0] = moved;
  else point_list[point_list.length - 1] = moved;
}

/** 有填充的路径按 SVG 规则隐式闭合（只用于判形）：补上从终点回到起点的点 */
function closeForFill(sub) {
  if (sub.closed) return;
  const a = sub.point_list[sub.point_list.length - 1], b = sub.point_list[0];
  const count = Math.ceil(distance(a, b));
  for (let k = 1; k <= count; k += 1) sub.point_list.push([a[0] + ((b[0] - a[0]) * k) / count, a[1] + ((b[1] - a[1]) * k) / count]);
  sub.closed = true;
}

/** 路径记录转成静态图元：先整体判形，否则按子路径输出折线 */
function convertPath(record) {
  const shape_list = record.subpath_list.map((s) => ({ point_list: s.point_list.slice(), closed: s.closed }));
  if (record.fill !== 'none') shape_list.forEach(closeForFill);
  const all_point_list = shape_list.flatMap((s) => s.point_list);
  const box = boundsOf(all_point_list);
  const style = { fill: record.fill, stroke: record.stroke, stroke_width: record.stroke_width, dash: record.dash };
  if (box.x1 - box.x0 >= 3 && box.y1 - box.y0 >= 3) {
    if (fitEllipse(all_point_list, box)) {
      return [{ kind: 'ellipse', cx: (box.x0 + box.x1) / 2, cy: (box.y0 + box.y1) / 2,
        rx: (box.x1 - box.x0) / 2, ry: (box.y1 - box.y0) / 2, ...style }];
    }
    const rect = fitRect(all_point_list, box);
    if (rect) return [{ kind: 'rect', x: box.x0, y: box.y0, w: box.x1 - box.x0, h: box.y1 - box.y0, rx: rect.rx, ...style }];
    if (fitRhombus(all_point_list, box)) return [{ kind: 'polygon', point_list: rhombusPoints(box), ...style }];
  }
  const subpath_list = record.subpath_list.map((s) => ({ point_list: s.point_list.slice(), closed: s.closed }));
  const extra_list = [];
  const head = subpath_list[0], tail = subpath_list[subpath_list.length - 1];
  for (const [marker, sub, at_start] of [[record.marker_start, head, true], [record.marker_end, tail, false]]) {
    if (!marker) continue;
    if (marker.type === 'filled-circle') {
      const p = at_start ? sub.point_list[0] : sub.point_list[sub.point_list.length - 1];
      extra_list.push({ kind: 'ellipse', cx: p[0], cy: p[1], rx: marker.radius, ry: marker.radius,
        fill: marker.fill, stroke: 'none', stroke_width: 0, dash: '' });
    } else extendEnd(sub.point_list, marker.tip_length, at_start);
  }
  const simple_list = [];
  for (const sub of subpath_list) {
    const point_list = simplify(sub.point_list);
    if (point_list.every((p) => distance(p, point_list[0]) <= SIMPLIFY_TOLERANCE) || simple_list.some((s) => samePoints(s.point_list, point_list))) continue;
    const last = simple_list[simple_list.length - 1];
    if (last && !last.closed && !sub.closed && distance(last.point_list[last.point_list.length - 1], point_list[0]) <= SIMPLIFY_TOLERANCE) {
      last.point_list = last.point_list.concat(point_list.slice(1));
    } else simple_list.push({ point_list, closed: sub.closed });
  }
  const out_list = simple_list.map((s, i) => {
    const closed = s.closed || (s.point_list.length > 3 && distance(s.point_list[0], s.point_list[s.point_list.length - 1]) <= SIMPLIFY_TOLERANCE);
    if (closed) {
      const point_list = distance(s.point_list[0], s.point_list[s.point_list.length - 1]) <= SIMPLIFY_TOLERANCE
        ? s.point_list.slice(0, -1) : s.point_list;
      return { kind: 'polygon', point_list, ...style };
    }
    const marker_start = i === 0 && record.marker_start && record.marker_start.type !== 'filled-circle' ? record.marker_start.type : '';
    const marker_end = i === simple_list.length - 1 && record.marker_end && record.marker_end.type !== 'filled-circle' ? record.marker_end.type : '';
    return { kind: 'polyline', point_list: s.point_list, ...style, fill: 'none', marker_start, marker_end };
  });
  return extra_list.concat(out_list);
}

/** 几何是否相同（用于合并填充与描边） */
function sameGeometry(a, b) {
  if (a.kind !== b.kind) return false;
  if (a.kind === 'rect') return Math.abs(a.x - b.x) <= 1 && Math.abs(a.y - b.y) <= 1 && Math.abs(a.w - b.w) <= 1 && Math.abs(a.h - b.h) <= 1;
  if (a.kind === 'ellipse') return Math.abs(a.cx - b.cx) <= 1 && Math.abs(a.cy - b.cy) <= 1 && Math.abs(a.rx - b.rx) <= 1 && Math.abs(a.ry - b.ry) <= 1;
  if (a.kind === 'polygon') return a.point_list.length === b.point_list.length && a.point_list.every((p, i) => distance(p, b.point_list[i]) <= 1);
  return false;
}

/**
 * 相邻且几何相同的图元合并成一个：至多一个有填充，描边相同或只有一个有描边。
 * 覆盖“一个只填充、一个只描边”与“同一图形再描一遍边”两种写法。
 */
function mergeFillStroke(element_list) {
  const out_list = [];
  for (const el of element_list) {
    const last = out_list[out_list.length - 1];
    if (last && el.kind !== 'text' && sameGeometry(last, el)) {
      const one_fill = last.fill === 'none' || el.fill === 'none';
      const same_stroke = last.stroke === 'none' || el.stroke === 'none'
        || (last.stroke === el.stroke && last.stroke_width === el.stroke_width && last.dash === el.dash);
      if (one_fill && same_stroke) {
        const stroker = last.stroke !== 'none' ? last : el;
        out_list[out_list.length - 1] = { ...stroker, ...(stroker.kind === 'rect' ? { rx: Math.max(last.rx, el.rx) } : {}),
          fill: last.fill !== 'none' ? last.fill : el.fill };
        continue;
      }
    }
    out_list.push(el);
  }
  return out_list;
}

/** 转换脚本会当作方框的图元：矩形、椭圆、有填充的四点多边形 */
function boxOutline(el) {
  if (el.kind === 'rect') {
    const p = [[el.x, el.y], [el.x + el.w, el.y], [el.x + el.w, el.y + el.h], [el.x, el.y + el.h]];
    return { area: el.w * el.h, center: [el.x + el.w / 2, el.y + el.h / 2], edge_list: p.map((a, i) => [a, p[(i + 1) % 4]]) };
  }
  if (el.kind === 'polygon' && el.point_list.length === 4 && el.fill !== 'none') {
    const b = boundsOf(el.point_list);
    const p = el.point_list;
    return { area: (b.x1 - b.x0) * (b.y1 - b.y0), center: [(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2], edge_list: p.map((a, i) => [a, p[(i + 1) % 4]]) };
  }
  if (el.kind === 'ellipse') {
    const p = Array.from({ length: 360 }, (_, i) => [el.cx + el.rx * Math.cos((i * Math.PI) / 180), el.cy + el.ry * Math.sin((i * Math.PI) / 180)]);
    return { area: 4 * el.rx * el.ry, center: [el.cx, el.cy], edge_list: p.map((a, i) => [a, p[(i + 1) % 360]]) };
  }
  return null;
}

/** 点到方框边界的最近点 */
function nearestOnOutline(p, outline) {
  let best = null;
  for (const [a, b] of outline.edge_list) {
    const hit = segmentDistance(p, a, b);
    if (!best || hit.d < best.d) best = { d: hit.d, point: [a[0] + hit.t * (b[0] - a[0]), a[1] + hit.t * (b[1] - a[1])] };
  }
  return best;
}

/**
 * 端点所粘的方框：中心在容差内的取最近中心，否则取边在容差内的最小方框（与转换脚本一致）；
 * 都没有时，取边界在 8 像素以内的最近方框作为吸附目标。
 */
function endpointTarget(p, outline_list) {
  const hit_list = outline_list.map((o) => ({ o, ...nearestOnOutline(p, o), center_d: distance(p, o.center) }));
  const center_list = hit_list.filter((h) => h.center_d <= GLUE_TOLERANCE).sort((a, b) => a.center_d - b.center_d);
  if (center_list.length) return { o: center_list[0].o, point: null };
  const edge_list = hit_list.filter((h) => h.d <= GLUE_TOLERANCE).sort((a, b) => a.o.area - b.o.area);
  if (edge_list.length) return { o: edge_list[0].o, point: null };
  const near_list = hit_list.filter((h) => h.d <= SNAP_DISTANCE).sort((a, b) => a.d - b.d);
  return near_list.length ? { o: near_list[0].o, point: near_list[0].point } : null;
}

/** 连线两端都能落到不同方框时，把容差之外、8 像素之内的端点移到方框边界上 */
function snapEndpoints(element_list) {
  const outline_list = element_list.map(boxOutline).filter(Boolean);
  let snap_count = 0;
  for (const el of element_list) {
    if (el.kind !== 'polyline') continue;
    const last = el.point_list.length - 1;
    const start = endpointTarget(el.point_list[0], outline_list);
    const end = endpointTarget(el.point_list[last], outline_list);
    if (!start || !end || start.o === end.o) continue;
    if (start.point) el.point_list[0] = start.point;
    if (end.point) el.point_list[last] = end.point;
    snap_count += (start.point ? 1 : 0) + (end.point ? 1 : 0);
  }
  return snap_count;
}

/** 数值保留两位小数 */
function fmt(v) {
  return String(+v.toFixed(2));
}

/** XML 转义 */
function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** 填充、描边的表现属性 */
function paintAttrs(el) {
  let s = ` fill="${el.fill}" stroke="${el.stroke}"`;
  if (el.stroke !== 'none') s += ` stroke-width="${fmt(el.stroke_width)}"`;
  if (el.stroke !== 'none' && el.dash) s += ` stroke-dasharray="${el.dash}"`;
  return s;
}

/** 一个静态图元写成 SVG 文本 */
function elementXml(el) {
  const points = (list) => list.map((p) => `${fmt(p[0])},${fmt(p[1])}`).join(' ');
  if (el.kind === 'rect') {
    const rx = el.rx > 0 ? ` rx="${fmt(el.rx)}"` : '';
    return `<rect x="${fmt(el.x)}" y="${fmt(el.y)}" width="${fmt(el.w)}" height="${fmt(el.h)}"${rx}${paintAttrs(el)}/>`;
  }
  if (el.kind === 'ellipse') return `<ellipse cx="${fmt(el.cx)}" cy="${fmt(el.cy)}" rx="${fmt(el.rx)}" ry="${fmt(el.ry)}"${paintAttrs(el)}/>`;
  if (el.kind === 'polygon') return `<polygon points="${points(el.point_list)}"${paintAttrs(el)}/>`;
  if (el.kind === 'polyline') {
    let marker = '';
    if (el.marker_start) marker += ` marker-start="url(#${el.marker_start})"`;
    if (el.marker_end) marker += ` marker-end="url(#${el.marker_end})"`;
    return `<polyline points="${points(el.point_list)}"${paintAttrs(el)}${marker}/>`;
  }
  const line_attrs = (line) => {
    const x = { middle: (line.left + line.right) / 2, end: line.right }[el.anchor] ?? line.left;
    const y = line.center + BASELINE_OFFSET * line.size;
    return { x, y, attrs: ` font-size="${fmt(line.size)}" fill="${line.color}" font-weight="${line.bold ? 'bold' : 'normal'}"` };
  };
  const first = line_attrs(el.line_list[0]);
  const bg = el.bg ? ` paint-order="stroke" stroke="${el.bg}" stroke-width="4" stroke-linejoin="round"` : '';
  const tspan_list = el.line_list.map((line) => {
    const a = line_attrs(line);
    return `<tspan x="${fmt(a.x)}" y="${fmt(a.y)}"${a.attrs}>${esc(line.text)}</tspan>`;
  });
  return `<text x="${fmt(first.x)}" y="${fmt(first.y)}" text-anchor="${el.anchor}"${first.attrs}${bg}>${tspan_list.join('')}</text>`;
}

/**
 * 对齐方式未定的标签：各行中心都与所在最小方框（之前出现的矩形、椭圆、四点填充多边形）的
 * 水平中心对齐时按居中处理，否则按左对齐；容差与转换脚本判断居中时相同。
 */
function resolveFreeAnchors(element_list) {
  element_list.forEach((el, index) => {
    if (el.kind !== 'text' || !el.align_free) return;
    const center_x = (line) => (line.left + line.right) / 2;
    const cx = el.line_list.reduce((sum, line) => sum + center_x(line), 0) / el.line_list.length;
    const cy = el.line_list.reduce((sum, line) => sum + line.center, 0) / el.line_list.length;
    const owner_list = element_list.slice(0, index).filter((box) => {
      if (box.kind === 'rect') return box.x <= cx && cx <= box.x + box.w && box.y <= cy && cy <= box.y + box.h;
      if (box.kind === 'ellipse') return Math.hypot((cx - box.cx) / box.rx, (cy - box.cy) / box.ry) <= 1;
      if (box.kind === 'polygon' && box.point_list.length === 4 && box.fill !== 'none') {
        const b = boundsOf(box.point_list);
        return b.x0 <= cx && cx <= b.x1 && b.y0 <= cy && cy <= b.y1;
      }
      return false;
    });
    if (!owner_list.length) return;
    const area = (box) => {
      if (box.kind === 'rect') return box.w * box.h;
      if (box.kind === 'ellipse') return 4 * box.rx * box.ry;
      const b = boundsOf(box.point_list);
      return (b.x1 - b.x0) * (b.y1 - b.y0);
    };
    const owner = owner_list.reduce((a, b) => (area(b) < area(a) ? b : a));
    const box_cx = owner.kind === 'rect' ? owner.x + owner.w / 2 : owner.kind === 'ellipse' ? owner.cx
      : (boundsOf(owner.point_list).x0 + boundsOf(owner.point_list).x1) / 2;
    const box_w = owner.kind === 'rect' ? owner.w : owner.kind === 'ellipse' ? 2 * owner.rx
      : boundsOf(owner.point_list).x1 - boundsOf(owner.point_list).x0;
    if (el.line_list.every((line) => Math.abs(center_x(line) - box_cx) <= Math.max(3, 0.02 * box_w))) el.anchor = 'middle';
  });
}

/**
 * 去掉与之前某个矩形边框重合的无箭头折线（如分段描边的类框又描了一遍外框），
 * 否则在 Visio 中移动方框时这些线会留在原处。折线上每隔 1 像素的点都在边框 1 像素内才算重合。
 */
function dropRedundantOutlines(element_list) {
  return element_list.filter((el, index) => {
    if (el.kind !== 'polyline' || el.marker_start || el.marker_end) return true;
    const sample_list = [];
    el.point_list.slice(1).forEach((b, i) => {
      const a = el.point_list[i];
      const count = Math.max(1, Math.ceil(distance(a, b)));
      for (let k = 0; k <= count; k += 1) sample_list.push([a[0] + ((b[0] - a[0]) * k) / count, a[1] + ((b[1] - a[1]) * k) / count]);
    });
    return !element_list.slice(0, index).some((box) => box.kind === 'rect' && box.stroke !== 'none'
      && sample_list.every((p) => nearestOnOutline(p, boxOutline(box)).d <= 1));
  });
}

/** 收集结果转成静态 SVG 图元列表 */
function buildElementList(record_list) {
  const element_list = [];
  for (const record of record_list) {
    if (record.kind === 'path') element_list.push(...convertPath(record));
    else element_list.push(record);
  }
  const merged_list = dropRedundantOutlines(mergeFillStroke(element_list));
  resolveFreeAnchors(merged_list);
  return merged_list;
}

const [, , source_dir_arg, out_dir_arg, filter_keyword] = process.argv;
if (!source_dir_arg || !out_dir_arg) {
  console.error('用法：node export-svg.mjs <图源目录> <输出目录> [文件名关键字]');
  process.exit(2);
}
const source_dir = resolve(source_dir_arg);
const out_dir = resolve(out_dir_arg);
mkdirSync(out_dir, { recursive: true });
const html_list = listDiagramHtml(source_dir, filter_keyword);
const { browser, page } = await launchDiagramPage({ viewport: { width: 1600, height: 1200 } });
const failure_list = [];

for (const html_name of html_list) {
  const error_text = await openDiagram(page, source_dir, html_name);
  if (error_text) {
    failure_list.push(`${html_name}: ${error_text}`);
    continue;
  }
  let result;
  try {
    result = await page.evaluate(collectDiagramInPage, MARKER_TYPE_MAP);
  } catch (error) {
    failure_list.push(`${html_name}: ${String(error.message || error).split('\n')[0]}`);
    continue;
  }
  const element_list = buildElementList(result.record_list);
  const snap_count = snapEndpoints(element_list);
  const title = (readFileSync(join(source_dir, html_name), 'utf8').match(/<title>([\s\S]*?)<\/title>/) || ['', ''])[1].trim();
  const svg_xml = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${result.view_box}" width="${fmt(result.width)}" height="${fmt(result.height)}"`
    + ` font-family="PingFang SC, Microsoft YaHei, sans-serif"><defs>${PREVIEW_MARKER_DEFS}</defs>`
    + element_list.map(elementXml).join('\n') + '</svg>';
  writeFileSync(join(out_dir, html_name), '<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
    + `<title>${title}</title>\n</head>\n<body style="margin:0" data-rendered="1">\n<div class="diagram" style="display:inline-block;padding:16px;background:#ffffff">\n`
    + svg_xml + '\n</div>\n</body>\n</html>\n');
  const count = (kind) => element_list.filter((el) => el.kind === kind).length;
  console.log(`${html_name}\trect ${count('rect')}\tellipse ${count('ellipse')}\tpolygon ${count('polygon')}`
    + `\tpolyline ${count('polyline')}\ttext ${count('text')}\t吸附 ${snap_count}`);
}

await browser.close();
if (failure_list.length > 0) {
  console.error('导出失败：\n' + failure_list.join('\n'));
  process.exit(1);
}
console.log(`完成，共 ${html_list.length} 张`);
