#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0"]
# ///
"""stdio MCP adapter for the persistent local observation API."""
import json
import os
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

mcp = FastMCP('iPhone Pilot')
API = os.environ.get('IPHONE_OBSERVE_URL', 'http://127.0.0.1:18766')
urlopen = build_opener(ProxyHandler({})).open
if urlsplit(API).hostname not in ('127.0.0.1', 'localhost', '::1'):
    raise ValueError('IPHONE_OBSERVE_URL must be loopback')


@mcp.tool()
def iphone_observe(include_image: bool = False, observation_id: str | None = None) -> list[TextContent | ImageContent]:
    """Read latest iPhone text and pixel centers. Set include_image for ambiguous icons.

    Supply a previous observation_id to inspect that SAME frame with an image.
    IDs expire from a bounded cache. stale=true means do not act on coordinates.
    Text boxes are not proof of tappability; verify new state after navigation.
    """
    query = {'include_image': str(include_image).lower()}
    if observation_id:
        query['observation_id'] = observation_id
    with urlopen(API.rstrip('/') + '/observe?' + urlencode(query), timeout=40) as response:
        result = json.load(response)
    picture = result.pop('image', None)
    content = [TextContent(type='text', text=json.dumps(result, ensure_ascii=False, separators=(',', ':')))]
    if picture:
        content.append(ImageContent(type='image', data=picture['data'], mimeType=picture['mime_type']))
    return content


if __name__ == '__main__':
    mcp.run(transport='stdio')
