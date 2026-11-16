"""把 make_drawio.Diagram 的内容写成原生 Visio (.vsdx) 文件。

骨架（document.xml 样式表、动态连接线主控形状等）取自 Visio 生成的模板，
本模块只负责重写页面、页面尺寸和文档属性。
"""
import datetime
import html
import math
import os
import re
import shutil
import zipfile
from html.parser import HTMLParser

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vsdx_template")
DPI = 96.0
FONT = "Microsoft YaHei"
CONNECTOR_MASTER = 2
# 箭头类型 → Visio 线端编号。block 是原有写法（实心三角）；
# 空心类线端用白色填充，实心菱形没有内置线端，用空心菱形并把填充设为线色。
ARROWS = {"block": 4, "filled-triangle": 4, "open-arrow": 1, "hollow-triangle": 16,
          "hollow-diamond": 22, "filled-diamond": 22, "filled-circle": 10}
HOLLOW_ARROWS = {"hollow-triangle", "hollow-diamond"}


def parse_style(st):
    d = {}
    for part in st.split(";"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            d[k] = v
        else:
            d[part] = "1"
    return d


class _Runs(HTMLParser):
    """把 draw.io 的 HTML 标签（b / br / font）拆成带格式的文字片段。"""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.stack = [base]
        self.runs = []

    def handle_starttag(self, tag, attrs):
        cur = dict(self.stack[-1])
        a = dict(attrs)
        if tag == "br":
            self.runs.append(("\n", self.stack[-1]))
            return
        if tag == "b":
            cur["bold"] = True
        if tag == "font":
            m = re.search(r"font-size:\s*(\d+(?:\.\d+)?)px", a.get("style", ""))
            if m:
                cur["size"] = float(m.group(1))
            if a.get("color"):
                cur["color"] = a["color"]
        self.stack.append(cur)

    def handle_endtag(self, tag):
        if tag in ("b", "font") and len(self.stack) > 1:
            self.stack.pop()

    def handle_data(self, data):
        if data:
            self.runs.append((data, self.stack[-1]))


def rich_text(value, st):
    base = dict(size=float(st.get("fontSize", 14)), color=st.get("fontColor", "#222222"),
                bold=int(st.get("fontStyle", "0")) & 1 == 1)
    p = _Runs(base)
    p.feed(value)
    p.close()
    return p.runs


def esc(s):
    return html.escape(s, quote=True)


def cell(n, v, f=None, u=None):
    s = f"<Cell N='{n}' V='{esc(str(v))}'"
    if u:
        s += f" U='{u}'"
    if f:
        s += f" F='{esc(f)}'"
    return s + "/>"


def inch(px):
    return round(px / DPI, 6)


def pt_in(px):
    return round(px * 0.75 / 72.0, 6)


def char_row(ix, size, color, bold):
    # Visio 只让第 0 行从文字样式继承；第 1 行起缺的单元格一律按 0 处理
    # （FontScale=0 会把文字压成一团），所以每行都写全。
    return (f"<Row IX='{ix}'>{cell('Font', FONT)}{cell('Color', color)}{cell('Style', 1 if bold else 0)}"
            f"{cell('Case', 0)}{cell('Pos', 0)}{cell('FontScale', 1)}{cell('Size', pt_in(size), u='PT')}"
            f"{cell('DblUnderline', 0)}{cell('Overline', 0)}{cell('Strikethru', 0)}"
            f"{cell('DoubleStrikethrough', 0)}{cell('Letterspace', 0)}{cell('ColorTrans', 0)}"
            f"{cell('AsianFont', FONT)}{cell('ComplexScriptFont', 'Themed', 'THEMEVAL()')}"
            f"{cell('ComplexScriptSize', -1)}{cell('LangID', 'zh-CN')}</Row>")


def para_row(h_align, sp_line=-1.2):
    # SpLine 取负值表示“字号的百分比”，-1.2 即 120% 行距
    return (f"<Row IX='0'>{cell('IndFirst', 0)}{cell('IndLeft', 0)}{cell('IndRight', 0)}"
            f"{cell('SpLine', sp_line)}{cell('SpBefore', 0)}{cell('SpAfter', 0)}"
            f"{cell('HorzAlign', h_align)}{cell('Bullet', 0)}{cell('BulletStr', '')}"
            f"{cell('BulletFont', 0)}{cell('BulletFontSize', -1)}{cell('TextPosAfterBullet', 0)}"
            f"{cell('Flags', 0)}</Row>")


def text_xml(value, st, h_align):
    """返回 (Character 节, Paragraph 节, Text 元素)。

    与 Visio 自己保存的写法一致：开头一个 cp + pp 标记，换行符留在上一段文字末尾，
    之后每换一种格式只插 cp 标记；所有段落共用第 0 行段落格式。
    """
    runs = rich_text(value, st)
    merged = []
    for t, fmt in runs:
        key = (fmt["size"], fmt["color"], fmt["bold"])
        if t == "\n":
            if merged:
                merged[-1][1] += "\n"
        elif merged and merged[-1][0] == key and not merged[-1][1].endswith("\n"):
            merged[-1][1] += t
        else:
            merged.append([key, t])
    if merged:
        merged[-1][1] = merged[-1][1].rstrip("\n")
    if not merged or not any(t for _, t in merged):
        return "", "", ""
    chars, text = [], []
    for i, ((size, color, bold), t) in enumerate(merged):
        chars.append(char_row(i, size, color, bold))
        text.append(f"<cp IX='{i}'/>" + ("<pp IX='0'/>" if i == 0 else "") + esc(t))
    return (f"<Section N='Character'>{''.join(chars)}</Section>",
            f"<Section N='Paragraph'>{para_row(h_align, float(st.get('vsdxSpLine', -1.2)))}</Section>",
            "<Text>" + "".join(text) + "</Text>")


H_ALIGN = {"left": 0, "center": 1, "right": 2}


def line_cells(st, default_color="#333333"):
    color = st.get("strokeColor", default_color)
    out = ""
    if color == "none":
        out += cell("LinePattern", 0)
    else:
        out += cell("LineColor", color)
        out += cell("LinePattern", 2 if st.get("dashed") == "1" else 1)
        out += cell("LineWeight", pt_in(float(st.get("strokeWidth", 1))), u="PT")
    return out


def arrow_fill_cells(st, default_color="#333333"):
    """按两端线端决定 1-D 形状的填充：有实心菱形时填线色，有空心线端时填白色，其余不写。"""
    ends = {st.get("endArrow"), st.get("startArrow")}
    if "filled-diamond" in ends:
        return cell("FillForegnd", st.get("strokeColor", default_color)) + cell("FillPattern", 1)
    if ends & HOLLOW_ARROWS:
        return cell("FillForegnd", "#ffffff") + cell("FillPattern", 1)
    return ""


class Writer:
    def __init__(self, diagram):
        self.d = diagram
        self.H = diagram.h
        self.ids = {}
        self.verts = {}
        self.conn_pts = {}  # vertex id -> [(fx, fy)]
        self.shapes = []
        self.connects = []

    def sid(self, cid):
        if cid not in self.ids:
            self.ids[cid] = len(self.ids) + 1
        return self.ids[cid]

    # ---------------- 几何计算 ----------------
    def center(self, cid):
        x, y, w, h = self.verts[cid][1]
        return x + w / 2, y + h / 2

    def boundary(self, cid, toward):
        st, (x, y, w, h) = self.verts[cid]
        cx, cy = x + w / 2, y + h / 2
        dx, dy = toward[0] - cx, toward[1] - cy
        if dx == 0 and dy == 0:
            return cx, cy
        if "ellipse" in st:
            t = 1.0 / ((dx / (w / 2)) ** 2 + (dy / (h / 2)) ** 2) ** 0.5
        elif "rhombus" in st:
            t = 1.0 / (abs(dx) / (w / 2) + abs(dy) / (h / 2))
        else:
            tx = (w / 2) / abs(dx) if dx else float("inf")
            ty = (h / 2) / abs(dy) if dy else float("inf")
            t = min(tx, ty)
        return cx + dx * t, cy + dy * t

    def point_on(self, cid, fx, fy):
        x, y, w, h = self.verts[cid][1]
        return x + fx * w, y + fy * h

    def conn_index(self, cid, fx, fy):
        pts = self.conn_pts.setdefault(cid, [])
        key = (round(fx, 4), round(fy, 4))
        if key not in pts:
            pts.append(key)
        return pts.index(key)

    def vx(self, px):
        return inch(px)

    def vy(self, py):
        return inch(self.H - py)

    # ---------------- 形状 ----------------
    def vertex_xml(self, cid, value, st, geo):
        x, y, w, h = geo
        sid = self.sid(cid)
        W, Hh = inch(w), inch(h)
        is_text = "text" in st
        s = [f"<Shape ID='{sid}' Type='Shape' LineStyle='3' FillStyle='3' TextStyle='3'>",
             cell("PinX", self.vx(x + w / 2)), cell("PinY", self.vy(y + h / 2)),
             cell("Width", W), cell("Height", Hh),
             cell("LocPinX", round(W / 2, 6), "Width*0.5"), cell("LocPinY", round(Hh / 2, 6), "Height*0.5"),
             cell("Angle", 0), cell("FlipX", 0), cell("FlipY", 0), cell("ResizeMode", 0)]
        if is_text:
            s += [cell("LinePattern", 0), cell("FillPattern", 0)]
            bg = st.get("labelBackgroundColor")
            if bg:
                s.append(cell("TextBkgnd", bg))
            margin = 0
        else:
            s.append(line_cells(st))
            fill = st.get("fillColor", "#ffffff")
            s.append(cell("FillPattern", 0) if fill == "none" else
                     cell("FillForegnd", fill) + cell("FillPattern", 1))
            s.append(cell("ShapeShdwShow", 0))
            r = float(st.get("arcSize", 0)) / 2 if st.get("rounded") == "1" else 0
            s.append(cell("Rounding", inch(r)))
            margin = 3
        top = st.get("verticalAlign") == "top"
        s += [cell("LeftMargin", inch(margin)), cell("RightMargin", inch(margin)),
              cell("TopMargin", inch(margin)), cell("BottomMargin", inch(margin)),
              cell("VerticalAlign", 0 if top else 1)]
        # 文本块显式跟随形状尺寸；顶部对齐的标题用文本块高度表达上边距
        off = inch(float(st.get("spacingTop", 0))) if top else 0
        th = round(Hh - off, 6)
        s += [cell("TxtPinX", round(W / 2, 6), "Width*0.5"),
              cell("TxtPinY", round(th / 2, 6), f"(Height-{off})*0.5"),
              cell("TxtWidth", W, "Width*1"), cell("TxtHeight", th, f"Height-{off}"),
              cell("TxtLocPinX", round(W / 2, 6), "TxtWidth*0.5"),
              cell("TxtLocPinY", round(th / 2, 6), "TxtHeight*0.5"), cell("TxtAngle", 0)]
        chars, para, text = text_xml(value, st, H_ALIGN.get(st.get("align", "center"), 1))
        s += [chars, para]
        if cid in self.conn_pts:
            rows = "".join(
                f"<Row IX='{i}'>{cell('X', round(W * fx, 6), f'Width*{fx}')}"
                f"{cell('Y', round(Hh * (1 - fy), 6), f'Height*{round(1 - fy, 4)}')}"
                f"{cell('DirX', 0)}{cell('DirY', 0)}{cell('Type', 0)}{cell('AutoGen', 0)}</Row>"
                for i, (fx, fy) in enumerate(self.conn_pts[cid]))
            s.append(f"<Section N='Connection'>{rows}</Section>")
        # 纯文字形状也给一个透明矩形几何，便于在 Visio 中点选和拖动
        nofill = 1 if is_text or st.get("fillColor") == "none" else 0
        noline = 1 if is_text else 0
        if "ellipse" in st:
            # 椭圆行：圆心 (X, Y)、长轴端点 (A, B)、短轴端点 (C, D)
            rows = (f"<Row T='Ellipse' IX='1'>{cell('X', round(W / 2, 6), 'Width*0.5')}"
                    f"{cell('Y', round(Hh / 2, 6), 'Height*0.5')}{cell('A', W, 'Width*1')}"
                    f"{cell('B', round(Hh / 2, 6), 'Height*0.5')}{cell('C', round(W / 2, 6), 'Width*0.5')}"
                    f"{cell('D', Hh, 'Height*1')}</Row>")
        else:
            if "rhombus" in st:
                pts = [(0.5, 0), (1, 0.5), (0.5, 1), (0, 0.5), (0.5, 0)]
            else:
                pts = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
            rows = "".join(
                f"<Row T='{'RelMoveTo' if i == 0 else 'RelLineTo'}' IX='{i + 1}'>{cell('X', px)}{cell('Y', py)}</Row>"
                for i, (px, py) in enumerate(pts))
        s.append(f"<Section N='Geometry' IX='0'>{cell('NoFill', nofill)}{cell('NoLine', noline)}"
                 f"{cell('NoShow', 0)}{cell('NoSnap', 0)}{cell('NoQuickDrag', 0)}{rows}</Section>")
        s += [text, "</Shape>"]
        return "".join(s)

    def plain_line_xml(self, cid, st, points):
        """不粘连的线。两点时写成一维直线；多点折线写成按外框定位的二维形状。"""
        sid = self.sid(cid)
        arrows = ""
        if st.get("endArrow") in ARROWS:
            arrows += cell("EndArrow", ARROWS[st["endArrow"]]) + cell("EndArrowSize", 1)
        if st.get("startArrow") in ARROWS:
            arrows += cell("BeginArrow", ARROWS[st["startArrow"]]) + cell("BeginArrowSize", 1)
        # 闭合的填充多边形（fillColor）画填充；线端需要的填充见 arrow_fill_cells
        fill = st.get("fillColor", "none")
        arrows += (cell("FillForegnd", fill) + cell("FillPattern", 1)) if fill != "none" else arrow_fill_cells(st)
        head = f"<Shape ID='{sid}' Type='Shape' LineStyle='3' FillStyle='3' TextStyle='3'>"
        geo_head = (f"<Section N='Geometry' IX='0'>{cell('NoFill', 0 if fill != 'none' else 1)}{cell('NoLine', 0)}"
                    f"{cell('NoShow', 0)}{cell('NoSnap', 0)}{cell('NoQuickDrag', 0)}")
        if len(points) == 2:
            p0, p1 = points
            bx, by, ex, ey = self.vx(p0[0]), self.vy(p0[1]), self.vx(p1[0]), self.vy(p1[1])
            L = round(((ex - bx) ** 2 + (ey - by) ** 2) ** 0.5, 6)
            return "".join([
                head,
                cell("PinX", round((bx + ex) / 2, 6), "(BeginX+EndX)/2"),
                cell("PinY", round((by + ey) / 2, 6), "(BeginY+EndY)/2"),
                cell("Width", L, "SQRT((EndX-BeginX)^2+(EndY-BeginY)^2)"), cell("Height", 0),
                cell("LocPinX", round(L / 2, 6), "Width*0.5"), cell("LocPinY", 0, "Height*0.5"),
                # 缓存值也写成实际角度：不重算公式的阅读器（如 libvisio）直接用这个值，写 0 会把竖线画成横线
                cell("Angle", round(math.atan2(ey - by, ex - bx), 6) or 0, "ATAN2(EndY-BeginY,EndX-BeginX)"),
                cell("BeginX", bx), cell("BeginY", by), cell("EndX", ex), cell("EndY", ey),
                line_cells(st), arrows,
                geo_head,
                f"<Row T='MoveTo' IX='1'>{cell('X', 0, 'Width*0')}{cell('Y', 0)}</Row>"
                f"<Row T='LineTo' IX='2'>{cell('X', L, 'Width*1')}{cell('Y', 0)}</Row></Section>",
                "</Shape>"])
        xs = [self.vx(p[0]) for p in points]
        ys = [self.vy(p[1]) for p in points]
        x0, y0 = min(xs), min(ys)
        W, Hh = round(max(xs) - x0, 6), round(max(ys) - y0, 6)
        rows = "".join(
            f"<Row T='{'MoveTo' if i == 0 else 'LineTo'}' IX='{i + 1}'>"
            f"{cell('X', round(px - x0, 6))}{cell('Y', round(py - y0, 6))}</Row>"
            for i, (px, py) in enumerate(zip(xs, ys)))
        return "".join([
            head,
            cell("PinX", round(x0 + W / 2, 6)), cell("PinY", round(y0 + Hh / 2, 6)),
            cell("Width", W), cell("Height", Hh),
            cell("LocPinX", round(W / 2, 6), "Width*0.5"), cell("LocPinY", round(Hh / 2, 6), "Height*0.5"),
            cell("Angle", 0), line_cells(st), arrows,
            geo_head, rows, "</Section>", "</Shape>"])

    def connector_xml(self, cid, value, st, src, tgt, pts):
        sid = self.sid(cid)
        ends = []
        for end, vid, other, fx_k, fy_k in (("Begin", src, tgt, "exitX", "exitY"),
                                            ("End", tgt, src, "entryX", "entryY")):
            if fx_k in st:
                fx, fy = float(st[fx_k]), float(st[fy_k])
                p = self.point_on(vid, fx, fy)
                ix = self.conn_index(vid, fx, fy)
                ends.append((p, vid, ix))
            else:
                ref = pts[0 if end == "Begin" else -1] if pts else self.center(other)
                ends.append((self.boundary(vid, ref), vid, None))
        (b, bs, bix), (e, es, eix) = ends
        bx, by, ex, ey = self.vx(b[0]), self.vy(b[1]), self.vx(e[0]), self.vy(e[1])
        W, Hh = round(ex - bx, 6), round(ey - by, 6)

        def glue_formula(vid, ix, first):
            n = self.sid(vid)
            if ix is None:
                return ("_WALKGLUE(BegTrigger,EndTrigger,WalkPreference)" if first
                        else "_WALKGLUE(EndTrigger,BegTrigger,WalkPreference)")
            return f"PAR(PNT(Sheet.{n}!Connections.X{ix + 1},Sheet.{n}!Connections.Y{ix + 1}))"

        dynamic = bix is None and eix is None
        route = 16 if dynamic else (1 if pts else 2)
        s = [f"<Shape ID='{sid}' Type='Shape' Master='{CONNECTOR_MASTER}'>",
             cell("PinX", round((bx + ex) / 2, 6), "Inh"), cell("PinY", round((by + ey) / 2, 6), "Inh"),
             cell("Width", W, "GUARD(EndX-BeginX)"), cell("Height", Hh, "GUARD(EndY-BeginY)"),
             cell("LocPinX", round(W / 2, 6), "Inh"), cell("LocPinY", round(Hh / 2, 6), "Inh"),
             cell("BeginX", bx, glue_formula(bs, bix, True)), cell("BeginY", by, glue_formula(bs, bix, True)),
             cell("EndX", ex, glue_formula(es, eix, False)), cell("EndY", ey, glue_formula(es, eix, False)),
             cell("LayerMember", 0),
             cell("BegTrigger", 2, f"_XFTRIGGER(Sheet.{self.sid(bs)}!EventXFMod)"),
             cell("EndTrigger", 2, f"_XFTRIGGER(Sheet.{self.sid(es)}!EventXFMod)"),
             cell("ShapeRouteStyle", route), cell("ConLineRouteExt", 1),
             line_cells(st),
             cell("EndArrow", ARROWS.get(st.get("endArrow"), 0)),
             cell("EndArrowSize", 1), cell("BeginArrow", ARROWS.get(st.get("startArrow"), 0))]
        # 主控形状的起点线端大小随主题，有起点线端时写明，与终点一致
        if ARROWS.get(st.get("startArrow"), 0):
            s.append(cell("BeginArrowSize", 1))
        s.append(arrow_fill_cells(st))
        if value:
            s += [cell("TextBkgnd", st.get("labelBackgroundColor", "#ffffff")),
                  cell("TxtPinX", round(W / 2, 6), "Inh"), cell("TxtPinY", round(Hh / 2, 6), "Inh"),
                  f"<Section N='Control'><Row N='TextPosition'>{cell('X', round(W / 2, 6))}"
                  f"{cell('Y', round(Hh / 2, 6))}{cell('XDyn', round(W / 2, 6), 'Inh')}"
                  f"{cell('YDyn', round(Hh / 2, 6), 'Inh')}{cell('XCon', 0, 'Inh')}</Row></Section>"]
            chars, para, text = text_xml(value, st, 1)
            s += [chars, para]
        rows = []
        path = [(self.vx(px) - bx, self.vy(py) - by) for px, py in (pts or [])] + [(W, Hh)]
        for i, (lx, ly) in enumerate(path):
            rows.append(f"<Row T='LineTo' IX='{i + 2}'>{cell('X', round(lx, 6))}{cell('Y', round(ly, 6))}</Row>")
        if len(path) == 1:
            rows.append("<Row T='LineTo' IX='3' Del='1'/>")
        s.append(f"<Section N='Geometry' IX='0'>{''.join(rows)}</Section>")
        if value:
            s.append(text)
        s.append("</Shape>")

        self.connects.append(
            f"<Connect FromSheet='{sid}' FromCell='BeginX' FromPart='9' ToSheet='{self.sid(bs)}' "
            + ("ToCell='PinX' ToPart='3'/>" if bix is None
               else f"ToCell='Connections.X{bix + 1}' ToPart='{100 + bix}'/>"))
        self.connects.append(
            f"<Connect FromSheet='{sid}' FromCell='EndX' FromPart='12' ToSheet='{self.sid(es)}' "
            + ("ToCell='PinX' ToPart='3'/>" if eix is None
               else f"ToCell='Connections.X{eix + 1}' ToPart='{100 + eix}'/>"))
        return "".join(s)

    def page_xml(self):
        items = self.d.back + self.d.edges + self.d.front
        for kind, cid, value, style, g in items:
            if kind == "v":
                self.verts[cid] = (parse_style(style), g)
                self.sid(cid)
        # 先生成连线，登记好各形状需要的连接点，再生成形状
        edge_xml = {}
        for kind, cid, value, style, g in items:
            if kind != "e":
                continue
            st = parse_style(style)
            src, tgt, pts, sp, tp = g
            if src and tgt:
                edge_xml[cid] = self.connector_xml(cid, value, st, src, tgt, pts)
            else:
                edge_xml[cid] = self.plain_line_xml(cid, st, [sp] + list(pts or []) + [tp])
        body = []
        for kind, cid, value, style, g in items:
            if kind == "v":
                body.append(self.vertex_xml(cid, value, self.verts[cid][0], g))
            else:
                body.append(edge_xml[cid])
        return ("<?xml version='1.0' encoding='utf-8' ?>"
                "<PageContents xmlns='http://schemas.microsoft.com/office/visio/2012/main' "
                "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships' "
                "xml:space='preserve'><Shapes>" + "".join(body) + "</Shapes><Connects>"
                + "".join(self.connects) + "</Connects></PageContents>")


def write(diagram, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    w = Writer(diagram)
    page = w.page_xml()
    pw, ph = inch(diagram.w), inch(diagram.h)
    name = esc(diagram.title)

    def read(p):
        with open(os.path.join(TEMPLATE, p), encoding="utf-8") as f:
            return f.read()

    parts = {}
    for root, _, files in os.walk(TEMPLATE):
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), TEMPLATE)
            # 跳过缩略图和以点开头的系统文件（如 .DS_Store），关系文件 .rels 除外
            if rel.endswith(".emf") or (fn.startswith(".") and fn != ".rels"):
                continue
            parts[rel] = read(rel)

    parts["visio/pages/page1.xml"] = page
    pages = parts["visio/pages/pages.xml"]
    pages = re.sub(r"NameU='Page-1' Name='Page-1'", f"NameU='{name}' Name='{name}'", pages)
    pages = re.sub(r"ViewCenterX='[^']*' ViewCenterY='[^']*'",
                   f"ViewCenterX='{pw / 2}' ViewCenterY='{ph / 2}'", pages)
    pages = re.sub(r"<Cell N='PageWidth' V='[^']*'/>", f"<Cell N='PageWidth' V='{pw}'/>", pages)
    pages = re.sub(r"<Cell N='PageHeight' V='[^']*'/>", f"<Cell N='PageHeight' V='{ph}'/>", pages)
    parts["visio/pages/pages.xml"] = pages
    parts["visio/windows.xml"] = re.sub(r"ViewCenterX='[^']*' ViewCenterY='[^']*'",
                                        f"ViewCenterX='{pw / 2}' ViewCenterY='{ph / 2}'",
                                        parts["visio/windows.xml"])
    parts["docProps/app.xml"] = parts["docProps/app.xml"].replace(
        "<vt:lpstr>Page-1</vt:lpstr>", f"<vt:lpstr>{name}</vt:lpstr>")
    core = parts["docProps/core.xml"]
    core = re.sub(r"<dc:title>.*?</dc:title>", "", core)
    core = re.sub(r"<dc:creator>.*?</dc:creator>", "<dc:creator></dc:creator>", core)
    core = re.sub(r"<cp:lastModifiedBy>.*?</cp:lastModifiedBy>", "", core)
    core = re.sub(r"<cp:lastPrinted>.*?</cp:lastPrinted>", "", core)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = re.sub(r"(<dcterms:(?:created|modified)[^>]*>)[^<]*", rf"\g<1>{now}", core)
    core = core.replace("<dc:language>en-GB</dc:language>", "<dc:language>zh-CN</dc:language>")
    parts["docProps/core.xml"] = core.replace("</cp:coreProperties>",
                                              f"<dc:title>{name}</dc:title></cp:coreProperties>")
    parts["_rels/.rels"] = re.sub(r"<Relationship [^>]*thumbnail[^>]*/>", "", parts["_rels/.rels"])
    doc = parts["visio/document.xml"]
    if f"NameU='{FONT}'" not in doc:
        doc = doc.replace("</FaceNames>", f"<FaceName NameU='{FONT}'/></FaceNames>")
    parts["visio/document.xml"] = doc

    out = os.path.join(out_dir, f"{diagram.name}.vsdx")
    order = ["[Content_Types].xml", "_rels/.rels"] + sorted(
        p for p in parts if p not in ("[Content_Types].xml", "_rels/.rels"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in order:
            z.writestr(p, parts[p])
    return out
