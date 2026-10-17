#!/bin/zsh
# Local UI preview using this checkout's Python environment.
set -eu

project_root="${0:A:h}"
python="$project_root/.venv/bin/python"
state_dir="$project_root/.demo-state/mac-manual"

fail() {
    print -r -- "$1"
    if [[ -t 0 ]]; then
        read -r '?按回车关闭此窗口…'
    fi
    exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail '此测试入口用于 macOS；其他平台请从源码运行 --demo。'
[[ -x "$python" ]] || fail '找不到项目 .venv/bin/python。请先按 README 创建 Python 3.10+ 虚拟环境并安装 requirements.txt。'

# Always run an isolated demo, regardless of shell or terminal preferences.
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1
export QT_QPA_PLATFORM=cocoa
cd "$project_root"
"$python" -c 'from PySide6 import QtCore, QtGui, QtWidgets; from PIL import Image' || fail '界面依赖不完整。请用项目 .venv/bin/python 安装 requirements.txt 后重试。'

print -r -- 'PyPyDance Cache · Mac 界面测试'
print -r -- '运行 / 存储 / 设置 / 外观：使用演示数据，服务、下载、测速和维护只作模拟。'
print -r -- '外观会自动保存，关闭后再次双击本入口即可检查恢复。'
print -r -- "外观目录：$state_dir"
print -r -- '存储和设置页的应用仅在本次演示有效，不修改真实业务配置。'
print -r -- '关闭 PyPyDance 窗口即可结束测试。'
print

# Do not forward arguments: this entry always stays in demo mode.
if "$python" "$project_root/dance_cache_gui.py" --demo --state-dir "$state_dir"; then
    exit 0
else
    status_code=$?
    fail "演示程序异常结束（退出码 $status_code），请保留此窗口中的错误信息。"
fi
