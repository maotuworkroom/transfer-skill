#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Yahoo! 乗換案内（Yahoo Japan Transit）路线查询工具。

从 Suki.ing app 的 TransitService（Dart）1:1 移植：
  - 构建 Yahoo!乗換案内 检索 URL
  - 抓取结果页 HTML（移动端 UA）
  - 解析出结构化路线（时间、票价、换乘、步骤、站台、车门位置等）

仅使用 Python 标准库，无需安装依赖。
"""

import argparse
import gzip
import io
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from line_colors import LINE_COLORS, PALETTE

BASE_URL = "https://transit.yahoo.co.jp/search/result"
USER_AGENT = ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36")
FETCH_TIMEOUT = 30

# 与 app 内文案保持一致的中文标签
LABEL_WALK = "步行"
LABEL_ROUTE_N = "路线{}"
LABEL_RESULT_TITLE = "换乘结果"
LABEL_DETAILS_URL = "详情: {}"
MSG_SAME_STATION = "出发地与目的地名称相同「{}」，请指定不同地点"
MSG_SAME_STATION_PAGE = ("错误: 出发地与目的地相同 —— 出发地与目的地名称或坐标相同，"
                         "请指定不同地点。")
MSG_NO_ROUTE = "未找到「{}」→「{}」的换乘方案"
MSG_NO_ROUTE_PAGE = ("未找到路线 —— 未找到符合条件的交通方案，"
                     "请更改出发地、目的地或时间后重试。")
MSG_EMPTY_PAGE = "页面为空，Yahoo 可能返回了异常响应，请稍后重试"
MSG_HTTP_ERROR = "换乘查询失败: HTTP {}"
MSG_PARSE_ANOMALY = "换乘查询完成（数据解析异常）"

SORT_MAP = {"time": 0, "transfers": 1, "fare": 2}
SORT_LABELS = {0: "时间优先", 1: "换乘少", 2: "票价低"}


def build_url(from_name, to_name, from_lat=None, from_lng=None,
              to_lat=None, to_lng=None, dt=None, departure_time=True,
              ticket_type="ic", sort_type=0, walk_speed=3,
              shinkansen=True, limited_express=True, express=True,
              local_train=True, private_railway=True):
    """构建 Yahoo!乗換案内 检索 URL（参数顺序与 Dart 版一致）。"""
    dt = dt or datetime.now()
    params = [
        ("from", from_name),
        ("to", to_name),
        ("fromgid", ""),
        ("togid", ""),
        ("via", ""),
        ("viacode", ""),
        ("y", str(dt.year)),
        ("m", "%02d" % dt.month),
        ("d", "%02d" % dt.day),
        ("hh", "%02d" % dt.hour),
        ("m1", str(dt.minute // 10)),
        ("m2", str(dt.minute % 10)),
        ("type", "1" if departure_time else "4"),
        ("ticket", ticket_type),
        ("expkind", "1"),
        ("userpass", "0"),
        ("ws", str(walk_speed)),
        ("s", str(sort_type)),
        ("al", "1"),
        ("shin", "1" if shinkansen else "0"),
        ("ex", "1" if limited_express else "0"),
        ("hb", "1" if express else "0"),
        ("lb", "1" if local_train else "0"),
        ("sr", "1" if private_railway else "0"),
    ]
    if from_lat is not None and from_lng is not None:
        params.insert(4, ("flatlon", "%s,%s," % (from_lat, from_lng)))
    if to_lat is not None and to_lng is not None:
        params.insert(5, ("tlatlon", "%s,%s," % (to_lat, to_lng)))
    query = "&".join(
        "%s=%s" % (k, urllib.parse.quote(v, safe="")) for k, v in params)
    return "%s?%s" % (BASE_URL, query)


def _ssl_context():
    """优先使用 certifi 的 CA 证书；没有则用系统默认。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch_html(url):
    """抓取页面 HTML；处理 gzip，证书校验失败时降级重试一次。"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT,
                                    context=_ssl_context()) as resp:
            data = resp.read()
    except urllib.error.URLError as e:
        if not (isinstance(e.reason, ssl.SSLError)
                and "CERTIFICATE_VERIFY_FAILED" in str(e.reason)):
            raise
        # macOS 上常见：Python 未接入系统钥匙串证书。降级重试（仅抓公开页面）。
        print("警告: 证书校验失败，已降级为不校验重试", file=sys.stderr)
        ctx = ssl._create_unverified_context()  # noqa: SLF001
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT,
                                    context=ctx) as resp:
            data = resp.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
    return data.decode("utf-8", errors="replace")


def strip_html(html):
    """HTML 转纯文本（移植 _stripHtml）。"""
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", html, flags=re.I)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<br\s*/?\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</?p[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</?div[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</?li[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</?tr[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</?h\d[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = decode_html_entities(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_html_entities(text):
    text = (text
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&nbsp;", " "))
    text = re.sub(r"&#(\d+);",
                  lambda m: chr(int(m.group(1)))
                  if m.group(1).isdigit() else m.group(0), text)
    text = re.sub(r"&#x([0-9a-fA-F]+);",
                  lambda m: chr(int(m.group(1), 16))
                  if m.group(1) else m.group(0), text)
    return text


def _is_noise(line):
    """移植 _isNoise：过滤页面噪音行。"""
    if len(line) > 120:
        return True
    if "script" in line or "function" in line:
        return True
    if line in ("時刻表", "地図", "地図でルートを表示"):
        return True
    if line in ("きっぷ購入", "PR", "ルート保存", "定期券", "ルート共有"):
        return True
    if line.startswith("出口"):
        return True
    if "情報なし" in line:
        return True
    # 注意：站台行「[発] 12番線 → [着] 17番線」不能按前缀过滤，
    # 它承载上下车站台信息，由列车步骤的相邻行扫描提取。
    if line in ("[発]", "[着]"):
        return True
    if re.match(r"^\d{1,2}:\d{2}[^\d]", line) and \
            not re.match(r"^\d{1,2}:\d{2}(発|着)$", line):
        return True
    return False


def _is_train_line(line):
    """移植 _isTrainLine：判断是否为线路名行。"""
    if line.startswith("[発]") or line.startswith("[着]"):
        return False
    if re.match(r"^\d{1,2}:\d{2}", line):
        return False
    if "新幹線" in line:
        return True
    if any(s in line for s in ("特急", "急行", "快速")):
        return True
    if any(s in line for s in ("のぞみ", "ひかり", "こだま", "こまち",
                               "はやぶさ", "かがやき")):
        return True
    if "線" in line and "番線" not in line:
        return True
    if re.search(r"ＪＲ|JR", line, re.IGNORECASE) and "線" in line:
        return True
    if "バス" in line and "行" in line:
        return True
    if re.search(r"・\d", line) and "バス" in line:
        return True
    return False


def _extract_line_code(line):
    """移植 _extractLineCode：提取线路代码（JY/JC/TX...）。"""
    m = re.search(r"([A-Z]{2,3})\d*", line)
    if m:
        return m.group(1)
    table = [
        ("山手", "JY"), ("中央線快速", "JC"), ("中央線特快", "JC"),
        ("総武", "JB"), ("京浜東北", "JK"), ("常磐", "JJ"), ("埼京", "JA"),
        ("湘南新宿", "JS"), ("上野東京", "JU"), ("成田エクスプレス", "NEX"),
        ("武蔵野", "JM"), ("横浜線", "JH"), ("横須賀", "JO"), ("りんかい", "R"),
        ("つくばエクスプレス", "TX"), ("烏丸", "K"), ("東西", "T"),
    ]
    for key, code in table:
        if key in line:
            return code
    return ""


def _line_color_for(line):
    """移植 _lineColorFor：线路名 → ARGB 颜色，未知线路用哈希调色板。"""
    for key, color in LINE_COLORS.items():
        if key in line:
            return color
    return _hash_color(line)


def _hash_color(name):
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0x7FFFFFFF
    return PALETTE[h % len(PALETTE)]


def _fmt_time(depart, arrive):
    if depart and arrive:
        return "%s→%s" % (depart, arrive)
    return depart or arrive or ""


def parse_steps(lines):
    """移植 _parseSteps 并适配当前 Yahoo 页面行结构：
    时间与「発」/「着」可能同行（旧版）也可能分离为两行（当前版）。"""
    steps = []
    last_depart = ""
    last_arrive = ""
    pending_time = ""

    for i, line in enumerate(lines):
        depart_m = re.match(r"^(\d{1,2}:\d{2})発$", line)
        if depart_m:
            last_depart = depart_m.group(1)
            pending_time = ""
            last_arrive = ""
            continue

        arrive_m = re.match(r"^(\d{1,2}:\d{2})着$", line)
        if arrive_m:
            last_arrive = arrive_m.group(1)
            pending_time = ""
            continue

        if re.match(r"^\d{1,2}:\d{2}$", line):
            pending_time = line
            continue
        if line == "発":
            if pending_time:
                last_depart = pending_time
                pending_time = ""
                last_arrive = ""
            continue
        if line == "着":
            if pending_time:
                last_arrive = pending_time
                pending_time = ""
            continue

        if _is_noise(line):
            continue

        # 步行段
        if line.startswith("徒歩") or re.match(r"^\d+分$", line):
            walk_min = re.search(r"(\d+)", line)
            label = ("步行%s分钟" % walk_min.group(1)) if walk_min else LABEL_WALK
            time = _fmt_time(last_depart, last_arrive)
            if last_arrive:
                last_depart = last_arrive
            last_arrive = ""
            step = {"type": "walk", "time": time, "label": label}
            # 当前页面步行块的出口信息在「徒歩N分」的上一行（出口：〇〇）
            if i > 0 and lines[i - 1].startswith("出口"):
                step["exitInfo"] = re.sub(r"^出口[：:]?\s*", "", lines[i - 1], count=1)
            steps.append(step)
            continue

        # 列车段
        if _is_train_line(line):
            dest = board = arrive = car = ""
            car_position = stations = 0
            fare = door_side = exit_info = ""
            stations = 0

            for lj in lines[i + 1: i + 15]:
                # 段边界：碰到下一段线路 / 步行段 / 下一站的到发时刻即停，
                # 防止把下一换乘段的站台、站数、票价误算入本段。
                if _is_train_line(lj):
                    break
                if re.match(r"^\d{1,2}:\d{2}(発|着)$", lj):
                    break
                if lj.startswith("徒歩") or re.match(r"^\d+分$", lj):
                    break
                if _is_noise(lj) and not lj.startswith("出口"):
                    continue
                if lj.endswith("行") and not dest and "[" not in lj:
                    dest = lj
                elif "[発]" in lj and "[着]" in lj:
                    parts = lj.split("→")
                    if len(parts) >= 2:
                        board = parts[0].replace("[発]", "").strip()
                        arrive = parts[1].replace("[着]", "").strip()
                elif lj.startswith("乗車位置"):
                    car_position = re.sub(r"^乗車位置[：:]?\s*", "", lj, count=1)
                elif re.match(r"^\d+駅$", lj):
                    stations = int(lj[:-1])
                elif "円" in lj and "きっぷ" not in lj and "購入" not in lj:
                    fare_num = re.search(r"(\d[\d,]+)円", lj)
                    fare = ("%s円" % fare_num.group(1)) if fare_num else lj
                elif lj.startswith("出口"):
                    exit_info = re.sub(r"^出口[：:]?\s*", "", lj, count=1)
                elif "ドア" in lj or "降りる" in lj:
                    door_side = lj

            time = _fmt_time(last_depart, last_arrive)
            if last_arrive:
                last_depart = last_arrive
            last_arrive = ""

            step = {
                "type": "train",
                "time": time,
                "line": line,
                "lineCode": _extract_line_code(line),
                "lineColor": _line_color_for(line),
                "boardPlatform": board,
                "arrivePlatform": arrive,
                "stations": stations,
            }
            if dest:
                step["dest"] = dest
            if fare:
                step["fare"] = fare
            if car_position:
                step["carPosition"] = car_position
            if exit_info:
                step["exitInfo"] = exit_info
            if door_side:
                step["doorSide"] = door_side
            if car:
                step["car"] = car
            steps.append(step)

    return steps


def _compact_step(step):
    """去掉空字段，与 Dart toJson 的省略风格一致。"""
    out = {}
    for k, v in step.items():
        if v not in ("", 0, None):
            out[k] = v
    return out


def parse_route_block(block, url, index):
    """移植 _parseRouteBlock：解析单个路线块。"""
    # 当前 Yahoo 页面把行き先放在 <span class="destination"> 里与线路名同行，
    # 插入换行使其成为独立行，便于步骤解析识别「〇〇行」。
    block = re.sub(r'<span class="destination[^>]*>', '\n', block)
    text = strip_html(block)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    time_match = re.search(r"(\d{1,2}:\d{2}).*?→.*?(\d{1,2}:\d{2})", block)
    duration_match = re.search(r"（(\d+時間\d+分|\d+分)）", block)
    fare_match = re.search(r"IC優先:(\d[\d,]+)円|(\d[\d,]+)円", block)
    transfer_match = re.search(r"乗換:(\d+)回", block)
    dist_match = re.search(r"([\d.]+)km", block)

    time_range = ("%s→%s" % (time_match.group(1), time_match.group(2))
                  if time_match else "")
    duration = duration_match.group(1) if duration_match else ""
    fare = (fare_match.group(1) or fare_match.group(2)) if fare_match else ""
    transfers = int(transfer_match.group(1)) if transfer_match else 0
    distance = dist_match.group(0) if dist_match else ""

    tags = []
    if "早" in block:
        tags.append("快")
    if "楽" in block:
        tags.append("省事")
    if "安" in block:
        tags.append("便宜")

    steps = [_compact_step(s) for s in parse_steps(lines)]
    summary_parts = [str(index)]
    if time_range:
        summary_parts.append(time_range)
    if duration:
        summary_parts.append(duration)
    if fare:
        summary_parts.append(fare + "円")
    if transfers > 0:
        summary_parts.append("乗換%d回" % transfers)
    summary = " ".join(summary_parts)

    route = {"summary": summary, "url": url}
    if time_range:
        route["time"] = time_range
    if duration:
        route["duration"] = duration
    if fare:
        route["fare"] = fare
    if transfers > 0:
        route["transfers"] = transfers
    if distance:
        route["distance"] = distance
    if tags:
        route["tags"] = tags
    if steps:
        route["steps"] = steps
    if lines:
        route["detail"] = "\n".join(lines)
    return route


def _is_route_header(line):
    """移植 _isRouteHeader：fallback 解析用。"""
    if re.match(r"^\d+$", line) or re.match(r"^\d+\s", line):
        return True
    if "→" in line and "（" in line:
        return True
    if re.search(r"\d{1,2}:\d{2}.*\d{1,2}:\d{2}", line):
        return True
    return False


def fallback_parse(html, url, max_routes):
    """移植 _fallbackParse：无 routeList 块时按行聚合。"""
    text = strip_html(html)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    routes = []
    buffer = []
    route_count = 0

    for line in lines:
        if _is_route_header(line):
            if buffer and route_count > 0:
                detail = "\n".join(buffer).strip()
                steps = [_compact_step(s) for s in
                         parse_steps([x.strip() for x in detail.split("\n")
                                      if x.strip()])]
                route = {"summary": LABEL_ROUTE_N.format(route_count), "url": url}
                if steps:
                    route["steps"] = steps
                route["detail"] = detail
                routes.append(route)
                buffer = []
                if len(routes) >= max_routes:
                    break
            route_count += 1
        if route_count > 0:
            buffer.append(line)

    if buffer and len(routes) < max_routes and route_count > 0:
        detail = "\n".join(buffer).strip()
        steps = [_compact_step(s) for s in
                 parse_steps([x.strip() for x in detail.split("\n")
                              if x.strip()])]
        route = {"summary": LABEL_ROUTE_N.format(route_count), "url": url}
        if steps:
            route["steps"] = steps
        route["detail"] = detail
        routes.append(route)

    if not routes:
        trimmed = text[:4000]
        routes.append({"summary": LABEL_RESULT_TITLE, "url": url,
                       "detail": trimmed})
    return routes


def parse_routes(html, url, max_routes):
    """路线切分：优先按 <section id="routeNN"> 块（当前 Yahoo 页面结构），
    其次按旧版 routeList div 块（与 app 的 Dart 版一致），最后按行 fallback。"""
    route_blocks = list(re.finditer(
        r'<section[^>]*id="route\d+"[^>]*>([\s\S]*?)(?=<section[^>]*id="route|\Z)',
        html))
    if not route_blocks:
        route_blocks = list(re.finditer(
            r'<div[^>]*class="[^"]*routeList[^"]*"[^>]*>([\s\S]*?)'
            r'(?=<div[^>]*class="[^"]*routeList|$)', html))
    if not route_blocks:
        return fallback_parse(html, url, max_routes)

    routes = []
    for m in route_blocks[:max_routes]:
        routes.append(parse_route_block(m.group(0), url, len(routes) + 1))
    return routes or fallback_parse(html, url, max_routes)


def query(from_name, to_name, from_lat=None, from_lng=None,
          to_lat=None, to_lng=None, dt=None, departure_time=True,
          ticket_type="ic", sort_type=0, walk_speed=3,
          shinkansen=True, limited_express=True, max_routes=5):
    """主入口：返回 (routes, url)。异常以 TransitError 抛出。"""
    max_routes = max(1, min(8, max_routes))
    url = build_url(from_name, to_name, from_lat, from_lng, to_lat, to_lng,
                    dt, departure_time, ticket_type, sort_type, walk_speed,
                    shinkansen, limited_express)
    try:
        html = fetch_html(url)
    except urllib.error.HTTPError as e:
        raise TransitError("http_error", MSG_HTTP_ERROR.format(e.code), url)
    except Exception as e:  # noqa: BLE001
        raise TransitError("network_error", "换乘查询失败: %s" % e, url)

    if not html:
        raise TransitError("empty_page", MSG_EMPTY_PAGE, url)
    # 当前 Yahoo 页面混有 <!-- --> 注释节点，会打断「（2時間12分）」等正则
    html = re.sub(r"<!--[\s\S]*?-->", "", html)
    if "出発地と目的地が同じ地点です" in html:
        raise TransitError("same_station", MSG_SAME_STATION_PAGE, url)
    if "該当する経路が見つかりませんでした" in html or \
            "路線検索結果が見つかりません" in html:
        raise TransitError("no_route", MSG_NO_ROUTE_PAGE, url)

    return parse_routes(html, url, max_routes), url


class TransitError(Exception):
    def __init__(self, kind, message, url):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.url = url


def format_text(from_name, to_name, dt, departure_time, sort_type,
                ticket_type, routes, url):
    """复刻 app 内的纯文本输出格式。"""
    time_part = "%02d:%02d" % (dt.hour, dt.minute)
    date_str = "%d年%02d月%02d日 %s" % (dt.year, dt.month, dt.day, time_part)
    type_label = "出发" if departure_time else "到达"
    ticket_label = "IC" if ticket_type == "ic" else "现金"

    out = ["■ %s → %s" % (from_name, to_name),
           "  %s%s / %s / %s" % (date_str, type_label,
                                 SORT_LABELS.get(sort_type, "时间优先"),
                                 ticket_label),
           ""]
    for r in routes:
        out.append(r.get("summary", ""))
        if r.get("detail"):
            out.append(r["detail"])
        out.append("")
    out.append(LABEL_DETAILS_URL.format(url))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="查询日本公共交通换乘路线（Yahoo!乗換案内）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            '  %(prog)s --from 京都 --to 東京\n'
            '  %(prog)s --from 新宿 --to 鎌倉 --time 09:30 --sort transfers\n'
            '  %(prog)s --from 東京 --to 大阪 --arrival --time 18:00 --json\n'
        ))
    parser.add_argument("--from", dest="from_name", required=True,
                        help="出发地名称（车站名/地名，日语效果最好）")
    parser.add_argument("--to", dest="to_name", required=True,
                        help="目的地名称")
    parser.add_argument("--from-lat", type=float, help="出发地纬度（可选）")
    parser.add_argument("--from-lng", type=float, help="出发地经度（可选）")
    parser.add_argument("--to-lat", type=float, help="目的地纬度（可选）")
    parser.add_argument("--to-lng", type=float, help="目的地经度（可选）")
    parser.add_argument("--date", help="出发日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--time", help="时间 HH:MM（默认当前时间）")
    parser.add_argument("--arrival", action="store_true",
                        help="按到达时间检索（默认按出发时间）")
    parser.add_argument("--ticket", choices=["ic", "normal"], default="ic",
                        help="票价类型：ic=IC卡（默认），normal=现金")
    parser.add_argument("--sort", choices=["time", "transfers", "fare"],
                        default="time",
                        help="排序：time=时间最短（默认），transfers=换乘最少，"
                             "fare=票价最低")
    parser.add_argument("--walk-speed", type=int, choices=range(1, 6),
                        default=3, help="步行速度 1-5，越大越少走路（默认3）")
    parser.add_argument("--no-shinkansen", action="store_true",
                        help="排除新干线")
    parser.add_argument("--no-limited-express", action="store_true",
                        help="排除特急列车")
    parser.add_argument("--max-routes", type=int, default=5,
                        help="最多返回路线数 1-8（默认5）")
    parser.add_argument("--json", action="store_true",
                        help="输出结构化 JSON（默认输出可读文本）")
    parser.add_argument("--url-only", action="store_true",
                        help="只打印构建的检索 URL，不请求网络")

    args = parser.parse_args(argv)

    dt = datetime.now()
    try:
        if args.date:
            y, m, d = (int(x) for x in args.date.split("-"))
            dt = dt.replace(year=y, month=m, day=d)
        if args.time:
            hh, mm = (int(x) for x in args.time.split(":"))
            dt = dt.replace(hour=hh, minute=mm)
    except ValueError:
        print("错误: 日期时间参数无效，请使用 --date YYYY-MM-DD --time HH:MM",
              file=sys.stderr)
        return 2

    sort_type = SORT_MAP[args.sort]

    if args.url_only:
        print(build_url(args.from_name, args.to_name, args.from_lat,
                        args.from_lng, args.to_lat, args.to_lng, dt,
                        not args.arrival, args.ticket, sort_type,
                        args.walk_speed, not args.no_shinkansen,
                        not args.no_limited_express))
        return 0

    if args.from_name == args.to_name and (
            args.from_lat is None or args.to_lat is None or
            (args.from_lat == args.to_lat and args.from_lng == args.to_lng)):
        msg = MSG_SAME_STATION.format(args.from_name)
        if args.json:
            print(json.dumps({"ok": False, "error": "same_station",
                              "message": msg}, ensure_ascii=False))
        else:
            print("错误: %s" % msg, file=sys.stderr)
        return 1

    try:
        routes, url = query(
            args.from_name, args.to_name, args.from_lat, args.from_lng,
            args.to_lat, args.to_lng, dt, not args.arrival, args.ticket,
            sort_type, args.walk_speed, not args.no_shinkansen,
            not args.no_limited_express, args.max_routes)
    except TransitError as e:
        if args.json:
            print(json.dumps({"ok": False, "error": e.kind,
                              "message": e.message, "url": e.url},
                             ensure_ascii=False))
        else:
            print("错误: %s\n%s" % (e.message, e.url), file=sys.stderr)
        return 1

    formatted = format_text(args.from_name, args.to_name, dt,
                            not args.arrival, sort_type, args.ticket,
                            routes, url)

    if args.json:
        payload = {
            "ok": True,
            "query": {
                "from": args.from_name,
                "to": args.to_name,
                "datetime": dt.strftime("%Y-%m-%d %H:%M"),
                "type": "departure" if not args.arrival else "arrival",
                "ticket": args.ticket,
                "sort": args.sort,
                "walk_speed": args.walk_speed,
                "shinkansen": not args.no_shinkansen,
                "limited_express": not args.no_limited_express,
            },
            "route_count": len(routes),
            "routes": routes,
            "url": url,
            "formatted": formatted,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(formatted)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
