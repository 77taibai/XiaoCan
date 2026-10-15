---
feature: xiaocan-monitor
status: in-progress
updated: 2026-10-15
branch: main
commits: 
---

# 小蚕余量监控 CLI

## Report

## [S1] Problem

小蚕（gw.xiaocantech.com）的满减活动余量是限量的，某个门店「当前时段有余量可点」的窗口很短且随时出现/消失。用户目前只能手动反复打开小程序查看，无法离线盯守。接口有腾讯云 WAF 频率风控，短时间连续请求会导致 IP 被封数小时。

需要一个部署在海外 Ubuntu 服务器、由 cron 定时驱动的 Python CLI 监控程序：
- 维护一份「要盯的店铺」清单，可搜索店铺、交互式增删；
- 每次运行对每个店铺查询接口，判断其在**中国大陆时间**的**当前时段**是否「可点」（在活动时段内且有余量）；
- 一旦发现可点，通过钉钉机器人推送一次，并且**当天（中国时区 0 点后）该店铺不再继续监控、不再推送**；
- 无论如何保证任意两次接口请求之间间隔不小于设定值（默认 3s，跨进程持久化），避免触发风控；
- 服务器时区非中国时区，程序必须自主使用中国时区判断「当天」「当前时段」。

## [S2] Design

### [S2.1] 组件与文件布局

标准库实现，无第三方依赖。新增包 `xiaocan_monitor/`（与既有三个测试脚本同目录根）。

```
xiaocan_monitor/
  __init__.py        # 版本号
  __main__.py        # python -m xiaocan_monitor 入口
  cli.py             # argparse 子命令分发（search/add/remove/run）
  settings.py        # .env 解析 + 默认值 + Settings
  timeutil.py        # 中国时区（Asia/Shanghai，回退固定 UTC+8）
  ratelimit.py       # 跨进程持久化的最小请求间隔
  rpc.py             # 签名 + HTTP RPC 客户端（对齐测试脚本）
  api.py             # 4.2 搜索封装 + Card/Offer 归一化与时段判断
  store.py           # monitors.json / state.json 读写（原子写）
  notify.py          # 钉钉自定义机器人加签推送
  engine.py          # run 一轮的纯逻辑（可注入依赖，便于测试）
  lock.py            # run 的单机互斥锁
tests/               # unittest，无网络
.env.example         # 配置模板
.gitignore           # 忽略 .env / data/ / __pycache__ 等
```

运行时数据目录默认 `data/`：
- `data/monitors.json`：监控清单（用户数据）。
- `data/state.json`：当日已完成标记 + 上次请求时间戳（运行时状态）。
- `data/xiaocan.lock`：run 互斥锁（运行时临时文件）。

`.env` 位于项目根（包的上一级目录），可用环境变量 `XIAOCAN_ENV_FILE` 指向其他路径。

### [S2.2] 配置（.env）

解析规则：读取 `.env` 后与进程环境合并，**进程环境优先于 .env，.env 优先于内置默认值**。解析为简单 `KEY=VALUE`：忽略空行与 `#` 注释，去掉两端空白与成对引号。不引入 python-dotenv。

| Key | 默认 | 说明 |
|---|---|---|
| `XIAOCAN_DINGTALK_WEBHOOK` | 空 | 钉钉机器人 webhook 完整地址 |
| `XIAOCAN_DINGTALK_SECRET` | 空 | 加签密钥（HMAC-SHA256） |
| `XIAOCAN_DINGTALK_TITLE` | `小蚕余量监控` | 推送标题 |
| `XIAOCAN_MIN_REQUEST_INTERVAL` | `3` | 任意两次请求最小间隔（秒） |
| `XIAOCAN_REQUEST_TIMEOUT` | `8` | 单请求超时（秒） |
| `XIAOCAN_RETRIES` | `1` | 传输层重试次数 |
| `XIAOCAN_DEFAULT_LAT` | `39.904200` | 默认纬度（与测试脚本一致） |
| `XIAOCAN_DEFAULT_LON` | `116.407400` | 默认经度 |
| `XIAOCAN_DEFAULT_CITY` | `110100` | 默认 `x-City` 城市编码 |
| `XIAOCAN_CONFIG_PATH` | `<root>/data/monitors.json` | 监控清单路径覆盖 |
| `XIAOCAN_STATE_PATH` | `<root>/data/state.json` | 状态路径覆盖 |

