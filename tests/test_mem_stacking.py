#!/usr/bin/env python3
"""Unit checks for the stacked Used/Cache/Free memory bar.

Covers:
  1. /proc/meminfo parsing (kB label == KiB, i.e. 1024 bytes per kB)
  2. Accounting: Used + Cache + Free == Total exactly, Cache includes
     SReclaimable (once — no double counting), Available kept separate,
     missing optional fields degrade instead of raising
  3. Largest-remainder rounding: segment widths always sum to the bar width
  4. Rendered Mem row draws a fully segmented bar (no gap) with legend,
     Total/Available and Swap rows present, and degrades on short screens
  5. Cross-check against the live /proc/meminfo on this machine

Usage:
    python3 tests/test_mem_stacking.py
"""
import os
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

failures = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def load_target(name):
    path = os.path.join(REPO, name) if not os.path.sep in name else name
    spec = importlib.util.spec_from_file_location(
        os.path.splitext(os.path.basename(path))[0], path
    )
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# SAMPLE = verbatim-shaped /proc/meminfo content (values in the "kB" label).
SAMPLE = """\
MemTotal:       129683104 kB
MemFree:          6322112 kB
MemAvailable:   84351232 kB
Buffers:          1213440 kB
Cached:          72658944 kB
SwapCached:            8192 kB
Active:          40123456 kB
SReclaimable:     4831232 kB
SUnreclaim:        913408 kB
"""


