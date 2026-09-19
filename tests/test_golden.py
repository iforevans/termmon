#!/usr/bin/env python3
"""Byte-level golden comparison of the C port against the Python oracle.

Both apps render the SAME frozen dataset (the C build reads a TERMMON_FIXTURE
file; the Python app gets the identical dict injected by test_pty_layout's
FAKE_SETUP). Screens are captured through a real PTY via pyte with the
stream stopped at a frame boundary, then compared row-for-row with only
the clock masked. This is the Phase 3 acceptance gate for the layout port.

Usage:
    python3 tests/test_golden.py             # all sizes
    python3 tests/test_golden.py --show 80   # print both screens at 80 cols
"""
import os
import re
import sys
import time
import pty
import select
import struct
import fcntl
import termios
import tempfile

import pyte

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_pty_layout as H  # noqa: E402

REPO = H.REPO
CBIN = os.path.join(REPO, 'termmon')

# Exactly the FAKE_SETUP values, in the C fixture (pipe/SYS) format.
FIXTURE = """\
SYS total_mem_gb 15.4
SYS used_mem_gb 12.5
SYS cache_mem_gb 2.4
SYS free_mem_gb 0.5
SYS avail_mem_gb 2.9
SYS mem_percent 81.2
SYS swap_total_mb 4400.0
SYS swap_used_mb 2750.0
SYS swap_percent 62.5
SYS cpu_usage 23.4
SYS cpu_temp 46.0
CORE 0 0
CORE 1 7
CORE 2 14
CORE 3 21
CORE 4 28
CORE 5 35
CORE 6 42
CORE 7 49
CORE 8 56
CORE 9 63
CORE 10 70
CORE 11 77
CORE 12 84
CORE 13 91
CORE 14 98
CORE 15 5
GPU 0|NVIDIA RTX A6000|49152.0|43800.0|5352.0|80.0|59.0|110.0
PROC 54321|iforevan|39506.0|7768.0|12.0|llama-server --host 127.0.0.1 --port 8080 -c 163840 -ngl 99 -m /home/iforevans/models/unsloth/Qwen3.6-27B-UD-Q8_K_XL.gguf -ctk q8_0 -ctv turbo4 --cache-ram 4096
"""

CLOCK = re.compile(r'\d{2}:\d{2}(?::\d{1,2})?')


def _fixture_path():
    path = os.path.join(tempfile.gettempdir(), 'termmon_golden_fixture.txt')
    with open(path, 'w') as fh:
        fh.write(FIXTURE)
    return path


def drain_to_boundary(fd, max_seconds=3.0, gap=0.2, min_seconds=1.0):
    """Read until output pauses at a frame boundary, so the stream never
    ends mid-refresh (which would feed pyte truncated escape sequences)."""
    buf = b''
    t0 = time.time()
    while time.time() - t0 < max_seconds:
        r, _, _ = select.select([fd], [], [], gap)
        if not r:
            if time.time() - t0 >= min_seconds:
                return buf
            continue
        try:
            data = os.read(fd, 65536)
        except OSError:
            return buf
        if not data:
            return buf
        buf += data
    return buf


def _finish(pid, fd, data):
    try:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    except OSError:
        pass
    os.close(fd)
    return data


def render_py(cols, rows, key=None, first_wait=1.2, args=()):
    script = H._child_script_path()
    pid, fd = pty.fork()
    if pid == 0:
        os.environ['TERM'] = 'xterm-256color'
        os.environ['LANG'] = 'en_US.UTF-8'
        os.execv(sys.executable, [sys.executable, script, *args])
        os._exit(1)
    H.set_size(fd, cols, rows)
    data = drain_to_boundary(fd, max_seconds=first_wait + 0.6,
                             min_seconds=first_wait)
    if key:
        os.write(fd, key)
        data += drain_to_boundary(fd, max_seconds=2.0, min_seconds=0.5)
    return _finish(pid, fd, data)


def render_c(cols, rows, key=None, first_wait=1.2, args=()):
    fixture = _fixture_path()
    pid, fd = pty.fork()
    if pid == 0:
        os.environ['TERM'] = 'xterm-256color'
        os.environ['LANG'] = 'en_US.UTF-8'
        os.environ['TERMMON_FIXTURE'] = fixture
        os.execv(CBIN, [CBIN, *args])
        os._exit(1)
    H.set_size(fd, cols, rows)
    data = drain_to_boundary(fd, max_seconds=first_wait + 0.6,
                             min_seconds=first_wait)
    if key:
        os.write(fd, key)
        data += drain_to_boundary(fd, max_seconds=2.0, min_seconds=0.5)
    return _finish(pid, fd, data)


