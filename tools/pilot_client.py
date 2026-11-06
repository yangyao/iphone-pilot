"""Fixed local API client shared by CLI and MCP. Never retries mutations."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class PilotError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class PilotAPI:
    def __init__(self, base='http://127.0.0.1:18766', timeout=150):
        parsed = urlsplit(base)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError('API must be a loopback HTTP origin')
        self.base, self.timeout = base.rstrip('/'), timeout
        self.opener = build_opener(ProxyHandler({}))

    def request(self, route, payload=None, query=None):
        url = self.base + route
        if query:
            url += '?' + urlencode(query)
        request = Request(url, data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as error:
            message = f'Local API returned HTTP {error.code}'
            try:
                message = json.load(error).get('error', message)
            except (ValueError, AttributeError):
                pass
            finally:
                error.close()
            raise PilotError(message, error.code) from None
        except (URLError, OSError, ValueError):
            raise PilotError('Local API unavailable or response unreadable; inspect state before retrying with SAME request_id') from None

    def observe(self, observation_id=None, include_image=False):
        query = {'include_image': str(include_image).lower()}
        if observation_id:
            query['observation_id'] = observation_id
        return self.request('/observe', query=query)

    def act(self, **payload):
        return self.request('/act', payload)

    def insert(self, text, request_id, allow_newlines=False):
        return self.request('/input', dict(text=text, request_id=request_id, allow_newlines=allow_newlines))


def compact(value):
    """Keep state/safety fields; remove duplicate pixel boxes and long boilerplate."""
    if isinstance(value, list):
        return [compact(item) for item in value]
    if not isinstance(value, dict):
        return value
    if 'text' in value and 'center' in value:
        result = {key: value[key] for key in ('id', 'text', 'confidence', 'clickable') if key in value}
        result['center'] = [round(number, 1) for number in value['center']]
        return result
    return {key: compact(item) for key, item in value.items()
            if key not in ('image_sha256', 'note', 'box')}
