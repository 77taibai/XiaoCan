# 小蚕 HTTP 接口文档

## 1. 概述

小蚕所有业务接口共用一个 JSON-RPC 风格网关：统一 `POST` 同一地址，通过请求头 `servername` + `methodname` 指定 RPC 方法，请求体为该方法的 JSON 参数。

| 项目 | 值 |
|---|---|
| 网关地址 | `https://gw.xiaocantech.com/rpc` |
| 请求方法 | `POST` |
| Content-Type | `application/json` |
| 鉴权 | 无需登录，匿名可用（`silk_id=0`），依赖签名头 + 小程序伪装头 |
| 频率限制 | 调用过频会被腾讯云 WAF 拦截，IP 封禁数小时 |

## 2. 请求头

### 2.1 固定头（每次请求携带）

```
Content-Type: application/json
appid: 20
X-Platform: mini
version: 3.15.9.10
X-Version: 3.15.9.10
X-Model: microsoft microsoft
x-Annie: XC
xweb_xhr: 1
Accept-Encoding: gzip, deflate, br
Accept-Language: zh-CN,zh;q=0.9
Sec-Fetch-Site: cross-site
Sec-Fetch-Mode: cors
Sec-Fetch-Dest: empty
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254173b) XWEB/19027
Referer: https://servicewechat.com/wx52ae84595214/965/page-frame.html
```

### 2.2 动态头（每次请求现算）

| Header | 说明 | 生成规则 |
|---|---|---|
| `servername` | RPC 服务名 | 见各接口定义 |
| `methodname` | RPC 方法名 | 见各接口定义 |
| `X-Garen` | 时间戳 | 当前毫秒时间戳 |
| `X-Nami` | 设备指纹 | 随机 UUID 去横线后：`前4位 + "0" + 再取15位`，共 20 字符；一次会话内可复用 |
| `X-Ashe` | 签名 | 见 2.3 |
| `x-City` | 城市行政编码 | 如 `440100`；部分接口可不传 |

### 2.3 签名算法（X-Ashe）

```
x      = MD5( lowercase( servername + "." + methodname ) )
X-Ashe = MD5( x + X-Garen + X-Nami )        // 三个值作为字符串直接拼接后再 MD5
```

示例：`servername=SilkwormRec`，`methodname=RecService.GetStorePromotionList`，
则 `x = MD5("silkwormrec.recservice.getstorepromotionlist")`，`X-Ashe = MD5(x + 时间戳 + X-Nami)`。

> 签名与「方法名 + 时间戳 + 指纹」绑定：换接口必须重算；三个动态头必须配套生成。

## 3. 响应格式

所有接口返回：

```json
{
  "status": { "code": 0, "msg": "..." },
  "...": "各接口业务字段"
}
```

- `status.code == 0` 表示成功，否则为业务失败（读 `status.msg`）。
- HTTP 状态码非 2xx 视为传输失败。

## 4. 接口列表

| # | servername | methodname | 用途 |
|---|---|---|---|
| 1 | `SilkwormRec` | `RecService.GetStorePromotionList` | 满减活动列表（分页） |
| 2 | `SilkwormRec` | `RecService.SearchStorePromotionList` | 满减活动搜索 |
| 3 | `SilkwormFusion` | `FusionService.GetMeiTuanPromotions` | 美团赏金列表（游标分页） |
| 4 | `SilkwormRcs` | `SilkwormRcsService.MeituanShangjinGetPoiList` | 美团赏金搜索 |
| 5 | `SilkwormLbs` | `SilkwormLbsService.Suggestion` | 地址关键词联想 |
| 6 | `Silkworm` | `SilkwormService.GetStorePromotionDetail` | 单个活动详情 |

---

### 4.1 满减活动列表

**用途**：按定位分页拉取附近门店的满减返现活动（美团/饿了么/京东）。

**请求体**：

| 字段 | 类型 | 必填 | 示例 | 说明 |
|---|---|---|---|---|
| `latitude` | number | 是 | `23.105` | 纬度 |
| `longitude` | number | 是 | `113.32` | 经度 |
| `city_code` | number | 是 | `440100` | 城市行政编码 |
| `offset` | number | 是 | `0` | 偏移量，按每页大小递增 |
| `number` | number | 是 | `30` | 每页条数（建议 30） |
| `promotion_sort` | number | 是 | `3` | 排序方式 |
| `store_type` | number | 是 | `0` | 门店类型，0=全部 |
| `store_category` | number | 是 | `0` | 品类，0=全部 |
| `promotion_filter` | number | 是 | `0` | 活动过滤，0=无 |
| `promotion_category` | number | 是 | `0` | 活动分类，0=全部 |
| `store_platform` | number | 是 | `0` | 平台：0=全部，1=美团，2=饿了么，3=京东 |
| `silk_id` | number | 是 | `0` | 匿名用户固定 0 |
| `app_id` | number | 是 | `20` | 固定 |

