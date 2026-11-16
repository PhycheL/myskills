"""把“网页里嵌一张 SVG”的示意图直接转成原生 Visio (.vsdx)。

用法：
    python3 html_to_vsdx.py 输入.html [更多 .html 或目录 ...] -o 输出目录

支持的 SVG 写法：
    rect、circle 与 ellipse（椭圆方框）、polygon（四点按菱形处理，其余保留填充）、line、polyline、
    path（M/L/H/V/Z）、text + tspan、g（translate / scale / matrix）、
    marker-start / marker-end（转成箭头；marker 的 id 是 ARROWS 中登记的箭头类型名时按该类型，
    其余一律为实心三角）、stroke-dasharray（转成虚线）、pattern 填充（折算成平均色）、
    <style> 里的简单选择器（元素名、.class、元素名.class）。

转换规则：
    - 方框里水平居中的文字并入该方框：整体垂直居中的作为方框文字，靠顶部的作为顶部标题；
    - 其余文字各自成为文字框，上下紧邻、对齐方式相同的几行合并为一个多行文字框；
    - 两端都落在方框/菱形边上（容差 TOL 像素）的线转成粘连的连接线，否则保留为普通线条；
    - 带白色描边衬底的文字（paint-order: stroke）转成白底文字框。
"""
import argparse
import html
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True  # 导入同目录模块时不在 skill 目录里生成 __pycache__
import vsdx_writer

TOL = 4.0
# 可在 marker 的 id 中直接写明的箭头类型（与 vsdx_writer.ARROWS 对应）
ARROWS = {"filled-triangle", "hollow-triangle", "filled-diamond", "hollow-diamond", "open-arrow", "filled-circle"}
INHERITED = {"fill", "stroke", "stroke-width", "stroke-dasharray", "font-size", "font-weight",
             "text-anchor", "paint-order", "marker-start", "marker-end"}
PRESENTATION = INHERITED | {"opacity"}
DEFAULTS = {"fill": "#000000", "stroke": "none", "stroke-width": "1", "font-size": "16px",
            "font-weight": "normal", "text-anchor": "start"}
NAMED = {"white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#008000",
         "blue": "#0000ff", "gray": "#808080", "grey": "#808080", "orange": "#ffa500",
         "yellow": "#ffff00", "transparent": "none"}
SKIP = {"defs", "marker", "pattern", "clipPath", "mask", "title", "desc", "style", "linearGradient",
        "radialGradient", "symbol", "metadata"}


def local(tag):
    return tag.rsplit("}", 1)[-1]


# ---------------- CSS ----------------
def parse_decl(body):
    d = {}
    for part in body.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            d[k.strip().lower()] = v.strip()
    return d


def parse_css(css):
    rules = []
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for order, (sels, body) in enumerate(re.findall(r"([^{}]+)\{([^}]*)\}", css)):
        decl = parse_decl(body)
        for sel in sels.split(","):
            sel = sel.strip()
            m = re.fullmatch(r"([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)", sel)
            if not sel or not m:
                continue
            tag = m.group(1)
            classes = set(c for c in m.group(2).split(".") if c)
            spec = (10 * len(classes)) + (1 if tag else 0)
            rules.append((spec, order, tag, classes, decl))
    rules.sort(key=lambda r: (r[0], r[1]))
    return rules


class Styler:
    def __init__(self, rules):
        self.rules = rules

    def compute(self, el, parent):
        st = {k: v for k, v in parent.items() if k in INHERITED}
        for k in PRESENTATION:
            if el.get(k) is not None:
                st[k] = el.get(k)
        tag, classes = local(el.tag), set(el.get("class", "").split())
        for _, _, rtag, rcls, decl in self.rules:
            if (rtag is None or rtag == tag) and rcls <= classes and (rtag or rcls):
                st.update(decl)
        st.update(parse_decl(el.get("style", "")))
        return st


# ---------------- 数值与颜色 ----------------
def num(v, default=0.0):
    if v is None:
        return default
    m = re.match(r"\s*(-?[\d.]+(?:e-?\d+)?)", str(v))
    return float(m.group(1)) if m else default


