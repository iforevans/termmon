#!/usr/bin/env python3
"""Real-PTY responsive test for termmon using actual curses + pyte.

Spawns termmon in a genuine pseudo-terminal with real curses rendering,
resizes the PTY live (SIGWINCH), and parses the output with pyte to verify
the rendered screen is clean at every size.

This catches what MockStdscr cannot: real curses line-wrapping behaviour,
scroll-on-last-cell, and stale content left over after a resize.

Usage:
    python3 tests/test_pty_layout.py            # run all sizes
    python3 tests/test_pty_layout.py --show 80  # print the 80-col screen
"""
import os
import sys
import pty
import time
import select
import signal
import struct
import fcntl
import termios

import pyte

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_SETUP = r'''
import sys, threading
sys.path.insert(0, REPO_PATH)
import termmon

app = termmon.TermMon()

# Freeze deterministic data and stop the background collector from
# overwriting it, so the screen is reproducible.
app.system_data = {
    'total_mem_gb': 15.4, 'used_mem_gb': 12.5, 'avail_mem_gb': 2.9,
    'mem_percent': 81.2, 'swap_total_mb': 4400.0, 'swap_used_mb': 2750.0,
    'swap_percent': 62.5, 'cpu_usage': 23.4, 'core_count': 16,
    'per_core_usage': [(i, (i * 7) % 100) for i in range(16)],
}
app.gpu_data = [{
    'idx': '0', 'name': 'NVIDIA RTX A6000', 'mem_total': 49152.0,
    'mem_used': 43800.0, 'mem_free': 5352.0, 'gpu_util': 80.0,
    'temp': 59.0, 'power': 110.0, 'gpu_cores': 0, 'is_uma': False,
}]
app.gpu_processes = [{
    'pid': 54321, 'user': 'iforevan', 'dev': '0', 'type': 'C',
    'gpu_pct': None, 'mem_used': 39506.0, 'host_mem': 7768.0, 'cpu_pct': 12.0,
    'process_name': 'llama-server',
    'cmdline': ('llama-server --host 127.0.0.1 --port 8080 -c 163840 -ngl 99 '
                '-m /home/iforevans/models/unsloth/Qwen3.6-27B-UD-Q8_K_XL.gguf '
                '-ctk q8_0 -ctv turbo4 --cache-ram 4096'),
}]

# Neuter the stats collector and the psutil seeding loop so the frozen
# data survives and startup is instant.
app._stats_updater_thread = lambda: None
termmon.psutil.process_iter = lambda *a, **k: []

app.run()
'''

BOX_CHARS = set("┌┐└┘│─")


def _child_script_path():
    """Materialise the child harness to a temp file (avoids exec/format quirks)."""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), 'termmon_pty_child.py')
    with open(path, 'w') as fh:
        fh.write(f"REPO_PATH = {REPO!r}\n")
        fh.write(FAKE_SETUP)
    return path


def spawn(cols, rows):
    """Fork termmon into a real PTY at the given size. Returns (pid, fd)."""
    script = _child_script_path()
    pid, fd = pty.fork()
    if pid == 0:
        os.environ['TERM'] = 'xterm-256color'
        os.environ['LANG'] = 'en_US.UTF-8'
        os.environ['PYTHONIOENCODING'] = 'utf-8'
        os.execv(sys.executable, [sys.executable, script])
        os._exit(1)
    set_size(fd, cols, rows)
    return pid, fd


def set_size(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))


def drain(fd, seconds):
    """Read whatever the child emits for `seconds`."""
    buf = b''
    deadline = time.time() + seconds
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            buf += data
    return buf


def check_screen(screen, cols, rows, label):
    """Verify the rendered grid has no wrap-around / overflow artifacts."""
    problems = []
    lines = screen.display

    box_rows = []
    for y, line in enumerate(lines):
        if len(line) > cols:
            problems.append(f"{label}: row {y} longer than {cols} cols ({len(line)})")
        stripped = line.rstrip()
        if not stripped:
            continue
        lead = len(line) - len(line.lstrip())
        if line.lstrip()[:1] in BOX_CHARS:
            box_rows.append((y, lead, len(stripped) - 1, stripped))

    # All box rows must share identical left/right columns and be closed.
    edges = {(l, r) for (_, l, r, _) in box_rows}
    if len(edges) > 1:
        problems.append(f"{label}: box edges inconsistent {sorted(edges)}")
    for (y, lead, last, stripped) in box_rows:
        if stripped[-1] not in BOX_CHARS:
            problems.append(f"{label}: row {y} unterminated box: |{stripped}|")
        if last >= cols:
            problems.append(f"{label}: row {y} border at col {last} >= {cols}")

    if not box_rows:
        problems.append(f"{label}: no box rows rendered at all")

    return problems


def render(cols, rows, resize_from=None, show=False):
    """Run termmon at (cols, rows), optionally after a live resize."""
    if resize_from:
        pid, fd = spawn(*resize_from)
        drain(fd, 1.5)                 # let it draw at the original size
        set_size(fd, cols, rows)       # SIGWINCH -> app must re-layout
        os.kill(pid, signal.SIGWINCH)
        data = drain(fd, 2.0)
        label = f"{resize_from[0]}x{resize_from[1]}->{cols}x{rows}"
    else:
        pid, fd = spawn(cols, rows)
        data = drain(fd, 2.0)
        label = f"{cols}x{rows}"

    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except OSError:
        pass
    os.close(fd)

    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(data.decode('utf-8', errors='replace'))

    problems = check_screen(screen, cols, rows, label)
    if show:
        print(f"--- {label} ---")
        for y, line in enumerate(screen.display):
            if line.strip():
                print(f"{y:2d}|{line}|")
    return problems, screen


def main():
    show_width = None
    if '--show' in sys.argv:
        show_width = int(sys.argv[sys.argv.index('--show') + 1])

    if show_width:
        problems, _ = render(show_width, 34, show=True)
        print("CLEAN" if not problems else f"PROBLEMS: {problems}")
        return 0 if not problems else 1

    print("=" * 66)
    print("REAL PTY LAYOUT TEST (actual curses + pyte)")
    print("=" * 66)

    failures = 0

    # 1. Static sizes
    for cols, rows in [(120, 40), (100, 34), (80, 30), (70, 30),
                       (60, 28), (50, 26), (40, 26), (34, 24), (28, 20)]:
        problems, _ = render(cols, rows)
        status = "clean" if not problems else f"FAIL ({len(problems)})"
        print(f"  static {cols:>3}x{rows:<3} : {status}")
        for p in problems[:3]:
            print(f"      {p}")
        failures += len(problems)

    # 2. Live shrink — the exact bug reported: resize smaller than content
    print("\n  live resizes (the reported bug):")
    for start, end in [((120, 40), (60, 28)), ((120, 40), (40, 26)),
                       ((100, 34), (30, 22)), ((80, 30), (45, 24)),
                       ((60, 28), (110, 36))]:
        problems, _ = render(end[0], end[1], resize_from=start)
        status = "clean" if not problems else f"FAIL ({len(problems)})"
        print(f"  {start[0]}x{start[1]} -> {end[0]}x{end[1]} : {status}")
        for p in problems[:3]:
            print(f"      {p}")
        failures += len(problems)

    print("\n" + "=" * 66)
    print("ALL PTY TESTS PASSED" if failures == 0 else f"{failures} PROBLEMS FOUND")
    print("=" * 66)
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
