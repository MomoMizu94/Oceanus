from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from html import escape


from agent_core.report_limits import MAX_MARKDOWN_CHARS


def parse_report_markdown(markdown: str, *, images: bool = False):
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
    if not images:
        parser.disable("image")

    parser.linkify.set({"fuzzy_link": False, "fuzzy_email": False})
    parser.use(anchors_plugin, min_level=1, max_level=6)

    environment = {}
    tokens = parser.parse(markdown, environment)
    return parser, tokens, environment


def image_sources(markdown: str) -> list[str]:
    _, tokens, _ = parse_report_markdown(markdown, images=True)
    sources = []

    def visit(items):
        for token in items:
            if token.type == 'image':
                source = token.attrGet('src')
                if source not in sources:
                    sources.append(source)
            elif token.children:
                visit(token.children)

    visit(tokens)
    return sources


def render_markdown_html(markdown: str, *, image_urls: dict | None = None) -> str:
    parser, tokens, environment = parse_report_markdown(markdown, images=image_urls is not None)
    if image_urls is not None:
        def render_image(renderer, items, index, options, env):
            token = items[index]
            source = token.attrGet('src')
            if source not in image_urls:
                raise ValueError('Report image has no approved snapshot.')
            url = escape(image_urls[source], quote=True)
            alt = escape(token.content, quote=True)
            return f'<img src="{url}" alt="{alt}">'

        parser.add_render_rule('image', render_image)
    return parser.renderer.render(tokens, parser.options, environment)