def color(v, patterns=None):
    if v is None:
        return "none"
    v = v.strip().lower()
    if v in NAMED:
        return NAMED[v]
    if v == "none":
        return "none"
    m = re.fullmatch(r"url\(\s*#([^)\s]+)\s*\)", v)
    if m:
        return (patterns or {}).get(m.group(1), "#ffffff")
    m = re.fullmatch(r"#([0-9a-f]{3})", v)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    if re.fullmatch(r"#[0-9a-f]{6}", v):
        return v
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        r, g, b = [int(float(x)) for x in m.group(1).split(",")[:3]]
        return f"#{r:02x}{g:02x}{b:02x}"
    return "#000000"


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def pattern_colors(root, styler):
    """pattern / 渐变填充在 Visio 里没有对应物，折算成覆盖面积加权的平均色。"""
    out = {}
    for el in root.iter():
        tag, pid = local(el.tag), el.get("id")
        if not pid:
            continue
        if tag == "pattern":
            pw = num(el.get("width"), 1) or 1
            base, c = "#ffffff", None
            for ch in el:
                st = styler.compute(ch, DEFAULTS)
                if local(ch.tag) == "rect" and color(st.get("fill")) != "none":
                    base = color(st.get("fill"))
                elif local(ch.tag) in ("line", "path") and color(st.get("stroke")) != "none":
                    c = (color(st.get("stroke")), min(1.0, num(st.get("stroke-width"), 1) / pw))
            out[pid] = mix(base, c[0], c[1]) if c else base
        elif tag in ("linearGradient", "radialGradient"):
            stops = [s for s in el if local(s.tag) == "stop"]
            if stops:
                st = parse_decl(stops[0].get("style", ""))
                out[pid] = color(stops[0].get("stop-color") or st.get("stop-color") or "#ffffff")
    return out


# ---------------- 变换 ----------------
def mat_mul(a, b):
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


def parse_transform(s):
    m = (1, 0, 0, 1, 0, 0)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "translate":
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale":
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "matrix" and len(v) == 6:
            t = tuple(v)
        else:
            print(f"  提示：忽略不支持的变换 {name}({args})", file=sys.stderr)
            continue
        m = mat_mul(m, t)
    return m


def apply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


# ---------------- 文字度量（估算，用于定位和判断归属） ----------------
def text_width(t, size, bold):
    w = 0.0
    for ch in t:
        o = ord(ch)
        if o >= 0x2E80 or 0xFF00 <= o <= 0xFFEF:
            w += size
        elif ch == " ":
            w += size * 0.3
        elif ch in "·•":
            w += size * 0.5
        elif ch.isupper() or ch.isdigit():
            w += size * 0.62
        else:
            w += size * 0.55
    return w * (1.05 if bold else 1.0)


def is_bold(w):
    w = str(w).strip().lower()
    return w in ("bold", "bolder") or (w.isdigit() and int(w) >= 600)


class Line:
    def __init__(self, x, y):
        self.x, self.y, self.runs = x, y, []

    @property
    def size(self):
        return max((r[1] for r in self.runs), default=14.0)

    @property
    def width(self):
        return sum(text_width(t, s, b) for t, s, _, b in self.runs)

    def text(self):
        return "".join(r[0] for r in self.runs)


class TextItem:
    def __init__(self, lines, anchor, bg, order):
        self.lines = [ln for ln in lines if ln.text().strip()]
        self.anchor, self.bg, self.order = anchor, bg, order
        for ln in self.lines:
            ln.runs[0] = (ln.runs[0][0].lstrip(),) + ln.runs[0][1:]
            ln.runs[-1] = (ln.runs[-1][0].rstrip(),) + ln.runs[-1][1:]

    def bbox(self):
        x0 = y0 = math.inf
        x1 = y1 = -math.inf
        for ln in self.lines:
            w = ln.width
            left = {"middle": ln.x - w / 2, "end": ln.x - w}.get(self.anchor, ln.x)
            x0, x1 = min(x0, left), max(x1, left + w)
            y0, y1 = min(y0, ln.y - 0.88 * ln.size), max(y1, ln.y + 0.26 * ln.size)
        return x0, y0, x1, y1

    def center(self):
        x0, y0, x1, y1 = self.bbox()
        return (x0 + x1) / 2, (y0 + y1) / 2

    def pitches_ok(self):
        return all(0.8 * b.size <= b.y - a.y <= 2.2 * b.size for a, b in zip(self.lines, self.lines[1:]))

    def sp_line(self):
        if len(self.lines) < 2:
            return None
        r = sum((b.y - a.y) / b.size for a, b in zip(self.lines, self.lines[1:])) / (len(self.lines) - 1)
        return round(-min(max(r, 1.0), 2.2), 3)

    def label(self):
        out = []
        for ln in self.lines:
            parts = []
            for t, size, col, bold in ln.runs:
                if not t:
                    continue
                s = html.escape(t)
                if bold:
                    s = f"<b>{s}</b>"
                parts.append(f'<font style="font-size:{size:g}px;" color="{col}">{s}</font>')
            out.append("".join(parts))
        return "<br>".join(out)