要求：任何打印/报错都不得输出 `XIAOCAN_DINGTALK_SECRET` 明文。

### [S2.3] 签名与 RPC（对齐测试脚本）

完全复刻三个测试脚本的协议：
- 固定头 `BASE_HEADERS`（`appid:20`、`X-Platform:mini`、`version/X-Version:3.15.9.10`、`X-Model`、`x-Annie:XC`、`xweb_xhr:1`、`Accept-Encoding: gzip, deflate`、`Accept-Language`、`Sec-Fetch-*`、`User-Agent`、`Referer`）。
- 动态头 `servername` / `methodname` / `X-Garen`（毫秒时间戳）/ `X-Nami`（`uuid4().hex` 前 4 位 + `"0"` + 再 15 位，共 20 字符，一次 run 内复用）/ `x-City`。
- `X-Ashe = md5hex( md5hex(lower(servername + "." + methodname)) + X-Garen + X-Nami )`。
- 请求体 `json.dumps(..., ensure_ascii=False).encode("utf-8")`；响应按 `Content-Encoding` 解 gzip/deflate，再按 UTF-8 解码。
- `status.code == 0` 视为业务成功；HTTP 非 2xx 视为传输失败；`401/403/405/429` 立即停止重试（疑似 WAF）。
- 传输异常/非 2xx（非上述 4 个码）按 `2 * attempt` 秒退避重试。

每次实际发送前必须先经过限流器（见 S2.5）。

### [S2.4] 数据模型

`Card` 表示一条 `promotion_list` 元素（门店 + 时段 + 多平台），`Offer` 表示其中一个有效平台：
- `Card`：`store_id`、`store_name`、`promotion_id`、`distance`、`start_minute`/`end_minute`（当日分钟数，缺失为 `None`）、`rebate_condition`、`offers: list[Offer]`。
- `Offer`：`platform`(1美团/2饿了么/3京东)、`platform_name`、`order_money`(分)、`rebate`(分)、`left`。
- 仅当 `meituan_status/eleme_status/tp_promotion.tp_status == 1` 时才生成对应 `Offer`（同测试脚本 `expand_item`）。

时段判断 `window_contains(minute_of_day)`：
- `start`/`end` 任一缺失 → 视为全天可点（返回 `True`），并记一条 WARN。
- `start == end` → 全天可点。
- `start < end` → `start <= now < end`。
- `start > end` → 跨零点：`now >= start or now < end`。

`monitors.json`：
```json
{
  "version": 1,
  "monitors": [
    {
      "id": "a1b2c3d4",
      "store_id": "1000001",
      "store_name": "示例门店A",
      "platform": 0,
      "lat": 39.904200,
      "lon": 116.407400,
      "city": "110100",
      "note": "",
      "enabled": true,
      "created_at": "2026-10-15T12:00:00+08:00"
    }
  ]
}
```
- `id`：8 位十六进制随机串，唯一；同一 `store_id` 不同 `platform` 视为两条监控。
- `platform`：0=全部，1/2/3 指定平台。

`state.json`：
```json
{
  "version": 1,
  "last_request_epoch": 1760000000.123,
  "daily": { "a1b2c3d4": "2026-10-15" }
}
```
- `daily[monitor_id] = "YYYY-MM-DD"`（中国时区）表示该监控**当天已发现可点并推送**，当天后续 run 直接跳过。
- `last_request_epoch`：上次实际发起请求的墙钟时间（跨进程限流用）。

写文件一律「写临时文件 + `os.replace`」原子替换。

### [S2.5] 限流（核心约束）

