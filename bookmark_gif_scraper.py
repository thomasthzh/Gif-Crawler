#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import hashlib
import html
import json
import mimetypes
import re
import socket
import ssl
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_GIFS = 8
DEFAULT_MAX_GIF_BYTES = 15 * 1024 * 1024
DEFAULT_MAX_PAGES = 30
DEFAULT_GIF_WORKERS = 4
TEXT_LIKE_MIME = {"text/html", "application/xhtml+xml"}


class BookmarkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if href:
            self._current_href = href.strip()
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        label = "".join(self._current_text).strip()
        self.links.append({"url": self._current_href, "label": label})
        self._current_href = None
        self._current_text = []


class TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.parts.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self.parts).split())


class GifCandidateParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {key.lower(): value for key, value in attrs if value}
        for key in ("src", "data-src", "data-lazy-src", "data-original", "data-gif"):
            value = attr_map.get(key)
            if value and tag in {"img", "source", "video", "amp-img"}:
                self._push(value)
        poster = attr_map.get("poster")
        if poster and tag == "video":
            self._push(poster)
        content = attr_map.get("content")
        prop = (attr_map.get("property") or attr_map.get("name") or "").lower()
        if content and tag == "meta" and prop in {"og:image", "twitter:image", "twitter:image:src"}:
            self._push(content)
        srcset = attr_map.get("srcset")
        if srcset and tag in {"img", "source", "amp-img"}:
            for part in srcset.split(","):
                url_part = part.strip().split(" ")[0]
                if url_part:
                    self._push(url_part)
        style = attr_map.get("style")
        if style:
            for match in re.findall(r"url\((['\"]?)(.*?)\1\)", style, flags=re.IGNORECASE):
                self._push(match[1])

    def handle_data(self, data: str) -> None:
        for url in re.findall(
            r"https?://[^\s'\"\\)<>]+?\.gif(?:\?[^\s'\"\\)<>]*)?",
            data,
            flags=re.IGNORECASE,
        ):
            self._push(url)

    def _push(self, candidate: str) -> None:
        joined = urljoin(self.base_url, html.unescape(candidate.strip()))
        if joined.lower().startswith(("http://", "https://")):
            self.candidates.append(joined)


class LinkCandidateParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value for key, value in attrs if value}
        href = attr_map.get("href")
        if not href:
            return
        joined = urljoin(self.base_url, html.unescape(href.strip()))
        if joined.lower().startswith(("http://", "https://")):
            self.links.append(joined)


@dataclasses.dataclass
class GifResult:
    source_url: str
    content_type: str
    size: int
    digest: str
    body: bytes
    asset_path: str = ""


@dataclasses.dataclass
class PageResult:
    url: str
    bookmark_label: str
    final_url: str | None
    title: str
    status: str
    gifs: list[GifResult]
    errors: list[str]
    elapsed_ms: int


@dataclasses.dataclass
class ScrapeConfig:
    input_path: Path | None
    urls: list[str]
    output_path: Path
    asset_dir: str
    blocked_json: Path | None
    max_workers: int
    gif_workers: int
    max_gifs: int
    max_gif_mb: int
    timeout: int
    cookie: str
    cookie_file: Path | None
    disable_auto_simple_cookie: bool
    crawl_site: bool
    max_pages: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape GIFs from direct URLs or browser bookmark export HTML."
    )
    parser.add_argument("--url", action="append", default=[], help="Target URL. Repeat for multiple URLs.")
    parser.add_argument(
        "-i",
        "--input",
        default=None,
        help="Bookmark HTML path. If omitted and --url is empty, auto-detect bookmark HTML in current folder.",
    )
    parser.add_argument("-o", "--output", default="scrape-report.html", help="Output report HTML path.")
    parser.add_argument("--asset-dir", default="gif-assets", help="Asset directory relative to output HTML.")
    parser.add_argument("--blocked-json", default=None, help="JSON file of blocked URLs to skip.")
    parser.add_argument("--max-workers", type=int, default=4, help="Parallel page workers.")
    parser.add_argument("--gif-workers", type=int, default=DEFAULT_GIF_WORKERS, help="Parallel gif workers per page.")
    parser.add_argument("--max-gifs", type=int, default=DEFAULT_MAX_GIFS, help="Max GIFs per page.")
    parser.add_argument(
        "--max-gif-mb",
        type=int,
        default=DEFAULT_MAX_GIF_BYTES // (1024 * 1024),
        help="Skip GIF files larger than this MB.",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds.")
    parser.add_argument("--cookie", default="", help="Cookie header string, such as 'a=1; b=2'.")
    parser.add_argument("--cookie-file", default=None, help="Read cookie header string from a text file.")
    parser.add_argument(
        "--disable-auto-simple-cookie",
        action="store_true",
        help="Disable automatic retry with verified=true for simple verification pages.",
    )
    parser.add_argument("--crawl-site", action="store_true", help="Expand seed URLs by crawling in-site links.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Max pages for --crawl-site.")
    return parser.parse_args()


def find_default_input(base_dir: Path) -> Path | None:
    candidates = sorted(
        path
        for path in base_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".html", ".htm"}
        and "report" not in path.name.lower()
    )
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


