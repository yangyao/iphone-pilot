"""macOS user launchd lifecycle for the two local iPhone Pilot services."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import shlex
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler


class Services:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.runtime = self.root / 'runtime'
        self.domain = f'gui/{os.getuid()}'
        self.prefix = 'local.iphone-pilot.' + hashlib.sha256(str(self.root).encode()).hexdigest()[:10]
        self.agents = Path.home() / 'Library/LaunchAgents'

    def label(self, name):
        return self.prefix + '.' + name

    def path(self, name):
        return self.agents / (self.label(name) + '.plist')

    def ctl(self, *args, check=True):
        result = subprocess.run(['launchctl', *args], capture_output=True, text=True)
        if check and result.returncode:
            raise ValueError(f'launchctl {args[0]} failed: {result.stderr.strip()}')
        return result

    def loaded(self, name):
        return self.ctl('print', self.domain + '/' + self.label(name), check=False).returncode == 0

    def read(self, port, route):
        try:
            with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}{route}', timeout=2) as response:
                return json.load(response)
        except Exception:
            return None

    def uxplay_processes(self):
        result = subprocess.run(['ps', '-ax', '-o', 'pid=,ppid=,command='],
                                capture_output=True, text=True, check=True)
        matches = []
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) != 3:
                continue
            try:
                arguments = shlex.split(fields[2])
            except ValueError:
                continue
            if (arguments and Path(arguments[0]).name == 'uxplay' and
                    '-n' in arguments and arguments[arguments.index('-n') + 1:][:3] == ['iPhone', 'Agent', 'Mac']):
                matches.append(dict(pid=int(fields[0]), parent_pid=int(fields[1])))
        return matches

    def status(self):
        receiver = self.read(18765, '/status')
        api = self.read(18766, '/health')
        uxplay = self.uxplay_processes()
        return dict(uxplay=dict(processes=uxplay, duplicate=len(uxplay) > 1, orphan_pids=[p['pid'] for p in uxplay if p['parent_pid'] == 1]), api='ready' if api and api.get('ok') else 'unavailable',
                    receiver='running' if receiver and receiver.get('source_running') else 'unavailable',
                    screen=('fresh' if api and api.get('fresh_frame_available') else
                            'stale' if api and api.get('cached_frame_available') else 'waiting_for_mirroring'),
                    actions='configured' if api and api.get('actions_enabled') else 'unavailable',
                    managed={name: self.loaded(name) for name in ('receiver', 'api')},
                    logs=str(self.runtime))

    def arguments(self, name, args):
        if self.path(name).exists() and not any((args.board_host, args.uxplay)):
            saved = plistlib.loads(self.path(name).read_bytes())
            arguments = saved['ProgramArguments']
            if name == 'api' and getattr(args, 'parser_backend', None):
                if '--parser-backend' in arguments:
                    index = arguments.index('--parser-backend')
                    del arguments[index:index+2]
                arguments += ['--parser-backend', args.parser_backend]
            return arguments, saved.get('EnvironmentVariables', {})
        python = str(Path(sys.executable).resolve())
        if name == 'receiver':
            binary = args.uxplay or os.environ.get('UXPLAY_BIN') or shutil.which('uxplay')
            if not binary or not os.access(binary, os.X_OK):
                raise ValueError('UxPlay missing; pass --uxplay /absolute/path/to/uxplay')
            return [python, str(self.root / 'receiver.py'), '--directory', str(self.runtime), '--', python, str(self.root / 'uxplay_source.py')], {'UXPLAY_BIN': str(Path(binary).resolve())}
        board = args.board_host
        if not board:
            try:
                board = json.loads((self.runtime / 'board.json').read_text())['host']
            except (OSError, ValueError, KeyError):
                raise ValueError('Board unknown; run ./iphone board discover --save or pass --board-host') from None
        config = Path(args.config).resolve()
        if not config.is_file():
            raise ValueError('Board config missing; pass --config /path/to/secrets.h')
        return [python, str(self.root / 'observe.py'), '--board-host', board, '--board-config', str(config), '--parser-backend', getattr(args, 'parser_backend', None) or 'apple'], {}

    def install(self, name, spec):
        arguments, environment = spec
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.agents.mkdir(parents=True, exist_ok=True)
        data = dict(Label=self.label(name), ProgramArguments=arguments,
                    WorkingDirectory=str(self.root), EnvironmentVariables=environment,
                    RunAtLoad=True, KeepAlive=True, ThrottleInterval=5,
                    StandardOutPath=str(self.runtime / (name + '.log')),
                    StandardErrorPath=str(self.runtime / (name + '.log')))
        path = self.path(name)
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(plistlib.dumps(data))
        temporary.chmod(0o600)
        temporary.replace(path)
        deadline = time.monotonic() + 5
        while True:
            result = self.ctl('bootstrap', self.domain, str(path), check=False)
            if result.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise ValueError('launchctl bootstrap failed: ' + result.stderr.strip())
            time.sleep(.25)

    def run(self, args):
        self.runtime.mkdir(parents=True, exist_ok=True)
        with (self.runtime / 'services.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._run(args)

    def _run(self, args):
        if sys.platform != 'darwin':
            raise ValueError('serve requires macOS launchd')
        names = ['api'] if args.api_only else ['receiver'] if args.receiver_only else ['receiver', 'api']
        if args.service_action == 'status':
            return self.status()
        if args.service_action == 'logs':
            return {name: (self.runtime / (name + '.log')).read_text(errors='replace').splitlines()[-args.lines:]
                    if (self.runtime / (name + '.log')).exists() else [] for name in names}
        if 'receiver' in names and args.service_action != 'stop':
            conflicts = [p for p in self.uxplay_processes() if p['parent_pid'] == 1]
            if conflicts:
                raise ValueError('Orphan UxPlay processes conflict with receiver: ' +
                                 ','.join(str(p['pid']) for p in conflicts) +
                                 '; inspect and stop these exact processes before starting')
        # Validate every new service before stopping any running service.
        specs = {name: self.arguments(name, args) for name in names
                 if args.service_action != 'stop' and (args.service_action == 'restart' or not self.loaded(name))}
        for name in names:
            loaded = self.loaded(name)
            if loaded and args.service_action in ('stop', 'restart'):
                self.ctl('bootout', self.domain + '/' + self.label(name))
            if args.service_action == 'stop':
                self.path(name).unlink(missing_ok=True)
            elif name in specs:
                port, route = (18765, '/status') if name == 'receiver' else (18766, '/health')
                if not loaded and self.read(port, route) is not None:
                    raise ValueError(f'{name} port is occupied by an unmanaged service; stop that process before serve start')
                self.install(name, specs[name])
        if args.service_action == 'stop':
            return self.status()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ready = all(self.read(18765 if name == 'receiver' else 18766,
                                  '/status' if name == 'receiver' else '/health') is not None for name in names)
            if ready:
                return self.status()
            time.sleep(.25)
        raise ValueError('Services did not become ready within 15s; run ./iphone serve logs')
