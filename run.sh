#!/usr/bin/env bash
#
# 小蚕余量监控 —— cron 入口脚本
#
# 用法（crontab 里直接调用本脚本即可，无需再 cd 或拼 python 命令）：
#   */5 * * * * /opt/XiaoCan/run.sh
#
# 手动调试：
#   ./run.sh --dry-run --verbose
#
# 环境变量：
#   XIAOCAN_PYTHON  指定 python 解释器（缺省自动探测 python3 / python）
#   XIAOCAN_LOG     指定日志文件（缺省 <项目>/data/cron.log）
#   XIAOCAN_LOG_MAX_BYTES  日志超过该大小则轮转一次（缺省 5242880，即 5MB）
#
# 退出码：透传 python 程序的退出码；其中「另一次 run 正在执行(3)」视为正常，返回 0，
#         以免 cron 因此发邮件。其余非 0 会触发 cron 邮件告警。

set -u

# ---------------------------------------------------------------- 基础定位 --
# 以脚本自身位置为项目根，不受 cron 的 cwd（通常是 $HOME）影响。
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
if [ -z "${SCRIPT_DIR:-}" ]; then
    echo "无法确定脚本所在目录" >&2
    exit 2
fi
cd "$SCRIPT_DIR" || exit 2

umask 022

# ---------------------------------------------------------------- 日志准备 --
DATA_DIR="$SCRIPT_DIR/data"
mkdir -p "$DATA_DIR" || exit 2

LOG_FILE="${XIAOCAN_LOG:-$DATA_DIR/cron.log}"
LOG_MAX_BYTES="${XIAOCAN_LOG_MAX_BYTES:-5242880}"

# 简单的单文件轮转：超过上限就备份为 .1（覆盖旧备份）
if [ -f "$LOG_FILE" ]; then
    size="$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)"
    if [ "$size" -gt "$LOG_MAX_BYTES" ] 2>/dev/null; then
        mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || true
    fi
fi

# 确保日志文件存在
: >> "$LOG_FILE" 2>/dev/null || true

# ---------------------------------------------------------------- 解释器 ----
PYTHON=""
if [ -n "${XIAOCAN_PYTHON:-}" ]; then
    if command -v "$XIAOCAN_PYTHON" >/dev/null 2>&1; then
        PYTHON="$(command -v "$XIAOCAN_PYTHON")"
    else
        echo "XIAOCAN_PYTHON 指定的解释器不存在: $XIAOCAN_PYTHON" >&2
        exit 2
    fi
fi
if [ -z "$PYTHON" ]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$(command -v "$candidate")"
            break
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    {
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 错误：未找到 python3/python，请设置 XIAOCAN_PYTHON"
    } >> "$LOG_FILE"
    exit 2
fi

# ---------------------------------------------------------------- 执行 ------
# 每轮输出之间加一条带时间戳的分隔行，便于排查。
{
    echo "===== [$(date '+%Y-%m-%d %H:%M:%S')] xiaocan run start (python: $PYTHON) ====="
} >> "$LOG_FILE"

"$PYTHON" -m xiaocan_monitor run "$@" >> "$LOG_FILE" 2>&1
status=$?

{
    echo "----- [$(date '+%Y-%m-%d %H:%M:%S')] xiaocan run end (exit=$status) -----"
} >> "$LOG_FILE"

# 并发时后启动的进程以 3 退出，属正常情况，收敛为成功，避免 cron 发信。
if [ "$status" -eq 3 ]; then
    exit 0
fi
exit "$status"
