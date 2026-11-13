""" Validate and serialize the supported JSON Canvas 1.0 format """
import json
import re
from urllib.parse import urlsplit


MAX_CANVAS_SOURCE_CHARS = 200_000
MAX_CANVAS_BYTES = 1_000_000
MAX_CANVAS_NODES = 200
MAX_CANVAS_EDGES = 500
MAX_CANVAS_TEXT_CHARS = 16_000

NODE_FIELDS = {
    "text": {"text"},
    "file": {"file", "subpath"},
    "link": {"url"},
    "group": {"label", "background", "backgroundStyle"},
}
COMMON_NODE_FIELDS = {"id", "type", "x", "y", "width", "height", "color"}
EDGE_FIELDS = {"id", "fromNode", "toNode", "fromSide", "toSide", "fromEnd", "toEnd", "color", "label"}


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Canvas JSON contains a duplicate object key.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Canvas JSON must not contain NaN or Infinity.")


def _text(value, label: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Canvas {label} must be text of at most {limit} characters.")
    if not allow_empty and not value.strip():
        raise ValueError(f"Canvas {label} must not be blank.")
    for character in value:
        code = ord(character)
        unsupported_control = (code < 32 and character not in "\t\n\r") or 127 <= code <= 159
        unpaired_surrogate = 0xD800 <= code <= 0xDFFF
        if unsupported_control or unpaired_surrogate:
            raise ValueError(f"Canvas {label} contains unsupported characters.")
    return value


def _choice(item: dict, field: str, choices: tuple) -> None:
    if field in item and item[field] not in choices:
        raise ValueError(f"Invalid Canvas {field}; expected one of {choices}.")


def _color(item: dict) -> None:
    if "color" in item:
        color = _text(item["color"], "color", 7)
        if not re.fullmatch(r"#[0-9a-fA-F]{6}|[1-6]", color):
            raise ValueError("Canvas color must be a six-digit hex color or a preset from 1 to 6.")


def _file_reference(value) -> None:
    path = _text(value, "file reference", 2048)
    # Obsidian resolves these from the vault root, not from the Canvas directory
    if any(character in path for character in "\\:#\t\n\r"):
        raise ValueError("Canvas file references must be vault-relative paths using forward slashes.")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise ValueError("Canvas file references must not be absolute or traverse directories.")


def _validate_node(node: dict, identifiers: set[str]) -> None:
    if not isinstance(node, dict):
        raise ValueError("Each Canvas node must be an object.")
    kind = node.get("type")
    if not isinstance(kind, str) or kind not in NODE_FIELDS:
        raise ValueError("Canvas node type must be text, file, link, or group.")
    if set(node) - COMMON_NODE_FIELDS - NODE_FIELDS[kind]:
        raise ValueError("Canvas node contains unsupported fields.")
    identifier = _text(node.get("id"), "node id", 128)
    if identifier in identifiers:
        raise ValueError("Canvas node and edge ids must be unique.")
    identifiers.add(identifier)
    for field in ("x", "y", "width", "height"):
        value = node.get(field)
        minimum, maximum = (-1_000_000, 1_000_000) if field in ("x", "y") else (1, 100_000)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"Canvas {field} must be an integer between {minimum} and {maximum}.")
    _color(node)
    if kind == "text":
        _text(node.get("text"), "node text", MAX_CANVAS_TEXT_CHARS, allow_empty=True)
    elif kind == "file":
        _file_reference(node.get("file"))
        if "subpath" in node:
            subpath = _text(node["subpath"], "subpath", 2048)
            if not subpath.startswith("#"):
                raise ValueError("Canvas file subpath must start with #.")
    elif kind == "link":
        url = _text(node.get("url"), "URL", 2048)
        if any(character.isspace() for character in url):
            raise ValueError("Canvas URLs must not contain whitespace.")
        parts = urlsplit(url)
        has_credentials = parts.username is not None or parts.password is not None
        if parts.scheme not in ("http", "https") or not parts.hostname or has_credentials:
            raise ValueError("Canvas links must be HTTP(S) URLs without credentials.")
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError("Canvas URL port is invalid.")
    else:
        if "label" in node:
            _text(node["label"], "group label", 1000, allow_empty=True)
        if "background" in node:
            _file_reference(node["background"])
        _choice(node, "backgroundStyle", ("cover", "ratio", "repeat"))


def _validate_edge(edge: dict, node_ids: set[str], identifiers: set[str]) -> None:
    if not isinstance(edge, dict) or set(edge) - EDGE_FIELDS:
        raise ValueError("Each Canvas edge must be an object with supported fields.")
    identifier = _text(edge.get("id"), "edge id", 128)
    if identifier in identifiers:
        raise ValueError("Canvas node and edge ids must be unique.")
    identifiers.add(identifier)
    for field in ("fromNode", "toNode"):
        endpoint = _text(edge.get(field), field, 128)
        if endpoint not in node_ids:
            raise ValueError(f"Canvas {field} refers to a missing node.")
    for field in ("fromSide", "toSide"):
        _choice(edge, field, ("top", "right", "bottom", "left"))
    for field in ("fromEnd", "toEnd"):
        _choice(edge, field, ("none", "arrow"))
    if "label" in edge:
        _text(edge["label"], "edge label", 1000, allow_empty=True)
    _color(edge)


def build_canvas_document(source: str) -> tuple[bytes, dict]:
    """ Return validated UTF-8 JSON and counts without opening any references """
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_CANVAS_SOURCE_CHARS:
        raise ValueError("Canvas source must be nonblank JSON within the 200,000-character limit.")
    try:
        document = json.loads(source, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except RecursionError as error:
        raise ValueError("Canvas JSON is nested too deeply.") from error
    if not isinstance(document, dict) or set(document) - {"nodes", "edges"}:
        raise ValueError("Canvas root must be an object containing only nodes and edges.")
    nodes = document.get("nodes", [])
    edges = document.get("edges", [])
    if not isinstance(nodes, list) or len(nodes) > MAX_CANVAS_NODES:
        raise ValueError("Canvas must contain at most 200 nodes.")
    if not isinstance(edges, list) or len(edges) > MAX_CANVAS_EDGES:
        raise ValueError("Canvas must contain at most 500 edges.")
    identifiers = set()
    for node in nodes:
        _validate_node(node, identifiers)
    node_ids = identifiers.copy()
    for edge in edges:
        _validate_edge(edge, node_ids, identifiers)
    # Preserve node order because it determines which cards appear on top
    normalized = {"nodes": nodes, "edges": edges}
    formatted = json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False)
    encoded = (formatted + "\n").encode("utf-8")
    if len(encoded) > MAX_CANVAS_BYTES:
        raise ValueError("Canvas output exceeds the 1,000,000-byte limit.")
    return encoded, {"node_count": len(nodes), "edge_count": len(edges)}
