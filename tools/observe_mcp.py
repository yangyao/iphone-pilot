#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0"]
# ///
"""stdio MCP adapter for the persistent local observation API."""
import json
import os
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

mcp = FastMCP('iPhone Pilot')
API = os.environ.get('IPHONE_OBSERVE_URL', 'http://127.0.0.1:18766')
urlopen = build_opener(ProxyHandler({})).open
if urlsplit(API).hostname not in ('127.0.0.1', 'localhost', '::1'):
    raise ValueError('IPHONE_OBSERVE_URL must be loopback')


def api_json(request, timeout):
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        message = f'Local API returned HTTP {error.code}'
        try:
            message = json.load(error).get('error', message)
        except (ValueError, AttributeError):
            pass
        finally:
            error.close()
        raise ValueError(message) from None


@mcp.tool()
def iphone_observe(include_image: bool = False, observation_id: str | None = None) -> list[TextContent | ImageContent]:
    """Read latest iPhone text, pixel centers and visible AirType address/pairing code.

    airtype.pairing_code is null if unreadable, ambiguous or stale; no token is exposed.
    Set include_image for ambiguous icons.

    Supply a previous observation_id to inspect that SAME frame with an image.
    IDs expire from a bounded cache. stale=true means do not act on coordinates.
    Text boxes are not proof of tappability; verify new state after navigation.
    """
    query = {'include_image': str(include_image).lower()}
    if observation_id:
        query['observation_id'] = observation_id
    result = api_json(API.rstrip('/') + '/observe?' + urlencode(query), 40)
    picture = result.pop('image', None)
    content = [TextContent(type='text', text=json.dumps(result, ensure_ascii=False, separators=(',', ':')))]
    if picture:
        content.append(ImageContent(type='image', data=picture['data'], mimeType=picture['mime_type']))
    return content


@mcp.tool()
def iphone_type(text: str, request_id: str, allow_newlines: bool = False) -> dict:
    """Insert whole Unicode text through the currently focused AirType keyboard.

    Focus the intended field and show AirType Web Access first. The API reads the
    current address/code with OCR, reuses or pairs a session, inserts ONCE and checks
    OCR for new text. Does not click Send/Publish. request_id must be unique per
    intended insertion; reuse the SAME id and text on timeout/retry to avoid replay.
    accepted=true is an HTTP acknowledgment; verification may be not_confirmed.
    Only allow newlines for a confirmed multiline field. Never retry uncertain
    insertion with a new request_id without inspecting the phone first.
    """
    request = Request(API.rstrip('/') + '/input',
                      data=json.dumps(dict(text=text, request_id=request_id,
                                           allow_newlines=allow_newlines), ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'})
    return api_json(request, 150)


if __name__ == '__main__':
    mcp.run(transport='stdio')
