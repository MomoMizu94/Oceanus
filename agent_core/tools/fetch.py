from urllib.parse import SplitResult, urlsplit
import ipaddress
import socket
import ssl
import http.client
from html.parser import HTMLParser
import json
from collections.abc import Callable


MAX_FETCH_URL_CHARS = 2048
# Limit IPv6 fetching to the global unicast address range
FETCH_IPV6_PUBLIC_RANGE = ipaddress.ip_network("2000::/3")
FETCH_LOCAL_HOST_SUFFIXES = ("localhost", "local", "home.arpa")
MAX_FETCH_DNS_RESULTS = 64
FETCH_SOCKET_TIMEOUT = 15
MAX_FETCH_RESPONSE_BYTES = 2_097_152
# Map supported HTTP charset names to Python's text decoders.
# UTF-8-SIG removes optinal UTF-8 marker at the start of the body
FETCH_TEXT_ENCODINGS = {
    "utf-8": "utf-8-sig",
    "utf8": "utf-8-sig",
    "utf-8-sig": "utf-8-sig",
    "ascii": "ascii",
    "us-ascii": "ascii",
    "iso-8859-1": "iso-8859-1",
    "iso8859-1": "iso-8859-1",
    "latin-1": "iso-8859-1",
    "latin1": "iso-8859-1",
    "windows-1252": "cp1252",
    "cp1252": "cp1252",
}
MAX_FETCH_TEXT_CHARS = 16_000
FETCH_IGNORED_TAGS = {"head", "script", "style", "template"}
FETCH_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "main",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "pre", "blockquote", "br", "hr", "table", "tr", "td", "th",
}


def parse_fetch_url(url: str) -> SplitResult:
    """ Check the URL's structure """

    # Check the OG text first
    if not isinstance(url, str):
        raise ValueError("Page URL must be a string!")
    if not url or len(url) > MAX_FETCH_URL_CHARS:
        raise ValueError("Page URL must contain 1 to 2048 characters!")
    if not url.isascii() or not url.isprintable() or " " in url:
        raise ValueError("Page URL must use ASCII text without spaces or controls!")
    if "\\" in url:
        raise ValueError("Page URL must not contain backslashes!")

    # Split the address into parts; keep errors short
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ValueError("Page URL has an invalid host or port!") from None

    # Only ordinary web requests are supported. Other schemes could refer to local files
    # which this tool is not meant to access.
    if parts.scheme not in ("http", "https"):
        raise ValueError("Page URL must use HTTP or HTTPS!")
    if not hostname:
        raise ValueError("Page URL must include a hostname!")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Page URL must not include login credentials!")

    # Encoded hostnames and IPv6 zone identifiers need different interpretation rules
    # Reject them for now
    if "%" in hostname:
        raise ValueError("Page hostname must no contain percent escapes!")

    # Limit to standard port for each web protocol
    expected_port = 443
    if parts.scheme == "http":
        expected_port = 80
    if port is not None and port != expected_port:
        raise ValueError("Page URL must use its protocol's standard port!")

    # IPv6 addresses use brackets. Compare the whole host-and-port section
    # to reject leftover text, empty ports and unusual port spelling
    authority = hostname
    if ":" in hostname:
        authority = f"[{hostname}]"
    allowed_authorities = (authority, f"{authority}:{expected_port}")
    if parts.netloc.lower() not in allowed_authorities:
        raise ValueError("Page URL has an unsupported host or port spelling!")

    return parts

def validate_public_ip(value: str) -> str:
    """ Return a normalized public IP address or reject it """

    # Require only text
    if not isinstance(value, str):
        raise ValueError("Page IP address must be a string!")
    if "%" in value:
        raise ValueError("Page IP address must not include a network interface!")

    # Parse numeric address only
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("Page destination must be a valid IP address!") from None

    # Reject private, loopback, link-local and other non-public destinations.
    # Multicast sends to a group of machines, so exclude it also.
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise PermissionError("Page destination must be a public unicast address!")

    if isinstance(address, ipaddress.IPv6Address):
        # Reject IPv4-mapped IPv6 and common translation prefixes
        if address not in FETCH_IPV6_PUBLIC_RANGE:
            raise PermissionError("Page destination uses an unsupported IPv6 range!")

        # Reject IPv6 mechanisms that carry traffic to an embedded IPv4 address
        if address.sixtofour is not None or address.teredo is not None:
            raise PermissionError("Page destination must not use an IPv6 tunnel.")

    # Return a consistent str representation of the address.
    # Connect alter code to this checked address!
    return str(address)

