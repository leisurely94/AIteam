#!/usr/bin/env bash
# 在 GitHub 上创建仓库并推送当前目录。
#
# 用法：
#   GITHUB_TOKEN=ghp_xxx ./scripts/push_github.sh [仓库名] [public|private]
#
# 说明：
# - Token 需要 repo 权限（classic）或对目标仓库有 Contents 读写（fine-grained）。
# - Token 只在推送命令里使用，不会写入 .git/config。
# - 仓库已存在时不会报错，直接复用并推送。
set -euo pipefail

NAME="${1:-ai-team}"
VISIBILITY="${2:-public}"
PRIVATE="false"
if [ "$VISIBILITY" = "private" ]; then PRIVATE="true"; fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "缺少 GITHUB_TOKEN。示例：GITHUB_TOKEN=ghp_xxx ./scripts/push_github.sh ai-team public" >&2
  exit 2
fi

API="https://api.github.com"
AUTH_HEADER=(-H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github+json")

LOGIN=$(curl -fsS "${AUTH_HEADER[@]}" "${API}/user" | python3 -c 'import json,sys; print(json.load(sys.stdin)["login"])')
if [ -z "$LOGIN" ]; then
  echo "无法获取 GitHub 账号信息，请确认 Token 有效。" >&2
  exit 2
fi
echo "账号：${LOGIN}"

if curl -fsS "${AUTH_HEADER[@]}" "${API}/repos/${LOGIN}/${NAME}" >/dev/null 2>&1; then
  echo "仓库已存在：https://github.com/${LOGIN}/${NAME}"
else
  echo "创建仓库：${LOGIN}/${NAME}（${VISIBILITY}）"
  curl -fsS -X POST "${AUTH_HEADER[@]}" "${API}/user/repos" \
    -d "{\"name\":\"${NAME}\",\"private\":${PRIVATE},\"description\":\"主入口对话驱动的数字人团队：说清目标，自动组队并把项目做完\",\"auto_init\":false}" \
    >/dev/null
fi

if [ ! -d .git ]; then
  git init -b main
fi

git add -A
if ! git diff --cached --quiet; then
  git -c user.name="${GIT_AUTHOR_NAME:-${LOGIN}}" \
      -c user.email="${GIT_AUTHOR_EMAIL:-${LOGIN}@users.noreply.github.com}" \
      commit -m "feat: AI Team 数字人团队协作内核"
fi

git branch -M main
git remote remove origin >/dev/null 2>&1 || true
git remote add origin "https://github.com/${LOGIN}/${NAME}.git"

# 带 token 推送，但 token 不落到远端配置里
git push "https://x-access-token:${GITHUB_TOKEN}@github.com/${LOGIN}/${NAME}.git" HEAD:main

echo "完成：https://github.com/${LOGIN}/${NAME}"