def screen_of(data, cols, rows):
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(data.decode('utf-8', errors='replace'))
    return [CLOCK.sub(lambda m: ' ' * len(m.group()), line)
            for line in screen.display]


def compare(cols, rows, show=False):
    py_lines = screen_of(render_py(cols, rows), cols, rows)
    c_lines = screen_of(render_c(cols, rows), cols, rows)
    diffs = []
    for y, (a, b) in enumerate(zip(py_lines, c_lines)):
        if a != b:
            diffs.append((y, a, b))
    if show:
        print(f"--- {cols}x{rows} ---")
        for y in range(rows):
            if py_lines[y].strip() or c_lines[y].strip():
                print(f"{y:2d} P|{py_lines[y]}|")
                print(f"   C|{c_lines[y]}|")
    return diffs


SIZES = [(120, 44), (100, 34), (80, 30), (70, 30), (60, 28),
         (50, 26), (40, 26), (34, 24), (28, 20)]


def test_golden_matches_python():
    bad = {}
    for cols, rows in SIZES:
        diffs = compare(cols, rows)
        if diffs:
            bad[(cols, rows)] = diffs
    assert not bad, f"{len(bad)} size(s) differ: " + ", ".join(
        f"{c}x{r} ({len(d)} rows)" for c, r, d in bad.items())


POPUP_SIZES = [(100, 34), (80, 30), (40, 26), (30, 12), (26, 8)]


def compare_popup(cols, rows):
    py_lines = screen_of(render_py(cols, rows, key=b'h'), cols, rows)
    c_lines = screen_of(render_c(cols, rows, key=b'h'), cols, rows)
    return [(y, a, b) for y, (a, b) in enumerate(zip(py_lines, c_lines))
            if a != b]


def test_help_popup_matches():
    bad = {}
    for cols, rows in POPUP_SIZES:
        diffs = compare_popup(cols, rows)
        if diffs:
            bad[(cols, rows)] = diffs
    assert not bad, f"popup differs at: " + ", ".join(
        f"{c}x{r} ({len(d)} rows)" for c, r, d in bad.items())


# Configurable refresh cadence: identical footer text in both impls.
INTERVAL_CASES = [
    (['-i', '3'], 'Refresh: 3s'),
    (['--interval', '3'], 'Refresh: 3s'),
    (['--interval=3'], 'Refresh: 3s'),
    (['-i', '2.5'], 'Refresh: 2.5s'),
    (['-i', '60'], 'Refresh: 60s'),     # max
    (['-i', '0.1'], 'Refresh: 0.5s'),   # clamped up to MIN
    (['-i', '999'], 'Refresh: 60s'),    # clamped down to MAX
]


def test_refresh_interval_footer():
    bad = {}
    for argv, expect in INTERVAL_CASES:
        py_lines = screen_of(render_c(80, 30, args=argv), 80, 30)
        # C is the implementation under test; assert its footer matches the
        # expected text, then assert Python renders the same for these argv.
        footer = next((ln for ln in py_lines if 'Refresh:' in ln), '<none>')
        if expect not in footer:
            bad[tuple(argv)] = f"C footer {footer.strip()!r} lacks {expect!r}"
            continue
        py_lines = screen_of(render_py(80, 30, args=argv), 80, 30)
        py_footer = next((ln for ln in py_lines if 'Refresh:' in ln), '<none>')
        if expect not in py_footer:
            bad[tuple(argv)] = f"py footer {py_footer.strip()!r} lacks {expect!r}"
    assert not bad, "interval footer mismatch: " + "; ".join(
        f"{k}: {v}" for k, v in bad.items())


def _footer_after_cycles(render, cols, rows, times):
    pid, fd = (pty.fork())
    if pid == 0:
        os.environ['TERM'] = 'xterm-256color'
        os.environ['LANG'] = 'en_US.UTF-8'
        script = H._child_script_path()
        if render == 'c':
            os.environ['TERMMON_FIXTURE'] = _fixture_path()
            os.execv(CBIN, [CBIN])
        else:
            os.execv(sys.executable, [sys.executable, script])
        os._exit(1)
    H.set_size(fd, cols, rows)
    data = drain_to_boundary(fd, max_seconds=1.8, min_seconds=1.2)
    for _ in range(times):
        os.write(fd, b'r')
        time.sleep(0.15)
    data += drain_to_boundary(fd, max_seconds=2.0, min_seconds=0.5)
    _finish(pid, fd, data)
    lines = screen_of(data, cols, rows)
    return next((ln for ln in lines if 'Refresh:' in ln), '<none>').strip()