def merge_texts(items):
    lines = [ln for it in items for ln in it.lines]
    return TextItem(lines, items[0].anchor, items[0].bg, items[0].order)


# ---------------- 形状 ----------------
class Box:
    def __init__(self, kind, x, y, w, h, st, order, rx=0):
        self.kind, self.x, self.y, self.w, self.h = kind, x, y, w, h
        self.st, self.order, self.rx = st, order, rx
        self.texts, self.valign, self.top = [], "middle", 0.0
        self.cid = None

    @property
    def area(self):
        return self.w * self.h

    def contains(self, px, py, pad=1.0):
        if self.kind == "ellipse":
            cx, cy = self.x + self.w / 2, self.y + self.h / 2
            return math.hypot((px - cx) / (self.w / 2), (py - cy) / (self.h / 2)) <= 1.0 + pad / min(self.w, self.h)
        if self.kind == "rhombus":
            cx, cy = self.x + self.w / 2, self.y + self.h / 2
            return abs(px - cx) / (self.w / 2) + abs(py - cy) / (self.h / 2) <= 1.0 + pad / min(self.w, self.h)
        return self.x - pad <= px <= self.x + self.w + pad and self.y - pad <= py <= self.y + self.h + pad

    def edges(self):
        x, y, w, h = self.x, self.y, self.w, self.h
        if self.kind == "ellipse":
            # 椭圆按 360 段折线近似
            p = [(x + w / 2 + w / 2 * math.cos(math.radians(a)), y + h / 2 + h / 2 * math.sin(math.radians(a)))
                 for a in range(360)]
        elif self.kind == "rhombus":
            p = [(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)]
        else:
            p = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        return list(zip(p, p[1:] + p[:1]))

    def snap(self, px, py):
        """点到边框的最近点；返回 (距离, 相对位置 fx, fy)。"""
        best = None
        for (ax, ay), (bx, by) in self.edges():
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
            qx, qy = ax + t * dx, ay + t * dy
            d = math.hypot(px - qx, py - qy)
            if best is None or d < best[0]:
                best = (d, qx, qy)
        d, qx, qy = best
        return d, round((qx - self.x) / self.w, 4), round((qy - self.y) / self.h, 4)


class Stroke:
    def __init__(self, pts, st, order, fill=None):
        self.pts, self.st, self.order, self.fill = pts, st, order, fill


def path_points(d):
    """只取直线段；曲线按端点连直线。返回若干条折线。"""
    toks = re.findall(r"[MmLlHhVvZzCcSsQqTtAa]|-?[\d.]+(?:e-?\d+)?", d)
    subs, cur, i, cmd = [], [], 0, None
    x = y = sx = sy = 0.0
    argc = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur:
                    cur.append((sx, sy))
                    subs.append(cur)
                cur = []
                x, y = sx, sy
            continue
        n = argc[cmd.upper()]
        a = [float(v) for v in toks[i:i + n]]
        i += n
        rel = cmd.islower()
        u = cmd.upper()
        if u == "M":
            if cur:
                subs.append(cur)
            x, y = (x + a[0], y + a[1]) if rel else (a[0], a[1])
            sx, sy = x, y
            cur = [(x, y)]
            cmd = "l" if rel else "L"
            continue
        if u == "H":
            x = x + a[0] if rel else a[0]
        elif u == "V":
            y = y + a[0] if rel else a[0]
        else:
            x, y = (x + a[-2], y + a[-1]) if rel else (a[-2], a[-1])
        cur.append((x, y))
    if cur:
        subs.append(cur)
    return [s for s in subs if len(s) >= 2]


# ---------------- 解析 SVG ----------------
class Parsed:
    def __init__(self):
        self.boxes, self.strokes, self.texts = [], [], []
        self.order = 0

    def next(self):
        self.order += 1
        return self.order