`RateLimiter.wait()` 在**每次**发送请求前调用：
1. 读取 `last_request_epoch`；
2. 若 `now - last < min_interval`，`sleep(min_interval - (now - last))`；
3. 将 `last_request_epoch = now` 写回 `state.json`（原子写）。

使用墙钟 `time.time()` 以跨进程生效，因此即使 cron 重叠或多进程并发，任意两次请求间隔也不会小于阈值。失败/重试的尝试同样占用并更新时间戳。`min_interval` 来自 `.env`，默认 3s，无单轮请求数量上限。

为便于测试，时钟与 `sleep` 可注入。

### [S2.6] 钉钉推送

自定义机器人**加签**模式：
- `timestamp = int(time.time() * 1000)`；
- `sign = base64(hmac_sha256(secret, f"{timestamp}\n{secret}"))`；
- 以 `&timestamp=..&sign=<urlencode(sign)>` 追加到 webhook 后请求；
- 消息体 `{"msgtype":"markdown","markdown":{"title":T,"text":TEXT}}`，成功判据 `errcode == 0`。

内容（不使用 emoji）示例：
```
## 小蚕余量可点
门店: 示例门店A  store_id=1000001
中国时间: 2026-10-15 14:23
活动时段: 10:00~22:00
- 美团: 满 20.00元 返现 5.00元 | 余 3
- 饿了么: 满 15.00元 返现 3.00元 | 余 1
```
`notify.py` 不得把 secret 写入日志或异常信息。

### [S2.7] 可点判定与「当天一次」

一轮 `run` 对每个启用监控：
1. 若 `state.daily[monitor.id] == 中国今天` → 打印「今日已完成」，跳过（**不请求接口**）。
2. 否则调用 4.2 搜索：`keyword=store_name`，`lat/lon/city` 取该监控，`offset=0`，`number=15`，`promotion_sort=1`，`store_type=99`，`store_platform=0`，`promotion_category=0`，`silk_id=0`，`app_id=20`（同测试脚本 `do_search`）。
3. 本地按 `str(store_id)` 过滤；若监控 `platform != 0`，只保留含该平台 `Offer` 的卡。
4. 对过滤后的卡：`window_contains(当前中国分钟数)` 且存在 `left > 0` 的 `Offer`（受 `platform` 约束）→ 判定「可点」。收集所有满足的 (Card, Offer)。
5. 若可点：组装钉钉 markdown 推送**一次**，成功后 `daily[monitor.id] = 中国今天` 并落盘。
6. 若不可点：不改 `daily`，留待当天后续 run 继续检查。

判定为纯函数（`engine.py`），依赖注入（`search` 客户端、`notifier`、`now`、`dry_run`），便于无网络测试。

### [S2.8] CLI

入口 `python -m xiaocan_monitor <cmd>`（同时提供 `xiaocan_monitor/__main__.py`）。

命令（最小集）：
- `search <keyword> [--lat F --lon F --city S --number N]`：调用 4.2 搜索，列出候选门店的 `store_id`、距离、时段、各平台余量，供复制 `store_id`。
- `add`：**纯交互式**。依次询问店名、store_id、平台、纬度/经度/城市（默认取 `.env` 默认定位）、备注，二次确认后写入 `monitors.json`。不发起网络请求。
- `remove`：**纯交互式**。列出全部监控（编号 + 店名 + store_id + 平台 + 当日状态），选择编号、确认后删除。
- `run [--dry-run] [--verbose]`：cron 入口。执行一轮监控；`--dry-run` 只判定并打印、不推送、不标记当天完成。

输出为中文、人类可读；`--verbose` 打印每次请求与判定细节。

退出码：
- `0`：正常完成。
- `1`：`run` 中有监控查询/推送失败（业务失败或网络失败），或 `search` 业务失败。
- `2`：参数或配置错误（如 `run` 时未配置钉钉但需要推送、清单文件损坏）。
- `3`：另一次 `run` 正在执行（未取得互斥锁）。