**响应**：`promotion_list[]`，每个元素为「门店 + 多平台活动」聚合对象：

| 字段 | 类型 | 说明 |
|---|---|---|
| `promotion_id` | number | 活动 id。**同一门店每天会变**，不可作持久主键 |
| `store.store_id` / `store.name` / `store.icon` | | 门店 id / 名称 / 图片 |
| `distance` | number | 距离，单位米 |
| `start_time_hour` / `start_time_minute` / `end_time_hour` / `end_time_minute` | number | 活动时段 |
| `rebate_condition` | number | 评价条件：99=无需评价，2=图文评价 |
| `meituan_status` | number | =1 时美团活动有效 |
| `meituan_order_money` | number | 美团满额门槛，**单位分**（÷100=元） |
| `meituan_user_rebate` | number | 美团返现金额，**单位分** |
| `meituan_left_number` | number | 美团剩余库存 |
| `eleme_status` / `eleme_order_money` / `eleme_user_rebate` / `eleme_left_number` | | 饿了么，含义与单位同上 |
| `tp_promotion.tp_status` / `tp_order_money` / `tp_user_rebate` / `tp_left_number` | | 京东，含义同上（存在 `tp_promotion` 且 `tp_status==1` 才有效） |

**翻页方式**：offset 分页，下一页 `offset += number`，返回条数不足一页即到底。

**请求示例**（签名需按 2.3 现算）：

```bash
NOW=$(date +%s000)
NAMI=3f2a08971234abcd5678            # 随机生成，会话内复用
X=$(echo -n "silkwormrec.recservice.getstorepromotionlist" | md5sum | cut -d' ' -f1)
ASHE=$(echo -n "${X}${NOW}${NAMI}" | md5sum | cut -d' ' -f1)

curl -s 'https://gw.xiaocantech.com/rpc' \
  -H 'Content-Type: application/json' \
  -H 'servername: SilkwormRec' \
  -H 'methodname: RecService.GetStorePromotionList' \
  -H "X-Garen: $NOW" -H "X-Nami: $NAMI" -H "X-Ashe: $ASHE" \
  -H 'x-City: 440100' -H 'appid: 20' -H 'X-Platform: mini' \
  -H 'version: 3.15.9.10' -H 'X-Version: 3.15.9.10' -H 'x-Annie: XC' \
  -H 'User-Agent: Mozilla/5.0 ... MiniProgramEnv/Windows ... XWEB/19027' \
  -H 'Referer: https://servicewechat.com/wx52ae84595214/965/page-frame.html' \
  --data-raw '{"latitude":23.105,"longitude":113.32,"promotion_sort":3,"store_type":0,"offset":0,"number":30,"silk_id":0,"promotion_filter":0,"promotion_category":0,"city_code":440100,"store_category":0,"store_platform":0,"app_id":20}'
```

---

### 4.2 满减活动搜索

**用途**：按关键词搜索满减活动。

**请求体**：

| 字段 | 类型 | 必填 | 示例 | 说明 |
|---|---|---|---|---|
| `keyword` | string | 是 | `"示例门店B"` | 搜索词 |
| `latitude` / `longitude` | number | 是 | | 定位 |
| `offset` / `number` | number | 是 | `0` / `15` | **仅支持取第一页**，offset>0 通常无结果 |
| `promotion_sort` | number | 是 | `1` | 搜索场景固定 1 |
| `store_type` | number | 是 | `99` | 搜索场景固定 99 |
| `store_platform` | number | 是 | `0` | |
| `promotion_category` | number | 是 | `0` | |
| `silk_id` / `app_id` | number | 是 | `0` / `20` | |

> 注意：请求体**没有 `city_code`**，城市通过 Header `x-City` 指定。

**响应**：同 4.1（`promotion_list[]`）。

---

### 4.3 美团赏金列表

**用途**：拉取"美团赏金"（按比例返佣）活动，游标分页。

**请求体**：

| 字段 | 类型 | 必填 | 示例 | 说明 |
|---|---|---|---|---|
| `lat` | number | 是 | `23.105` | 纬度 |
| `lon` | number | 是 | `113.32` | 经度（键名 `lon`） |
| `silk_id` | number | 是 | `897154359` | **固定魔法数**（与满减接口的 0 不同） |
| `pv_id` | string | 否 | `"xxx"` | 上一页响应的 `pv_id`；**第一页传 null/不传** |
| `scene` | number | 是 | `1` | 场景，固定 |
| `app_id` | number | 是 | `20` | 固定 |

**响应**：

