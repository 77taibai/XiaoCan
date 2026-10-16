# 小蚕余量监控（xiaocan-monitor）

一个仅用 Python 标准库实现的命令行程序，用来盯守「小蚕」（`gw.xiaocantech.com`）上指定店铺的满减活动余量：

- 定时（由 cron 驱动）请求接口，判断指定店铺在**中国大陆时区的当前时段**是否「可点」（在活动时段内且有余量）；
- 一旦可点，通过**钉钉机器人**推送**一次**，并在**当天**（中国时区 0 点后）不再监控、不再推送该店；
- 全程严格控制请求频率（默认任意两次请求间隔 ≥ 3 秒，跨进程持久化），避免触发腾讯云 WAF 封 IP。

> 面向海外 Ubuntu 服务器部署：程序**自主使用中国时区**（`Asia/Shanghai`，无 tzdata 时回退固定 UTC+8），与服务器本地时区无关。

---

## 功能特性

- 命令集（最小集）：`search`（搜店拿 `store_id`）、`add` / `remove`（交互式增删监控）、`run`（cron 入口）。
- 中国时区自主判定「当天」与「当前时段」，支持跨零点的活动时段。
- 钉钉自定义机器人**加签**推送；同一天同一店只推一次。
- 防封控：跨进程持久化的最小请求间隔（默认 3s），失败/重试同样计入间隔。
- 单机互斥：并发 `run` 只有一个执行，另一个直接退出。
- 无第三方依赖；配置与密钥放在 `.env`。

---

## 目录结构

```
XiaoCan/
  xiaocan_monitor/        # 程序包（CLI）
  tests/                  # 单元测试（unittest，无网络）
  reference/              # 参考资料：接口文档 + 三个接口测试脚本
  docs/compose/spec/      # 功能设计文档
  data/                   # 运行时数据（自动创建，勿入库）
  .env.example            # 配置模板
  .gitignore
  README.md
```

---

## 环境要求

- Python **3.9+**（3.8 亦可运行，时区回退为固定 UTC+8）；仅标准库，无需 pip 安装任何依赖。
- 可执行 cron 的 Linux/Ubuntu 服务器（或其他类 Unix 环境）。
- 一个钉钉群自定义机器人（加签方式）。

---

## 快速开始

```bash
# 1) 进入项目目录
cd /opt/XiaoCan

# 2) 准备配置
cp .env.example .env
#    编辑 .env，至少填写钉钉 webhook 与加签密钥（见下文）

# 3) 确认程序可用
python3 -m xiaocan_monitor --version
python3 -m xiaocan_monitor --help

# 4) 搜索一个店铺，拿到它的 store_id
python3 -m xiaocan_monitor search 示例门店B

# 5) 交互式添加监控
python3 -m xiaocan_monitor add

# 6) 试跑一轮（不发钉钉、不落盘）
python3 -m xiaocan_monitor run --dry-run
```

配置定时任务（示例：每 5 分钟一轮）——直接调用仓库根目录的 `run.sh` 即可：

```bash
chmod +x /opt/XiaoCan/run.sh   # 只需一次
```

```cron
*/5 * * * * /opt/XiaoCan/run.sh
```

`run.sh` 会自动完成以下工作，无需在 crontab 里 `cd`、拼 `python3` 路径或重定向日志：

- 以脚本自身所在目录为项目根，不受 cron 默认 `$HOME` 工作目录影响；
- 自动探测 `python3` / `python`（可用环境变量 `XIAOCAN_PYTHON` 指定）；
- 自动创建 `data/` 与日志 `data/cron.log`，每轮写入带时间戳的开始/结束行，并在超过 5MB 时轮转为 `.1`；
- 透传程序退出码；其中「另一次 `run` 正在执行(3)」收敛为 `0`，避免 cron 误发邮件。

> 手动调试：`/opt/XiaoCan/run.sh --dry-run --verbose`（参数会原样传给 `xiaocan_monitor run`）。

---

## 配置（.env）

复制 `.env.example` 为 `.env` 后按需修改。`KEY=VALUE` 形式，`#` 开头为注释，值可用单/双引号包裹。

优先级：**进程环境变量 > `.env` 文件 > 内置默认值**。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `XIAOCAN_DINGTALK_WEBHOOK` | 空 | 钉钉机器人 webhook 完整地址（**必填**） |
| `XIAOCAN_DINGTALK_SECRET` | 空 | 钉钉加签密钥（`SEC` 开头，**必填**，加签模式） |
| `XIAOCAN_DINGTALK_TITLE` | `小蚕余量监控` | 推送消息标题 |
| `XIAOCAN_MIN_REQUEST_INTERVAL` | `3` | 任意两次接口请求的最小间隔（秒） |
| `XIAOCAN_REQUEST_TIMEOUT` | `8` | 单次请求超时（秒） |
| `XIAOCAN_RETRIES` | `1` | 传输层重试次数 |
| `XIAOCAN_DEFAULT_LAT` | `39.904200` | 默认纬度（search/add 定位默认值） |
| `XIAOCAN_DEFAULT_LON` | `116.407400` | 默认经度 |
| `XIAOCAN_DEFAULT_CITY` | `110100` | 默认城市行政编码（`x-City`） |
| `XIAOCAN_CONFIG_PATH` | `<项目>/data/monitors.json` | 监控清单文件路径覆盖 |
| `XIAOCAN_STATE_PATH` | `<项目>/data/state.json` | 运行状态文件路径覆盖 |
| `XIAOCAN_ENV_FILE` | `<项目>/.env` | 指定另一个 `.env` 路径 |

