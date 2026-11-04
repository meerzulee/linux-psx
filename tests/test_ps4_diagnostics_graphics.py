"""Fixed diagnostic display sessions using mocked services and executable shims."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "rootfs/ps4-diagnostics-desktop"
loader = importlib.machinery.SourceFileLoader("psx_fixed_graphics_tests", str(SOURCE / "launch-graphics"))
spec = importlib.util.spec_from_loader(loader.name, loader)
APP = importlib.util.module_from_spec(spec)
loader.exec_module(APP)
wm_loader = importlib.machinery.SourceFileLoader("psx_wm_wait_tests", str(SOURCE / "wait-graphics-wm"))
wm_spec = importlib.util.spec_from_loader(wm_loader.name, wm_loader)
WM = importlib.util.module_from_spec(wm_spec)
wm_loader.exec_module(WM)


class FixedGraphicsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / "calls.jsonl"
        self.home = self.root / "home"
        self.home.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.environment = self.root / "mesa.env"
        self.environment.write_text("PSX_MESA_MANIFEST_SHA256=fixture-pin\n")
        self.env = dict(os.environ, HOME=str(self.home), PSX_GRAPHICS_MODE="diagnostics",
                        TEST_GRAPHICS_LOG=str(self.log))
        self.shim = """#!/usr/bin/python3
import json, os, sys
record = {'program': os.path.basename(sys.argv[0]), 'argv': sys.argv[1:],
          'env': {key: os.environ.get(key) for key in ('HOME','USER','LOGNAME','XDG_RUNTIME_DIR','XAUTHORITY','PSX_GRAPHICS_MODE','PSX_GRAPHICS_WINDOWED','COLORTERM','TERM','LD_LIBRARY_PATH')}}