def validate_fetch_host(hostname: str) -> str:
    """ Checks a hostname or numeric IP without performing a DNS check """

    if not isinstance(hostname, str) or not hostname:
        raise ValueError("Page hostname must not be empty text!")
    if not hostname.isascii():
        raise ValueError("Page hostname must use its ASCII spelling!")

    # DNS checks ignore letter case. Final dot marks a complete DNS name.
    # Remove one here so the same checks apply with or without the dot.
    host = hostname.lower()
    if host.endswith("."):
        host = host[:-1]
    if not host or len(host) > 253:
        raise ValueError("Page hostname must contain 1 to 253 characters!")

    # Numeric address needs the public-IP check, not a DNS lookup
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return validate_public_ip(host)

    # Check suffixes
    for suffix in FETCH_LOCAL_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            raise PermissionError("Page hostname must not name a local service!")

    # Require a dotted name
    labels = host.split(".")
    if len(labels) < 2:
        raise ValueError("Page hostname must be a full dotted name!")

    # Each dot-separated part must use ordianry DNS hostname chars
    for label in labels:
        if not label or len(label) > 63:
            raise ValueError("Each hostname label must contain 1 to 63 characters!")
        if label.startswith("-") or label.endswith("-"):
            raise ValueError("Hostname labels must not start or end with a hyphen!")
        for character in label:
            if not character.isalnum() and character != "-":
                raise ValueError("Hostname labels must use letters, digits or hyphens!")

    # Require alphanumeric start for the final label
    # Rejects malformed IPv4 and numeric shorthand passed off as DNS names
    if not labels[-1][0].isalpha():
        raise ValueError("The final hostname label must begin with a letter!")

    # Only checks spelling. DNS results must still pass validate_public_ip
    return host

def _resolve_fetch_addresses(hostname: str, port: int) -> list[str]:
    """ Resolve and check a destination. Caller must obtain approval first """

    # Recheck inputs
    host = validate_fetch_host(hostname)
    if type(port) is not int or port not in (80, 443):
        raise ValueError("Page destination must use port 80 or 443")

    # Numeric addresses already passed the check in validate_fetch_host
    # Return them directly
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return [str(address)]

    # The final dot tells the resolver this is a final name.
    # Don't append a local suffix to the supplied hostname.
    lookup_name = host + "."
    try:
        records = socket.getaddrinfo(
            lookup_name,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP
        )
    except OSError:
        # Don't echo resolver deets
        raise OSError("Couldn't resolve the page hostname!") from None

    # Reject oversized lists
    if not records:
        raise OSError("The page hostname returned no addresses!")
    if len(records) > MAX_FETCH_DNS_RESULTS:
        raise ValueError("The page hostname retuned too many addresses!")

    addresses = []
    for family, socket_type, protocol, canonical_name, destination in records:
        # Reject unsupported families, socket_types and destinations
        if family not in(socket.AF_INET, socket.AF_INET6):
            raise ValueError("The resolver returned an unsupported address family!")
        if socket_type != socket.SOCK_STREAM or protocol != socket.IPPROTO_TCP:
            raise ValueError("The resolver returned unsupported connection type!")
        if destination[1] != port:
            raise ValueError("The resolver returned an unexpected port!")

        # IPv6 results contain flow and interface fields -> not supported
        if family == socket.AF_INET6:
            if destination[2] != 0 or destination[3] != 0:
                raise PermissionError("The resolver returned a scoped IPv6 address!")

        checked = validate_public_ip(destination[0])
        address = ipaddress.ip_address(checked)
        expected_family = socket.AF_INET
        if address.version == 6:
            expected_family = socket.AF_INET6
        if family != expected_family:
            raise ValueError("The resolver returned a mismatched address family!")

        # Preserve the resolver's order; avoid duplicate connection attempts
        if checked not in addresses:
            addresses.append(checked)

    # Return after every result passes. Pub address followed by private address rejects the entire lookup
    return addresses

