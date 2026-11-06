"""Optional protocol integration: uv run --with mcp==1.30.0 python tests/check_mcp.py"""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from observe import Observer, make_server


async def check(port):
    parameters = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / 'tools/observe_mcp.py')],
        env=dict(os.environ, IPHONE_OBSERVE_URL=f'http://127.0.0.1:{port}'))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            assert (await session.list_tools()).tools[0].name == 'iphone_observe'
            text = await session.call_tool('iphone_observe', {})
            assert not text.isError
            assert [part.type for part in text.content] == ['text']
            result = json.loads(text.content[0].text)
            picture = await session.call_tool('iphone_observe', dict(
                include_image=True, observation_id=result['observation_id']))
            assert not picture.isError
            assert [part.type for part in picture.content] == ['text', 'image']
            assert base64.b64decode(picture.content[1].data) == IMAGE
            assert 'data' not in json.loads(picture.content[0].text)
            print('MCP text/image protocol roundtrip passed')


# Protocol fixture only; real OCR is exercised separately on macOS.
IMAGE = base64.b64decode('/9j/2Q==')
observer = Observer(lambda: (IMAGE, dict(received_at=time.time(), source_running=True)),
                    lambda image: dict(image_sha256=hashlib.sha256(image).hexdigest(),
                                       width=1, height=1, items=[]))
if __name__ == '__main__':
    server = make_server(observer, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        asyncio.run(check(server.server_port))
    finally:
        server.shutdown()
        server.server_close()
