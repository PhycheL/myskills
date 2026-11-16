"""把一组 PNG 按每页 N 张（默认 4 张，两列）拼成总览图，便于整体检查；每张图上方标出文件名。

用法：python3 contact_sheet.py <图片目录> <前缀> <输出目录> [每页张数]
输出 <输出目录>/<前缀>sheet-01.png 等。依赖 Pillow；找不到中文字体时用 Pillow 的默认字体。
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CELL_W, CELL_H, LABEL_H = 900, 1000, 36
FONT_CANDIDATE_LIST = (
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/STHeiti Medium.ttc',
    'C:/Windows/Fonts/msyh.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
)


def load_font():
    for path in FONT_CANDIDATE_LIST:
        try:
            return ImageFont.truetype(path, 24)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    if len(sys.argv) not in (4, 5):
        print('用法：python3 contact_sheet.py <图片目录> <前缀> <输出目录> [每页张数]')
        sys.exit(2)
    image_dir, prefix, out_dir = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    per_page = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    paths = sorted(p for p in image_dir.glob(prefix + '*.png') if 'sheet-' not in p.name)
    if not paths:
        print(f'{image_dir} 中没有以 {prefix} 开头的 PNG')
        sys.exit(1)
    out_dir.mkdir(parents=True, exist_ok=True)
    font = load_font()
    cols = 2
    for start in range(0, len(paths), per_page):
        group = paths[start:start + per_page]
        rows = (len(group) + cols - 1) // cols
        sheet = Image.new('RGB', (CELL_W * cols, (CELL_H + LABEL_H) * rows), 'white')
        draw = ImageDraw.Draw(sheet)
        for i, path in enumerate(group):
            img = Image.open(path).convert('RGB')
            img.thumbnail((CELL_W - 20, CELL_H - 10))
            x = (i % cols) * CELL_W + 10
            y = (i // cols) * (CELL_H + LABEL_H)
            draw.text((x, y + 4), path.stem, fill='black', font=font)
            sheet.paste(img, (x, y + LABEL_H))
            draw.rectangle([x - 5, y, x + CELL_W - 15, y + CELL_H + LABEL_H - 5], outline='#cccccc')
        stem = prefix if not prefix or prefix.endswith('-') else prefix + '-'
        out = out_dir / f'{stem}sheet-{start // per_page + 1:02d}.png'
        sheet.save(out)
        print(out)


if __name__ == '__main__':
    main()
