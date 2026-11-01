#!/bin/bash
# macOS 一键启动脚本（Finder 中双击即可在顶部菜单栏驻留）
set -e
cd "$(dirname "$0")" || exit 1

fail() {
  echo "启动失败：$1"
  if [ -t 0 ]; then
    read -r -p "按回车关闭窗口…" _answer
  fi
  exit 1
}

if [ ! -x .venv/bin/python ]; then
  PYTHON="$(command -v python3 || true)"
  # Finder 启动的 Terminal 不一定包含 Homebrew 的 PATH。
  if [ -z "$PYTHON" ] || [ "$PYTHON" = /usr/bin/python3 ]; then
    for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
      if [ -x "$candidate" ]; then
        PYTHON="$candidate"
        break
      fi
    done
  fi
  [ -n "$PYTHON" ] || fail "未找到 Python 3，请先安装 Python 3。"
  "$PYTHON" -m venv .venv || fail "无法创建项目 Python 环境。"
fi
PYTHON="$PWD/.venv/bin/python"
if ! "$PYTHON" -c "import flask, pystray, PIL, zstandard" >/dev/null 2>&1; then
  echo "首次启动：正在安装项目依赖…"
  "$PYTHON" -m pip install --disable-pip-version-check flask pystray pillow zstandard || \
    fail "依赖安装失败，请检查网络后再次双击。"
fi

mkdir -p data
if [ -f data/tray.pid ]; then
  old_pid="$(cat data/tray.pid)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    command_line="$(ps -p "$old_pid" -o command= || true)"
    if [[ "$command_line" == *"$PWD/tray.py"* ]]; then
      echo "Agent Watch 已在运行，请使用顶部菜单栏图标。"
      exit 0
    fi
  fi
fi
nohup "$PYTHON" "$PWD/tray.py" > data/tray.out.log 2> data/tray.err.log < /dev/null &
tray_pid=$!
echo "$tray_pid" > data/tray.pid
disown
sleep 2
kill -0 "$tray_pid" 2>/dev/null || fail "程序已退出，详情见 data/tray.err.log。"
echo "Agent Watch 已在后台启动，请使用顶部菜单栏图标打开监控面板。"
