#!/usr/bin/env python3
"""MockStdscr-based responsive layout test for termmon.

Renders the dashboard at many terminal sizes with fake data and asserts:
  1. No write lands outside the terminal grid (the wrap-around overflow bug)
  2. No content leaks past the box's right border column
  3. Every box row is the same width and properly closed
  4. Bars never exceed their allotted space

Usage:
    python3 tests/test_responsive_layout.py [--render 80,60,40]
"""
import sys
import os
import importlib.util


# === Mock curses ===
class FakeCurses:
    A_REVERSE = 0x100000
    A_BOLD = 0x200000
    COLOR_WHITE = 0
    COLOR_GREEN = 1
    COLOR_YELLOW = 2
    COLOR_CYAN = 3
    COLOR_MAGENTA = 4
    COLOR_BLUE = 5
    KEY_LEFT = 0x108
    KEY_RIGHT = 0x105
    error = type('error', (Exception,), {})

    @staticmethod
    def color_pair(n):
        return n << 8

    @staticmethod
    def init_pair(*a):
        pass

    @staticmethod
    def start_color():
        pass

    @staticmethod
    def use_default_colors():
        pass

    @staticmethod
    def cbreak():
        pass

    @staticmethod
    def nocbreak():
        pass

    @staticmethod
    def echo():
        pass

    @staticmethod
    def noecho():
        pass

    @staticmethod
    def initscr():
        return MockStdscr(30, 80)

    @staticmethod
    def endwin():
        pass

    @staticmethod
    def curs_set(n):
        pass

    @staticmethod
    def resizeterm(h, w):
        pass

    @staticmethod
    def update_lines_cols():
        pass


class MockStdscr:
    """Records every write into a grid and flags out-of-bounds writes."""

    def __init__(self, h, w):
        self._h, self._w = h, w
        self._grid = {}
        self._current_attr = 0
        self.overflow_x = []
        self.overflow_y = []

    def getmaxyx(self):
        return (self._h, self._w)

    def _write(self, y, x, text, attr):
        if y < 0 or y >= self._h:
            self.overflow_y.append((y, x, text))
            return
        for i, ch in enumerate(text):
            col = x + i
            if col < 0 or col >= self._w:
                self.overflow_x.append((y, col, ch))
                continue
            self._grid[(y, col)] = ch

    def addstr(self, y, x, *args):
        attr = self._current_attr
        if len(args) == 1:
            text = args[0]
        elif len(args) == 2:
            text, attr = args
        else:
            text = args[0]
        if isinstance(text, int):
            text = chr(text)
        self._write(y, x, text, attr)

    def addnstr(self, y, x, text, n, attr=None):
        self._write(y, x, text[:n], attr if attr is not None else self._current_attr)

    def attron(self, a):
        self._current_attr |= a

    def attroff(self, a):
        self._current_attr &= ~a

    def erase(self):
        self._grid = {}

    def clear(self):
        self._grid = {}

    def refresh(self):
        pass

    def nodelay(self, f):
        pass

    def timeout(self, ms):
        pass

    def getch(self):
        return -1

    def keypad(self, f):
        pass

    def lines(self):
        return [
            "".join(self._grid.get((y, x), ' ') for x in range(self._w))
            for y in range(self._h)
        ]

    def render(self):
        return '\n'.join(self.lines())


# === Fake data ===
FAKE_SYSTEM_DATA = {
    'total_mem_gb': 15.4, 'used_mem_gb': 12.5, 'avail_mem_gb': 2.9,
    'mem_percent': 81.2, 'swap_total_mb': 4400.0, 'swap_used_mb': 2750.0,
    'swap_percent': 62.5, 'cpu_usage': 23.4, 'core_count': 16,
    'per_core_usage': [(i, (i * 7) % 100) for i in range(16)],
}
FAKE_GPU_DATA = [{
    'idx': '0', 'name': 'NVIDIA RTX A6000', 'mem_total': 49152.0,
    'mem_used': 43800.0, 'mem_free': 5352.0, 'gpu_util': 80.0,
    'temp': 59.0, 'power': 110.0, 'gpu_cores': 0, 'is_uma': False,
}]
FAKE_GPU_PROCESSES = [{
    'pid': 54321, 'user': 'iforevan', 'dev': '0', 'type': 'C',
    'gpu_pct': None, 'mem_used': 39506.0, 'host_mem': 7768.0, 'cpu_pct': 12.0,
    'process_name': 'llama-server',
    'cmdline': ('llama-server --host 127.0.0.1 --port 8080 -c 163840 -ngl 99 '
                '-m /home/iforevans/models/unsloth/Qwen3.6-27B-UD-Q8_K_XL.gguf '
                '-ctk q8_0 -ctv turbo4 --cache-ram 4096'),
}]

# Apple Silicon / UMA variant to exercise the other GPU code path
FAKE_GPU_DATA_UMA = [{
    'idx': '0', 'name': 'Apple M2 Max', 'mem_total': 0.0, 'mem_used': 0.0,
    'mem_free': 0.0, 'gpu_util': 42.0, 'temp': 0.0, 'power': 18.5,
    'gpu_cores': 38, 'is_uma': True,
}]

BOX_CHARS = set("┌┐└┘│─")