def _open_fetch_socket(hostname: str, address: str, scheme: str) -> socket.socket:
    """ Connect to a checked IP, caller must obtain approval first """

    # Input validation
    # Caller must obtain this address from checked result of _resolve_fetch_addresses
    host = validate_fetch_host(hostname)
    checked = validate_public_ip(address)
    if scheme not in ("http", "https"):
        raise ValueError("Page connection must use HTTP or HTTPS!")

    try:
        host_address = ipaddress.ip_address(host)
    except ValueError:
        host_address = None
    if host_address is not None and str(host_address) != checked:
        raise ValueError("Page connection does not match the requested IP!")

    port = 80
    if scheme == "https":
        port = 443

    # Choose socket family from the checked address
    ip = ipaddress.ip_address(checked)
    family = socket.AF_INET
    destination = (checked, port)
    if ip.version == 6:
        family = socket.AF_INET6
        destination = (checked, port, 0, 0)

    context = None
    if scheme == "https":
        # TLS_CLIENT requires a trusted certificate and checks its hostname.
        # Load the system's trusted certificate authorities
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        context.set_alpn_protocols(["http/1.1"])

    connection = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    try:
        # Bounds individual socket operations, not the entire page fetch
        connection.settimeout(FETCH_SOCKET_TIMEOUT)
        connection.connect(destination)

        if context is not None:
            # Connect to the checked IP, but verify the certificate against the OG hostname
            # TLS also uses this name to select the website
            return context.wrap_socket(connection, server_hostname=host)

        # HTTP uses the connected socket without TLS wrapper
        return connection
    except BaseException:
        # Release the socket on connection errors, TLS failures or interruption.
        # Re-raise the same error so the caller knows the operation failed
        connection.close()
        raise

def _request_fetch_page(url: str) -> dict:
    """ Download one page. Approval required """

    parts = parse_fetch_url(url)
    host = validate_fetch_host(parts.hostname)
    port = 80
    if parts.scheme == "https":
        port = 443

    # Send the page path and query
    # Keep an explicit empty query too. Some servers distinguish it
    target = parts.path or "/"
    if target.startswith("//"):
        raise ValueError("Page paths starting with // are not supported!")
    before_fragment = url.split("#", 1)[0]
    if "?" in before_fragment:
        target += "?" + parts.query

    # IPv6 needs brackets in the host header
    authority = host
    if ":" in host:
        authority = f"[{host}]"
    headers = {
        "Host": authority,
        "User-Agent": "Oceanus/0.1",
        "Accept": "text/html, text/plain",
        "Accept-Encoding": "identity",
        "Connection": "close"
    }

    # Make the HTTP client use existing checked connection and prevent automatic new connection openings
    connection = http.client.HTTPConnection(host, port, timeout=FETCH_SOCKET_TIMEOUT)
    connection.auto_open = 0
    try:
        addresses = _resolve_fetch_addresses(host, port)
        connection.sock = _open_fetch_socket(host, addresses[0], parts.scheme)
        connection.request("GET", target, headers=headers)

        with connection.getresponse() as response:
            # Don't follow redirects
            if 300 <= response.status < 400:
                raise ValueError("Page redirects are not supported!")
            if response.status != 200:
                raise ValueError(f"Page server returned HTTP {response.status}!")

            # Limit to uncompressed HTML or plaintext
            encoding = response.getheader("Content-Encoding", "identity")
            if encoding.strip().lower() != "identity":
                raise ValueError("Page response uses unsupported content encoding!")
            transfer = response.getheader("Transfer-Encoding", "")
            if transfer.strip().lower() not in ("", "chunked"):
                raise ValueError("Page response uses an unsupported transfer encoding!")
            if not response.getheader("Content-Type"):
                raise ValueError("Page response is missing its content type!")
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "text/plain"):
                raise ValueError("Page response must be HTML or plain text!")
            charset = response.headers.get_content_charset() or "utf-8"

            # Reject an oversized declared body before reading, but also cap the read
            # for chunked responses and responses with no declared length.
            expected_length = response.length
            if expected_length is not None and expected_length > MAX_FETCH_RESPONSE_BYTES:
                raise ValueError("Page response exceeds the size limit!")
            body = response.read(MAX_FETCH_RESPONSE_BYTES + 1)
            if len(body) > MAX_FETCH_RESPONSE_BYTES:
                raise ValueError("Page response exceeds the size limit!")
            if expected_length is not None and len(body) != expected_length:
                raise ValueError("Page response ended before its declared length!")

            # Bytes stay inside the application
            return {
                "url": url,
                "content_type": content_type,
                "charset": charset,
                "body": body
            }
    except PermissionError:
        # Destination-policy denial -> permission error
        raise
    except TimeoutError:
        raise TimeoutError("Page request timed out!") from None
    except (OSError, http.client.HTTPException):
        raise OSError("Couldn't complete the page request!") from None
    finally:
        # Close on success, rejected responses and failures
        connection.close()

