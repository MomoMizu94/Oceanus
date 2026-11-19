"""Render a public page in an offline Chromium process fed by the Oceanus fetcher."""

from pathlib import Path
from urllib.parse import urldefrag, urljoin

from agent_core.shell_sandbox import ShellSandboxError, find_bubblewrap
from agent_core.tools.fetch import (
    MAX_FETCH_TEXT_CHARS, ResourceRedirect, _request_fetch_document, parse_fetch_url, validate_fetch_host,
)

MAX_REQUESTS = 96
MAX_RESOURCE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
DATA_TYPES = ('application/json', 'application/geo+json', 'text/plain')
RESOURCE_TYPES = {
    'document': ('text/html',),
    'script': ('application/javascript', 'text/javascript', 'application/x-javascript'),
    'stylesheet': ('text/css',),
    'fetch': DATA_TYPES,
    'xhr': DATA_TYPES,
}


def origin(url):
    parts = parse_fetch_url(url)
    return parts.scheme, validate_fetch_host(parts.hostname)


def document_key(url):
    parts = parse_fetch_url(url)
    return origin(url), parts.path or '/', parts.query, parts.fragment


def browser_args(executable):
    binary = Path(executable)
    if not binary.is_file():
        raise FileNotFoundError('Missing Chromium')
    args = ['--unshare-all', '--die-with-parent', '--clearenv', '--setenv', 'HOME', '/tmp',
            '--setenv', 'PATH', '/usr/bin', '--ro-bind', '/usr', '/usr']
    for path in ('/bin', '/lib', '/lib64', '/etc/fonts', '/etc/ld.so.cache'):
        if Path(path).exists():
            args.extend(['--ro-bind', path, path])
    args.extend(['--ro-bind', str(binary.parent), '/browser', '--dev', '/dev', '--proc', '/proc',
                 '--size', str(128 * 1024 * 1024), '--tmpfs', '/tmp', '--chdir', '/tmp',
                 '--', '/browser/' + binary.name, '--headless', '--remote-debugging-pipe',
                 '--disable-dev-shm-usage', '--disable-gpu', '--no-first-run',
                 '--disable-background-networking', '--disable-extensions', '--disable-breakpad',
                 '--user-data-dir=/tmp/profile', '--renderer-process-limit=2', 'about:blank'])
    return args


class RenderResources:
    def __init__(self, url, page):
        self.url = urldefrag(url).url
        self.origin = origin(url)
        self.page = page
        self.requests = 0
        self.remaining = MAX_TOTAL_BYTES
        self.document_loaded = False
        self.limit_reached = False
        self.document_error = None

    def handle(self, route):
        request = route.request
        kind = request.resource_type
        target = urldefrag(request.url).url
        if request.method != 'GET' or kind not in RESOURCE_TYPES:
            return route.abort()
        if kind == 'document' and (self.document_loaded or document_key(target) != document_key(self.url)):
            return route.abort()
        try:
            if origin(target) != self.origin or request.frame != self.page.main_frame:
                return route.abort()
            resource = self.download(target, kind)
        except (OSError, ValueError) as error:
            if kind == 'document':
                self.document_error = error
            return route.abort()
        if kind == 'document':
            self.document_loaded = True
        route.fulfill(status=200, headers=resource['headers'], body=resource['body'])

    def download(self, target, kind):
        for hop in range(4):
            if origin(target) != self.origin:
                raise PermissionError('Cross-origin resource')
            if self.requests >= MAX_REQUESTS or self.remaining <= 0:
                self.limit_reached = True
                raise ValueError('Page resource budget exhausted')
            self.requests += 1
            allowance = min(MAX_RESOURCE_BYTES, self.remaining)
            # Reserve the allowance until the received body size is known
            self.remaining -= allowance
            try:
                resource = _request_fetch_document(
                    target, content_types=RESOURCE_TYPES[kind], max_bytes=allowance,
                    browser_headers=True, data_redirect=kind in ('xhr', 'fetch'),
                )
            except ResourceRedirect as redirect:
                self.remaining += allowance
                target = urljoin(target, str(redirect))
                continue
            self.remaining += allowance - len(resource['body'])
            return resource
        raise ValueError('Too many data redirects')


def render_visible_text(url):
    from playwright.sync_api import Error, TimeoutError as BrowserTimeout, sync_playwright

    with sync_playwright() as driver:
        args = browser_args(driver.chromium.executable_path)
        try:
            launcher = find_bubblewrap()
        except ShellSandboxError as error:
            raise FileNotFoundError('Missing sandbox launcher') from error
        with driver.chromium.launch(executable_path=launcher, args=args,
                                    ignore_default_args=True, chromium_sandbox=True) as browser:
            context = browser.new_context(service_workers='block', accept_downloads=False)
            page = context.new_page()
            resources = RenderResources(url, page)
            context.route('**/*', resources.handle)
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=35_000)
                # Give client-side fetches and the following DOM updates time to finish
                page.wait_for_load_state('networkidle', timeout=12_000)
                page.wait_for_timeout(500)
                if resources.limit_reached:
                    raise ValueError('Page resource budget exhausted')
                if not resources.document_loaded or document_key(page.url) != document_key(url):
                    raise ValueError('Unexpected rendered document')
                # Read DOM text with browser built-ins isolated from page-defined JavaScript
                cdp = context.new_cdp_session(page)
                frame = cdp.send('Page.getFrameTree')['frameTree']['frame']
                world = cdp.send('Page.createIsolatedWorld', {'frameId': frame['id'], 'worldName': 'oceanus'})
                text = cdp.send('Runtime.evaluate', {
                    'contextId': world['executionContextId'], 'returnByValue': True,
                    'expression': f'document.body.innerText.slice(0, {2 * (MAX_FETCH_TEXT_CHARS + 1)})',
                })
                content = text['result'].get('value')
                if not isinstance(content, str):
                    raise ValueError('Page has no readable body')
            except BrowserTimeout as error:
                raise TimeoutError('Rendering timed out') from error
            except Error:
                if resources.document_error is not None:
                    raise resources.document_error
                raise
            return {'url': url, 'content_type': 'text/html', 'content': content[:MAX_FETCH_TEXT_CHARS],
                    'truncated': len(content) > MAX_FETCH_TEXT_CHARS}
