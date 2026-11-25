#!/usr/bin/env python3
"""查询公众号搜索摘要，或读取已知链接的公开公众号原文。"""

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



class PublicArticle(html.parser.HTMLParser):
    """Extract article metadata, text and poster links without running page scripts."""

    BLOCKS = {"br", "div", "li", "p", "section", "h1", "h2", "h3", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.text = []
        self.images = []
        self.body_depth = 0
        self.skip_text = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.meta[attrs.get("property")] = attrs.get("content") or ""
        if tag == "div":
            if self.body_depth or attrs.get("id") == "js_content":
                self.body_depth += 1
        if not self.body_depth:
            return
        if tag in ("script", "style"):
            self.skip_text = True
        if tag == "br":
            self.text.append("\n")
        if tag == "img":
            link = attrs.get("data-src") or attrs.get("src") or ""
            if is_wechat_image(link) and link not in self.images:
                self.images.append(link)

    def handle_endtag(self, tag):
        if self.body_depth:
            if tag in self.BLOCKS:
                self.text.append("\n")
            if tag in ("script", "style"):
                self.skip_text = False
            if tag == "div":
                self.body_depth -= 1

    def handle_data(self, data):
        if self.body_depth and not self.skip_text:
            self.text.append(data)


def is_wechat_image(link):
    parsed = urllib.parse.urlsplit(link)
    return parsed.scheme == "https" and parsed.hostname in {"mmbiz.qpic.cn", "mmbiz.qlogo.cn"}


def read_article(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "mp.weixin.qq.com"
            or not (parsed.path == "/s" or parsed.path.startswith("/s/"))):
        raise ValueError("需要 mp.weixin.qq.com 的 HTTPS 文章原文链接")
    mobile_ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) "
        "AppleWebKit/605.1.15 Mobile/15E148 MicroMessenger/8.0.61"
    )
    request = urllib.request.Request(url, headers={"User-Agent": mobile_ua})
    with urllib.request.urlopen(request, timeout=20) as response:
        page = response.read().decode("utf-8", errors="replace")
    article = PublicArticle()
    article.feed(page)
    title = article.meta.get("og:title")
    text = "\n".join(" ".join(line.split()) for line in "".join(article.text).splitlines() if line.strip())
    if not title or not (text or article.images):
        raise RuntimeError("未取得文章正文或海报，可能需要微信验证")
    account = article.meta.get("og:article:author")
    if not account:
        nickname = re.search(r'id="js_name"[^>]*>\s*([^<]+)', page)
        account = html.unescape(nickname[1]).strip() if nickname else None
    stamp = re.search(r'\bvar ct\s*=\s*["\'](\d+)["\']', page)
    published = dt.datetime.fromtimestamp(int(stamp[1]), dt.timezone(dt.timedelta(hours=8))) if stamp else None
    return {
        "url": url, "title": title, "account": account,
        "published_at": published.isoformat() if published else None,
        "body": text, "poster_urls": article.images,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="搜索关键词，例如：Benchmark 攀岩 换线")
    parser.add_argument("--account", help="只保留完全匹配的公众号名称")
    parser.add_argument("--article", metavar="URL", help="读取已知链接的公众号原文")
    args = parser.parse_args()
    if args.article:
        if args.query or args.account:
            parser.error("--article 不与搜索关键词或 --account 一起使用")
        try:
            print(json.dumps(read_article(args.article), ensure_ascii=False, indent=2))
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as error:
            print(f"读取原文失败：{error}", file=sys.stderr)
            return 1
        return 0
    if not args.query:
        parser.error("请输入搜索关键词或使用 --article URL")
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
