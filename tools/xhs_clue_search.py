#!/usr/bin/env python3
"""Find public note leads for a venue using the local Xiaohongshu session."""

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib import error, request

APP_DIR = Path.home() / "Library/Application Support/Shanghai Climbmap/xhs"
API = "http://127.0.0.1:18060/api/v1"
RESET_TEXT = re.compile("换线|拆线|定线|闭馆|线路更新")
NEW_LINES = re.compile("新线|上新|新.{0,2}条")
OFFICIAL_SCHEDULE = re.compile("日历|月历|排期|本周安排|本月安排|营业调整|营业安排|开放安排")
RESET_ANNOUNCEMENT = re.compile("闭馆|拆线|(?:换线|定线).{0,3}(?:通知|公告|安排|日历)|(?:通知|公告).{0,3}(?:换线|定线)")


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def lookup_venue(name):
    html = (Path(__file__).resolve().parents[1] / "dist/index.html").read_text()
    venues = json.loads(re.search(r'id="venue-data">(.*?)</script>', html, re.S)[1])["venues"]
    matches = [venue for venue in venues if compact(name) == compact(venue["name"])]
    if len(matches) != 1:
        raise ValueError("请使用地图上的完整场馆名，例如 AOB2·创智天地")
    label, _, branch = matches[0]["name"].partition("·")
    brand = re.sub(r"\d+$", "", label) or label
    branch = re.split(r"[（(]", branch, maxsplit=1)[0]
    return brand, branch, label


def api_call(endpoint, payload, timeout=65):
    token = (APP_DIR / "local-auth").read_text().strip()
    req = request.Request(
        API + endpoint, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        raise RuntimeError(f"本机小红书接口 HTTP {exc.code}") from None
    if not result.get("success"):
        raise RuntimeError("本机小红书接口未返回成功")
    return result.get("data") or {}


def rank(feeds, brand, branch, label, official_ids):
    qualified = []
    seen = set()
    numbered = compact(label) != compact(brand)
    for item in feeds:
        note_id = item.get("id", "")
        card = item.get("noteCard") or {}
        title = card.get("displayTitle") or ""
        author = card.get("user") or {}
        text = compact(title)
        official = bool(author.get("userId") in official_ids)
        named_branch = bool(branch and compact(branch) in text)
        named_label = numbered and compact(label) in text
        named_brand = bool(brand and compact(brand) in text)
        explicit = bool(RESET_TEXT.search(title) or (official and OFFICIAL_SCHEDULE.search(title)))
        shares_new_lines = bool(NEW_LINES.search(title))
        branch_match = named_label or (named_branch and (official or named_brand))
        if explicit and official and branch_match:
            reason, priority = "官号点名当前分店的公告候选", 6
        elif explicit and branch_match:
            reason, priority = "点名分店的公告候选", 5
        elif official and explicit:
            reason, priority = "已核对的官号发文，需核对分店", 4
        elif explicit and (named_brand or (
            "上海" in text and re.search("攀岩|岩馆|抱石|爬新线|上墙", text)
        )):
            reason, priority = "品牌或上海汇总，需核对分店", 3
        elif branch_match and shares_new_lines:
            reason, priority = "岩友新线线索，未证实换线时段", 2
        else:
            continue
        if not re.fullmatch(r"[0-9a-f]{24}", note_id) or note_id in seen:
            continue
        seen.add(note_id)
        candidate = {
            "title": title, "author": author.get("nickname") or author.get("nickName"),
            "url": "https://www.xiaohongshu.com/explore/" + note_id,
            "reason": reason, "can_attempt_read": bool(item.get("xsecToken")),
        }
        qualified.append((priority, candidate, item))
    return sorted(qualified, key=lambda row: (-row[0], not RESET_ANNOUNCEMENT.search(row[1]["title"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venue", help="地图中的完整场馆名")
    parser.add_argument("--term", default="换线", help="本次搜索词，例如 拆线、定线、闭馆")
    parser.add_argument("--query", help="覆盖关键词，用于已确认的官号名称或门店别名")
    parser.add_argument("--official-id", action="append", default=[], help="人工核验的官号公开 ID")
    parser.add_argument("--profile", action="store_true", help="用匹配的公开账号查馆方主页")
    parser.add_argument("--cached", type=Path, help="用本机搜索结果回放筛选")
    parser.add_argument("--history", action="store_true", help="补查上次换线，放宽平台的一周筛选")
    parser.add_argument("--read", action="store_true", help="尝试读优先级最高的一篇")
    args = parser.parse_args()
    if args.cached and args.read:
        parser.error("--cached 与 --read 不能同时使用")
    if args.profile and (args.cached or len(args.official_id) != 1):
        parser.error("--profile 需恰好一个 --official-id，不能与 --cached 一起使用")
    try:
        brand, branch, label = lookup_venue(args.venue)
        query = args.query or " ".join(filter(None, (brand, branch, args.term)))
        if args.cached:
            cache = json.loads(args.cached.read_text())
            feeds = [note for row in cache if compact(row["query"]) == compact(query) for note in row["feeds"]]
        else:
            filters = ({"sort_by": "综合", "publish_time": "不限"} if args.profile
                       else {"sort_by": "综合", "publish_time": "半年内"} if args.history
                       else {"sort_by": "最新", "publish_time": "一周内"})
            feeds = api_call("/feeds/search", {
                "keyword": query, "filters": filters,
            }).get("feeds") or []
        homepage = None
        if args.profile:
            official_id = args.official_id[0]
            for item in feeds:
                user = (item.get("noteCard") or {}).get("user") or {}
                token = user.get("xsecToken") or item.get("xsecToken")
                if user.get("userId") == official_id and token:
                    break
            else:
                raise ValueError("未找到指定官号，换一个已知帖子的标题或官号关键词重试")
            result = api_call("/user/profile", {
                "user_id": official_id, "xsec_token": token, "tab": "note",
            }, timeout=30)
            profile = result.get("data") or result
            if not isinstance(profile.get("feeds"), list):
                raise RuntimeError("官号主页未返回帖子清单")
            feeds = profile["feeds"]
            homepage = "https://www.xiaohongshu.com/user/profile/" + official_id
            for item in feeds:
                if item.get("noteCard"):
                    item["noteCard"]["user"] = user
        matches = rank(feeds, brand, branch, label, set(args.official_id))
        report = {"venue": args.venue, "query": query, "scanned": len(feeds),
                  "candidates": [note for _, note, _ in matches[:5]]}
        if homepage:
            report["profile"] = homepage
            report["recent_posts"] = [
                {"title": item["noteCard"].get("displayTitle"),
                 "url": "https://www.xiaohongshu.com/explore/" + item["id"]}
                for item in feeds[:12]
                if item.get("noteCard") and re.fullmatch(r"[0-9a-f]{24}", item.get("id", ""))
            ]
        if args.read and matches and matches[0][1]["can_attempt_read"]:
            time.sleep(3)
            _, candidate, raw = matches[0]
            try:
                detail = api_call("/feeds/detail", {
                    "feed_id": raw["id"], "xsec_token": raw["xsecToken"],
                    "load_all_comments": False,
                }, timeout=45)
                data = detail.get("data") or detail
                note = data.get("note") or {}
                candidate["body"] = note.get("desc") or ""
                candidate["published_at"] = note.get("time")
                candidate["image_count"] = len(note.get("imageList") or [])
            except (OSError, RuntimeError, ValueError) as exc:
                candidate["read_failure"] = type(exc).__name__
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        reason = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        print(f"小红书查询未完成（{reason}）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