def walk(el, st_parent, m_parent, styler, pats, out):
    tag = local(el.tag)
    if tag in SKIP:
        return
    st = styler.compute(el, st_parent)
    m = mat_mul(m_parent, parse_transform(el.get("transform")))
    if st.get("display") == "none" or st.get("visibility") == "hidden":
        return
    if tag == "rect":
        x, y = apply(m, num(el.get("x")), num(el.get("y")))
        x2, y2 = apply(m, num(el.get("x")) + num(el.get("width")), num(el.get("y")) + num(el.get("height")))
        rx = num(el.get("rx"), num(el.get("ry"))) * abs(m[0])
        if x2 - x > 0 and y2 - y > 0:
            out.boxes.append(Box("rect", x, y, x2 - x, y2 - y, st, out.next(), rx))
    elif tag in ("circle", "ellipse"):
        rx = num(el.get("r")) if tag == "circle" else num(el.get("rx"))
        ry = num(el.get("r")) if tag == "circle" else num(el.get("ry"))
        x, y = apply(m, num(el.get("cx")) - rx, num(el.get("cy")) - ry)
        x2, y2 = apply(m, num(el.get("cx")) + rx, num(el.get("cy")) + ry)
        if x2 - x > 0 and y2 - y > 0:
            out.boxes.append(Box("ellipse", x, y, x2 - x, y2 - y, st, out.next()))
    elif tag in ("polygon", "polyline"):
        v = [float(t) for t in re.split(r"[\s,]+", el.get("points", "").strip()) if t]
        pts = [apply(m, v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)]
        filled = color(st.get("fill"), pats) != "none"
        if tag == "polygon" and len(pts) == 4 and filled:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            kind = "rect" if len({round(a) for a in xs}) == 2 and len({round(b) for b in ys}) == 2 else "rhombus"
            out.boxes.append(Box(kind, min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), st, out.next()))
        else:
            fill = None
            if tag == "polygon":
                pts.append(pts[0])
                if filled:
                    fill = color(st.get("fill"), pats)
            out.strokes.append(Stroke(pts, st, out.next(), fill))
    elif tag == "line":
        p0 = apply(m, num(el.get("x1")), num(el.get("y1")))
        p1 = apply(m, num(el.get("x2")), num(el.get("y2")))
        out.strokes.append(Stroke([p0, p1], st, out.next()))
    elif tag == "path":
        for pts in path_points(el.get("d", "")):
            out.strokes.append(Stroke([apply(m, *p) for p in pts], st, out.next()))
    elif tag == "text":
        out.texts.append(parse_text(el, st, m, styler, out.next()))
        return
    for ch in el:
        walk(ch, st, m, styler, pats, out)


def parse_text(el, st, m, styler, order):
    x, y = num(el.get("x")), num(el.get("y"))
    lines = [Line(*apply(m, x, y))]
    cur = {"x": x, "y": y}

    def add(t, s):
        if t:
            t = re.sub(r"\s+", " ", t)
            lines[-1].runs.append((t, num(s.get("font-size"), 16), color(s.get("fill")), is_bold(s.get("font-weight"))))

    add(el.text, st)
    for ch in el:
        if local(ch.tag) == "tspan":
            cst = styler.compute(ch, st)
            nx = num(ch.get("x"), cur["x"]) if ch.get("x") is not None else cur["x"]
            ny = num(ch.get("y"), cur["y"]) if ch.get("y") is not None else cur["y"] + num(ch.get("dy"), 0)
            if (ny != cur["y"] or ch.get("x") is not None) and lines[-1].runs:
                lines.append(Line(*apply(m, nx, ny)))
            cur.update(x=nx, y=ny)
            add(ch.text, cst)
        add(ch.tail, st)
    bg = None
    if "stroke" in st.get("paint-order", "") and color(st.get("stroke")) != "none":
        bg = color(st.get("stroke"))
    return TextItem(lines, st.get("text-anchor", "start"), bg, order)


# ---------------- 文字归属 ----------------
def owner_box(item, boxes):
    cx, cy = item.center()
    x0, _, x1, _ = item.bbox()
    cands = [b for b in boxes if b.order < item.order and b.contains(cx, cy) and x1 - x0 <= b.w + 4]
    return min(cands, key=lambda b: b.area) if cands else None


def centered_in(item, box):
    tol = max(3.0, 0.02 * box.w)
    return item.anchor == "middle" and all(abs(ln.x - (box.x + box.w / 2)) <= tol for ln in item.lines)