| 字段 | 说明 |
|---|---|
| `pv_id` | 下一页游标 |
| `mt_pv_id` | 美团侧游标 |
| `list[]` | POI 数组 |
| `list[].name` / `picture` | 门店名 / 图片 |
| `list[].wm_poi_id` | 门店唯一 id |
| `list[].delivery_distance` | 配送距离，**自带单位的字符串**（如 `"1.2km"`） |
| `list[].plan_activity_info_list[]` | 该店赏金活动数组 |
| `plan_activity_info_list[].poi_event_id` | 活动 id |
| `plan_activity_info_list[].ratio` | 返现比例，**×100 的整数**（÷100=实际比例） |
| `plan_activity_info_list[].max_commission` | 最高返现金额，**×100**（÷100=元） |
| `plan_activity_info_list[].inventory` | 剩余库存 |
| `plan_activity_info_list[].plan_activity_type` | 1=无需评价，2=图文评价 |

**翻页方式**：游标式"加载更多"——把响应 `pv_id` 原样作为下一页 `pv_id` 传入。

---

### 4.4 美团赏金搜索

**用途**：按关键词搜索美团赏金活动。

**请求体**：

| 字段 | 类型 | 必填 | 示例 | 说明 |
|---|---|---|---|---|
| `lat` | number | 是 | | 纬度 |
| `lng` | number | 是 | | 经度（⚠️ 键名 `lng`，与 4.3 的 `lon` 不同） |
| `search_word` | string | 是 | `"示例门店C"` | 搜索词（⚠️ 键名非 keyword） |
| `sort_type` | number | 是 | `3` | 固定 |
| `silk_id` | number | 是 | `897154359` | 同 4.3 |
| `pv_id` | string | 否 | | 游标 |
| `app_id` | number | 是 | `20` | |

**响应**：`poi_list[]`（内部结构同 4.3 的 `list[]`），游标字段为 **`page_pv_id` / `meituan_pv_id`**（键名与 4.3 的 `pv_id`/`mt_pv_id` 不同）。

---

### 4.5 地址搜索（POI 联想）

**用途**：地址关键词联想，返回带经纬度与城市编码的候选地址，用于选择定位。

**请求体**：

| 字段 | 类型 | 必填 | 示例 | 说明 |
|---|---|---|---|---|
| `keyword` | string | 是 | `"天河城"` | 搜索词 |
| `region` | string | 是 | `""` | 区域过滤，可传空串 |
| `page` / `page_size` | number | 是 | `1` / `20` | 分页 |
| `silk_id` / `app_id` | number | 是 | `0` / `20` | |

**响应**：`result[]`：

| 字段 | 说明 |
|---|---|
| `id` / `title` / `address` | POI 标识 / 名称 / 详细地址 |
| `location.lat` / `location.lng` | 纬度 / 经度 |
| `adcode` | 城市行政编码（可直接用作 `x-City` / `city_code`） |
| `province` / `city` / `district` | 省 / 市 / 区 |

---

### 4.6 活动详情

**用途**：按活动 id 拉取单个活动的完整信息，字段比列表更全。

**请求体**：

| 字段 | 类型 | 必填 | 示例 | 说明 |
|---|---|---|---|---|
| `promotion_id` | number | 是 | `123456` | 活动 id（来自 4.1/4.2） |
| `silk_id` / `app_id` | number | 是 | `0` / `20` | |

**响应**：`promotion_detail`，字段结构与 4.1 `promotion_list[]` 元素一致。

---

## 5. 注意事项

1. **频率风控**：请求过频会被腾讯云 WAF 拦截，IP 被封禁数小时；批量轮询需控制间隔（分钟级）。
2. **签名必须现算**：`X-Garen` / `X-Nami` / `X-Ashe` 三者配套，`X-Ashe` 与 `servername.methodname` 绑定，换接口需重算。
3. **单位换算**：满减金额类字段单位为**分**（÷100=元）；赏金 `ratio`、`max_commission` 为**×100** 后的值。
4. **赏金接口两版键名不一致**：经纬度 `lon`（列表）vs `lng`（搜索）；游标 `pv_id`/`mt_pv_id`（列表响应）vs `page_pv_id`/`meituan_pv_id`（搜索响应）。
5. **`promotion_id` 每日变化**：做门店级持久化/去重应使用门店 id（`store_id` 或 `wm_poi_id`）。
6. **搜索接口不支持深分页**：一律 offset=0 / 单页取回。
7. **固定值汇总**：`app_id=20`；满减/地址/详情 `silk_id=0`；赏金 `silk_id=897154359`；列表 `number=30`；搜索 `number=15`。
8. 建议超时 3~10s；失败（`status.code != 0` 或非 2xx）应做退避重试，避免触发风控。