fd = os.open(os.environ['TEST_GRAPHICS_LOG'], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
os.write(fd, (json.dumps(record)+'\\n').encode()); os.close(fd)
"""

    def executable(self, name):
        path = self.root / name
        path.write_text(self.shim)
        path.chmod(0o755)
        return path

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_session(self, mode="diagnostics", missing_font=False, chromium=False):
        text = (SOURCE / "graphics-session").read_text()
        text = text.replace("/etc/psx-diagnostics/mesa.env", str(self.environment))
        prefs = self.root / "firefox-user.js"
        prefs.write_text('user_pref("layers.acceleration.disabled", true);\n')
        text = text.replace("/etc/psx-diagnostics/firefox-user.js", str(prefs))
        text = text.replace("/run/psx-frontend", str(self.runtime))
        for binary in ("xfwm4", "fc-cache", "timeout", "python3"):
            text = text.replace("/usr/bin/" + binary, str(self.executable(binary)))
        terminal = self.runtime / "xfce4-terminal"
        terminal.write_text(self.shim)
        terminal.chmod(0o755)
        wait = self.runtime / "wait-graphics-wm"
        wait.write_text(self.shim)
        wait.chmod(0o755)
        (self.runtime / "preferences.py").write_text(self.shim)
        if chromium:
            provider = self.runtime / "launch-chromium-provider"
            provider.write_text(self.shim)
            provider.chmod(0o755)
        if not missing_font:
            (self.runtime / "JetBrainsMonoNerdFont-Regular.ttf").write_bytes(b"fixture font")
        script = self.root / "session"
        script.write_text(text)
        return subprocess.run(["/bin/bash", str(script)], env=dict(self.env, PSX_GRAPHICS_MODE=mode),
                              capture_output=True, text=True, timeout=5)

    def test_diagnostics_mode_selects_fixed_service_and_checks_all_other_sessions(self):
        run = mock.Mock(return_value=SimpleNamespace(stdout="inactive\n"))
        with mock.patch.object(APP.os, "geteuid", return_value=0), \
             mock.patch.object(APP.sys, "argv", ["launch-graphics", "diagnostics"]), \
             mock.patch.object(APP.os, "uname", return_value=SimpleNamespace(release="fixture-kernel")), \
             mock.patch.object(APP.Path, "read_text", return_value='{"kernel_release":"fixture-kernel"}'), \
             mock.patch.object(APP.subprocess, "run", run), contextlib.redirect_stdout(io.StringIO()):
            APP.main()
        queried = [call.args[0][-1] for call in run.call_args_list[:-1]]
        self.assertEqual(set(queried), {"psx-diagnostics-graphics@" + name + ".service" for name in APP.MODES}
                         | {"psx-diagnostics-window-" + name + ".service" for name in APP.WINDOW_MODES})
        self.assertEqual(run.call_args.args[0], ["systemctl", "start", "psx-diagnostics-graphics@diagnostics.service"])

    def test_diagnostics_terminal_opens_apps_on_existing_display_without_another_xorg(self):
        calls = []
        def respond(command, **_):
            calls.append(command)
            return SimpleNamespace(stdout="active\n" if command[-1].endswith("@diagnostics.service") else "inactive\n")
        with mock.patch.object(APP.os, "geteuid", return_value=0), \
             mock.patch.object(APP.sys, "argv", ["launch-graphics", "browser"]), \
             mock.patch.object(APP.os, "uname", return_value=SimpleNamespace(release="fixture-kernel")), \
             mock.patch.object(APP.Path, "read_text", return_value='{"kernel_release":"fixture-kernel"}'), \
             mock.patch.object(APP.os, "access", return_value=True), \
             mock.patch.object(APP.subprocess, "run", side_effect=respond), contextlib.redirect_stdout(io.StringIO()):
            APP.main()
        self.assertEqual(calls[-1][0], "systemd-run")
        self.assertIn("--unit=psx-diagnostics-window-browser.service", calls[-1])
        self.assertIn("--property=BindsTo=psx-diagnostics-graphics@diagnostics.service", calls[-1])
        self.assertEqual(calls[-1][-2:], ["/run/psx-frontend/graphics-window", "browser"])
        self.assertFalse(any(command[:2] == ["systemctl", "start"] for command in calls))

    def test_existing_app_window_blocks_another_app_but_keeps_terminal_alive(self):
        def respond(command, **_):
            self.assertEqual(command[0], "systemctl")
            active = command[-1].endswith(("@diagnostics.service", "window-opengl.service"))
            return SimpleNamespace(stdout="active\n" if active else "inactive\n")
        with mock.patch.object(APP.os, "geteuid", return_value=0), \
             mock.patch.object(APP.sys, "argv", ["launch-graphics", "browser"]), \
             mock.patch.object(APP.os, "uname", return_value=SimpleNamespace(release="fixture-kernel")), \
             mock.patch.object(APP.Path, "read_text", return_value='{"kernel_release":"fixture-kernel"}'), \
             mock.patch.object(APP.subprocess, "run", side_effect=respond), self.assertRaises(ValueError):
            APP.main()

    def test_terminal_uses_nerd_font_and_only_fixed_diagnostic_command(self):
        result = self.run_session()
        self.assertEqual(result.returncode, 0, result.stderr)
        call = next(call for call in self.calls() if call["program"] == "xfce4-terminal")
        self.assertEqual(call["argv"][call["argv"].index("--execute") + 1:],
                         ["/usr/bin/psx-diagnostics-tui", "--socket", "/run/psx-diagnostics/control.sock"])
        for option in ("--fullscreen", "--disable-server", "--hide-menubar", "--hide-toolbar", "--hide-scrollbar", "--hide-borders"):
            self.assertIn(option, call["argv"])
        self.assertFalse(any(option.startswith(("--font", "--color-")) for option in call["argv"]))
        self.assertEqual(call["env"]["COLORTERM"], "truecolor")
        self.assertEqual(call["env"]["XDG_RUNTIME_DIR"], "/run/user/0")
        self.assertTrue(call["env"]["LD_LIBRARY_PATH"].startswith(str(self.runtime)))
        bootstrap = next(call for call in self.calls() if call["program"] == "python3" and "session-start" in call["argv"])
        self.assertEqual(bootstrap["argv"][:3], [str(self.runtime / "preferences.py"), "session-start", "--session-pid"])
        self.assertTrue(bootstrap["argv"][3].isdecimal())
        font = self.home / ".local/share/fonts/JetBrainsMonoNerdFont-Regular.ttf"
        self.assertEqual(font.resolve(), self.runtime / font.name)
        self.assertTrue(any(call["program"] == "fc-cache" for call in self.calls()))
        programs = [call["program"] for call in self.calls()]
        self.assertLess(programs.index("python3"), programs.index("xfce4-terminal"))

    def test_missing_runtime_font_prevents_terminal_launch(self):
        result = self.run_session(missing_font=True)
        self.assertEqual(result.returncode, 1)
        calls = self.calls() if self.log.exists() else []
        self.assertFalse(any(call["program"] == "xfce4-terminal" for call in calls))

    def test_unknown_session_mode_never_starts_a_program(self):
        result = self.run_session(mode="arbitrary-shell")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())

    def test_windowed_opengl_keeps_existing_window_manager_and_cookie(self):
        self.env["PSX_GRAPHICS_WINDOWED"] = "1"
        result = self.run_session(mode="opengl")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertFalse(any(call["program"] == "xfwm4" for call in calls))
        self.assertEqual(calls[-1]["program"], "timeout")
        self.assertIn("/usr/bin/glxgears", calls[-1]["argv"])
        self.assertEqual(calls[-1]["env"]["XAUTHORITY"], "/run/psx-graphics-user/Xauthority")

    def test_window_helper_uses_private_cookie_and_fixed_unprivileged_client(self):
        text = (SOURCE / "graphics-window").read_text().replace('"$(id -u)"', '"0"')
        text = text.replace("systemctl is-active", str(self.executable("systemctl")) + " is-active")
        text = text.replace("install ", str(self.executable("install")) + " ")
        text = text.replace("/usr/lib/psx-diagnostics/graphics-client", str(self.executable("client")))
        script = self.root / "window"
        script.write_text(text)
        result = subprocess.run(["/bin/bash", str(script), "vulkan"], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(calls[0]["argv"], ["is-active", "--quiet", "psx-diagnostics-graphics@diagnostics.service"])
        copy = next(call for call in calls if call["program"] == "install" and "0600" in call["argv"])
        self.assertEqual(copy["argv"][-2:], ["/run/psx-desktop/Xauthority", "/run/psx-graphics-user/Xauthority"])
        self.assertEqual(calls[-1]["program"], "client")
        self.assertEqual(calls[-1]["env"]["PSX_GRAPHICS_MODE"], "vulkan")
        self.assertEqual(calls[-1]["env"]["PSX_GRAPHICS_WINDOWED"], "1")

    def test_only_diagnostics_client_keeps_root_and_other_modes_drop_privileges(self):
        text = (SOURCE / "graphics-client").read_text().replace("/etc/psx-diagnostics/mesa.env", str(self.environment))
        text = text.replace("/usr/bin/python3", str(self.executable("python3")))
        text = text.replace("/usr/bin/setpriv", str(self.executable("setpriv")))
        script = self.root / "client"
        script.write_text(text)
        for mode in ("diagnostics", "browser", "chromium", "protondb", "opengl", "vulkan"):
            with self.subTest(mode=mode):
                result = subprocess.run(["/bin/sh", str(script)], env=dict(self.env, PSX_GRAPHICS_MODE=mode),
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                call = self.calls()[-1]
                if mode == "diagnostics":
                    self.assertEqual(call["program"], "python3")
                    self.assertEqual(call["env"]["HOME"], "/root")
                    self.assertEqual(call["env"]["USER"], "root")
                    self.assertIn("/run/psx-mesa/0", call["argv"])
                else:
                    self.assertEqual(call["program"], "setpriv")
                    self.assertEqual(call["env"]["USER"], "psx")
                    self.assertEqual(call["argv"][:3], ["--reuid=psx", "--regid=psx", "--init-groups"])

    def test_protondb_opens_only_the_fixed_https_target_with_fresh_private_firefox_profiles(self):
        profiles=[]
        for _ in range(2):
            result=self.run_session(mode='protondb')
            self.assertEqual(result.returncode,0,result.stderr)
            call=next(call for call in reversed(self.calls()) if '/usr/bin/firefox' in call['argv'])
            self.assertEqual(call['argv'][-1],'https://www.protondb.com/')
            self.assertNotIn('http://127.0.0.1:8080/lab',call['argv'])
            self.assertIn('--no-remote',call['argv']);self.assertIn('--kiosk',call['argv'])
            profile=Path(call['argv'][call['argv'].index('--profile')+1]);profiles.append(profile)
            self.assertEqual(profile.stat().st_mode&0o777,0o700)
            self.assertIn('layers.acceleration.disabled',(profile/'user.js').read_text())
            self.assertIn('browser.sessionstore.resume_from_crash',(profile/'user.js').read_text())
        self.assertNotEqual(profiles[0],profiles[1])

    def test_chromium_window_uses_fixed_optional_provider_without_new_wm(self):
        self.env["PSX_GRAPHICS_WINDOWED"] = "1"
        result = self.run_session(mode="chromium", chromium=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual([call["program"] for call in calls], ["launch-chromium-provider"])
        self.assertEqual(calls[0]["argv"], [])
        self.assertEqual(calls[0]["env"]["XAUTHORITY"], "/run/psx-graphics-user/Xauthority")

    def test_missing_chromium_provider_stops_clearly_without_starting_an_app(self):
        self.env["PSX_GRAPHICS_WINDOWED"] = "1"
        result = self.run_session(mode="chromium")
        self.assertEqual(result.returncode, 1)
        self.assertIn("384 MiB USB provider", result.stderr)
        self.assertFalse(self.log.exists())

    def test_chromium_uses_existing_display_unit_and_excludes_other_browser_windows(self):
        calls = []
        def respond(command, **_):
            calls.append(command)
            return SimpleNamespace(stdout="active\n" if command[-1].endswith("@diagnostics.service") else "inactive\n")
        with mock.patch.object(APP.os,"geteuid",return_value=0), \
             mock.patch.object(APP.sys,"argv",["launch-graphics","chromium"]), \
             mock.patch.object(APP.os,"uname",return_value=SimpleNamespace(release="fixture-kernel")), \
             mock.patch.object(APP.Path,"read_text",return_value='{"kernel_release":"fixture-kernel"}'), \
             mock.patch.object(APP.os,"access",return_value=True), \
             mock.patch.object(APP.subprocess,"run",side_effect=respond), contextlib.redirect_stdout(io.StringIO()):
            APP.main()
        self.assertIn("--unit=psx-diagnostics-window-chromium.service",calls[-1])
        self.assertEqual(calls[-1][-2:],["/run/psx-frontend/graphics-window","chromium"])
        self.assertIn("--property=BindsTo=psx-diagnostics-graphics@diagnostics.service",calls[-1])
        self.assertTrue(any(command[-1]=="psx-diagnostics-window-browser.service" for command in calls[:-1]))

    def test_standalone_chromium_copies_the_same_private_cookie_before_xinit(self):
        text = (SOURCE / "graphics-start").read_text()
        text = text.replace("/usr/bin/xinit", str(self.executable("xinit")))
        for name in ("install", "mcookie", "xauth", "chown"):
            self.executable(name)
        script = self.root / "start"
        script.write_text(text)
        result = subprocess.run(["/bin/bash", str(script), "chromium"],
            env=dict(self.env, PATH=str(self.root) + ":/usr/bin:/bin"), capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        cookie = next(call for call in calls if call["program"] == "install"
            and call["argv"][-1] == "/run/psx-graphics-user/Xauthority")
        self.assertEqual(cookie["argv"], ["-o", "psx", "-g", "psx", "-m", "0600",
            "/run/psx-desktop/Xauthority", "/run/psx-graphics-user/Xauthority"])
        self.assertEqual(calls[-1]["program"], "xinit")
        self.assertIn("-dpms", calls[-1]["argv"])
        self.assertEqual(calls[-1]["argv"][calls[-1]["argv"].index("-s") + 1], "0")

    def test_firefox_launches_get_distinct_private_profiles_and_preserve_base_prefs(self):
        self.env["PSX_GRAPHICS_WINDOWED"] = "1"
        profiles = []
        for _ in range(2):
            result = self.run_session(mode="browser")
            self.assertEqual(result.returncode, 0, result.stderr)
            call = self.calls()[-1]
            profile = Path(call["argv"][call["argv"].index("--profile") + 1])
            profiles.append(profile)
            self.assertEqual(profile.stat().st_mode & 0o777, 0o700)
            preferences = (profile / 'user.js').read_text()
            self.assertIn('user_pref("layers.acceleration.disabled", true)', preferences)
            self.assertIn('user_pref("browser.sessionstore.resume_from_crash", false)', preferences)
            self.assertIn('user_pref("browser.startup.page", 0)', preferences)
        self.assertNotEqual(profiles[0], profiles[1])

    def test_active_chromium_window_blocks_a_second_firefox_window(self):
        def respond(command, **_):
            self.assertEqual(command[0], "systemctl")
            active = command[-1].endswith(("@diagnostics.service", "window-chromium.service"))
            return SimpleNamespace(stdout="active\n" if active else "inactive\n")
        with mock.patch.object(APP.os,"geteuid",return_value=0), \
             mock.patch.object(APP.sys,"argv",["launch-graphics","browser"]), \
             mock.patch.object(APP.os,"uname",return_value=SimpleNamespace(release="fixture-kernel")), \
             mock.patch.object(APP.Path,"read_text",return_value='{"kernel_release":"fixture-kernel"}'), \
             mock.patch.object(APP.subprocess,"run",side_effect=respond), self.assertRaisesRegex(ValueError,"already active"):
            APP.main()

    def test_window_manager_requires_a_self_consistent_supporting_window(self):
        probe = WM.XConnection.__new__(WM.XConnection)
        probe.root = 1
        values = {1: 41, 41: 42}
        probe.property_window = values.get
        self.assertFalse(probe.ready())
        values[41] = 41
        self.assertTrue(probe.ready())
        values[1] = 0
        self.assertFalse(probe.ready())

    def test_window_manager_wait_is_bounded_and_always_closes_connection(self):
        class Connection:
            closed = False
            ready = mock.Mock(return_value=False)
            def __enter__(self): return self
            def __exit__(self, *_): self.closed = True
        now = [0.0]
        def sleep(seconds): now[0] += seconds
        connection = Connection()
        with self.assertRaisesRegex(RuntimeError, "within 10 seconds"):
            WM.wait_for_wm(lambda: connection, lambda: now[0], sleep)
        self.assertTrue(connection.closed)
        self.assertAlmostEqual(now[0], 10)
        connection = Connection()
        connection.ready = mock.Mock(side_effect=[False, False, True])
        now[0] = 0
        WM.wait_for_wm(lambda: connection, lambda: now[0], sleep)
        self.assertTrue(connection.closed)
        self.assertAlmostEqual(now[0], 0.2)


if __name__ == "__main__":
    unittest.main()
