"""Optional protocol integration: uv run --with mcp==1.30.0 python tests/check_mcp.py"""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from observe import Observer, make_server
from tools.action_runtime import PhoneActions


async def check(port):
    parameters = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / 'tools/observe_mcp.py')],
        env=dict(os.environ, IPHONE_OBSERVE_URL=f'http://127.0.0.1:{port}'))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert names == {'iphone_observe', 'iphone_type', 'iphone_act'}
            text = await session.call_tool('iphone_observe', {})
            assert not text.isError
            assert [part.type for part in text.content] == ['text']
            result = json.loads(text.content[0].text)
            assert 'box' not in result['items'][0]
            full = await session.call_tool('iphone_observe', {'full': True})
            assert 'box' in json.loads(full.content[0].text)['items'][0]
            picture = await session.call_tool('iphone_observe', dict(
                include_image=True, observation_id=result['observation_id']))
            assert not picture.isError
            assert [part.type for part in picture.content] == ['text', 'image']
            assert base64.b64decode(picture.content[1].data) == IMAGE
            assert 'data' not in json.loads(picture.content[0].text)
            inserted = await session.call_tool('iphone_type', dict(
                text='中文文章🙂\n第二段', request_id='mcp-test-1', allow_newlines=True))
            assert not inserted.isError, inserted
            response = json.loads(inserted.content[0].text)
            assert response['accepted'] and response['submitted'] is False
            assert inputter.received == ('中文文章🙂\n第二段', 'mcp-test-1', True)
            action_arguments = dict(action='tap', observation_id=result['observation_id'],
                                    request_id='action-test-1', target_id=0,
                                    wait_for_text='新页面', include_image=True)
            acted = await session.call_tool('iphone_act', action_arguments)
            assert not acted.isError, acted
            assert [part.type for part in acted.content] == ['text', 'image']
            action_result = json.loads(acted.content[0].text)
            assert action_result['accepted'] and action_result['wait']['status'] == 'condition_met'
            assert 'image' not in action_result['observation']
            assert base64.b64decode(acted.content[1].data) == IMAGE_AFTER
            replayed = await session.call_tool('iphone_act', action_arguments)
            assert json.loads(replayed.content[0].text)['replayed'] is True
            assert len(board.commands) == 1
            expired = await session.call_tool('iphone_observe', {'observation_id': 'missing'})
            assert expired.isError
            assert 'Observation expired' in expired.content[0].text
            async def cli(*arguments):
                process = await asyncio.create_subprocess_exec(
                    str(ROOT / 'iphone'), '--api', f'http://127.0.0.1:{port}',
                    '--session', str(Path(directory.name) / 'cli.json'), *arguments,
                    cwd=directory.name, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()
                assert process.returncode == 0, stderr.decode() + stdout.decode()
                return json.loads(stdout)
            seen = await cli('observe')
            assert seen['items'][0]['text'] == '新页面'
            assert 'box' not in seen['items'][0]
            clicked = await cli('tap', '0', '--id', 'cli-1', '--timeout', '200')
            assert clicked['accepted'] and len(board.commands) == 2
            again = await cli('tap', '0', '--id', 'cli-1', '--timeout', '200')
            assert again['replayed'] and len(board.commands) == 2
            picture_file = await cli('image')
            assert Path(picture_file['image_file']).read_bytes() == IMAGE_AFTER
            print('MCP and fixed CLI observation/image/input/action/replay integration passed')


# Protocol fixture only; real OCR is exercised separately on macOS.
IMAGE = base64.b64decode('/9j/2Q==')
IMAGE_AFTER = IMAGE + b'after'
phase = {'changed': False}
observer = Observer(lambda: (IMAGE_AFTER if phase['changed'] else IMAGE,
                             dict(received_at=time.time(), source_running=True, metadata_exact=True)),
                    lambda image: dict(image_sha256=hashlib.sha256(image).hexdigest(),
                                       width=1, height=1,
                                       items=[dict(text='新页面' if image == IMAGE_AFTER else '原页面',
                                                   center=[.5, .5], box=[0, 0, 1, 1],
                                                   confidence=1., clickable=None)]))


class FakeInput:
    def insert(self, text, request_id, allow_newlines):
        self.received = text, request_id, allow_newlines
        return dict(accepted=True, submitted=False, request_id=request_id)


inputter = FakeInput()


class FakeBoard:
    def __init__(self):
        self.commands = []

    def status(self):
        return dict(usb='ok', busy=0, owner='none')

    def command(self, line):
        self.commands.append(line)
        phase['changed'] = True
        return 'ok'


board = FakeBoard()
if __name__ == '__main__':
    directory = tempfile.TemporaryDirectory()
    actor = PhoneActions(observer, board, directory.name)
    server = make_server(observer, 0, inputter, actor)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        asyncio.run(check(server.server_port))
    finally:
        server.shutdown()
        server.server_close()
        directory.cleanup()