def assign_texts(parsed):
    groups, free = {}, []
    for it in parsed.texts:
        if not it.lines:
            continue
        b = owner_box(it, parsed.boxes)
        if b is None:
            free.append(it)
        else:
            groups.setdefault(id(b), (b, []))[1].append(it)
    for b, items in groups.values():
        items.sort(key=lambda it: it.bbox()[1])
        whole = merge_texts(items)
        _, y0, _, y1 = whole.bbox()
        if (all(centered_in(it, b) for it in items) and whole.pitches_ok()
                and abs((y0 + y1) / 2 - (b.y + b.h / 2)) <= max(4.0, 0.1 * b.h)):
            b.texts = items
            continue
        top = []
        for it in items:
            if not centered_in(it, b):
                break
            cand = merge_texts(top + [it])
            if cand.pitches_ok() and cand.bbox()[1] - b.y <= min(40.0, 0.4 * b.h):
                top.append(it)
            else:
                break
        if top and b.kind == "rect":
            b.texts, b.valign = top, "top"
            b.top = max(0.0, merge_texts(top).bbox()[1] - b.y - 3)
        free += [it for it in items if it not in b.texts]
    return free


def group_free(free):
    free.sort(key=lambda it: it.order)
    blocks = []
    for it in free:
        if blocks:
            last = blocks[-1]
            prev = last[-1].lines[-1]
            first = it.lines[0]
            if (it.anchor == last[-1].anchor and it.bg == last[-1].bg and abs(first.x - prev.x) <= 1
                    and 0.8 * first.size <= first.y - prev.y <= 2.1 * first.size):
                last.append(it)
                continue
        blocks.append([it])
    return [merge_texts(b) for b in blocks]


# ---------------- 生成图形描述 ----------------
class Doc:
    """vsdx_writer 需要的图形描述：name / title / w / h / back / edges / front。"""

    def __init__(self, name, title, w, h):
        self.name, self.title, self.w, self.h = name, title, w, h
        self.back, self.edges, self.front = [], [], []


def stroke_style(st, pats):
    c = color(st.get("stroke"), pats)
    s = f"strokeColor={c};strokeWidth={num(st.get('stroke-width'), 1):g};"
    dash = st.get("stroke-dasharray", "none")
    if dash and dash != "none":
        s += f"dashed=1;dashPattern={dash.replace(',', ' ')};"
    return s


def box_style(b, pats):
    st = b.st
    fill = color(st.get("fill"), pats)
    if b.kind == "ellipse":
        s = "ellipse;"
    else:
        s = ("rhombus;" if b.kind == "rhombus" else
             f"rounded={1 if b.rx > 0 else 0};absoluteArcSize=1;arcSize={2 * b.rx:g};")
    s += f"whiteSpace=wrap;html=1;fillColor={fill};" + stroke_style(st, pats) + "align=center;"
    if b.valign == "top":
        s += f"verticalAlign=top;spacingTop={b.top:g};"
    if b.texts:
        sp = merge_texts(b.texts).sp_line()
        if sp:
            s += f"vsdxSpLine={sp};"
    return s


def text_vertex(it):
    x0, y0, x1, y1 = it.bbox()
    w = (x1 - x0) * 1.08 + 6
    h = (y1 - y0) + 4
    cx = (x0 + x1) / 2
    align = {"middle": "center", "end": "right"}.get(it.anchor, "left")
    left = {"center": cx - w / 2, "right": x1 + 3 - w}.get(align, x0 - 3)
    st = (f"text;html=1;whiteSpace=wrap;strokeColor=none;fillColor=none;align={align};"
          f"verticalAlign=middle;fontSize={it.lines[0].size:g};spacing=0;")
    if it.bg:
        st += f"labelBackgroundColor={it.bg};"
    sp = it.sp_line()
    if sp:
        st += f"vsdxSpLine={sp};"
    return (left, y0 - 2, w, h), st


def arrow_type(v):
    """marker 引用转箭头类型：无为 None，id 是登记的箭头类型名时用该类型，其余为 block。"""
    if not v or v == "none":
        return None
    m = re.fullmatch(r"url\(\s*#([^)\s]+)\s*\)", v.strip())
    return m.group(1) if m and m.group(1) in ARROWS else "block"


