"""检查 .vsdx 包的结构：部件与内容类型、关系目标、XML 良构、形状编号、连接引用、数值、页面尺寸。

用法：python3 check_vsdx.py <vsdx 目录> <静态 SVG 目录>
页面尺寸按静态 SVG 的 viewBox（96 像素每英寸）核对。任一文件不通过时以非零码退出。
"""
import math
import os
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

NS = {"v": "http://schemas.microsoft.com/office/visio/2012/main"}
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def check(path, static_dir):
    errors = []
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if not n.endswith("/")]
    # 内容类型
    types = ET.fromstring(z.read("[Content_Types].xml"))
    defaults = {d.get("Extension").lower() for d in types.iter(CT + "Default")}
    overrides = {o.get("PartName").lstrip("/") for o in types.iter(CT + "Override")}
    for n in names:
        if n != "[Content_Types].xml" and n not in overrides and n.rsplit(".", 1)[-1].lower() not in defaults:
            errors.append(f"部件没有内容类型：{n}")
    for o in overrides:
        if o not in names:
            errors.append(f"内容类型登记的部件不存在：{o}")
    # XML 良构
    trees = {}
    for n in names:
        if n.endswith((".xml", ".rels")):
            try:
                trees[n] = ET.fromstring(z.read(n))
            except ET.ParseError as e:
                errors.append(f"XML 不良构：{n}：{e}")
    # 关系目标
    if "_rels/.rels" not in names:
        errors.append("缺少根关系文件 _rels/.rels")
    for n, t in trees.items():
        if not n.endswith(".rels"):
            continue
        base = posixpath.dirname(posixpath.dirname(n))
        for r in t.iter(REL + "Relationship"):
            if r.get("TargetMode") == "External":
                continue
            target = posixpath.normpath(posixpath.join(base, r.get("Target"))).lstrip("/")
            if target not in names:
                errors.append(f"关系目标不存在：{n} -> {r.get('Target')}")
    # 页面：形状编号、连接引用、数值
    page = trees.get("visio/pages/page1.xml")
    masters = {m.get("ID") for m in trees["visio/masters/masters.xml"].iter("{%s}Master" % NS["v"])}
    shapes = {}
    for s in page.iter("{%s}Shape" % NS["v"]):
        sid = s.get("ID")
        if sid in shapes:
            errors.append(f"形状编号重复：{sid}")
        shapes[sid] = s
        if s.get("Master") and s.get("Master") not in masters:
            errors.append(f"形状 {sid} 引用的主控形状不存在：{s.get('Master')}")
    for c in page.iter("{%s}Cell" % NS["v"]):
        v = c.get("V")
        if v is not None and re.fullmatch(r"-?(nan|inf|infinity)", v.strip().lower()):
            errors.append(f"数值非有限：{c.get('N')}={v}")
        try:
            if v is not None and not math.isfinite(float(v)):
                errors.append(f"数值非有限：{c.get('N')}={v}")
        except ValueError:
            pass
    connects = list(page.iter("{%s}Connect" % NS["v"]))
    for c in connects:
        frm, to, cell = c.get("FromSheet"), c.get("ToSheet"), c.get("ToCell")
        if frm not in shapes or shapes[frm].get("Master") is None:
            errors.append(f"连接的起点不是连接线：{frm}")
        if to not in shapes:
            errors.append(f"连接目标形状不存在：{to}")
            continue
        m = re.fullmatch(r"Connections\.X(\d+)", cell)
        if m:
            rows = [r.get("IX") for sec in shapes[to].findall("v:Section", NS) if sec.get("N") == "Connection"
                    for r in sec.findall("v:Row", NS)]
            if str(int(m.group(1)) - 1) not in rows:
                errors.append(f"连接点不存在：形状 {to} 的 {cell}")
        elif cell != "PinX":
            errors.append(f"未知的连接目标单元格：{cell}")
    # 页面尺寸
    src = os.path.join(static_dir, os.path.basename(path)[:-5] + ".html")
    vb = re.search(r'viewBox="([^"]+)"', open(src, encoding="utf-8").read()).group(1).split()
    cells = {c.get("N"): float(c.get("V")) for c in trees["visio/pages/pages.xml"].iter("{%s}Cell" % NS["v"])
             if c.get("N") in ("PageWidth", "PageHeight")}
    if abs(cells["PageWidth"] - float(vb[2]) / 96) > 1e-4 or abs(cells["PageHeight"] - float(vb[3]) / 96) > 1e-4:
        errors.append(f"页面尺寸与图不一致：{cells} vs viewBox {vb}")
    return errors, len(shapes), len(connects) // 2


def main():
    if len(sys.argv) != 3:
        print("用法：python3 check_vsdx.py <vsdx 目录> <静态 SVG 目录>")
        sys.exit(2)
    vsdx_dir, static_dir = sys.argv[1], sys.argv[2]
    failed = 0
    files = sorted(f for f in os.listdir(vsdx_dir) if f.endswith(".vsdx"))
    for f in files:
        errors, shape_count, connector_count = check(os.path.join(vsdx_dir, f), static_dir)
        status = "通过" if not errors else "不通过"
        print(f"{f}\t{status}\t形状 {shape_count}\t粘连连接线 {connector_count}")
        for e in errors:
            print("  " + e)
        failed += bool(errors)
    print(f"共 {len(files)} 个，不通过 {failed} 个")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
