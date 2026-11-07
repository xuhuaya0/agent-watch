#!/bin/bash
# First-install bootstrap. No sudo, Homebrew, Git or Xcode is required.
set -euo pipefail
umask 077
[ "$(uname -s)" = Darwin ] || { echo '此安装入口仅适用于 macOS。'; exit 1; }
AW_INSTALL_HOME="${AGENT_WATCH_INSTALL_HOME:-$HOME/Library/Application Support/AgentWatch}"
mkdir -p "$AW_INSTALL_HOME"
AW_STAGE="$(mktemp -d "$AW_INSTALL_HOME/bootstrap.XXXXXX")"
trap 'rm -rf "$AW_STAGE"' EXIT
export UV_PYTHON_INSTALL_DIR="$AW_INSTALL_HOME/python"
export UV_PYTHON_BIN_DIR="$AW_INSTALL_HOME/bin"
export UV_CACHE_DIR="$AW_INSTALL_HOME/cache"
AW_UV="$AW_INSTALL_HOME/uv-0.13.7/uv"
if [ ! -x "$AW_UV" ]; then
    echo '正在准备独立运行环境（不修改系统 Python 或 shell 配置）…'
    curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
      https://releases.astral.sh/github/uv/releases/download/0.13.7/uv-installer.sh -o "$AW_STAGE/uv-install.sh"
    echo "d263ee68314cf5bea36b08e6bd9e2c4563240d7adb73756cf33af5c199b92db4  $AW_STAGE/uv-install.sh" | shasum -a 256 -c - >/dev/null
    UV_UNMANAGED_INSTALL="$AW_INSTALL_HOME/uv-0.13.7" /bin/sh "$AW_STAGE/uv-install.sh"
fi
"$AW_UV" python install 3.13
AW_PYTHON="$("$AW_UV" python find --managed-python 3.13)"
# --source is for an isolated checkout/CI acceptance; normal users fetch one
# immutable public commit, including both the installer and its source archive.
if [ "${1:-}" = '--source' ]; then
    AW_SOURCE="$2"; shift 2
    "$AW_PYTHON" "$AW_SOURCE/tools/mac_setup.py" --root "$AW_INSTALL_HOME" --uv "$AW_UV" --source "$AW_SOURCE" "$@"
else
    AW_REVISION="$("$AW_PYTHON" -c 'import json,urllib.request; print(json.load(urllib.request.urlopen("https://api.github.com/repos/xuhuaya0/agent-watch/commits/main",timeout=30))["sha"])')"
    [[ "$AW_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo '无法核对发布版本'; exit 1; }
    curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
      "https://raw.githubusercontent.com/xuhuaya0/agent-watch/$AW_REVISION/tools/mac_setup.py" -o "$AW_STAGE/mac_setup.py"
    "$AW_PYTHON" "$AW_STAGE/mac_setup.py" --root "$AW_INSTALL_HOME" --uv "$AW_UV" --revision "$AW_REVISION" "$@"
fi
