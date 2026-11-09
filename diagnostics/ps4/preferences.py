#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fixed diagnostic display preferences; no desktop, driver or service changes."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import sys

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
if '/run/psx-ram' not in sys.path:
    sys.path.insert(1, '/run/psx-ram')
try:
    import ram_state
except ImportError:
    ram_state = None

FONT = 'JetBrainsMono Nerd Font'
DEFAULT = {'schema_version': 1, 'theme': 'olive', 'font_family': FONT, 'font_size': 16}
ROLES = {'background', 'panel', 'accent', 'muted', 'selection', 'foreground'}
HEX = re.compile(r'#[a-fA-F0-9]{6}\Z')
BUS = re.compile(r'unix:path=(/(?:tmp/dbus-[A-Za-z0-9_-]+|run/user/0/bus))(?:,guid=[a-f0-9]{32})?\Z')


class DisplayPreferences:
    def __init__(self, root_prefix='/', executor=None):
        self.root = Path(root_prefix).resolve()
        self.fixture = self.root != Path('/')
        self.owner = os.geteuid() if self.fixture else 0
        self.execute = executor
        self.ram = ram_state is not None and ram_state.active(self.root)
        self.path = self.local('/run/psx-state/state/display.json' if self.ram else
                               '/run/psx-media/psx-diagnostics/state/display.json')
        self.session = self.local('/run/psx-frontend/terminal-session.json')
        manifest = json.loads(Path(__file__).with_name('themes.json').read_text())
        if manifest.get('schema_version') != 1:
            raise ValueError('Unsupported diagnostic palette manifest')
        self.themes = {}
        for theme in manifest['themes']:
            palette = theme.get('palette', {})
            if (not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', theme['id']) or theme['id'] in self.themes
                    or set(palette) != ROLES or not all(HEX.fullmatch(color) for color in palette.values())):
                raise ValueError('Invalid diagnostic palette')
            self.themes[theme['id']] = theme
        if 'olive' not in self.themes:
            raise ValueError('Default diagnostic palette is missing')
        self.last_apply = {'applied': False, 'message': 'Graphical terminal changes apply on its next launch.'}

    def local(self, name):
        path = self.root / name.lstrip('/')
        # All locations are fixed; refuse symlink redirection even within RAM.
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink():
                raise ValueError('Display preference paths must not contain symlinks')
        return path

    def read_private(self, path):
        self.local('/' + str(path.relative_to(self.root)))
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384):
            raise ValueError('Display state must be a private bounded regular file')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            actual = os.fstat(stream.fileno())
            if (actual.st_dev, actual.st_ino, actual.st_size) != (info.st_dev, info.st_ino, info.st_size):
                raise ValueError('Display state changed while opening')
            data = stream.read(16385)
        if len(data) > 16384:
            raise ValueError('Display state exceeds its bound')
        return json.loads(data)

    def validate(self, values):
        if (not isinstance(values, dict) or set(values) != set(DEFAULT)
                or type(values['schema_version']) is not int or values['schema_version'] != 1
                or not isinstance(values['theme'], str) or values['theme'] not in self.themes or values['font_family'] != FONT
                or type(values['font_size']) is not int or not 10 <= values['font_size'] <= 24):
            raise ValueError('Choose an installed theme and JetBrains Mono Nerd Font size 10–24')
        return dict(values)

    def load(self):
        try:
            return self.validate(self.read_private(self.path))
        except FileNotFoundError:
            return dict(DEFAULT)

    def state(self):
        error = ''
        try:
            values = self.load()
        except (OSError, ValueError, TypeError, KeyError):
            values = dict(DEFAULT)
            error = 'Saved display settings could not be read; using the default palette.'
        theme = self.themes[values['theme']]
        live = self.last_apply if not self.last_apply['applied'] else {
            'applied': False, 'message': 'Saved. Reopen the graphical terminal to apply these preferences.'}
        try:
            session = self.current_session()
            if session['applied_preferences'] == values:
                live = {'applied': True, 'message': 'Current preferences are applied to the graphical terminal. Browser colors update on reload.'}
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            pass
        return dict(values, theme_name=theme['name'], palette=theme['palette'],
                    available_themes=[{'id': item['id'], 'name': item['name']} for item in self.themes.values()],
                    live=live, error=error, persistent=not self.ram,
                    state_lifetime='this boot' if self.ram else 'USB installation')

    def atomic(self, path, values):
        self.local('/' + str(path.relative_to(self.root)))
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.display-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(values, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def updated(self, changes):
        if (not isinstance(changes, dict) or not changes
                or set(changes) - {'theme', 'font_family', 'font_size'}):
            raise ValueError('Unknown display setting')
        return self.validate(dict(self.load(), **changes))

    def set(self, changes):
        values = self.updated(changes)
        if self.ram:
            ram_state.validate(self.root, fixture=self.fixture)
        elif not self.fixture and not self.local('/run/psx-media').is_mount():
            raise ValueError('Diagnostic USB is not mounted; settings were not saved')
        self.atomic(self.path, values)
        self.last_apply = self.apply_live(values)
        return {'message': self.last_apply['message'], 'settings': self.state(), 'hardware_acceptance': False}

    def process_start(self, pid):
        if type(pid) is not int or pid <= 1:
            raise ValueError('Invalid graphical session process')
        path = self.local('/proc/' + str(pid))
        if path.stat().st_uid != self.owner:
            raise ValueError('Graphical session is not owned by root')
        fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        argv = (path / 'cmdline').read_bytes().split(b'\0')
        if not any(item == b'/usr/lib/psx-diagnostics/graphics-session' for item in argv):
            raise ValueError('Process is not the fixed graphical session')
        return fields[19]

    def session_start(self, pid):
        if os.environ.get('DISPLAY') != ':0' or (not self.fixture and os.geteuid() != 0):
            raise ValueError('Display setup requires the fixed root diagnostic session')
        address = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
        match = BUS.fullmatch(address)
        if not match:
            raise ValueError('A private local graphical D-Bus session is required')
        bus = self.local(match[1]).lstat()
        if not stat.S_ISSOCK(bus.st_mode) or bus.st_uid != self.owner:
            raise ValueError('Graphical D-Bus socket is not private to root')
        record = {'schema_version': 1, 'boot_id': self.local('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  'pid': pid, 'process_start': self.process_start(pid), 'dbus_address': address,
                  'applied_preferences': None}
        self.atomic(self.session, record)
        self.last_apply = self.apply_live(self.load())
        if not self.last_apply['applied']:
            raise ValueError(self.last_apply['message'])
        return self.last_apply

    def current_session(self):
        record = self.read_private(self.session)
        if (set(record) != {'schema_version', 'boot_id', 'pid', 'process_start', 'dbus_address', 'applied_preferences'}
                or type(record['schema_version']) is not int or record['schema_version'] != 1
                or record['boot_id'] != self.local('/proc/sys/kernel/random/boot_id').read_text().strip()
                or record['process_start'] != self.process_start(record['pid'])):
            raise ValueError('Graphical session receipt is stale')
        return record

    def apply_live(self, values):
        deferred = {'applied': False, 'message': 'Saved. Graphical terminal changes apply on its next launch; browser colors on reload.'}
        if self.fixture and self.execute is None:
            return deferred
        try:
            record = self.current_session()
            match = BUS.fullmatch(record['dbus_address'])
            if not match:
                raise ValueError('Graphical D-Bus address is not local')
            bus = self.local(match[1]).lstat()
            if not stat.S_ISSOCK(bus.st_mode) or bus.st_uid != self.owner:
                raise ValueError('Graphical D-Bus socket is unavailable')
            tool = self.local('/usr/bin/xfconf-query')
            if not tool.is_file() or not os.access(tool, os.X_OK):
                raise ValueError('The graphical preference tool is unavailable')
            palette = self.themes[values['theme']]['palette']
            properties = [('/font-use-system', 'bool', 'false'),
                          ('/font-name', 'string', FONT + ' ' + str(values['font_size'])),
                          ('/color-use-theme', 'bool', 'false'),
                          ('/color-foreground', 'string', palette['foreground']),
                          ('/color-background', 'string', palette['background']),
                          ('/color-cursor-use-default', 'bool', 'false'),
                          ('/color-cursor', 'string', palette['accent']),
                          ('/color-selection-use-default', 'bool', 'false'),
                          ('/color-selection-background', 'string', palette['selection']),
                          ('/color-selection', 'string', palette['foreground']),
                          ('/command-update-records', 'bool', 'false')]
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.local('/root')), 'USER': 'root', 'LOGNAME': 'root',
                   'DISPLAY': ':0', 'XAUTHORITY': str(self.local('/run/psx-desktop/Xauthority')),
                   'XDG_RUNTIME_DIR': str(self.local('/run/user/0')), 'DBUS_SESSION_BUS_ADDRESS': record['dbus_address']}
            if self.execute is None:
                def execute(argv, environment):
                    return subprocess.run(argv, env=environment, stdin=subprocess.DEVNULL,
                                          capture_output=True, text=True, timeout=2, check=True)
            else:
                execute = self.execute
            for name, kind, value in properties:
                execute([str(tool), '--channel', 'xfce4-terminal', '--property', name,
                         '--create', '--type', kind, '--set', value], env)
            record['applied_preferences'] = dict(values)
            self.atomic(self.session, record)
            return {'applied': True, 'message': 'Saved and applied to the graphical terminal. Browser colors update on reload.'}
        except FileNotFoundError:
            return deferred
        except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError):
            return {'applied': False, 'message': 'Saved; live terminal update was unavailable or incomplete. Reopen the graphical terminal to apply it.'}

    def browser_css(self):
        p = self.state()['palette']
        return ('\n/* Diagnostic palette; imported values are validated hexadecimal colors. */\n'
                ':root{--bg:' + p['background'] + ';--panel:' + p['panel'] +
                ';--accent:' + p['accent'] + ';--muted:' + p['muted'] +
                ';--selection:' + p['selection'] + ';--fg:' + p['foreground'] +
                ';color:' + p['foreground'] + ';background:' + p['background'] + '}'
                'body{background:' + p['background'] + '}button,.stage{background:' + p['panel'] + '}'
                '.intro,#summary,button span,footer{color:' + p['muted'] + '}'
                '#connection{color:' + p['accent'] + '}.preview{background:' + p['background'] + '}'
                'button:hover,button:focus-visible{outline-color:' + p['accent'] + ';background:' + p['selection'] + '}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('session-start',))
    parser.add_argument('--session-pid', type=int, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(DisplayPreferences().session_start(args.session_pid)))
    except (OSError, ValueError, KeyError, IndexError) as error:
        raise SystemExit('Diagnostic display preferences: ' + str(error))
