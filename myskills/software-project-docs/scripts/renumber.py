"""按出现顺序把文档中的图号、表号在各章内连续编号，并同步正文引用与图片说明文字。

图注是图片下一行单独的“图 X-Y 名称”，表注是表格上方单独的“表 X-Y 名称”。
重编在一次正则替换中完成，旧号到新号不会连环替换。
有重复的图注、表注，或正文引用了不存在的图号、表号时，列出问题、不改文件并以非零码退出。

用法：python3 renumber.py <文档文件>
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # 导入同目录模块时不在 skill 目录里生成 __pycache__
from doc_types import NUMBER_REF_RE, iter_captions, read_lines


def build_mapping(lines):
    mapping = {'图': {}, '表': {}}
    counter = {'图': {}, '表': {}}
    duplicate_list = []
    for kind, chapter, number, _ in iter_captions(lines):
        old_key = f'{chapter}-{number}'
        if old_key in mapping[kind]:
            duplicate_list.append(f'{kind} {old_key}')
            continue
        counter[kind][chapter] = counter[kind].get(chapter, 0) + 1
        mapping[kind][old_key] = f'{chapter}-{counter[kind][chapter]}'
    return mapping, duplicate_list


def main():
    if len(sys.argv) != 2:
        print('用法：python3 renumber.py <文档文件>')
        sys.exit(2)
    path = Path(sys.argv[1])
    lines = read_lines(path)
    mapping, duplicate_list = build_mapping(lines)
    dangling_list = []

    def replace(match):
        label, chapter, number = match.group(1), match.group(2), match.group(3)
        old_key = f'{chapter}-{number}'
        new_key = mapping[label].get(old_key)
        if new_key is None:
            dangling_list.append(match.group(0))
            return match.group(0)
        separator = ' ' if match.group(0)[1] == ' ' else ''
        return f'{label}{separator}{new_key}'

    text = NUMBER_REF_RE.sub(replace, '\n'.join(lines) + '\n')
    if duplicate_list:
        print('重复的图注或表注：' + '、'.join(duplicate_list))
    if dangling_list:
        print('引用了不存在的图号或表号：' + '、'.join(sorted(set(dangling_list))))
    if duplicate_list or dangling_list:
        print('未修改文件')
        sys.exit(1)
    path.write_text(text, encoding='utf-8')
    changed = sum(1 for k in mapping for o, n in mapping[k].items() if o != n)
    print(f'图 {len(mapping["图"])} 个、表 {len(mapping["表"])} 个，改号 {changed} 个')


if __name__ == '__main__':
    main()