def test_refresh_cycle_key():
    # Ladder is 0.5->1->2->5->10->30->60->(wrap). Start is default 1s
    # (index 1), so after `times` presses the index is (1 + times) % 7.
    ladder = ['0.5', '1', '2', '5', '10', '30', '60']
    bad = {}
    for times in (1, 2, 3, 6, 7):   # 7 presses: full loop back to 1s
        expect = f"Refresh: {ladder[(1 + times) % 7]}s"
        c = _footer_after_cycles('c', 80, 30, times)
        p = _footer_after_cycles('p', 80, 30, times)
        if expect not in c or expect not in p:
            bad[times] = f"want {expect!r}: C|{c}| P|{p}|"
    assert not bad, "r-cycle mismatch: " + "; ".join(
        f"{k}: {v}" for k, v in bad.items())


def test_invalid_interval_rejected():
    import subprocess
    for argv in (['-i'], ['-i', 'x'], ['-i', 'abc'], ['--interval='], ['--bogus']):
        r = subprocess.run([CBIN, *argv], capture_output=True, timeout=5)
        assert r.returncode != 0, f"C {argv} should fail, got rc=0"
        r = subprocess.run([sys.executable, os.path.join(REPO, 'termmon.py'),
                            *argv], capture_output=True, timeout=5,
                           env={**os.environ, 'TERM': 'dumb'})
        assert r.returncode != 0, f"py {argv} should fail, got rc=0"


def main():
    if '--show' in sys.argv:
        w = int(sys.argv[sys.argv.index('--show') + 1])
        diffs = compare(w, 34, show=True)
        print("IDENTICAL" if not diffs else f"{len(diffs)} DIFF ROWS")
        for y, a, b in diffs[:8]:
            print(f"{y:2d} P|{a}|\n   C|{b}|")
        return 0 if not diffs else 1

    print("=" * 66)
    print("GOLDEN: C port vs Python oracle (identical frozen data)")
    print("=" * 66)
    failures = 0
    for cols, rows in SIZES:
        diffs = compare(cols, rows)
        status = "identical" if not diffs else f"FAIL ({len(diffs)} rows)"
        print(f"  {cols:>3}x{rows:<3} : {status}")
        for y, a, b in diffs[:3]:
            print(f"      row {y} py|{a}|")
            print(f"           c|{b}|")
        failures += len(diffs)
    print("\n  help popup ('h' pressed):")
    for cols, rows in POPUP_SIZES:
        diffs = compare_popup(cols, rows)
        status = "identical" if not diffs else f"FAIL ({len(diffs)} rows)"
        print(f"  {cols:>3}x{rows:<3} : {status}")
        for y, a, b in diffs[:3]:
            print(f"      row {y} py|{a}|")
            print(f"           c|{b}|")
        failures += len(diffs)
    print("\n  refresh interval footer (C vs Python):")
    for argv, expect in INTERVAL_CASES:
        c_footer = next((ln for ln in screen_of(render_c(80, 30, args=argv),
                                                80, 30)
                         if 'Refresh:' in ln), '<none>').strip()
        p_footer = next((ln for ln in screen_of(render_py(80, 30, args=argv),
                                                80, 30)
                         if 'Refresh:' in ln), '<none>').strip()
        ok = expect in c_footer and expect in p_footer and c_footer == p_footer
        print(f"  {' '.join(argv):<22} : {'ok' if ok else 'FAIL'} "
              f"({expect}) C|{c_footer}| P|{p_footer}|")
        if not ok:
            failures += 1
    print("\n  r-key cadence cycle (C vs Python, from default 1s):")
    for times, expect in ((1, 'Refresh: 2s'), (2, 'Refresh: 5s'),
                          (6, 'Refresh: 0.5s'), (7, 'Refresh: 1s')):
        c = _footer_after_cycles('c', 80, 30, times)
        p = _footer_after_cycles('p', 80, 30, times)
        ok = expect in c and expect in p and c == p
        print(f"  r x{times:<3}              : {'ok' if ok else 'FAIL'} "
              f"({expect}) C|{c}| P|{p}|")
        if not ok:
            failures += 1
    print("=" * 66)
    print("GOLDEN MATCH" if failures == 0 else f"{failures} DIFF ROWS")
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
