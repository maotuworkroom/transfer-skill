---
name: transfer-skill
description: 查询日本公共交通换乘路线（Yahoo!乗換案内 / Yahoo Transit）。Use whenever the user asks how to travel between two places in Japan by train/subway/bus — e.g. "从京都到东京怎么坐车"、"新宿到镰仓的电车路线"、"乗り換え案内"、"Japan transit route"、"怎么去XX站"、"新干线/票价/换乘次数查询" — even if they never say "换乘" or mention Yahoo. Runs a bundled script that queries Yahoo!乗換案内 and returns structured routes with times, fares, transfers, platforms and door positions.
---

# 日本公共交通换乘查询（Yahoo! 乗換案内）

一个独立、零依赖的换乘查询工具（Python 3.8+，仅标准库；装有 `certifi` 时证书校验更稳）。查询 [Yahoo!乗換案内](https://transit.yahoo.co.jp/) 的路线检索结果，解析成结构化数据：出发/到达时间、总时长、票价、换乘次数、每段乘坐的线路、上下车站台、乘车位置、出口信息等。

逻辑 1:1 移植自 Suki.ing app 的 `TransitService`（Dart），解析行为与 app 保持一致。

## 快速开始

```bash
# 最简用法：可读文本输出
python3 "<本目录>/scripts/yahoo_transit.py" --from 京都 --to 東京

# 结构化 JSON（推荐给 AI 消费）
python3 "<本目录>/scripts/yahoo_transit.py" --from 新宿 --to 鎌倉 --time 09:30 --json

# 指定日期、按到达时间检索、换乘最少优先、排除新干线
python3 "<本目录>/scripts/yahoo_transit.py" --from 東京 --to 大阪 \
  --date 2026-10-12 --time 18:00 --arrival --sort transfers --no-shinkansen
```

`--url-only` 只构建并打印检索 URL，不请求网络，适合直接给用户一个可点击的 Yahoo!乗換案内 链接。

## 参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--from` / `--to` | 出发地 / 目的地名称（必填）。车站名、地名均可，**日语效果最好** | — |
| `--from-lat/--from-lng` `--to-lat/--to-lng` | 可选坐标，用于精确定位（地名重名时） | 不传 |
| `--date` | 日期 `YYYY-MM-DD` | 今天 |
| `--time` | 时间 `HH:MM` | 当前时间 |
| `--arrival` | 按到达时间检索 | 按出发时间 |
| `--ticket` | `ic`（IC卡价）/ `normal`（现金原价） | `ic` |
| `--sort` | `time`（最快）/ `transfers`（换乘最少）/ `fare`（最便宜） | `time` |
| `--walk-speed` | 1-5，越大越少步行 | 3 |
| `--no-shinkansen` / `--no-limited-express` | 排除新干线 / 特急 | 默认都包含 |
| `--max-routes` | 最多返回路线数（1-8） | 5 |
| `--json` / `--url-only` | 输出格式控制 | 可读文本 |

## 输出格式

默认文本输出（与 app 内格式一致）：

```text
■ 京都 → 東京
  2026年09月03日 10:30出发 / 时间优先 / IC

1 9:20→11:14 1時間54分 8,910円
[route detail: Yahoo 返回的日文原文步骤行]

...

详情: https://transit.yahoo.co.jp/search/result?...
```

`--json` 输出（`ensure_ascii=False`，UTF-8）：

```json
{
  "ok": true,
  "query": { "from": "...", "to": "...", "datetime": "...", "type": "departure", "ticket": "ic", "sort": "time", "walk_speed": 3, "shinkansen": true, "limited_express": true },
  "route_count": 5,
  "routes": [
    {
      "summary": "1 11:01→13:15 2時間14分 13,320円",
      "time": "11:01→13:15",
      "duration": "2時間14分",
      "fare": "13,320",
      "transfers": 0,
      "distance": "513.6km",
      "tags": ["快", "省事", "便宜"],
      "steps": [
        { "type": "walk", "time": "11:03", "label": "步行11分钟", "exitInfo": "A6" },
        {
          "type": "train", "time": "11:01",
          "line": "ＪＲ新幹線のぞみ12号", "lineCode": "JR",
          "lineColor": "FF003F6C", "dest": "東京行",
          "boardPlatform": "12番線", "arrivePlatform": "19番線",
          "stations": 4, "fare": "8,360円",
          "carPosition": "[10両] 前 中 後"
        }
      ],
      "detail": "..."
    }
  ],
  "url": "https://transit.yahoo.co.jp/search/result?...",
  "formatted": "同默认文本输出"
}
```

字段说明：`steps[].type` 为 `walk` 或 `train`；train 的 `boardPlatform`/`arrivePlatform` 是上下车站台，`carPosition` 是 Yahoo 建议的乘车位置，`exitInfo` 是下车后使用的出口（walk 段通常也有）。`steps[].time` 是**该段出发时刻**（Yahoo 当前页面把到达时刻放在段之后，故一般只填出发时刻）；整体出发/到达时间看路线级的 `time`。空字段会被省略。`detail` 是 Yahoo 页面的日文原文行，内容以此为准。

## 错误处理

脚本退出码：`0` 成功，`1` 查询错误，`2` 用法/参数错误。`--json` 模式下错误也输出 JSON：

```json
{ "ok": false, "error": "no_route", "message": "未找到路线 —— ...", "url": "..." }
```

错误类别：`same_station`（出发地=目的地）、`no_route`（无符合条件的路线）、`empty_page`、`http_error`、`network_error`。

- Yahoo 对模糊地名可能匹配不到：**优先用车站名**（如「新宿」「京都駅」），必要时换表达或加「駅」。
- 解析异常（比如 Yahoo 改版页面）时结果可能只剩 `detail` 原文，仍可从中读出路线信息；也可把 `url` 给用户在浏览器打开。
- 本工具为非官方 HTML 解析，Yahoo 页面结构变化可能导致解析退化；请低频、个人用途使用，遵守 Yahoo! JAPAN 的服务条款。

## 供其他 AI 作为工具（function calling）调用

`references/tool_schema.json` 是 OpenAI function calling 格式的 `query_transit_route` 工具定义（与 app 内 `ai_tools.dart` 中的一致）。把模型输出的参数映射到 CLI：

| 工具参数 | CLI 参数 |
| --- | --- |
| `from_name` / `to_name` | `--from` / `--to` |
| `from_lat`,`from_lng` / `to_lat`,`to_lng` | `--from-lat --from-lng` / `--to-lat --to-lng` |
| `year`,`month`,`day`,`hour`,`minute` | 拼成 `--date YYYY-MM-DD --time HH:MM` |
| `is_departure=false` | `--arrival` |
| `ticket_type` | `--ticket` |
| `sort_type`（0/1/2） | `--sort time/transfers/fare` |
| `walk_speed` | `--walk-speed` |
| `shinkansen=false` / `limited_express=false` | `--no-shinkansen` / `--no-limited-express` |
| `max_routes` | `--max-routes` |

调用后把 `--json` 输出的 `routes` 数组交给模型，即得到可直接回答用户的结构化路线。

## 构建 Yahoo!乗換案内 链接（不运行脚本时）

若只需要给用户一个检索链接，可按 `references/yahoo_url_params.md` 手工拼 URL。注意 Yahoo 把分钟拆成两个参数（`m1` 十位、`m2` 个位），坐标参数格式为 `lat,lng,`（带尾逗号）。
