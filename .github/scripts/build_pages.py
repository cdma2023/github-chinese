#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Markdown 文档渲染成 GitHub 风格的静态 HTML 页面。

GitHub Pages 在「GitHub Actions」部署模式下不会执行 Jekyll，
因此不会用 README.md 自动生成 index.html，需要本脚本在本地/CI 预先生成。

用法:
    python .github/scripts/build_pages.py <markdown 文件> [<markdown 文件> ...]

输出: 同目录下的同名 .html 文件；README.md 会输出为 index.html。
"""

import io
import os
import re
import sys

import markdown

# ---------------------------------------------------------------- GitHub 锚点
# 与 GitHub 的 slugify 保持一致的简化实现：
# 去掉除字母/数字/下划线/连字符/空格/CJK 之外的字符，空格转连字符。
_PUNCT = re.compile(r"[^\w\- ]", re.UNICODE)


def slugify(value, separator="-"):
    value = value.strip().lower()
    value = _PUNCT.sub("", value)
    return re.sub(r"[ ]+", separator, value)


# ------------------------------------------------------------------ 页面样式
CSS = """
:root {
  --fg: #1f2328; --bg: #ffffff; --muted: #59636e; --border: #d1d9e0;
  --accent: #0969da; --code-bg: #f6f8fa; --quote-bg: #f6f8fa;
  --note: #0969da; --warn: #9a6700; --tip: #1a7f37;
  --note-bg: #ddf4ff; --warn-bg: #fff8c5; --tip-bg: #dafbe1;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #e6edf3; --bg: #0d1117; --muted: #9198a1; --border: #3d444d;
    --accent: #4493f8; --code-bg: #151b23; --quote-bg: #151b23;
    --note: #4493f8; --warn: #d29922; --tip: #3fb950;
    --note-bg: #12283b; --warn-bg: #2e2400; --tip-bg: #0f2f1d;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 32px 20px 64px; max-width: 1012px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans",
               "PingFang SC", "Microsoft YaHei", Helvetica, Arial, sans-serif;
  font-size: 16px; line-height: 1.6; color: var(--fg); background: var(--bg);
  -webkit-font-smoothing: antialiased;
}
h1, h2, h3, h4 { font-weight: 600; line-height: 1.25; margin: 24px 0 16px; }
h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
h3 { font-size: 1.25em; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
img { max-width: 100%; vertical-align: middle; }
code {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 85%; background: var(--code-bg); padding: .2em .4em;
  border-radius: 6px;
}
pre {
  background: var(--code-bg); padding: 16px; overflow: auto;
  border-radius: 6px; line-height: 1.45;
}
pre code { background: none; padding: 0; font-size: 100%; }
blockquote {
  margin: 0 0 16px; padding: 0 1em; color: var(--muted);
  border-left: .25em solid var(--border);
}
table { border-collapse: collapse; display: block; width: max-content;
        max-width: 100%; overflow: auto; margin: 0 0 16px; }
th, td { border: 1px solid var(--border); padding: 6px 13px; }
th { background: var(--code-bg); font-weight: 600; }
tr:nth-child(2n) { background: var(--quote-bg); }
hr { height: 1px; border: 0; background: var(--border); margin: 24px 0; }
details { margin: 12px 0; padding: 8px 12px; border: 1px solid var(--border);
          border-radius: 6px; }
summary { cursor: pointer; font-weight: 600; }
kbd { font-family: ui-monospace, monospace; font-size: 85%;
      background: var(--code-bg); border: 1px solid var(--border);
      border-radius: 6px; padding: 2px 6px; }

/* GitHub 风格提示框: > [!note] / [!warning] / [!tip] / [!important] */
blockquote.alert { border-left: .25em solid var(--note); padding: 8px 16px;
                   margin: 16px 0; border-radius: 6px;
                   background: var(--note-bg); color: var(--fg); }
blockquote.alert > p { margin: 4px 0; }
blockquote.alert-warning { border-left-color: var(--warn); background: var(--warn-bg); }
blockquote.alert-tip      { border-left-color: var(--tip);  background: var(--tip-bg); }
blockquote.alert-important{ border-left-color: #8250df; background: var(--note-bg); }

.footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--border);
          font-size: 85%; color: var(--muted); }