> `.env` 已加入 `.gitignore`，不会入库；请勿把密钥写进代码或提交到仓库。

### 钉钉机器人（加签）

1. 在钉钉群「群设置 → 智能群助手 → 添加机器人 → 自定义」创建机器人；
2. 安全设置选择「**加签**」，复制 `SEC...` 密钥；
3. 把 webhook 与密钥分别填入 `XIAOCAN_DINGTALK_WEBHOOK` 与 `XIAOCAN_DINGTALK_SECRET`。

---

## 命令用法

### `search` — 搜索店铺，列出 store_id

```bash
python3 -m xiaocan_monitor search <关键词> [--lat 纬度 --lon 经度 --city 城市编码 --number 15]
```

不传定位参数时使用 `.env` 默认定位。输出示例：

```
关键词 '示例门店B' 命中 3 条（lat=39.904200 lon=116.407400 x-City=110100）
[01] 示例门店B
     store_id=1000003  距离=320m  时段=10:00~22:00
     美团: 余 5
     饿了么: 余 2
```

> 搜索接口仅支持第一页；请从结果中复制 `store_id` 用于 `add`。

### `add` — 交互式添加监控

```bash
python3 -m xiaocan_monitor add
```

依次提示：店名（用于搜索，建议完整店名）、`store_id`、平台（`0=全部 / 1=美团 / 2=饿了么 / 3=京东`）、纬度、经度、城市编码、备注，最后二次确认。**不发起网络请求。**

### `remove` — 交互式删除监控

```bash
python3 -m xiaocan_monitor remove
```

列出全部监控（含当日状态），输入编号并二次确认后删除。

### `run` — 执行一轮监控（cron 入口）

```bash
python3 -m xiaocan_monitor run [--dry-run] [--verbose]
```

- `--dry-run`：只判定并打印结果，**不推送、不落盘**（未配置钉钉时可用）。
- `--verbose`：打印每次请求等细节。

---

## 监控与推送逻辑

对每个启用监控：

1. 若**当天（中国时区）已推送过**，直接跳过，**不请求接口**；
2. 否则以店名为关键词调用搜索接口，本地按 `store_id`（及可选平台）过滤；
3. 判定「可点」＝ 当前中国时区时刻落在活动时段内 **且** 存在 `left > 0` 的平台活动；
4. 可点即推送**一次**钉钉消息，并记录当天已完成；当天后续 `run` 不再检查该店；
5. 不可点则保持监控，等待当天后续 `run` 继续检查。

「当天」以中国时区日期为准，0 点后自动重置。

---

## 限流与防风控

- 任意两次接口请求之间强制间隔 ≥ `XIAOCAN_MIN_REQUEST_INTERVAL`（默认 3 秒），上限不限。
- 上次请求时间持久化在 `data/state.json`，**跨进程生效**；即使 cron 重叠或多进程并发，也不会突发短间隔请求。
- 网络失败 / 非 2xx 会按 `2 × 尝试次数` 秒退避重试；命中 `401/403/405/429`（疑似 WAF）立即停止重试。
- `run` 使用单机互斥锁，并发时后启动的进程以退出码 `3` 退出。

---

## 数据文件（data/，勿入库）

| 文件 | 说明 |
|---|---|
| `data/monitors.json` | 监控清单（店名、`store_id`、平台、定位、备注、启停） |
| `data/state.json` | 当日已完成标记 + 上次请求时间戳 |
| `data/xiaocan.lock` | `run` 单机互斥锁（运行时临时文件） |
| `data/state.json.ratelimit.lock` | 限流跨进程互斥锁（运行时临时文件） |

均为自动创建/维护的 JSON；可用 `XIAOCAN_CONFIG_PATH` / `XIAOCAN_STATE_PATH` 改到别处。

---

## 退出码

| 码 | 含义 |
|---|---|
| `0` | 正常完成 |
| `1` | `run` 中有监控查询或推送失败；或 `search` 业务失败 |
| `2` | 参数/配置错误（如 `run` 非 `--dry-run` 但未配置钉钉 webhook、清单文件损坏） |
| `3` | 另一次 `run` 正在执行 |
| `130` | 用户中断（Ctrl+C） |

---

## 测试

```bash
python -m unittest discover -s tests -t .
```

仅使用标准库，全部为无网络单元测试（固定向量校验签名/加签、注入假时钟/假请求验证限流与判定逻辑）。

---

## 注意事项

- 本程序只做**监控 + 通知**，不包含任何自动下单/抢购行为。
- 请遵守目标服务的使用条款；因频率过高导致的封禁风险由使用者自行承担。
- 依赖小蚕接口的私有签名协议；若接口变更，需同步更新 `xiaocan_monitor/rpc.py`。
- 通知去重用「中国时区日期」按监控 ID 记录；如需第二天重新监控，无需手动干预。

---

## 参考资料

`reference/` 目录下保留了原始资料，供核对协议与联调：

- `reference/小蚕HTTP接口文档.md`
- `reference/xiaocan_search_test.py`（4.2 搜索）
- `reference/xiaocan_list_test.py`（4.1 列表）
- `reference/xiaocan_store_track_test.py`（4.2 搜索 + 4.6 详情）
