#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0"]
# ///
"""stdio MCP adapter for the persistent local observation API."""
import json
import os
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
try:
    from .pilot_client import PilotAPI, compact
except ImportError:
    from pilot_client import PilotAPI, compact

mcp = FastMCP('iPhone Pilot')
API = PilotAPI(os.environ.get('IPHONE_OBSERVE_URL', 'http://127.0.0.1:18766'))


@mcp.tool()
def iphone_status() -> dict:
    """Check whether the local service can currently provide a fresh screen frame.

    fresh_frame_available reports whether a frame is recent enough for actions;
    cached_frame_available reports whether an older image can still be viewed.
    Neither field infers whether phone mirroring is connected. Status is read
    immediately; observation still returns the latest cached frame when stale.
    """
    return API.request('/health')


def content(result, picture=None, full=False):
    output = result if full else compact(result)
    blocks = [TextContent(type='text', text=json.dumps(output, ensure_ascii=False, separators=(',', ':')))]
    if picture:
        blocks.append(ImageContent(type='image', data=picture['data'], mimeType=picture['mime_type']))
    return blocks


@mcp.tool()
def iphone_observe(include_image: bool = False, observation_id: str | None = None,
                   full: bool = False) -> list[TextContent | ImageContent]:
    """Read latest iPhone text, pixel centers and visible AirType address/pairing code.

    airtype.pairing_code is null if unreadable, ambiguous or stale; no token is exposed.
    Set include_image for ambiguous icons.

    Supply a previous observation_id to inspect that SAME frame with an image.
    IDs expire from a bounded cache. If capture pauses, the most recent cached
    frame is still returned with stale=true; inspect it, but never act on its
    coordinates. A temporary-unavailable error means no readable frame is cached.
    Text boxes are not proof of tappability; verify new state after navigation.
    Results are compact by default; full=true restores original boxes/metadata.
    """
    result = API.observe(observation_id, include_image)
    picture = result.pop('image', None)
    return content(result, picture, full)


@mcp.tool()
def iphone_type(text: str, request_id: str, allow_newlines: bool = False,
                include_image: bool = False) -> list[TextContent | ImageContent]:
    """Insert whole Unicode text through the currently focused AirType keyboard.

    Focus the intended field and show AirType Web Access first. The API reads the
    current address/code with OCR, reuses or pairs a session, inserts ONCE and checks
    OCR for new text and returns that post-input OCR in the same result. Set
    include_image for the matching frame. Does not click Send/Publish. request_id must be unique per
    intended insertion; reuse the SAME id and text on timeout/retry to avoid replay.
    accepted=true is an HTTP acknowledgment; verification may be not_confirmed.
    Only allow newlines for a confirmed multiline field. Never retry uncertain
    insertion with a new request_id without inspecting the phone first.
    """
    result = API.insert(text, request_id, allow_newlines, include_image)
    observation = result.get('observation')
    picture = observation.pop('image', None) if observation else None
    return content(result, picture)


@mcp.tool()
def iphone_act(action: str, observation_id: str, request_id: str,
               target_id: int | None = None, x: float | None = None, y: float | None = None,
               end_x: float | None = None, end_y: float | None = None, duration_ms: int = 400,
               wait_for_text: str | None = None, timeout_ms: int = 5000,
               changes_only: bool = False, include_image: bool = False,
               full: bool = False) -> list[TextContent | ImageContent]:
    """Tap or swipe, wait for a stable content change, and return new OCR in one call.

    Use the current observation_id. For tap supply target_id OR screenshot pixel x/y;
    for swipe supply x/y/end_x/end_y. target_id is an OCR text center, not proof of
    tappability. The runtime rejects changed screen layouts before sending.
    Optional wait_for_text must appear on a changed, stable post-command screen.
    accepted=true only acknowledges HID; wait.status=timeout is NOT success.
    Reuse SAME request_id and action on retry: commands are durably protected from
    replay, including after restart. Never use a new ID to blindly repeat a submit.
    changes_only omits full OCR items, leaving metadata plus added/removed items;
    include_image adds an actual MCP image block. If the cached frame is stale,
    the service first moves the pointer (without clicking) to an OCR-empty
    location and requires a newly received changed frame. It then revalidates
    the intended target. The tap/swipe itself is never retried automatically.
    full=true restores full OCR boxes; default output keeps IDs/centers and safety fields.
    """
    payload = dict(action=action, observation_id=observation_id, request_id=request_id,
                   target_id=target_id, x=x, y=y, end_x=end_x, end_y=end_y,
                   duration_ms=duration_ms, wait_for_text=wait_for_text,
                   timeout_ms=timeout_ms, changes_only=changes_only, include_image=include_image)
    result = API.act(**payload)
    picture = (result.get('observation') or {}).pop('image', None)
    return content(result, picture, full)


if __name__ == '__main__':
    mcp.run(transport='stdio')