def read_bookmarks(path: Path) -> list[dict[str, str]]:
    parser = BookmarkParser()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.links:
        url = item["url"].strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        deduped.append({"url": url, "label": item["label"].strip()})
    return deduped


def normalize_seed_urls(raw_urls: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_urls:
        url = raw.strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "label": ""})
    return out


def detect_encoding(content_type: str, body: bytes) -> str:
    charset_match = re.search(r"charset=([^\s;]+)", content_type, re.IGNORECASE)
    if charset_match:
        return charset_match.group(1).strip("\"'")
    head = body[:2048].decode("ascii", errors="ignore")
    meta_match = re.search(r"charset=['\"]?([a-zA-Z0-9_\-]+)", head, re.IGNORECASE)
    if meta_match:
        return meta_match.group(1)
    return "utf-8"


def make_request(url: str, timeout: int, extra_headers: dict[str, str] | None = None) -> tuple[str, bytes, str]:
    headers = {"User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    request = Request(url, headers=headers)
    context = ssl.create_default_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        body = response.read()
        final_url = response.geturl()
        content_type = response.headers.get_content_type()
        content_header = response.headers.get("Content-Type", content_type)
        return final_url, body, content_header


def extract_title(html_text: str) -> str:
    parser = TitleParser()
    parser.feed(html_text)
    return parser.title


def is_verification_page(html_text: str, title: str) -> bool:
    lowered = html_text.lower()
    title_lower = title.lower()
    signals = (
        "verified=true",
        "当前可用，请点击验证",
        "确认进入",
        "点击验证",
        "验证通过",
        "returnurl",
        "captcha",
        "verify you are human",
        "安全验证",
        "人机验证",
        "cloudflare",
    )
    return any(signal in lowered for signal in signals) or any(
        signal in title_lower for signal in ("确认进入", "点击继续", "验证")
    )


def load_cookie_header(cookie: str, cookie_file: str | None) -> str:
    parts: list[str] = []
    if cookie_file:
        path = Path(cookie_file).expanduser().resolve()
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore").strip()
            if content:
                parts.append(content)
    if cookie.strip():
        parts.append(cookie.strip())
    tokens: list[str] = []
    seen_keys: set[str] = set()
    for raw in "; ".join(parts).split(";"):
        token = raw.strip()
        if "=" not in token:
            continue
        key = token.split("=", 1)[0].strip().lower()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        tokens.append(token)
    return "; ".join(tokens)


def append_cookie(cookie_header: str, extra_token: str) -> str:
    if not extra_token:
        return cookie_header
    if not cookie_header:
        return extra_token
    key = extra_token.split("=", 1)[0].strip().lower()
    existing = {part.split("=", 1)[0].strip().lower() for part in cookie_header.split(";") if "=" in part}
    if key in existing:
        return cookie_header
    return f"{cookie_header}; {extra_token}"


def fetch_page(
    url: str,
    timeout: int,
    cookie_header: str,
    auto_simple_cookie: bool,
) -> tuple[str, str, str, bool, str]:
    headers: dict[str, str] = {}
    if cookie_header:
        headers["Cookie"] = cookie_header
    final_url, body, content_header = make_request(url, timeout=timeout, extra_headers=headers or None)
    encoding = detect_encoding(content_header, body)
    html_text = body.decode(encoding, errors="replace")
    title = extract_title(html_text)
    used_cookie = cookie_header

    if auto_simple_cookie and is_verification_page(html_text, title):
        retry_cookie = append_cookie(cookie_header, "verified=true")
        retry_final, retry_body, retry_header = make_request(
            url,
            timeout=timeout,
            extra_headers={"Cookie": retry_cookie},
        )
        retry_encoding = detect_encoding(retry_header, retry_body)
        retry_html = retry_body.decode(retry_encoding, errors="replace")
        retry_title = extract_title(retry_html)
        blocked = is_verification_page(retry_html, retry_title)
        return retry_final, retry_html, retry_header, blocked, retry_cookie

    blocked = is_verification_page(html_text, title)
    return final_url, html_text, content_header, blocked, used_cookie


def extract_gif_urls(base_url: str, html_text: str, limit: int) -> list[str]:
    parser = GifCandidateParser(base_url)
    parser.feed(html_text)
    raw_candidates = parser.candidates
    raw_candidates.extend(
        re.findall(
            r"""(?:https?://[^\s'"<>]+?\.gif(?:\?[^\s'"<>]*)?|(?:/|\.\./|\./)[^\s'"<>]+?\.gif(?:\?[^\s'"<>]*)?)""",
            html_text,
            flags=re.IGNORECASE,
        )
    )
    cleaned: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        normalized = urljoin(base_url, html.unescape(candidate.strip().strip("'\""))).split("#", 1)[0]
        if not normalized.lower().startswith(("http://", "https://")):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
        if len(cleaned) >= limit * 3:
            break
    return cleaned


def extract_page_links(base_url: str, html_text: str) -> list[str]:
    parser = LinkCandidateParser(base_url)
    parser.feed(html_text)
    out: list[str] = []
    seen: set[str] = set()
    for link in parser.links:
        normalized = link.split("#", 1)[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def load_blocked_urls(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    if isinstance(data, list):
        return {str(item).strip() for item in data if str(item).strip()}
    if isinstance(data, dict) and isinstance(data.get("blocked_urls"), list):
        return {str(item).strip() for item in data["blocked_urls"] if str(item).strip()}
    return set()


def expand_site_urls(
    seeds: list[dict[str, str]],
    timeout: int,
    max_pages: int,
    cookie_header: str,
    auto_simple_cookie: bool,
) -> list[dict[str, str]]:
    if not seeds:
        return []
    queue: list[str] = [item["url"] for item in seeds]
    seed_domains = {(urlparse(item["url"]).netloc or "").lower() for item in seeds}
    visited: set[str] = set()
    output: list[dict[str, str]] = []

    while queue and len(output) < max_pages:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        try:
            final_url, html_text, content_header, _, _ = fetch_page(
                current,
                timeout=timeout,
                cookie_header=cookie_header,
                auto_simple_cookie=auto_simple_cookie,
            )
            output.append({"url": final_url, "label": ""})
            mime = content_header.split(";", 1)[0].lower()
            if mime not in TEXT_LIKE_MIME and "html" not in mime:
                continue
            for link in extract_page_links(final_url, html_text):
                if link in visited:
                    continue
                domain = (urlparse(link).netloc or "").lower()
                if domain in seed_domains:
                    queue.append(link)
        except Exception:  # noqa: BLE001
            output.append({"url": current, "label": ""})

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in output:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        deduped.append(item)
    return deduped


def download_gif(
    url: str,
    timeout: int,
    max_bytes: int,
    cookie_header: str,
    referer: str | None = None,
) -> GifResult | None:
    headers: dict[str, str] = {}
    if cookie_header:
        headers["Cookie"] = cookie_header
    if referer:
        headers["Referer"] = referer
    final_url, body, content_header = make_request(url, timeout=timeout, extra_headers=headers or None)
    content_type = content_header.split(";", 1)[0].lower()
    guessed_type, _ = mimetypes.guess_type(final_url)
    is_gif_binary = body.startswith((b"GIF87a", b"GIF89a"))
    is_gif = (
        content_type == "image/gif"
        or is_gif_binary
        or (guessed_type == "image/gif" and content_type not in TEXT_LIKE_MIME)
    )
    if not is_gif:
        return None
    if len(body) > max_bytes:
        raise ValueError(f"GIF too large: {len(body) / (1024 * 1024):.1f} MB")
    return GifResult(
        source_url=final_url,
        content_type="image/gif",
        size=len(body),
        digest=hashlib.sha1(body).hexdigest()[:16],
        body=body,
    )


def scrape_one(
    bookmark: dict[str, str],
    timeout: int,
    max_gifs: int,
    max_gif_bytes: int,
    gif_workers: int,
    cookie_header: str,
    auto_simple_cookie: bool,
    blocked_urls: set[str],
) -> PageResult:
    start = time.perf_counter()
    url = bookmark["url"]
    label = bookmark["label"]
    title = ""
    final_url: str | None = None
    gifs: list[GifResult] = []
    errors: list[str] = []
    status = "ok"

    try:
        final_url, html_text, content_header, needs_verification, effective_cookie = fetch_page(
            url=url,
            timeout=timeout,
            cookie_header=cookie_header,
            auto_simple_cookie=auto_simple_cookie,
        )
        mime = content_header.split(";", 1)[0].lower()
        if needs_verification:
            status = "needs verification"
            errors.append("Detected complex verification. Finish it in browser and rerun with --cookie or --cookie-file.")
        elif mime not in TEXT_LIKE_MIME and "html" not in mime:
            status = f"skipped: unsupported content type {mime}"
        else:
            title = extract_title(html_text) or label or url
            candidates = [u for u in extract_gif_urls(final_url, html_text, max_gifs) if u not in blocked_urls]
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, gif_workers)) as pool:
                future_map = {
                    pool.submit(
                        download_gif,
                        gif_url,
                        timeout,
                        max_gif_bytes,
                        effective_cookie,
                        final_url,
                    ): gif_url
                    for gif_url in candidates
                }
                for future in concurrent.futures.as_completed(future_map):
                    gif_url = future_map[future]
                    if len(gifs) >= max_gifs:
                        continue
                    try:
                        gif = future.result()
                        if gif:
                            gifs.append(gif)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{gif_url} -> {exc}")
            if not gifs and not errors:
                status = "ok: no gif found"
    except HTTPError as exc:
        status = f"http error {exc.code}"
        errors.append(str(exc))
    except URLError as exc:
        status = "network error"
        errors.append(str(exc.reason))
    except socket.timeout:
        status = "timeout"
        errors.append("request timed out")
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        errors.append(str(exc))

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return PageResult(
        url=url,
        bookmark_label=label,
        final_url=final_url,
        title=title or label or url,
        status=status,
        gifs=gifs,
        errors=errors,
        elapsed_ms=elapsed_ms,
    )


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._")
    return text or "gif-assets"


def save_assets(results: list[PageResult], output_path: Path, asset_dir_name: str) -> None:
    asset_dir = (output_path.parent / asset_dir_name).resolve()
    asset_dir.mkdir(parents=True, exist_ok=True)
    by_digest: dict[str, str] = {}
    for page_index, page in enumerate(results, start=1):
        for gif_index, gif in enumerate(page.gifs, start=1):
            if gif.digest in by_digest:
                gif.asset_path = by_digest[gif.digest]
                continue
            filename = f"p{page_index:04d}_g{gif_index:03d}_{gif.digest}.gif"
            abs_path = asset_dir / filename
            abs_path.write_bytes(gif.body)
            rel_path = f"{asset_dir_name}/{filename}"
            gif.asset_path = rel_path
            by_digest[gif.digest] = rel_path


def render_report(results: list[PageResult], source_name: str) -> str:
    total_links = len(results)
    total_gifs = sum(len(item.gifs) for item in results)
    success_count = sum(1 for item in results if item.status.startswith("ok"))
    payload = [
        {
            "url": item.url,
            "bookmark_label": item.bookmark_label,
            "final_url": item.final_url,
            "title": item.title,
            "status": item.status,
            "elapsed_ms": item.elapsed_ms,
            "gifs": [
                {
                    "source_url": gif.source_url,
                    "size": gif.size,
                    "asset_path": gif.asset_path,
                }
                for gif in item.gifs
            ],
            "errors": item.errors,
        }
        for item in results
    ]
    embedded_json = json.dumps(payload, ensure_ascii=False).replace("</script>", "<\\/script>")
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GIF 抓取报告</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5efe5;
      --card: rgba(255,255,255,0.88);
      --line: rgba(73, 50, 25, 0.15);
      --text: #2b2117;
      --muted: #6c5a49;
      --accent: #b5542f;
      --accent-2: #1f6c63;
      --ok: #1f6c63;
      --warn: #9a6b00;
      --bad: #9d2f2f;
      --shadow: 0 14px 40px rgba(68, 40, 19, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(181,84,47,0.18), transparent 32%),
        radial-gradient(circle at top right, rgba(31,108,99,0.15), transparent 28%),
        linear-gradient(180deg, #f8f2ea, var(--bg));
    }}
    .wrap {{
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    .hero {{
      padding: 24px;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: linear-gradient(135deg, rgba(255,255,255,0.93), rgba(252,246,239,0.82));
      box-shadow: var(--shadow);
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: clamp(28px, 5vw, 48px);
      line-height: 1.05;
      letter-spacing: -0.03em;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
      word-break: break-all;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .stat {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255,255,255,0.72);
      border: 1px solid var(--line);
    }}
    .stat strong {{
      display: block;
      font-size: 24px;
      margin-bottom: 4px;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin: 20px 0 14px;
      align-items: center;
    }}
    .toolbar input[type="search"] {{
      flex: 1 1 260px;
      padding: 14px 16px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.86);
      color: var(--text);
      outline: none;
      font-size: 15px;
    }}
    .toolbar button, .toolbar .file-label {{
      border: none;
      border-radius: 999px;
      padding: 14px 18px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      font-size: 14px;
    }}
    .toolbar .file-label {{
      background: var(--accent-2);
      display: inline-flex;
      align-items: center;
    }}
    .list {{
      display: grid;
      gap: 14px;
    }}
    details {{
      border: 1px solid var(--line);
      border-radius: 22px;
      overflow: hidden;
      background: var(--card);
      box-shadow: var(--shadow);
    }}
    summary {{
      list-style: none;
      cursor: pointer;
      padding: 18px 20px;
    }}
    summary::-webkit-details-marker {{ display: none; }}
    .row {{
      display: grid;
      gap: 8px;
    }}
    .title {{
      font-size: 20px;
      font-weight: 700;
      line-height: 1.3;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(107,90,73,0.08);
      border: 1px solid rgba(107,90,73,0.12);
    }}
    .pill.ok {{ color: var(--ok); }}
    .pill.warn {{ color: var(--warn); }}
    .pill.bad {{ color: var(--bad); }}
    .content {{
      padding: 0 20px 20px;
      border-top: 1px solid var(--line);
    }}
    .links {{
      margin: 16px 0;
      display: grid;
      gap: 8px;
    }}
    .links a {{
      color: var(--accent-2);
      text-decoration: none;
      word-break: break-all;
    }}
    .gallery {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-top: 14px;
    }}
    figure {{
      margin: 0;
      padding: 12px;
      background: rgba(255,255,255,0.7);
      border-radius: 18px;
      border: 1px solid var(--line);
      transition: opacity .2s ease;
    }}
    figure.blocked {{
      opacity: .35;
      filter: grayscale(0.8);
    }}
    img {{
      width: 100%;
      height: auto;
      max-height: 320px;
      object-fit: contain;
      border-radius: 12px;
      background: #fff9f2;
    }}
    figcaption {{
      margin-top: 8px;
      font-size: 12px;
      color: var(--muted);
      word-break: break-all;
    }}
    .gif-actions {{
      margin-top: 8px;
      display: flex;
      justify-content: flex-end;
    }}
    .small-btn {{
      border: none;
      border-radius: 999px;
      padding: 6px 12px;
      cursor: pointer;
      background: rgba(181,84,47,0.12);
      color: var(--accent);
      font-size: 12px;
    }}
    .error-list {{
      margin: 14px 0 0;
      padding-left: 18px;
      color: var(--bad);
      line-height: 1.5;
    }}
    .empty {{
      padding: 18px;
      border-radius: 18px;
      background: rgba(255,255,255,0.72);
      border: 1px dashed var(--line);
      color: var(--muted);
    }}
    .hidden {{ display: none !important; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>GIF 抓取报告</h1>
      <p>来源：{html.escape(source_name)}<br>生成时间：{html.escape(generated_at)}</p>
      <div class="stats">
        <div class="stat"><strong>{total_links}</strong><span>总链接数</span></div>
        <div class="stat"><strong>{success_count}</strong><span>抓取完成</span></div>
        <div class="stat"><strong>{total_gifs}</strong><span>本地 GIF 数</span></div>
      </div>
    </section>
    <div class="toolbar">
      <input id="search" type="search" placeholder="搜索标题、书签名、网址">
      <button id="expand-all" type="button">全部展开</button>
      <button id="collapse-all" type="button">全部收起</button>
      <button id="export-blocked" type="button">导出屏蔽清单</button>
      <label for="import-blocked" class="file-label">
        导入屏蔽清单
        <input id="import-blocked" type="file" accept="application/json" class="hidden">
      </label>
    </div>
    <section id="list" class="list"></section>
  </div>
  <script id="payload" type="application/json">{embedded_json}</script>
  <script>
    const payload = JSON.parse(document.getElementById("payload").textContent);
    const list = document.getElementById("list");
    const blockedStorageKey = "gif-report-blocked-list-v1";
    const blocked = new Set(JSON.parse(localStorage.getItem(blockedStorageKey) || "[]"));

    function statusClass(status) {{
      if (status.startsWith("ok")) return "ok";
      if (status.includes("no gif") || status.includes("skipped")) return "warn";
      return "bad";
    }}

    function persistBlocked() {{
      localStorage.setItem(blockedStorageKey, JSON.stringify(Array.from(blocked)));
    }}

    function createCard(item, index) {{
      const details = document.createElement("details");
      if (index < 3) details.open = true;

      const summary = document.createElement("summary");
      const row = document.createElement("div");
      row.className = "row";

      const title = document.createElement("div");
      title.className = "title";
      title.textContent = item.title || item.bookmark_label || item.url;

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.innerHTML = `
        <span class="pill ${{statusClass(item.status)}}">${{item.status}}</span>
        <span class="pill">GIF ${{item.gifs.length}}</span>
        <span class="pill">${{item.elapsed_ms}} ms</span>
      `;

      row.appendChild(title);
      row.appendChild(meta);
      summary.appendChild(row);
      details.appendChild(summary);

      const content = document.createElement("div");
      content.className = "content";
      const links = document.createElement("div");
      links.className = "links";

      const bookmark = document.createElement("a");
      bookmark.href = item.url;
      bookmark.target = "_blank";
      bookmark.rel = "noreferrer";
      bookmark.textContent = "入口链接: " + item.url;
      links.appendChild(bookmark);

      if (item.final_url && item.final_url !== item.url) {{
        const finalLink = document.createElement("a");
        finalLink.href = item.final_url;
        finalLink.target = "_blank";
        finalLink.rel = "noreferrer";
        finalLink.textContent = "实际访问地址: " + item.final_url;
        links.appendChild(finalLink);
      }}
      content.appendChild(links);

      if (item.gifs.length) {{
        const gallery = document.createElement("div");
        gallery.className = "gallery";
        for (const gif of item.gifs) {{
          const figure = document.createElement("figure");
          figure.dataset.sourceUrl = gif.source_url;
          if (blocked.has(gif.source_url)) figure.classList.add("blocked");

          const img = document.createElement("img");
          img.loading = "lazy";
          img.src = gif.asset_path;
          img.alt = item.title;

          const cap = document.createElement("figcaption");
          cap.textContent = `${{gif.source_url}} | ${{(gif.size / 1024).toFixed(1)}} KB`;

          const actions = document.createElement("div");
          actions.className = "gif-actions";
          const toggle = document.createElement("button");
          toggle.type = "button";
          toggle.className = "small-btn";
          toggle.textContent = blocked.has(gif.source_url) ? "取消屏蔽" : "屏蔽";
          toggle.addEventListener("click", () => {{
            if (blocked.has(gif.source_url)) {{
              blocked.delete(gif.source_url);
              figure.classList.remove("blocked");
              toggle.textContent = "屏蔽";
            }} else {{
              blocked.add(gif.source_url);
              figure.classList.add("blocked");
              toggle.textContent = "取消屏蔽";
            }}
            persistBlocked();
          }});
          actions.appendChild(toggle);

          figure.appendChild(img);
          figure.appendChild(cap);
          figure.appendChild(actions);
          gallery.appendChild(figure);
        }}
        content.appendChild(gallery);
      }} else {{
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "这个链接没有抓到可用 GIF。";
        content.appendChild(empty);
      }}

      if (item.errors.length) {{
        const errors = document.createElement("ul");
        errors.className = "error-list";
        for (const error of item.errors) {{
          const li = document.createElement("li");
          li.textContent = error;
          errors.appendChild(li);
        }}
        content.appendChild(errors);
      }}

      details.appendChild(content);
      details.dataset.search = [item.title, item.bookmark_label, item.url, item.final_url || ""].join(" ").toLowerCase();
      return details;
    }}

    payload.forEach((item, index) => list.appendChild(createCard(item, index)));

    document.getElementById("search").addEventListener("input", (event) => {{
      const keyword = event.target.value.trim().toLowerCase();
      for (const node of list.children) {{
        node.classList.toggle("hidden", keyword && !node.dataset.search.includes(keyword));
      }}
    }});

    document.getElementById("expand-all").addEventListener("click", () => {{
      for (const node of list.children) node.open = true;
    }});
    document.getElementById("collapse-all").addEventListener("click", () => {{
      for (const node of list.children) node.open = false;
    }});

    document.getElementById("export-blocked").addEventListener("click", () => {{
      const data = {{
        blocked_urls: Array.from(blocked).sort(),
        exported_at: new Date().toISOString()
      }};
      const blob = new Blob([JSON.stringify(data, null, 2)], {{ type: "application/json" }});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "blocked-gifs.json";
      a.click();
      URL.revokeObjectURL(a.href);
    }});

    document.getElementById("import-blocked").addEventListener("change", async (event) => {{
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {{
        const text = await file.text();
        const data = JSON.parse(text);
        const arr = Array.isArray(data) ? data : (Array.isArray(data.blocked_urls) ? data.blocked_urls : []);
        for (const item of arr) {{
          if (typeof item === "string" && item.trim()) blocked.add(item.trim());
        }}
        persistBlocked();
        location.reload();
      }} catch (_) {{
        alert("导入失败：JSON 格式不正确。");
      }}
    }});
  </script>
</body>
</html>
"""


def merge_targets(bookmarks: list[dict[str, str]], urls: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in [*bookmarks, *urls]:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        out.append(item)
    return out


def run_scrape(
    config: ScrapeConfig,
    log: Callable[[str], None] = print,
    progress: Callable[[dict[str, object]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    base_dir = Path.cwd()
    output_path = config.output_path.expanduser().resolve()
    max_gif_bytes = max(1, config.max_gif_mb) * 1024 * 1024
    cookie_file = str(config.cookie_file) if config.cookie_file else None
    cookie_header = load_cookie_header(config.cookie, cookie_file)
    auto_simple_cookie = not config.disable_auto_simple_cookie
    blocked_urls = load_blocked_urls(config.blocked_json)

    input_path = config.input_path.expanduser().resolve() if config.input_path else None
    if input_path is None and not config.urls:
        input_path = find_default_input(base_dir)

    if should_stop and should_stop():
        log("Task cancelled before start.")
        if progress:
            progress({"stage": "cancelled", "message": "cancelled"})
        return 130

    bookmarks = read_bookmarks(input_path) if input_path and input_path.exists() else []
    if input_path and not input_path.exists():
        log(f"Input file not found: {input_path}")
        if progress:
            progress({"stage": "error", "message": f"Input file not found: {input_path}"})
        return 1

    urls = normalize_seed_urls(config.urls)
    targets = merge_targets(bookmarks, urls)
    if not targets:
        log("No input links found. Use URL or bookmark HTML input.")
        if progress:
            progress({"stage": "error", "message": "No input links found."})
        return 1

    if config.crawl_site:
        log(f"Expanding in-site links from {len(targets)} seed URLs (max pages: {max(1, config.max_pages)}) ...")
        if progress:
            progress({"stage": "expanding", "seed_count": len(targets), "max_pages": max(1, config.max_pages)})
        targets = expand_site_urls(
            seeds=targets,
            timeout=config.timeout,
            max_pages=max(1, config.max_pages),
            cookie_header=cookie_header,
            auto_simple_cookie=auto_simple_cookie,
        )

    log(f"Found {len(targets)} unique links. blocked URLs: {len(blocked_urls)}")
    total_targets = len(targets)
    if progress:
        progress({"stage": "start", "total": total_targets, "blocked": len(blocked_urls)})
    results: list[PageResult] = []
    ok_count = 0
    fail_count = 0
    gif_total = 0
    cancelled = False
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, config.max_workers))
    try:
        future_map = {
            executor.submit(
                scrape_one,
                bookmark,
                config.timeout,
                max(1, config.max_gifs),
                max_gif_bytes,
                max(1, config.gif_workers),
                cookie_header,
                auto_simple_cookie,
                blocked_urls,
            ): bookmark
            for bookmark in targets
        }
        for index, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
            if should_stop and should_stop():
                cancelled = True
                log("Task cancelled by user. Finishing current items...")
                if progress:
                    progress({"stage": "cancelled", "done": index - 1, "total": total_targets})
                break
            bookmark = future_map[future]
            try:
                result = future.result()
                results.append(result)
                log(f"[{index}/{len(targets)}] {bookmark['url']} -> {result.status} (gif: {len(result.gifs)})")
                if result.status.startswith("ok"):
                    ok_count += 1
                else:
                    fail_count += 1
                gif_total += len(result.gifs)
                if progress:
                    progress(
                        {
                            "stage": "item",
                            "done": index,
                            "total": total_targets,
                            "ok": ok_count,
                            "failed": fail_count,
                            "gif_total": gif_total,
                            "url": bookmark["url"],
                            "status": result.status,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                log(f"[{index}/{len(targets)}] {bookmark['url']} -> failed: {exc}")
                fail_count += 1
                if progress:
                    progress(
                        {
                            "stage": "item",
                            "done": index,
                            "total": total_targets,
                            "ok": ok_count,
                            "failed": fail_count,
                            "gif_total": gif_total,
                            "url": bookmark["url"],
                            "status": "failed",
                        }
                    )
                results.append(
                    PageResult(
                        url=bookmark["url"],
                        bookmark_label=bookmark["label"],
                        final_url=None,
                        title=bookmark["label"] or bookmark["url"],
                        status="failed",
                        gifs=[],
                        errors=[str(exc)],
                        elapsed_ms=0,
                    )
                )
    finally:
        executor.shutdown(wait=not cancelled, cancel_futures=cancelled)

    if cancelled:
        return 130

    results.sort(key=lambda item: item.title.lower())
    asset_dir_name = slugify(config.asset_dir)
    save_assets(results, output_path=output_path, asset_dir_name=asset_dir_name)
    source_name = str(input_path) if input_path else ("direct URLs" if urls else "unknown")
    report = render_report(results, source_name=source_name)
    output_path.write_text(report, encoding="utf-8")
    log(f"Saved report to: {output_path}")
    log(f"GIF assets directory: {output_path.parent / asset_dir_name}")
    if progress:
        progress(
            {
                "stage": "done",
                "total": total_targets,
                "ok": ok_count,
                "failed": fail_count,
                "gif_total": gif_total,
                "report": str(output_path),
            }
        )
    return 0


def main() -> int:
    args = parse_args()
    config = ScrapeConfig(
        input_path=Path(args.input).expanduser().resolve() if args.input else None,
        urls=args.url,
        output_path=Path(args.output).expanduser().resolve(),
        asset_dir=args.asset_dir,
        blocked_json=Path(args.blocked_json).expanduser().resolve() if args.blocked_json else None,
        max_workers=args.max_workers,
        gif_workers=args.gif_workers,
        max_gifs=args.max_gifs,
        max_gif_mb=args.max_gif_mb,
        timeout=args.timeout,
        cookie=args.cookie,
        cookie_file=Path(args.cookie_file).expanduser().resolve() if args.cookie_file else None,
        disable_auto_simple_cookie=args.disable_auto_simple_cookie,
        crawl_site=args.crawl_site,
        max_pages=args.max_pages,
    )
    return run_scrape(config=config, log=print)


if __name__ == "__main__":
    raise SystemExit(main())
