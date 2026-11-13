from html import escape


from agent_core.report_markdown import render_markdown_html
from agent_core.report_limits import MAX_REPORT_TITLE_CHARS


REPORT_CSS = """
@page {
    size: A4;
    margin: 18mm;
    @bottom-center {
        content: counter(page);
        font-size: 9pt;
        color: #666;
    }
}
body {
    font-family: sans-serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #222;
    margin: 0;
    overflow-wrap: break-word;
}
h1, h2, h3, h4, h5, h6 {
    break-after: avoid;
    line-height: 1.2;
}
h1 { font-size: 22pt; }
h2 { font-size: 15pt; margin-top: 1.4em; }
p, li { orphans: 3; widows: 3; }
a { color: #18547a; text-decoration: underline; }
table {
    width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    font-size: 9pt;
    margin: 1em 0;
}
thead { display: table-header-group; }
tr { break-inside: avoid; }
th, td {
    border: 1px solid #ccc;
    padding: 6pt;
    text-align: left;
    vertical-align: top;
}
th { background: #eee; }
pre {
    white-space: pre-wrap;
    overflow-wrap: break-word;
    background: #f4f4f4;
    padding: 8pt;
}
code { font-family: monospace; font-size: 0.9em; }
blockquote {
    border-left: 3px solid #ccc;
    margin-left: 0;
    padding-left: 12pt;
    color: #444;
}
"""

def build_report_document(markdown: str, *, title: str = "Oceanus report") -> str:
    if not isinstance(title, str):
        raise ValueError("Report title must be text!")
    if not title.strip() or len(title) > MAX_REPORT_TITLE_CHARS:
        raise ValueError("Report title must contain 1 to 200 characters!")
    if "\x00" in title:
        raise ValueError("Report title must not contain NUL characters!")

    body_html = render_markdown_html(markdown)
    escaped_title = escape(title)

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{escaped_title}</title>
<style>{REPORT_CSS}</style>
</head>
<body>
<main>
{body_html}
</main>
</body>
</html>
"""
    return document
