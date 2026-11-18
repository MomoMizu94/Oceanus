""" Bounded HTTP for local image services without proxies or redirects """
import http.client
import json
import time

from agent_core.local_image_config import local_endpoint


def check_image_job(deadline, should_cancel=None):
    if should_cancel is not None and should_cancel():
        raise InterruptedError('Local image generation cancelled.')
    if time.monotonic() >= deadline:
        raise TimeoutError('Local image generation timed out.')


class LocalImageClient:
    def __init__(self, endpoint, deadline, should_cancel=None):
        self.host, self.port = local_endpoint(endpoint)
        self.deadline = deadline
        self.should_cancel = should_cancel

    def request(self, method, path, payload=None, *, max_bytes=1_048_576):
        check_image_job(self.deadline, self.should_cancel)
        if not path.startswith('/') or path.startswith('//') or '\r' in path or '\n' in path:
            raise ValueError('Invalid local image service request path.')
        body = None if payload is None else json.dumps(payload).encode('utf-8')
        timeout = min(5.0, self.deadline - time.monotonic())
        connection = http.client.HTTPConnection(self.host, self.port, timeout=max(0.01, timeout))
        try:
            connection.request(method, path, body=body, headers={
                'Content-Type': 'application/json', 'Accept-Encoding': 'identity',
            })
            with connection.getresponse() as response:
                if response.status != 200:
                    raise OSError(f'Local image service returned HTTP {response.status} for {path.split("?")[0]}.')
                if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                    raise ValueError('Compressed local image responses are unsupported.')
                expected = response.length
                if expected is not None and expected > max_bytes:
                    raise ValueError('Local image response exceeds its size limit.')
                chunks = []
                size = 0
                while True:
                    check_image_job(self.deadline, self.should_cancel)
                    if connection.sock is not None:
                        connection.sock.settimeout(max(0.01, min(5.0, self.deadline - time.monotonic())))
                    chunk = response.read1(min(65536, max_bytes + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError('Local image response exceeds its size limit.')
                if expected is not None and size != expected:
                    raise OSError('Incomplete local image service response.')
                check_image_job(self.deadline, self.should_cancel)
                return b''.join(chunks)
        except http.client.HTTPException as error:
            raise OSError('Invalid local image service response.') from error
        finally:
            connection.close()

    def json(self, method, path, payload=None):
        raw = self.request(method, path, payload)
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError) as error:
            raise ValueError('Invalid JSON from local image service.') from error
        if not isinstance(value, dict):
            raise ValueError('Expected an object from local image service.')
        return value