"""

ALERT_RE = re.compile(
    r"<blockquote>\s*<p>\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\](.*?)</p>(.*?)</blockquote>",
    re.IGNORECASE | re.DOTALL,
)


def _render_alerts(html: str) -> str:
    """把 `> [!note]` 语法转成带样式的提示框。"""

    def repl(m):
        kind = m.group(1).lower()
        if kind == "caution":
            kind = "warning"
        body = (m.group(2) or "") + (m.group(3) or "")
        return (
            f'<blockquote class="alert alert-{kind}">'
            f"<p><strong>{m.group(1).capitalize()}</strong></p>{body}</blockquote>"
        )

    return ALERT_RE.sub(repl, html)


MD_LINK_RE = re.compile(r"\]\(([^)\s]+\.md)(#[^)\s]*)?\)")


def _rewrite_md_links(text: str) -> str:
    """把 .md 内链改写成 .html，README.md 例外（会渲染成 index.html）。"""

    def repl(m):
        target = m.group(1)
        anchor = m.group(2) or ""
        # 只有仓库根目录的 README.md 会被渲染成 index.html
        is_root_readme = os.path.basename(target).lower() == "readme.md" and \
            os.path.dirname(target).replace("\\", "/").strip("./") == ""
        new = "index.html" if is_root_readme else target[:-3] + ".html"
        return f"]({new}{anchor})"

    return MD_LINK_RE.sub(repl, text)


# 上游仓库 raw 资源地址 -> 本站点相对路径（镜像不应该再去请求上游）
UPSTREAM_RAW = "https://raw.githubusercontent.com/maboloshi/github-chinese/gh-pages/"
# python-markdown 的 md_in_html 只在显式声明 markdown="1" 的块级标签内渲染
# Markdown，而 GitHub 默认就是这么做的，所以这里统一补上该属性。
BLOCK_TAG_RE = re.compile(r"<(div|details)\b((?:(?!markdown=)[^>])*)>", re.IGNORECASE)


def _localize_assets(text: str) -> str:
    return text.replace(UPSTREAM_RAW, "")


def _enable_md_in_html(text: str) -> str:
    return BLOCK_TAG_RE.sub(lambda m: f'<{m.group(1)}{m.group(2)} markdown="1">', text)


def render(md_path: str) -> str:
    text = io.open(md_path, encoding="utf-8").read()
    text = _localize_assets(text)
    text = _enable_md_in_html(text)
    text = _rewrite_md_links(text)

    body = markdown.markdown(
        text,
        extensions=[
            "extra",          # 表格 / 围栏代码 / 脚注 等
            "sane_lists",
            "md_in_html",     # 渲染 <div>/<details> 等 HTML 块内部的 Markdown
            "toc",            # 生成与 GitHub 一致的标题锚点
        ],
        extension_configs={"toc": {"slugify": slugify, "permalink": False}},
    )
    body = _render_alerts(body)

    title = "GitHub 中文化插件"
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        title = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", m.group(1)).strip()

    # 只有仓库根目录的 README.md 输出为 index.html，子目录保持原名 README.html
    _dir = os.path.dirname(md_path).replace("\\", "/").strip("./")
    out_name = "index.html" if (
        os.path.basename(md_path).lower() == "readme.md" and _dir == ""
    ) else os.path.basename(md_path)[:-3] + ".html"
    out_path = os.path.join(os.path.dirname(md_path), out_name)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="GitHub 界面中文化插件 - maboloshi/github-chinese 自托管镜像">
<link rel="icon" href="https://github.githubassets.com/pinned-octocat.svg">
<style>{CSS}</style>
</head>
<body>
<a id="readme-top"></a>
<main class="markdown-body">
{body}
</main>
<p class="footer">
  本页面由 <a href="https://github.com/cdma2023/github-chinese">cdma2023/github-chinese</a>
  自动构建 · 上游项目
  <a href="https://github.com/maboloshi/github-chinese">maboloshi/github-chinese</a>
  · GPL-3.0
</p>
</body>
</html>
"""
    io.open(out_path, "w", encoding="utf-8", newline="\n").write(html)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for p in sys.argv[1:]:
        print("rendered ->", render(p))