def glue_target(p, boxes):
    """端点落在框中心 → 形状对形状粘连（fx, fy 为 None）；落在框边上 → 粘到边上该点。

    中心优先：从框中心画出的线常常恰好擦过框内的小框边缘。
    嵌套框的中心往往只差几个像素，取中心最近的，距离相同取大框。
    """
    centers = [(math.hypot(p[0] - (b.x + b.w / 2), p[1] - (b.y + b.h / 2)), -b.area, i)
               for i, b in enumerate(boxes)]
    centers = [c for c in centers if c[0] <= TOL]
    if centers:
        return boxes[min(centers)[2]], None, None
    best = None
    for b in boxes:
        d, fx, fy = b.snap(*p)
        if d <= TOL and (best is None or b.area < best[0].area):
            best = (b, fx, fy)
    return best


def build_doc(name, title, w, h, parsed, pats):
    doc = Doc(name, title, w, h)
    free = group_free(assign_texts(parsed))
    items = []
    for i, b in enumerate(parsed.boxes):
        b.cid = f"b{i}"
        value = merge_texts(b.texts).label() if b.texts else ""
        items.append((b.order, ("v", b.cid, value, box_style(b, pats), (b.x, b.y, b.w, b.h))))
    for i, it in enumerate(free):
        geo, st = text_vertex(it)
        items.append((it.order, ("v", f"t{i}", it.label(), st, geo)))
    for i, s in enumerate(parsed.strokes):
        st = "edgeStyle=none;html=1;rounded=0;" + stroke_style(s.st, pats)
        end, start = arrow_type(s.st.get("marker-end")), arrow_type(s.st.get("marker-start"))
        st += ("endArrow=block;endFill=1;" if end == "block" else f"endArrow={end};") if end else "endArrow=none;"
        if start:
            st += f"startArrow={start};"
        if s.fill:
            st += f"fillColor={s.fill};"
        a, z = glue_target(s.pts[0], parsed.boxes), glue_target(s.pts[-1], parsed.boxes)
        mid = [tuple(p) for p in s.pts[1:-1]] or None
        if a and z and a[0] is not z[0]:
            if a[1] is not None:
                st += f"exitX={a[1]};exitY={a[2]};"
            if z[1] is not None:
                st += f"entryX={z[1]};entryY={z[2]};"
            g = (a[0].cid, z[0].cid, mid, None, None)
        else:
            g = (None, None, mid, tuple(s.pts[0]), tuple(s.pts[-1]))
        items.append((s.order, ("e", f"e{i}", "", st, g)))
    doc.back = [it for _, it in sorted(items, key=lambda x: x[0])]
    return doc


# ---------------- 入口 ----------------
def load(path):
    src = open(path, encoding="utf-8").read()
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", src, re.S | re.I))
    m = re.search(r"<title>(.*?)</title>", src, re.S | re.I)
    title = html.unescape(m.group(1).strip()) if m else ""
    m = re.search(r"<svg\b.*?</svg>", src, re.S | re.I)
    if not m:
        raise ValueError("页面里没有找到 <svg>")
    svg = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)(\w+);",
                 lambda e: html.unescape(e.group(0)), m.group(0))
    root = ET.fromstring(svg)
    styler = Styler(parse_css(css))
    pats = pattern_colors(root, styler)
    vb = [float(v) for v in re.split(r"[\s,]+", root.get("viewBox", "").strip()) if v]
    if len(vb) == 4:
        ox, oy, w, h = vb
    else:
        ox, oy, w, h = 0, 0, num(root.get("width"), 1000), num(root.get("height"), 600)
    base = dict(DEFAULTS)
    base.update({k: v for k, v in styler.compute(root, DEFAULTS).items() if k in INHERITED})
    parsed = Parsed()
    for ch in root:
        walk(ch, base, (1, 0, 0, 1, -ox, -oy), styler, pats, parsed)
    name = re.sub(r"-v\d+$", "", os.path.splitext(os.path.basename(path))[0])
    return build_doc(name, title or name, w, h, parsed, pats)


def main():
    ap = argparse.ArgumentParser(description="把嵌有 SVG 的 HTML 示意图转成 Visio .vsdx")
    ap.add_argument("inputs", nargs="+", help=".html 文件或包含 .html 的目录")
    ap.add_argument("-o", "--out", required=True, help="输出目录")
    args = ap.parse_args()
    files = []
    for p in args.inputs:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith((".html", ".htm")))
        else:
            files.append(p)
    for f in files:
        print(f"{os.path.basename(f)} -> ", end="", flush=True)
        print(vsdx_writer.write(load(f), args.out))


if __name__ == "__main__":
    main()
