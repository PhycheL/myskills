# 生成 Word

把定稿的 md 渲染成同名 .docx：版式统一，每张图是嵌入的 Visio 对象，在 Word 中双击即用 Visio 编辑。这一步可选，在用户确认文档没有问题、要 Word 版时做。

## 前提

每张图在 `vsdx/` 下都有同名 .vsdx。没有时先按 diagrams.md 的“转成 Visio”一节转换。

## 依赖

python-docx 1.2.0，装在本地磁盘工具目录的虚拟环境里（第 2 步的工具目录即可）：

```bash
python3 -m venv <工具目录>/venv
<工具目录>/venv/bin/pip install python-docx==1.2.0
```

check_docx.py 只用标准库，用系统的 python3 运行。

## 命令

```text
<工具目录>/venv/bin/python <skill>/scripts/word/build_docx.py <md 文件> [<md 文件> ...]
python3 <skill>/scripts/word/check_docx.py <md 文件>
cp <skill>/scripts/word/build_docx.py <文档目录>/tools/
```

- build_docx.py：每个 md 生成同目录、同名的 .docx。它只依赖 python-docx 与标准库，复制到 `tools/` 后，没装本 skill 的人只改正文时也能重新生成。
- check_docx.py：检查 md 同目录的同名 docx，每份文档运行一次。

## md 约定

约定写在 build_docx.py 的文件头里，以它为准。按本 skill 的格式写成、check_docs.py 通过的文档都满足它（已用实测文档和测试样例验证）。不符合时脚本指出行号并以非零码退出，不生成文档：回到 md 里按格式改，再重新生成。

## 图的嵌入

- 每张图是 Visio 对象：数据是 `vsdx/<名>.vsdx` 原文件，程序标识 Visio.Drawing.15。用 Visio 2013 及以后的版本双击即可编辑，退出编辑后 Word 用 Visio 重绘的图替换预览图；没装 Visio 时显示预览图。
- 预览图就是 `images/<名>.png`，与 Visio 页面覆盖同一区域，编辑后图才不变形。render.mjs 只截 Mermaid 输出的 svg 元素，正是为此。预览图与页面的宽高比相差超过 1% 时脚本报错：截图早于这个截图范围时就会这样，用 render.mjs 全部重截，再转 Visio。
- 显示尺寸按 Visio 页面等比缩小，以版心宽度和 22 cm 图高为限，不放大。

## 版式

版式由 build_docx.py 实现，check_docx.py 按同样的值检查；改版式时把这两个脚本和本节一起改。

- 页面：A4，上下边距 2.54 cm，左右 2.8 cm；页脚居中写“— 页码 —”。
- 正文：宋体，西文 Times New Roman，小四；1.5 倍行距，首行缩进 2 字符，两端对齐，段后 3 磅。
- 标题：文档名用标题（Title）样式，黑体小二加粗居中。`##` 至 `#####` 依次为 Word 内置标题 1 至 4：黑体三号、黑体四号、黑体小四、宋体小四加粗，一律黑色，与下段同页。编号是标题文字的一部分，不用 Word 自动编号。
- 图注、表注：黑体五号居中；图与图注同页，表注与表格同页。
- 表格：通栏，列宽按内容长度分配；表头行浅蓝底纹（DCE6F4），黑体加粗居中，跨页时重复；边框 0.5 磅，颜色 8C9BB0；单元格五号字，1.25 倍行距，行不跨页断开。
- 列表：无序列表用项目符号；有序列表保留 md 中的序号文字；都悬挂缩进。
- 修订记录、目录：两个标题用不进目录的标题样式，居中；目录另起一页，第 1 章另起一页。目录是 Word 目录域，收录标题 1 至 2，域结果预先写入 md 目录的条目；文档设为打开时更新域。
- 兼容性与属性：按 Word 2013 及以后的格式保存；样式字体不用主题字体；东亚语言为中文；文档属性只写标题。

## 校验

1. check_docx.py 通过：包结构、嵌入对象（数量、顺序、预览图与数据同字节、尺寸）、与 md 逐块对照、样式与页面设置。
2. 看版式：在 macOS 上用快速查看转成网页，`qlmanage -p -o <输出目录> <docx>`，用浏览器打开 `<输出目录>/<docx 文件名>.qlpreview/Preview.html`，看封面与目录、带图的一节、带表格的一节。快速查看不分页，表格按内容宽度排，嵌入对象的预览图不一定显示：这几项以 check_docx.py 和 Word 中的实际效果为准。

## 交付

在文档目录 README 的“生成 Word”一节（按 README-template.md）写给人看的用法。回复里告诉用户：

- Word 打开时询问是否更新域，选“是”，目录即生成带页码的条目。
- 双击图用 Visio 编辑；没装 Visio 时只显示预览图，不影响阅读和打印。
- 已知问题：宽图在 A4 版心中缩得较小，部分图的字偏小；个别表格的列宽可能要在 Word 中手调。
- 本机没有 Word 时，分页、目录页码和双击编辑的效果请用户在 Word 中确认。

## 改动后重新生成

- 改了图：render.mjs → export-svg.mjs → html_to_vsdx.py → check_vsdx.py → build_docx.py → check_docx.py。
- 只改正文：build_docx.py → check_docx.py。