def main():
    # termmon imports curses at import time — reuse the mock from the
    # responsive suite before loading it.
    responsive = load_target(os.path.join(HERE, 'test_responsive_layout.py'))
    import sys
    sys.modules['curses'] = responsive.FakeCurses
    termmon = load_target(os.path.join(REPO, 'termmon.py'))
    T = termmon.TermMon

    print("=" * 66)
    print("STACKED MEMORY BAR TESTS")
    print("=" * 66)

    # --- 1. parsing ------------------------------------------------ #
    print("\nparse /proc/meminfo:")
    fields = T._parse_meminfo_kib(SAMPLE)
    check("MemTotal parsed as int", fields['MemTotal'] == 129683104)
    check("SReclaimable parsed", fields['SReclaimable'] == 4831232)
    check("empty text parses to {}", T._parse_meminfo_kib("") == {})

    # --- 2. accounting --------------------------------------------- #
    print("\naccounting (KiB maths, GiB = KiB / 1024**2):")
    stats = T._account_meminfo_gib(fields)
    want_cache = (1213440 + 72658944 + 4831232) / 1024 ** 2   # Buffers+Cached+SReclaimable
    want_used = (129683104 - 6322112 - (1213440 + 72658944 + 4831232)) / 1024 ** 2
    check("Cache = Buffers + Cached + SReclaimable",
          abs(stats['cache_mem_gb'] - want_cache) < 1e-9, f"{stats['cache_mem_gb']:.4f}")
    check("Used = Total - Free - Cache",
          abs(stats['used_mem_gb'] - want_used) < 1e-9, f"{stats['used_mem_gb']:.4f}")
    seg_sum = stats['used_mem_gb'] + stats['cache_mem_gb'] + stats['free_mem_gb']
    check("Used + Cache + Free == Total", abs(seg_sum - stats['total_mem_gb']) < 1e-9)
    check("Available reported separately (MemAvailable)",
          abs(stats['avail_mem_gb'] - 84351232 / 1024 ** 2) < 1e-9)

    no_avail = {k: v for k, v in fields.items() if k != 'MemAvailable'}
    est = T._account_meminfo_gib(no_avail)
    check("missing MemAvailable falls back (no raise)", est['avail_mem_gb'] > 0)
    sparse = T._account_meminfo_gib({'MemTotal': 1024 * 1024})
    check("missing optional fields -> Used=Total, no raise",
          abs(sparse['used_mem_gb'] - 1.0) < 1e-9 and sparse['cache_mem_gb'] == 0.0)
    skewed = T._account_meminfo_gib(
        {'MemTotal': 1000, 'MemFree': 900, 'Buffers': 200, 'Cached': 200, 'SReclaimable': 100})
    check("counter skew clamped (Used >= 0, invariant holds)",
          skewed['used_mem_gb'] == 0.0
          and abs(skewed['free_mem_gb'] + skewed['cache_mem_gb'] - 1000 / 1024 ** 2) < 1e-9)

    # --- 3. rounding: widths must sum to the bar width -------------- #
    print("\nlargest-remainder rounding:")
    cases = [
        ((0, 0, 0), 0), ((0, 0, 0), 10), ((5, 0, 0), 7), ((1, 1, 1), 2),
        ((1, 1, 1), 20), ((100, 0.0001, 0), 5), ((0.3, 0.3, 0.4), 20),
        ((12.5, 2.4, 0.5), 13), ((128.4, 244.2, 4.1), 20), ((1, 2, 3), 1),
        ((0.5, 0.5, 0.5), 16), ((1024.0, 0.0, 0.0), 20),
    ]
    cases += [((i * 7.13, i * 3.71, (100 - i) * 1.97), w)
              for i in range(1, 37) for w in (5, 8, 13, 20, 37, 60)]
    ok_sum = ok_nonneg = ok_prop = True
    for vals, width in cases:
        w = T._stacked_segment_widths(vals, width)
        total = sum(v for v in vals if v > 0)
        if sum(w) != (width if total > 0 else 0):
            ok_sum = False
        if any(x < 0 for x in w):
            ok_nonneg = False
        if total > 0 and width > 0:
            for v, got in zip(vals, w):  # every width within 1 of its ideal share
                if abs(got - v / total * width) >= 1.0:
                    ok_prop = False
    check("segment widths sum exactly to bar width", ok_sum)
    check("no negative segment widths", ok_nonneg)
    check("each segment within 1 col of its ideal share", ok_prop)
    check("all-zero values -> zero widths (bar drawn empty)",
          T._stacked_segment_widths((0, 0, 0), 10) == [0, 0, 0])

    # --- 4. rendered section invariants ------------------------------ #
    print("\nrendered Mem row:")
    sysdata = {
        'total_mem_gb': 128.0, 'used_mem_gb': 17.8, 'cache_mem_gb': 90.2,
        'free_mem_gb': 20.0, 'avail_mem_gb': 100.5, 'mem_percent': 13.9,
        'swap_total_mb': 4400.0, 'swap_used_mb': 2750.0, 'swap_percent': 62.5,
    }
    app = T()
    for width, label in ((118, 'wide'), (58, 'narrow')):
        scr = responsive.MockStdscr(34, width)
        app._box_width = width - 2
        app._draw_memory_section(scr, 2, 1, 34, {'system_data': sysdata}, width - 2)
        lines = scr.lines()
        mem_line = next((l for l in lines if 'Mem:' in l), "")
        blocks = mem_line[mem_line.find('Mem:') + 4:mem_line.find('Used')
                          if 'Used' in mem_line else None]
        filled = blocks.count('█') if blocks else 0
        check(f"{label}: bar fully segmented (no gap)", filled >= 5
              and '░' not in blocks.replace(mem_line[-1], ''), f"blocks={filled}")
        joined = "\n".join(lines)
        check(f"{label}: legend shows Used/Cache/Free", all(k in joined for k in ('Used', 'Cache', 'Free')))
        check(f"{label}: Total + Available on their own row", 'Total' in joined and 'Available' in joined)
        check(f"{label}: swap row kept", 'Swap' in joined)
    # Short terminal: section must degrade without writing off-screen.
    scr = responsive.MockStdscr(8, 78)
    app._draw_memory_section(scr, 2, 1, 8, {'system_data': sysdata}, 76)
    check("short terminal: no off-screen writes", not scr.overflow_y and not scr.overflow_x)

    # Legacy sysdata without the new keys still renders (Free absorbs residual).
    u, c, f, t = T._mem_segments({'total_mem_gb': 15.4, 'used_mem_gb': 12.5})
    check("legacy keys: invariant forced", abs(u + c + f - t) < 1e-9 and t == 15.4)
    u, c, f, t = T._mem_segments({})
    check("empty sysdata -> all zeros", (u, c, f, t) == (0.0, 0.0, 0.0, 0.0))

    # --- 5. live /proc/meminfo cross-check --------------------------- #
    if termmon._IS_LINUX and os.path.exists('/proc/meminfo'):
        print("\nlive /proc/meminfo:")
        live = T._read_meminfo_kib()
        check("live read succeeds", live is not None and 'MemTotal' in live)
        ls = T._account_meminfo_gib(live)
        check("live Used+Cache+Free == Total",
              abs(ls['used_mem_gb'] + ls['cache_mem_gb'] + ls['free_mem_gb'] - ls['total_mem_gb']) < 1e-9)
        check("live numbers in sane ranges",
              0 <= ls['used_mem_gb'] <= ls['total_mem_gb']
              and 0 <= ls['avail_mem_gb'] <= ls['total_mem_gb']
              and ls['cache_mem_gb'] >= 0)
        collected = T._collect_memory_stats()
        check("_collect_memory_stats emits all stacked keys",
              all(k in collected for k in ('total_mem_gb', 'used_mem_gb', 'cache_mem_gb',
                                           'free_mem_gb', 'avail_mem_gb')))
        check("collected invariant holds",
              abs(collected['used_mem_gb'] + collected['cache_mem_gb']
                  + collected['free_mem_gb'] - collected['total_mem_gb']) < 1e-9)
    else:
        print("\nlive /proc/meminfo: skipped (not Linux)")

    print("\n" + "=" * 66)
    print("ALL STACKED-MEMORY TESTS PASSED" if not failures else f"{len(failures)} FAILURES: {failures}")
    print("=" * 66)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
