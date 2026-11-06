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


@mcp.tool()
def iphone_act(action: str, observation_id: str, request_id: str,
               target_id: int | None = None, x: float | None = None, y: float | None = None,
               end_x: float | None = None, end_y: float | None = None, duration_ms: int = 400,
               wait_for_text: str | None = None, timeout_ms: int = 5000,
               changes_only: bool = False, include_image: bool = False) -> list[TextContent | ImageContent]:
    """Tap or swipe, wait for a stable content change, and return new OCR in one call.

    Use the current observation_id. For tap supply target_id OR screenshot pixel x/y;
    for swipe supply x/y/end_x/end_y. target_id is an OCR text center, not proof of
    tappability. The runtime rejects changed screen layouts before sending.
    Optional wait_for_text must appear on a changed, stable post-command screen.
    accepted=true only acknowledges HID; wait.status=timeout is NOT success.
    Reuse SAME request_id and action on retry: commands are durably protected from
    replay, including after restart. Never use a new ID to blindly repeat a submit.
    changes_only omits full OCR items, leaving metadata plus added/removed items;
    include_image adds an actual MCP image block. No implicit extra clicks/retries.
    """
    payload = dict(action=action, observation_id=observation_id, request_id=request_id,
                   target_id=target_id, x=x, y=y, end_x=end_x, end_y=end_y,
                   duration_ms=duration_ms, wait_for_text=wait_for_text,
                   timeout_ms=timeout_ms, changes_only=changes_only, include_image=include_image)
    request = Request(API.rstrip('/') + '/act', data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
    result = api_json(request, 150)
    picture = (result.get('observation') or {}).pop('image', None)
    content = [TextContent(type='text', text=json.dumps(result, ensure_ascii=False, separators=(',', ':')))]
    if picture:
        content.append(ImageContent(type='image', data=picture['data'], mimeType=picture['mime_type']))
    return content


if __name__ == '__main__':
    mcp.run(transport='stdio')