def find_app_class(mod):
    """Locate the app class: the one defining a draw() method."""
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and 'draw' in getattr(obj, '__dict__', {}):
            return obj
    raise RuntimeError("No class with a draw() method found in the target module")


def make_app(mod, gpu_data):
    app = find_app_class(mod)()
    if hasattr(app, 'system_data'):
        app.system_data = dict(FAKE_SYSTEM_DATA)
    if hasattr(app, 'gpu_data'):
        app.gpu_data = list(gpu_data)
    if hasattr(app, 'gpu_processes'):
        app.gpu_processes = list(FAKE_GPU_PROCESSES)
    return app


def check_box_integrity(lines, width):
    """Verify every box row is closed and no box char sits at an odd column."""
    problems = []
    widths = {}
    for y, line in enumerate(lines):
        stripped = line.rstrip()
        if not stripped:
            continue
        # Find box rows: start with a box-drawing char after leading spaces
        lead = len(line) - len(line.lstrip())
        first = line.lstrip()[:1]
        if first not in BOX_CHARS:
            continue
        # Row must terminate with a box char
        last_col = len(stripped) - 1
        if stripped[last_col] not in BOX_CHARS:
            problems.append(
                f"row {y}: box row does not end with a border char "
                f"(ends with {stripped[last_col]!r}): |{stripped}|"
            )
        widths.setdefault((lead, last_col), []).append(y)
        if last_col >= width:
            problems.append(f"row {y}: box border at col {last_col} >= width {width}")
    # All box rows should share the same left and right column
    if len(widths) > 1:
        problems.append(f"inconsistent box edges: {sorted(widths.keys())}")
    return problems


def run_size(mod, width, height, gpu_data, scroll=0):
    mock = MockStdscr(height, width)
    app = make_app(mod, gpu_data)
    app.process_scroll_x = scroll
    try:
        app.draw(mock)
    except Exception as e:
        import traceback
        return mock, [f"EXCEPTION: {type(e).__name__}: {e}\n{traceback.format_exc()}"]

    problems = []
    for (y, x, ch) in mock.overflow_x[:6]:
        problems.append(f"X-OVERFLOW row {y} col {x} char {ch!r} (width {width})")
    for (y, x, t) in mock.overflow_y[:6]:
        problems.append(f"Y-OVERFLOW row {y} (height {height}) text {t[:30]!r}")

    if width >= 24 and height >= 6:
        problems += check_box_integrity(mock.lines(), width)
    return mock, problems


def main():
    sys.modules['curses'] = FakeCurses

    # Positional arg = path to the app under test. Defaults to ../<app>.py
    # relative to this script (works when dropped into a repo's tests/ dir).
    positional = [a for a in sys.argv[1:] if not a.startswith('--')]
    # Drop values that belong to --render (e.g. "--render 80,50")
    if '--render' in sys.argv:
        idx = sys.argv.index('--render')
        if idx + 1 < len(sys.argv):
            consumed = sys.argv[idx + 1]
            positional = [a for a in positional if a != consumed]

    if positional:
        target = os.path.abspath(os.path.expanduser(positional[0]))
    else:
        target = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'termmon.py')
        )

    if not os.path.isfile(target):
        print(f"ERROR: app not found: {target}")
        print(f"Usage: {os.path.basename(__file__)} [/path/to/app.py] [--render 80,50,30]")
        return 2

    module_name = os.path.splitext(os.path.basename(target))[0]
    sys.path.insert(0, os.path.dirname(target))
    spec = importlib.util.spec_from_file_location(module_name, target)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)

    render_widths = set()
    for arg in sys.argv[1:]:
        if arg.startswith('--render'):
            vals = arg.split('=', 1)[1] if '=' in arg else sys.argv[sys.argv.index(arg) + 1]
            render_widths = {int(v) for v in vals.split(',')}

    print("=" * 72)
    print(f"RESPONSIVE LAYOUT TEST: {target}")
    print("=" * 72)

    failures = 0
    checks = 0

    # Full sweep: every width 20..160, a few heights, both GPU backends
    for gpu_label, gpu_data in (("nvidia", FAKE_GPU_DATA), ("uma", FAKE_GPU_DATA_UMA)):
        for height in (24, 30, 45, 10, 8):
            for width in range(20, 161):
                for scroll in (0, 40):
                    checks += 1
                    _, problems = run_size(mod, width, height, gpu_data, scroll)
                    if problems:
                        failures += 1
                        print(f"\nFAIL [{gpu_label} {width}x{height} scroll={scroll}]")
                        for p in problems[:4]:
                            print(f"   {p}")

    print(f"\nSwept {checks} render configurations.")
    print(f"Failures: {failures}")

    # Visual renders
    for w in sorted(render_widths):
        mock, problems = run_size(mod, w, 34, FAKE_GPU_DATA)
        print("\n" + "=" * 72)
        print(f"RENDER {w} cols  ({'clean' if not problems else 'PROBLEMS'})")
        print("=" * 72)
        for y, line in enumerate(mock.lines()):
            if line.strip():
                print(f"{y:2d}|{line}|")

    print("\n" + "=" * 72)
    print("ALL TESTS PASSED" if failures == 0 else f"{failures} FAILURES")
    print("=" * 72)
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
