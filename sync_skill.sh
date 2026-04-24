#!/bin/bash

# 同步 skill 到 .cc-switch/skills 目录
# 用法: ./sync_skill.sh <skill-name>
# 示例: ./sync_skill.sh daily-report

set -e

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查参数
if [ $# -eq 0 ]; then
    echo -e "${RED}错误: 请指定要同步的 skill 名称${NC}"
    echo "用法: $0 <skill-name>"
    echo "示例: $0 daily-report"
    echo ""
    echo "可用的 skills:"
    ls -1 myskills/
    exit 1
fi

SKILL_NAME=$1
SOURCE_DIR="myskills/${SKILL_NAME}"
TARGET_DIR="/Users/bemied/.cc-switch/skills/${SKILL_NAME}"

# 检查源目录是否存在
if [ ! -d "$SOURCE_DIR" ]; then
    echo -e "${RED}错误: skill '${SKILL_NAME}' 不存在${NC}"
    echo "可用的 skills:"
    ls -1 myskills/
    exit 1
fi

# 检查目标目录是否存在
if [ ! -d "$TARGET_DIR" ]; then
    echo -e "${YELLOW}警告: 目标目录不存在，将创建: ${TARGET_DIR}${NC}"
    mkdir -p "$TARGET_DIR"
fi

# 执行同步
echo -e "${GREEN}开始同步 skill: ${SKILL_NAME}${NC}"
echo "源目录: $SOURCE_DIR"
echo "目标目录: $TARGET_DIR"
echo ""

# 使用 rsync 进行递归拷贝（保留权限和时间戳）
rsync -av --delete "$SOURCE_DIR/" "$TARGET_DIR/"

echo ""
echo -e "${GREEN}✓ 同步完成！${NC}"
echo ""
echo "同步的文件:"
ls -lh "$TARGET_DIR"
