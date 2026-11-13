from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin


from agent_core.report_limits import MAX_MARKDOWN_CHARS


def render_markdown_html(markdown: str) -> str:
    # Data validation
    if not isinstance(markdown, str):
        raise ValueError("Report Markdown must be text!")
    if len(markdown) > MAX_MARKDOWN_CHARS:
        raise ValueError("Report Markdown exceeds the character limit!")
    if not markdown.strip():
        raise ValueError("Report Markdown must not be blank!")
    if "\x00" in markdown:
        raise ValueError("Report Markdown must not contain NUL characters!")

    parser = MarkdownIt("commonmark", {"html": False, "linkify": True})
    parser.enable("table")
    parser.enable("linkify")
    parser.disable("image")

    parser.linkify.set({"fuzzy_link": False, "fuzzy_email": False})
    parser.use(anchors_plugin, min_level=1, max_level=6)

    return parser.render(markdown)
