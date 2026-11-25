#!/usr/bin/env python3
"""Validate a direct official-account history provider against a known notice."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from wechat_search import read_article

ENDPOINTS = {
    "dataxapi": (
        "https://api.dataxapi.com/v1/wechat_mp/accounts/articles/list",
        "DATAXAPI_API_KEY",
    ),
    "okdata": (
        "https://api.okdata.online/v1/wechat_mp/accounts/articles/list",
        "OKDATA_API_KEY",
    ),
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def list_articles(provider, account_id, key):
    endpoint, _ = ENDPOINTS[provider]
    query = urllib.parse.urlencode({"id": account_id, "tab": "articles"})
    request = urllib.request.Request(endpoint + "?" + query, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{provider} 返回 HTTP {error.code}") from None
    except (OSError, ValueError) as error:
        raise RuntimeError(f"{provider} 请求未完成（{type(error).__name__}）") from None
    if payload.get("code") != "ok":
        raise RuntimeError(f"{provider} 未返回成功")
    result = payload.get("data", {}).get("items")
    if not isinstance(result, list):
        raise RuntimeError(f"{provider} 返回的发文列表格式不符")
    articles = {}
    for item in result:
        author = (item.get("author") or {}).get("id")
        url = item.get("url") or ""
        parsed = urllib.parse.urlsplit(url)
        if author and author != account_id:
            continue
        if (parsed.scheme != "https"
                or parsed.netloc != "mp.weixin.qq.com"
                or not (parsed.path == "/s" or parsed.path.startswith("/s/"))):
            continue
        articles[url] = {"title": item.get("title") or "", "url": url,
                         "published_at": item.get("published_at")}
    return list(articles.values())


def verify_notice(articles, title, account, published_on):
    match = [row for row in articles if row["title"].strip() == title]
    if not match:
        raise RuntimeError("近期列表未命中验收公告")
    # Verify the returned URL rather than opening a known article separately.
    for row in match:
        detail = read_article(row["url"])
        author = detail.get("account") or ""
        posted = detail.get("published_at") or ""
        if (author.casefold() == account.casefold() and posted.startswith(published_on + "T")
                and detail["title"].strip() == title and (detail["body"] or detail["poster_urls"])):
            return {"title": title, "account": author, "published_at": posted, "url": row["url"]}
    raise RuntimeError("找到同名文章，但馆方原文的账号或发文日期未通过核对")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=ENDPOINTS)
    parser.add_argument("account_id", help="由公众号原文核实的 gh_ 原始 ID")
    parser.add_argument("--expect", nargs=3, metavar=("TITLE", "ACCOUNT", "DATE"),
                        help="从列表查到指定公告后，打开对应微信原文验证")
    parser.add_argument("--run", action="store_true", help="实际发出一次可能计费的列表请求")
    args = parser.parse_args()
    if not re.fullmatch(r"gh_[0-9a-z]{12}", args.account_id):
        parser.error("需要 gh_ 开头的公众号原始 ID")
    _, key_name = ENDPOINTS[args.provider]
    if not args.run:
        print(f"试查 {args.account_id}，最多发出一次 {args.provider} 付费列表请求。配置 {key_name} 后加 --run 才会执行。")
        return 0
    key = os.environ.get(key_name, "").strip()
    if not key:
        print(f"缺少本机 {key_name}，未发出请求。", file=sys.stderr)
        return 1
    try:
        rows = list_articles(args.provider, args.account_id, key)
        output = {"account_id": args.account_id, "provider": args.provider, "items": rows}
        if args.expect:
            output["verified_notice"] = verify_notice(rows, *args.expect)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"验证失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
