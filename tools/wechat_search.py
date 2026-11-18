#!/usr/bin/env python3
"""查询公开的公众号搜索摘要；结果仅供发现，需核对文章原文。"""

import argparse
import datetime as dt
import html.parser
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class SearchResults(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.current = None
        self.capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and attrs.get("id", "").startswith("sogou_vr_11002601_box_"):
            self.current = {"title": "", "summary": "", "account": "", "timestamp": None, "search_link": ""}
        elif self.current is not None:
            if tag == "h3":
                self.capture = "title"
            elif tag == "a" and self.capture == "title":
                link = urllib.parse.urljoin("https://weixin.sogou.com", attrs.get("href", ""))
                self.current["search_link"] = urllib.parse.quote(link, safe=":/?&=%._~-")
            elif tag == "p" and "txt-info" in attrs.get("class", "").split():
                self.capture = "summary"
            elif tag == "span" and "all-time-y2" in attrs.get("class", "").split():
                self.capture = "account"

    def handle_data(self, data):
        if self.current is None:
            return
        if self.capture:
            self.current[self.capture] += data
        match = re.search(r"timeConvert\(['\"](\d+)['\"]\)", data)
        if match:
            self.current["timestamp"] = int(match.group(1))

    def handle_endtag(self, tag):
        if tag in ("h3", "p", "span"):
            self.capture = None
        if tag == "li" and self.current is not None:
            item = self.current
            for key in ("title", "summary", "account"):
                item[key] = " ".join(item[key].split())
            stamp = item.pop("timestamp")
            item["published_at"] = dt.datetime.fromtimestamp(stamp, dt.timezone(dt.timedelta(hours=8))).isoformat() if stamp else None
            if item["title"]:
                self.items.append(item)
            self.current = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="搜索关键词，例如：Benchmark 攀岩 换线")
    parser.add_argument("--account", help="只保留完全匹配的公众号名称")
    args = parser.parse_args()
    params = urllib.parse.urlencode({"type": 2, "query": args.query})
    request = urllib.request.Request("https://weixin.sogou.com/weixin?" + params, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            page = response.read().decode("utf-8", errors="replace")
        if any(marker in page for marker in ("请输入验证码", "访问过于频繁", "请输入图片中的验证码")):
            raise RuntimeError("搜索服务要求验证或限制访问，请稍后手动检查")
        results = SearchResults()
        results.feed(page)
        if not results.items and not any(marker in page for marker in ("没有找到", "未找到相关", "找到约0条")):
            raise RuntimeError("搜索页面没有可识别的结果；可能被限制或页面结构已改变")
        items = [item for item in results.items if not args.account or item["account"] == args.account]
        print(json.dumps({"query": args.query, "account_filter": args.account, "matched": len(items), "search_results": len(results.items), "items": items}, ensure_ascii=False, indent=2))
    except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
        print(f"查询失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