def _decode_fetch_body(body: bytes, charset: str) -> str:
    """ Decode a bounded response body """

    # Body size check
    if not isinstance(body, bytes):
        raise ValueError("Page body must be bytes!")
    if len(body) > MAX_FETCH_RESPONSE_BYTES:
        raise ValueError("Page body exceeds the size limit!")

    # Only accept text encodings listed at the top of this file
    if not isinstance(charset, str) or not charset or len(charset) > 64:
        raise ValueError("Page charset must contain 1 to 64 characters!")
    if not charset.isascii() or not charset.isprintable():
        raise ValueError("Page charset must use printable ASCII characters!")
    name = charset.strip()
    name = name.lower()
    name = name.replace("_", "-")
    encoding = FETCH_TEXT_ENCODINGS.get(name)
    if encoding is None:
        raise ValueError("Page charset is not supported!")

    # Invalid bytes are an error
    try:
        text = body.decode(encoding, errors="strict")
    except UnicodeDecodeError:
        raise ValueError("Page body doesn't match its declared dataset!") from None

    # Preserve normal line breaks and tabs, reject other low-level controls
    for character in text:
        if character in ("\t", "\n", "\r"):
            continue
        number = ord(character)
        if number < 32 or 127 <= number <= 159:
            raise ValueError("Page text contains unsupported control characters!")

    return text


class _PageTextParser(HTMLParser):
    """ Collect page text """

    def __init__(self) -> None:
        # Convert entities like &amp; into ordinary text
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.length = 0
        self.skipped_tag = None
        self.skipped_depth = 0

    def _append(self, text: str) -> None:
        # Check text again after HTML entity conversion
        for character in text:
            if character in ("\t", "\n", "\r"):
                continue
            number = ord(character)
            if number < 32 or 127 <= number <= 159:
                raise ValueError("Page text contains unsupported control characters!")

        # Keep one etra character to detect truncation
        # Ignore further text once this buffer is full
        remaining = MAX_FETCH_TEXT_CHARS + 1 - self.length
        if remaining > 0:
            kept = text[:remaining]
            if kept:
                self.parts.append(kept)
                self.length += len(kept)

    def _line_break(self) -> None:
        # Separate paragraphs without adding a leading or duplicate newline
        if self.parts and not self.parts[-1].endswith("\n"):
            self._append("\n")

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self.skipped_tag is not None:
            # Nested templates must not end the skipped section too early
            if tag == self.skipped_tag:
                self.skipped_depth += 1
            return

        if tag in FETCH_IGNORED_TAGS:
            self.skipped_tag = tag
            self.skipped_depth = 1
            return

        if tag in FETCH_BLOCK_TAGS:
            self._line_break()

    def handle_endtag(self, tag: str) -> None:
        if self.skipped_tag is not None:
            if tag == self.skipped_tag:
                self.skipped_depth -= 1
                if self.skipped_depth == 0:
                    self.skipped_tag = None
            return

        if tag in FETCH_BLOCK_TAGS:
            self._line_break()

    def handle_data(self, data: str) -> None:
        if self.skipped_tag is None:
            # Preserve spacing, including indentation inside code examples
            self._append(data)


def _extract_fetch_text(body: bytes, charset: str, content_type: str) -> dict:
    """ Decode a page and return bounded text with a truncation flag """

    if content_type not in ("text/html", "text/plain"):
        raise ValueError("Page content must be HTML or plain text!")
    text = _decode_fetch_body(body, charset)

    if content_type == "text/html":
        parser = _PageTextParser()
        parser.feed(text)
        parser.close()
        text = "".join(parser.parts)

    # Plain text bypasses HTML parsing: literal tags and entities stay unchanged.
    # Count chars, including spacing and line breaks
    return {
        "content": text[:MAX_FETCH_TEXT_CHARS],
        "truncated": len(text) > MAX_FETCH_TEXT_CHARS,
    }

def fetch_page(
        url: str,
        *,
        on_fetch_approval: Callable[[str], bool] | None = None,
        should_cancel: Callable[[], bool] | None = None
) -> str:
    """ Fetch one page after approval of its exact url """

    # Check the address locally
    parts = parse_fetch_url(url)
    validate_fetch_host(parts.hostname)
    if parts.path.startswith("//"):
        raise ValueError("Page URL path must not start with //!")

    def check_cancel() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Page request cancelled!")

    check_cancel()
    if on_fetch_approval is None:
        raise PermissionError("Page fetch requires approval!")

    # Only approved operations gets passed onto the worker
    decision = on_fetch_approval(url)
    check_cancel()
    if decision is not True:
        raise PermissionError("Page fetch denied! No request was sent!")

    # Delay this import because the runtime also imports fetch helpers
    from agent_core.fetch_runtime import _collect_fetch_reply, _parse_fetch_reply

    raw = _collect_fetch_reply(url, should_cancel=should_cancel)
    result = _parse_fetch_reply(raw, url)
    check_cancel()

    # Match the JSON string format used by other tools
    return json.dumps(result, ensure_ascii=False)