#!/bin/bash
set -eu
cd "$(dirname "$0")"
mkdir -p "Agent Watch.app/Contents/MacOS"
/usr/bin/clang -fobjc-arc -framework Cocoa macos/launcher.m \
  -o "Agent Watch.app/Contents/MacOS/AgentWatch"
/usr/bin/codesign --force --sign - "Agent Watch.app"
echo "已生成 Agent Watch.app，可在 Finder 中双击启动。"
