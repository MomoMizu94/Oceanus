"""Browser links in Markdown replies and literal transcript entries."""

from urllib.parse import urlsplit

from linkify_it import LinkifyIt
from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from textual.content import Content, Span
from textual.style import Style
from textual.widgets import Static


def is_web_url(url: str) -> bool:
    if not url.lower().startswith(("http://", "https://")):
        return False
    if "\\" in url or any(char.isspace() or ord(char) < 32 for char in url):
        return False
    try:
        return bool(urlsplit(url).hostname)
    except ValueError:
        return False


def _prepare_transcript_tokens(state: StateCore) -> None:
    for token in state.tokens:
        # Textual otherwise guesses a language for unlabelled snippets
        if token.type in ("fence", "code_block") and not token.info.strip():
            token.info = "text"

        # Textual decodes link targets once on click, including reserved characters like %26
        for child in token.children or ():
            if child.type in ("link_open", "image"):
                attribute = "href" if child.type == "link_open" else "src"
                target = child.attrGet(attribute)
                if target and "%" in target:
                    child.attrSet(attribute, target.replace("%", "%25"))


def conversation_markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("gfm-like")
    parser.core.ruler.push("prepare_transcript_tokens", _prepare_transcript_tokens)
    return parser


class LiteralMessageBody(Static):
    def __init__(self, text: str) -> None:
        body = text
        if "http" in text.lower():
            plain = Content(text).plain
            matcher = LinkifyIt(options={"fuzzy_link": False, "fuzzy_email": False})
            spans = []
            # Backslashes in saved JSON can precede the closing quote after a URL
            for match in matcher.match(plain.replace("\\", " ")) or ():
                if is_web_url(match.raw):
                    action = ("visit", (match.raw,))
                    style = Style.from_meta({"@click": action})
                    spans.append(Span(match.index, match.last_index, style))
            if spans:
                body = Content(plain, spans)
        super().__init__(body, classes="message-body", markup=False)

    def action_visit(self, url: str) -> None:
        self.app.open_url(url)