### [S2.9] 单机互斥锁

`run` 启动时以 `O_CREAT|O_EXCL` 创建 `data/xiaocan.lock` 并写入 PID；退出（含异常）时删除。
- 已存在锁：若 PID 不存活或锁文件 `mtime` 超过 15 分钟 → 视为陈旧，覆盖；否则打印提示并以退出码 `3` 结束。
- 锁仅防止并发 run 破坏状态与放大请求；不做「距上次运行过近则跳过」（运行频率完全由 cron 决定）。

### [S2.10] 部署与 cron

`.env` 保存 webhook/secret/间隔等；在服务器：
```
cd /opt/XiaoCan
cp .env.example .env   # 填写 XIAOCAN_DINGTALK_* 等
# crontab（每 5 分钟）:
*/5 * * * * cd /opt/XiaoCan && /usr/bin/python3 -m xiaocan_monitor run >> data/cron.log 2>&1
```
`.gitignore` 忽略 `.env`、`data/`、`__pycache__/`、`.venv/`、`*.pyc`，避免密钥与运行状态入库。

## [S3] Out of Scope

- 美团赏金接口 4.3/4.4、地址联想 4.5、4.1 列表分页、4.6 详情：本期监控只用 4.2 搜索。
- 任何自动下单/抢购行为：仅监控 + 推送。
- 除钉钉外的推送渠道（微信/邮件/Server酱等）。
- Web UI、常驻后台服务、多机分布式协调（锁仅单机）。
- 微信小程序登录态、付费/登录接口。
- 运行频率调度本身（由 cron 负责；程序不做定时循环）。

## Tasks

- [ ] T1: `.env` 解析 + Settings + 中国时区 + 持久化限流 — acceptance: `load_settings` 体现「环境变量 > .env > 默认」优先级；`cn_now()` 在服务器任意时区下返回 `Asia/Shanghai`（无 tzdata 时回退 UTC+8）；`RateLimiter.wait()` 在注入假时钟下保证相邻请求间隔 ≥ 阈值并写回状态 (covers: S2.2, S2.5)
- [ ] T2: RPC 客户端与搜索/卡片归一化 — acceptance: 用固定时间戳/指纹对 `SilkwormRec.RecService.SearchStorePromotionList` 生成的签名与测试脚本算法一致；`expand_promotion` 对样例 payload 生成受 `*_status` 约束的 Offers；`window_contains` 覆盖普通/跨零点/相等/缺失四种情形 (covers: S2.3, S2.4)
- [ ] T3: 持久化层 — acceptance: `Store` 能原子读写 `monitors.json`/`state.json`，`mark_done` 保留其它键，损坏文件返回可读错误；不残留临时文件 (covers: S2.4)
- [ ] T4: 钉钉加签推送 — acceptance: 构造的加签 URL 与钉钉官方算法一致（可用固定 key/时间戳断言）；`errcode != 0` 视为失败；异常与日志不泄露 secret (covers: S2.6)
- [ ] T5: run 判定引擎 — acceptance: 注入假搜索与假推送后，可点状态触发**一次**推送并写入当日标记；当天第二次 run 直接跳过且不请求接口；`left==0` 或窗外不推送；`--dry-run` 不推送不落盘 (covers: S2.7)
- [ ] T6: CLI 与互斥锁 — acceptance: `search/add/remove/run` 可用；`add`/`remove` 交互式；退出码符合 S2.8；并发 `run` 第二个以码 `3` 退出 (covers: S2.8, S2.9)
- [ ] T7: 单元测试 — acceptance: `python -m unittest discover -s tests` 全部通过且不访问网络 (covers: S2.3–S2.7; depends: T1,T2,T3,T4,T5)
- [ ] T8: 仓库卫生与部署说明 — acceptance: `.gitignore` 生效（`.env`/`data/` 不被跟踪）；`.env.example` 列出全部键；cron 用法写入本文件 S2.10 (covers: S2.1, S2.10)
