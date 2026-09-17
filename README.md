# termmon - Terminal System Monitor

A unified terminal-based system monitor combining `htop` (system RAM/CPU) and `nvidia-smi` (GPU/VRAM) functionality into a single dashboard.

Originally created to solve the problem of monitoring CPU/system RAM/swap and GPU/VRAM usage from one window while testing local AI models on an RTX 3090/24GB, now running on RTX A6000/48GB.

## Features

- **System Memory**: One stacked RAM bar with non-overlapping Used / Cache / Free segments (GiB legend inline), so the ~90 GB hiding in buffers/file-cache is visible; Total + the kernel's Available shown separately, plus a swap bar
- **CPU Usage**: Overall and per-core real-time utilization
- **CPU Temperature**: Package temperature (°C) via psutil sensor detection
- **NVIDIA GPU Monitoring**: VRAM usage, GPU utilization, temperature, power draw
- **GPU Process Tracking**: Top 5 active GPU compute processes (nvtop-style)
  - Shows PID, user, GPU memory, host memory, and command
  - Sorted by VRAM usage (descending)
- **LLM Inference Telemetry**: auto-detects local llama.cpp/vLLM/ollama servers and shows live decode rate (tok/s), phase (PREFILL/DECODE), MTP flag, a rate sparkline, and context fill — per model. Hides itself when nothing is serving
- **Color-coded progress bars**: Visual feedback for resource usage
- **Fully responsive layout**: Reflows to any terminal size (nvtop-style) — resize freely, content never wraps or overwrites itself. Degrades gracefully from ultra-wide down to ~28 columns
- **Auto-refresh**: Updates every 2 seconds
- **Cross-platform**: Linux (NVIDIA GPUs) and macOS (Apple Silicon GPUs)
- **Pure Python**: Minimal external dependencies

## Requirements

- Python 3.9+
- `psutil` (`pip3 install psutil`)

### Linux
- NVIDIA drivers with `nvidia-smi` (for GPU monitoring)

### macOS (Apple Silicon)
- `macmon` — **required** for accurate GPU utilization/power without sudo
  - Install: `cargo install macmon` (requires Rust toolchain)
  - Or build from source: `git clone https://github.com/vladkens/macmon && cd macmon && cargo build --release`
  - Place binary in your `PATH` (e.g., `cp target/release/macmon ~/bin/`)
- Fallbacks (if `macmon` unavailable): `socpwrbud` (no sudo, archived) or `powermetrics` (requires sudo on macOS 13+)

## Installation

```bash
# Clone or copy termmon.py anywhere
cd ~/dev/termmon

# Run directly
python3 termmon.py

# Or create a launcher
ln -s ~/dev/termmon/termmon.py ~/bin/termmon
termmon
```

## Usage

Simply run `termmon` and watch your system resources in real-time.

### Keybindings

- `q` - Quit
- `r` - Manual refresh (immediate update)
- `h` - Show help
- `←` / `→` - Horizontally scroll the GPU process Command column

**Auto-refresh**: Every 2 seconds (no action needed)

## Display Layout

The layout is **fully responsive** — it reflows to whatever size your terminal is, nvtop-style. Both mockups below are real rendered output.

At 80 columns, the RAM bar is stacked (Used/Cache/Free) with the legend inline, cores run in two columns, and GPU Util/VRAM share a row:

```
 termmon 1.19.0 - System Monitor | 14:32:07 | q:quit r:refresh h:help
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ SYSTEM MEMORY                                                              │
 │────────────────────────────────────────────────────────────────────────────│
 │ Mem: ████████████████ Used:  12.5 GiB | Cache:   2.4 GiB | Free:   0.5 GiB │
 │ Total:   15.4 GiB | Available:    2.9 GiB                                  │
 │ Swap:████████████░░░░░░░░  2.7/ 4.3GB  62.5%                               │
 └────────────────────────────────────────────────────────────────────────────┘
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ CPU (8 cores, overall  15.1%)                                              │
 │────────────────────────────────────────────────────────────────────────────│
 │ Core 0: ████░░░░░░░░░░░░░░░░░  23.4%  Core 4: ██░░░░░░░░░░░░░░░░░░░   9.8% │
 │ Core 1: ██████░░░░░░░░░░░░░░░  31.0%  Core 5: ███░░░░░░░░░░░░░░░░░░  15.2% │
 │ Core 2: ██░░░░░░░░░░░░░░░░░░░  12.8%  Core 6: █░░░░░░░░░░░░░░░░░░░░   4.3% │
 │ Core 3: █████░░░░░░░░░░░░░░░░  24.5%  Core 7: ░░░░░░░░░░░░░░░░░░░░░   0.0% │
 └────────────────────────────────────────────────────────────────────────────┘
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ GPUs                                                                       │
 │────────────────────────────────────────────────────────────────────────────│
 │ GPU 0: NVIDIA RTX A6000 | Util: 80.0% | Temp: 59°C | Power: 110.0W         │
 │ Util:████████████████░░░░   80.0%  VRAM:█████████████████░░░  42.8GB/48.0G │
 └────────────────────────────────────────────────────────────────────────────┘
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ GPU PROCESSES  ←/→ scroll 0                                                │
 │────────────────────────────────────────────────────────────────────────────│
 │ PID     USER     DEV TYPE   GPU  GPU MEM    CPU HOST MEM Command           │
 │────────────────────────────────────────────────────────────────────────────│
 │ 54321   iforevan 0   C       --   39506M  12.0%    7768M llama-server --hos│
 └────────────────────────────────────────────────────────────────────────────┘
 Refresh: 2s | q:quit r:refresh h:help ←/→:process scroll
```

Shrink to 50 columns and the box narrows with the terminal, bars shorten, the GPU title drops segments, and Util/VRAM take their own rows — no wrapping, no overwritten content:

```
 termmon 1.19.0 | 14:32:07
 ┌──────────────────────────────────────────────┐
 │ SYSTEM MEMORY                                │
 │──────────────────────────────────────────────│
 │ Mem: █████████████ U 12.5 | C  2.4 | F  0.5G │
 │ Total:   15.4 GiB | Available:    2.9 GiB    │
 │ Swap:███████████░░░░░░░░  2.7/ 4.3GB  62.5%  │
 └──────────────────────────────────────────────┘
 ┌──────────────────────────────────────────────┐
 │ CPU (8 cores, overall  15.1%)                │
 │──────────────────────────────────────────────│
 │ Core 0: █░░░░░  23.4%  Core 4: █░░░░░   9.8% │
 │ Core 1: █░░░░░  31.0%  Core 5: █░░░░░  15.2% │
 │ Core 2: █░░░░░  12.8%  Core 6: █░░░░░   4.3% │
 │ Core 3: █░░░░░  24.5%  Core 7: ░░░░░░   0.0% │
 └──────────────────────────────────────────────┘
 ┌──────────────────────────────────────────────┐
 │ GPUs                                         │
 │──────────────────────────────────────────────│
 │ GPU 0: NVIDIA RTX A6000 | Util: 80.0%        │
 │ Util:████████████████░░░░   80.0%            │
 │ VRAM:███████████████░░  42.8GB/48.0G  89.1%  │
 └──────────────────────────────────────────────┘
 ┌──────────────────────────────────────────────┐
 │ GPU PROCESSES  ←/→ scroll 0                  │
 │──────────────────────────────────────────────│
 │ PID     USER     DEV TYPE   GPU  GPU MEM    C│
 └──────────────────────────────────────────────┘
 q:quit r:refresh h:help ←/→:scroll
```

Below ~24 columns termmon shows a "Terminal too small" notice rather than rendering a mangled dashboard.

## Color Scheme

- 🟢 **Green**: Used RAM (stacked bar segment) / system memory
- 🔵 **Cyan**: RAM held as Cache (buffers + page cache + reclaimable slab; includes tmpfs/shared pages, so not fully reclaimable) / CPU usage
- ⚪ **White**: Free RAM (stacked bar segment)
- 🟡 **Yellow**: Swap usage
- 🟣 **Magenta**: VRAM usage
- 🔴 **Red**: Error messages

## Use Cases

### Local LLM Inference
- Monitor VRAM usage while running llama.cpp, text-generation-webui, etc.
- Track GPU utilization during inference
- Watch power draw and temperature for thermal management
- See which processes are using GPU memory

### System Diagnostics
- Quick overview of system health
- Identify memory pressure or swap usage
- Monitor per-core CPU load for debugging

### Development
- Keep an eye on resources while building/training models
- Single-window monitoring (no more multiple terminals!)

## Technical Details

- **Built with**: Python + curses + psutil
- **Dependencies**: `psutil` (cross-platform system stats)
- **Platform detection**: Auto-detects Linux (NVIDIA) or macOS (Apple Silicon)
- **GPU Detection**: `nvidia-smi` on Linux; `macmon` (preferred), `socpwrbud`, or `powermetrics` + `system_profiler` on macOS
- **macOS GPU**: Three-tier fallback — `macmon` (actively maintained, no sudo), `socpwrbud` (archived, no sudo), `powermetrics` (requires sudo on macOS 13+)
- **CPU Stats**: psutil (cross-platform, replaces `/proc/stat`)
- **Memory Stats**: Linux reads `/proc/meminfo` directly — `Cache = Buffers + Cached + SReclaimable`, `Used = Total − Free − Cache` (so Used + Cache + Free == Total); `MemAvailable` is shown beside the bar, never as a segment. Other platforms use psutil (its Linux `.cached` already folds in SReclaimable, so mixing both would double-count)
- **Process Info**: psutil.Process() (cross-platform, replaces `/proc/[pid]`)
- **Refresh Rate**: 2 seconds (configurable in source)
- **Layout**: Adaptive box width (`min(120, terminal_width - 2)`), computed bar widths, and per-section two-column/single-column breakpoints. All drawing goes through a single bounds-clipping `_safe_addstr()` helper
- **Resize handling**: `SIGWINCH` triggers a kernel `TIOCGWINSZ` query (not the stale curses `getmaxyx()` cache), then `resizeterm()` + `clear()`

## Testing

Three harnesses cover the memory bar and layout; all must be clean after any change to the draw path.

```bash
# MockStdscr sweep — 2,820 configs (widths 20-160 x 5 heights x NVIDIA/UMA x 2 scroll offsets)
python3 tests/test_responsive_layout.py

# Stacked Used/Cache/Free accounting, largest-remainder rounding, live meminfo
python3 tests/test_mem_stacking.py

# Render specific widths for visual inspection
python3 tests/test_responsive_layout.py --render 80,50,30

# Real PTY + curses, parsed with pyte — 9 static sizes and 5 live SIGWINCH resizes
python3 tests/test_pty_layout.py          # requires: pip3 install pyte
python3 tests/test_pty_layout.py --show 80
```

The mock suite asserts no write lands outside the terminal grid and that box edges stay consistent. The PTY suite is the one that catches resize bugs — a mock harness never fires `SIGWINCH`, so it cannot detect stale curses geometry.

## Development Timeline

### v1.20.0 (2026-09-17)
- **LLM INFERENCE section**: a live decode-rate panel for locally served models, inspired by llm-visuals (DingoOz). TermMon now auto-discovers llama.cpp-family inference servers (ports with a LISTEN socket owned by a `llama-server`/vLLM/ollama/sglang process, via `/proc/net/tcp{,6}` inode matching), polls `/slots` + `/props` each refresh, and draws per model: `name :port PHASE rate tok/s [MTP]` with an auto-scaled block sparkline of recent instantaneous rates, plus a context-fill bar (`ctx used/total`). The panel draws nothing when no server is detected.
- **Honest rate math**: token deltas per poll interval go into a sliding 4 s window; tokens/sec is the window's token sum over its time span — per-poll rates flicker under MTP because speculative decoders land tokens in bursts. Request boundary (`id_task`) changes re-anchor counters instead of producing jump artifacts; negative deltas clamp. Phase is derived from which counter moves: `n_prompt_tokens_processed` climbing = PREFILL, `n_decoded` climbing = DECODE.
- **Robust port handling**: non-HTTP responders (raw debug sockets) are muted instantly via a `NOT_HTTP` sentinel; unreachable ports mute after 5 consecutive failures; mutes clear on the periodic (10 s) port rescan so servers rebound on the same port are found. TermMon excludes its own PID from discovery (cmdline keyword matches would otherwise catch development/test invocations). `next_token` is normalised from dict-or-list — the buun llama.cpp fork serialises it as a one-element list.
- **Threading**: collection runs in the stats thread after GPU data (a blocked HTTP poll cannot delay resource stats), never under `_stats_lock` during requests; results join the atomic swap. Colour pair `COLOR_INFER` (green) highlights active rates.
- Tests: responsive sweep now renders the inference panel across all widths (5,640 configs, was 2,820); PTY suite passes static + live resizes. Live-verified against llama-server (buun fork) on :8080 at 64 tok/s mid-generation.

### v1.19.0 (2026-09-11)
- **Stacked Used/Cache/Free RAM bar**: the single system-memory bar is now one stacked bar of three non-overlapping, colour-coded segments — 🟢 Used (green), 🔵 Cache (cyan), ⚪ Free (white) — with a colour-keyed legend giving each amount in GiB (inline beside the bar when it fits, otherwise on its own row), plus a `Total: … | Available: …` row and the swap bar. The point: memory held for mmap/file caching is now visible. A llama-server host could previously headline ~18 GiB "used" while ~90 GiB sat in buffers/cache.
- **Accounting from `/proc/meminfo` directly (Linux)**: `Total=MemTotal`, `Free=MemFree`, `Cache=Buffers+Cached+SReclaimable`, `Used=Total−Free−Cache` (values in the file's "kB" label are KiB, 1024 bytes each), so `Used + Cache + Free == Total` exactly. `Available=MemAvailable` is the kernel's overlapping estimate — displayed separately, never a fourth segment; `Total − Available` is deliberately *not* Used because it would overlap Cache. psutil is bypassed on Linux (its `virtual_memory().cached` already folds SReclaimable in — reusing it here would double-count); macOS/psutil fallback forces the same invariant with Cache as the remainder. Missing optional fields and transient `/proc` read failures degrade to sane zeros/fallbacks instead of crashing the refresh loop; memory collection is isolated from swap/CPU collection.
- **Exact-fit segments**: new `_stacked_segment_widths()` uses largest-remainder (Hare quota) rounding so segment widths always sum to exactly the bar width — no gap, no overlap — at every terminal size. Legend/summary/swap rows degrade by width (four label templates) and by height (drop swap, then summary, then legend on very short terminals); `MEM_TWO_COL_MIN` retired. New tests: `tests/test_mem_stacking.py`.

### v1.18.0 (2026-08-27)
- **Consistent GPU section header**: the GPU section title is now a plain `GPUs` label (backend name when no data), matching the clean style of the other section titles. Per-GPU stats moved from the right-aligned `Temp: ... Power: ...` block onto each GPU's own detail line: `GPU 0: NVIDIA RTX A6000 | Util: 80.0% | Temp: 59°C | Power: 110.0W`, with segments dropped right-to-left (Power, then Temp) for narrow boxes. Cuts one row per GPU at narrow widths and removes the duplicated name between the title and the detail line.
- **Removed `GPU_HEADER_TWO_COL_MIN` breakpoint**: no longer needed now that temp/power live inline on the GPU detail line.

### v1.17.0 (2026-08-20)
- **CPU temperature monitoring**: Added `_get_cpu_temp()` which reads `psutil.sensors_temperatures()` and prefers the `coretemp` sensor (Intel package reading), falling back to `cpu_thermal`, `k10temp`, `zenpower`, then any available sensor group. Returns the max reading or `None` when no sensor is available (most VMs, ARM Macs). Displayed in the CPU section title as `CPU: 16 Cores | Usage: 0.8% | Temp: 47°C`, with three-tier fallback for narrow terminals.

### v1.16.4 (2026-08-05)
- **GPU collection error logging**: `_stats_updater_thread` now logs GPU data collection failures via `logger.error()` instead of silently swallowing exceptions. Persistent nvidia-smi failures (driver crash, permission loss) are now visible in logs.
- **Removed dead startup CPU seed loop**: `run()` no longer iterates all system processes to seed `cpu_percent`. The targeted `_seed_cpu_percent()` calls inside `_get_gpu_processes_nvidia` and `_get_gpu_processes_apple` already seed only the PIDs that appear in the GPU table, eliminating O(N) wasted psutil calls at startup.

### v1.16.3 (2026-08-05)
- **Help popup CPU spin fix**: `_show_help()` used `nodelay(False)`/`nodelay(True)` to toggle blocking input, but `run()` sets up input via `stdscr.timeout(50)`. The `nodelay(True)` restore permanently overrode the 50ms timeout, so after dismissing the help popup `getch()` returned -1 instantly every iteration, spinning the main loop at ~100% CPU. Replaced with `timeout(-1)` (block) / `timeout(50)` (restore). Verified empirically: idle 2.0% → 99.5% before fix, 2.0% → 2.0% after.
- **DRY refresh interval**: main loop's draw trigger used a hardcoded `2` instead of the `REFRESH_INTERVAL` constant, so changing the constant would desync the main loop from the background thread.

### v1.16.1 (2026-07-28)
- **Clean terminal exit**: Added `clear` and cursor-visible escape (`\033[?25h`) after `curses.endwin()` so the screen is clean and the cursor is visible when termmon exits.

### v1.16.0 (2026-07-26)
- **Fully responsive layout**: the dashboard now reflows to any terminal size instead of wrapping around and overwriting itself when the window is made narrower than the content (nvtop-style behaviour).
  - **Adaptive box width**: replaced `max(80, min(120, width * 0.85))` with `min(120, width - 2)`. The old 80-column floor meant that at 60 columns the app still drew an 80-wide box, and every write past the right edge wrapped onto the next line and clobbered it. The box now fills the terminal with a 1-char margin.
  - **`_safe_addstr()` bounds guard**: every single write in the draw path goes through one clipping helper that respects both the terminal edge and the box's right border (`max_x`). Zero raw `addstr` calls remain outside the helper.
  - **Adaptive bar widths**: `_bar_width()` sizes progress bars from the space actually available (measured overhead, not estimates), clamped to `[5, 20]`, instead of a hardcoded `BAR_WIDTH = 20`.
  - **Layout mode switching**: Memory (Mem/Swap), CPU (per-core), and GPU (Util/VRAM, name/temp+power) each drop from two columns to stacked single-column rows at their own measured breakpoints. Info strings shorten progressively so values are never clipped mid-number.
  - **Removed hardcoded column offsets**: the GPU section's `left_col_width = 43` and the memory section's `+ 16` right-column offset are now computed from real content lengths.
  - **True resize handling (the actual root cause)**: curses caches `LINES`/`COLS` at `initscr()`, so `stdscr.getmaxyx()` returns the *old* geometry right after `SIGWINCH` — feeding it back into `resizeterm()` was a no-op and the app kept rendering at the previous width. `_true_terminal_size()` now queries the kernel via `TIOCGWINSZ` and `_apply_resize()` resizes then clears stale cells.
  - **Graceful degradation**: renders cleanly down to ~28 columns; below 24 shows a "Terminal too small" notice. The help popup shrinks to fit and drops lines rather than overflowing.
- **Test harnesses added**: `tests/test_responsive_layout.py` (MockStdscr, sweeps widths 20–160 × 5 heights × 2 GPU backends × 2 scroll offsets = 2,820 configs, asserts no out-of-bounds write and consistent box edges) and `tests/test_pty_layout.py` (real PTY + `pyte`, verifies actual curses output at 9 static sizes and 5 **live** resizes). Both pass with zero failures; the mock suite reports 2,592 failures against v1.15.0, confirming it detects the original bug.

### v1.15.0 (2026-07-24)
- **Fix GPU util sentinel**: `_apple_gpu_util_*` tiers now return `Optional[Tuple]` — `None` means "tool unavailable", `(0.0, 0.0)` means "ran but GPU was genuinely idle". Old code used `!= (0.0, 0.0)` which skipped real idle readings. Added `try/except ValueError` around float parsing in powermetrics tier for malformed output.

### v1.14.0 (2026-07-24)
- **macOS GPU metadata consolidation**: Merged `_apple_gpu_model()` and `_apple_gpu_cores()` (which each ran `system_profiler`) into a single cached `_apple_gpu_metadata` property with `_detect_apple_gpu_metadata()` helper. Reduces subprocess calls from 2 to 0 on repeated refreshes (cached after first call).
- **Process table header caching**: Replaced per-draw `_gpu_process_fixed_header()` method with static class constant `_GPU_PROCESS_FIXED_HEADER` and `_GPU_PROCESS_FIXED_HEADER_LEN`. Eliminates string construction + f-string parsing on every render frame.
- **Dead code removal**: Removed `_gpu_process_table_row()` method (unreachable — the scrolling draw path uses `_draw_scrolled_process_line` instead).

### v1.13.0 (2026-07-24)
- **macOS GPU core count fix**: `_apple_gpu_cores()` now parses `system_profiler SPDisplaysDataType -json` (`sppci_cores`) instead of the non-existent `hw.gpus` sysctl. GPU core count was always 0 on Apple Silicon.
- **CPU percent enrichment fix**: `psutil.Process.cpu_percent(interval=None)` returns 0.0 on first call per Process object. Added `_seed_cpu_percent(pids)` that seeds all candidate PIDs before the enrichment pass, so CPU % columns show real values instead of 0.0% on every refresh.
- **Clean Ctrl+C exit**: Added explicit `except KeyboardInterrupt` in `run()` and `curses.curs_set(1)` in cleanup to restore cursor visibility.
- **Persistent thread pool**: Replaced per-refresh `ThreadPoolExecutor` creation with a persistent `self._gpu_data_executor` created in `__init__` and shut down on exit. Eliminates thread pool creation/destruction overhead every 2-second refresh cycle.

### v1.12.0 (2026-07-20)
- **cpu_percent seeding**: All processes are seeded on startup so the first `cpu_percent(interval=None)` read returns real values instead of 0
- **Structured logging**: Replaced swallowed exception in stats thread with `logging.error()` — collection failures are now visible in logs
- **Consistent timing**: Main loop switched from `time.time()` to `time.monotonic()`, matching the background thread. Eliminates drift if the system clock jumps.
- **Cleaner startup**: Removed redundant second event fire + dead sleep at startup. Single event + 0.5s sleep is sufficient.
- **Startup tool detection**: Replaced `subprocess.run(['which', ...])` with `shutil.which()`, avoiding unnecessary process forks.

### v1.11.0 (2026-07-20)
- **Thread safety: eliminated race condition in draw()**
  - `draw()` now takes a thread-safe snapshot under `_stats_lock` and passes it through `_draw_frame` → all `_draw_*` methods as a `snapshot` dict
  - No instance attribute mutation during draw — the background thread and draw never touch the same objects concurrently
- **Thread safety: eliminated global mutable `BOX_WIDTH`**
  - `BOX_WIDTH` replaced with `self._box_width`, computed once per frame in `_draw_frame` and passed as `bw` to all draw methods
  - No more `global BOX_WIDTH` — all draw methods are now pure readers of their arguments
- **Background thread auto-refresh**
  - `_stats_updater_thread` now drives itself on a `REFRESH_INTERVAL` timer via `time.monotonic()`
  - Stats stay fresh even when the main loop is blocked (e.g., help popup `getch()`)
  - The event still provides an immediate-trigger path (e.g., `r` key, terminal resize)

### v1.10.1 (2026-07-10)
- **README display layout mockup updated**: ASCII mockup now accurately reflects v1.10.0 UI
  - Memory section shows Mem/Swap on same line (two-column layout)
  - CPU cores show compact bar format matching actual rendering
  - GPU section shows compact 2-row layout with Util+VRAM side-by-side
  - GPU processes section shows ←/→ scroll indicator and correct column format
  - Footer reverse-status bar added
  - Hardware reference updated: RTX 3090/24GB → RTX A6000/48GB

### v1.10.0 (2026-07-08)
- **Bug fix: nvidia-smi CSV parsing**: GPU names containing commas (e.g. "Tesla V100-SXM2, 32GB") no longer break the parser
  - Now parses index from the left and 6 numeric fields from the right, treating everything in between as the name
- **Threading improvement**: replaced `_stats_should_update` boolean with `threading.Event()`
  - Background thread blocks on `wait(timeout=0.1)` instead of busy-polling — more efficient, race-free signaling
- **Cleanup**: removed dead `get_system_stats()` method (~48 lines), unused `Optional` import, unused color pairs (`COLOR_ERROR`, `COLOR_PROCESS`), redundant local `import pwd`
- **UI consistency**: memory section data rows now have the same 1-space padding as CPU/GPU sections
- **Net result**: 47 fewer lines, same functionality, fewer bugs

### v1.9.1 (2026-07-07)
- **Threading safety fix**: `self._stats_lock` now actually protects data access
  - Background thread collects stats into local vars, then swaps atomically under lock
  - `draw()` snapshots data under the lock before rendering — no more torn reads
  - Clean shutdown: background thread joined in `finally` block before exit
- **Cleanup**: Removed duplicate `import threading` inside `__init__`

### v1.9.0 (2026-07-07)
- **Instant scroll response on macOS**: Refactored to use background thread for stats updates
  - `update_stats()` now signals background thread instead of blocking
  - Scroll keys (`←`/`→`) redraw immediately without waiting for GPU queries
  - `getch()` timeout (50ms) instead of `nodelay()` + sleep pattern
- **Non-blocking architecture**: Stats update every 100ms in background thread
- **Performance**: Scroll response now instant even with slow GPU monitoring tools

### v1.8.2 (2026-05-20)
- **macmon dependency documented**: README now clearly states `macmon` is required for accurate macOS GPU stats
  - Added install instructions (`cargo install macmon` or build from source)
  - Clarified that `macmon` binary must be in `PATH` (e.g., `~/bin/`)
  - Split Requirements into Linux and macOS subsections for clarity

### v1.8.1 (2026-05-20)
- **macOS GPU utilization via `socpwrbud`**: Added `socpwrbud` as a sudoless fallback for GPU utilization
  - Reads GPU active residency from IOReport power counters (no elevated privileges needed)
  - Fallback chain: `socpwrbud` → `powermetrics` (requires sudo on macOS 13+)
  - Startup warning if neither tool is found, recommending installation

### v1.8.0 (2026-05-20)
- **Cross-platform support (Linux + macOS)**: termmon now auto-detects platform and GPU backend
  - System stats (CPU, memory, swap, per-core) now use `psutil` — works on both Linux and macOS
- **GPU backend auto-detection**: NVIDIA (`nvidia-smi`) on Linux, Apple Silicon (`macmon`/`socpwrbud`/`powermetrics` + `sysctl`) on macOS
- **macOS GPU utilization fix**: Three-tier fallback — `macmon` (preferred, actively maintained, no sudo), `socpwrbud` (fallback, no sudo), `powermetrics` (last resort, requires sudo)
  - Apple Silicon GPU stats: model name, GPU core count, utilization %, power draw
  - Unified Memory Architecture (UMA) awareness — shows "shared w/ system memory" instead of VRAM bar on Apple Silicon
  - GPU process tracking on macOS via `psutil.process_iter()` (host memory as proxy for GPU activity)
  - Dynamic GPU section title and "no data" messages per platform
  - Single `powermetrics` call for both utilization and power (was two calls)
  - Added `psutil` as the only external dependency
  - Graceful fallback to "no GPU" when monitoring tools are unavailable or restricted
- **CPU and GPU process sections padded**: Added 1-space padding between content and box borders in CPU per-core rows and GPU process table
  - CPU title line, per-core rows, and GPU process title/header/data rows all have side padding now
  - Scrolled process viewport and max scroll calculation adjusted for the extra 2 chars of padding

### v1.7.2 (2026-05-05)
- **Documentation and terminal compatibility cleanup**: README and module docstring now document the current `←` / `→` GPU process scroll keys and v1.7 CPU/process layouts
  - Wraps `curses.curs_set(0)` so terminals that cannot hide the cursor do not crash the dashboard

### v1.7.1 (2026-05-05)
- **CPU bars compacted and recolored**: Per-core rows now put the utilization bar directly after the `Core n:` label, with the percentage after the bar
  - Restores cyan/bold filled CPU bar segments while keeping stable whole-row rendering
  - Preserves two-column CPU layout and Core 10+ alignment without the large label-to-bar gap

### v1.7.0 (2026-05-05)
- **nvtop-style GPU process table**: Replaced wrapped multi-line process commands with a single-line table:
  - `PID USER DEV TYPE GPU GPU MEM CPU HOST MEM Command`
  - Left/right arrow keys horizontally scroll the command column to reveal long arguments
  - Fixed metadata columns stay visible while the command viewport scrolls

### v1.6.10 (2026-05-05)
- **CPU row rendering stabilized**: Per-core CPU rows are now composed as complete bordered strings and written once per row
  - Avoids partial curses writes/draw_bar positioning drift on narrow/mobile terminals
  - Keeps two-column CPU layout and overall CPU in the title

### v1.6.9 (2026-05-05)
- **Inline GPU process command start**: First command fragment now starts on the same row as PID/GPU/HOST/USER metadata under the `PROCESSES` header
  - Saves the remaining wasted vertical row in the GPU process section
  - Continuation command lines still wrap across the full process box width

### v1.6.8 (2026-05-05)
- **CPU tile vertical compaction**: Moved overall CPU percentage into the CPU title line after the core count
  - Removes the separate `Overall` CPU row, freeing one vertical row for the GPU process command section
  - Keeps per-core CPU rows unchanged

### v1.6.7 (2026-05-05)
- **Aligned compact GPU process columns**: PID/GPU/HOST/USER headers now use the same fixed-width columns as the metadata row below
  - Keeps a single header/title row to avoid stealing command parameter space
  - Keeps command parameters full-width below the aligned metadata row

### v1.6.6 (2026-05-05)
- **iPad GPU process rows compacted again**: moved the column labels into the GPU process title and removed the extra header/separator pair
  - Keeps PID/GPU/HOST/USER visible in one compact process row
  - Saves two vertical rows so late command flags remain visible on short terminals
  - Removes the trailing blank separator after the last process

### v1.6.5 (2026-05-05)
- **Compact GPU process header restored**: Shows PID, GPU memory, host memory, and user above each command while preserving full-width command wrapping
  - Header: `PID GPU MEM HOST MEM USER`
  - Process summary appears before the command line, so key resource data is visible at a glance
  - Command parameters still wrap across almost the full box width

### v1.6.4 (2026-05-05)
- **Command-first GPU process layout**: Command line parameters now render before metadata so they remain visible on short iPad terminals
  - Removed the verbose helper heading that wasted vertical space
  - Full command wraps across almost the entire GPU process box
  - PID/user/GPU/HOST metadata appears after the command instead of pushing it down

### v1.6.3 (2026-05-05)
- **iPad/narrow-terminal GPU process layout**: Metadata now appears on its own line and command lines get nearly the full box width
  - Replaced the old wide `PID | USER | GPU MEM | HOST MEM | Command` row that consumed 50 columns before the command
  - Command wrapping now has ~72 chars in an 80-column box instead of ~29 chars
  - Keeps GPU/HOST memory visible without sacrificing the command line

### v1.6.2 (2026-04-22)
- **Help popup overhaul**: Styled popup matching termepub pattern
  - White-on-blue colored background (new `COLOR_POPUP` pair)
  - `+`/`-`/`|` border chars with popup color
  - Yellow bold title on blue background
  - Blocking `getch()` — stays up until user presses any key
  - Dashboard drawn underneath first so it's ready when dismissed
  - `stdscr.refresh()` called explicitly (fixes popup not rendering)
  - `nodelay` toggled off/on around the blocking key wait

### v1.6.1 (2026-04-22)
- **Code quality pass**: Comprehensive fixes from systematic code review
  - Removed unused `LABEL_WIDTH` constant (dead code)
  - Fixed help popup box chars: single-line (`┌┐└┘`) to match rest of UI (was mixed double-line `╔╗╚╝`)
  - Fixed `except Exception` swallowing `KeyboardInterrupt` in 3 locations (clean Ctrl+C exit)
  - Cached core count in `__init__` (reads `/proc/cpuinfo` once instead of every refresh cycle)
  - Added `SIGWINCH` handler for graceful terminal resize (forces full redraw)
  - Parallelized `nvidia-smi` calls: GPU stats and GPU processes now query concurrently via `concurrent.futures` (faster refresh on multi-GPU systems)

### v1.6.0 (2026-04-22)
- **Major command wrapping overhaul**: Fixed word truncation, unnecessary line breaks, and path splitting
  - Extracted `_wrap_command()` into a dedicated method (4-phase pipeline)
  - Fixed `cmd_width` calculation: corrected column prefix from 42 to 51 chars (was clipping 8 chars)
  - Path continuation segments (`__PCONT__`) wrap independently while maintaining `/` joins
  - Flag+path pairs (`-m /path/to/file`) keep the flag with the first segment, remaining segments wrap naturally
  - No more truncated words, no more broken paths, no more unnecessary breaks
- **Code cleanup**: Moved runtime `import os` and `import pwd` to top level
- **Performance**: Merged duplicate `/proc/[pid]/status` reads into single pass

### v1.5.6 (2026-04-20)
- **Continuation line alignment**: Fixed GPU process command wrapping
  - Continuation lines now align perfectly with command column
  - Corrected indent calculation: 50 chars (was 48, briefly 52)
  - Memory columns are 8 chars + "MB" suffix, properly accounted for

### v1.5.5 (2026-04-20)
- **Version in header**: Display version number in title bar
  - Header now shows: `termmon 1.5.5 - System Monitor`
  - Version visible at a glance without checking docs

### v1.5.4 (2026-04-20)
- **Flag-value pair wrapping**: Command-line flags stay with their values
  - `--cache-ram 4096` no longer splits across lines
  - Flags starting with `-` or `--` are paired with following non-flag arguments
  - Cleaner, more readable command display

### v1.5.3 (2026-04-20)
- **Path separator fix**: Long paths now preserve `/` between segments
  - Path segments rejoin with `/` instead of spaces when wrapping
  - Proper context tracking ensures arguments stay separate from paths
  - Clean, readable command display with correct path formatting

### v1.5.2 (2026-04-20)
- **Command wrapping fix**: Intelligent path splitting for long commands
  - Paths longer than 50 chars are split on `/` for better readability
  - Words wrap naturally at space boundaries
  - Continuation lines chunk properly if they exceed available width
  - No more truncated words or unnecessary line breaks

### v1.5.1 (2026-04-20)
- **GPU column layout adjustment**: Swapped Util and VRAM columns
  - Row 2 now shows Util (left) + VRAM (right)
  - Better visual hierarchy: quick utilization check first, then detailed VRAM

### v1.5.0 (2026-04-20)
- **GPU display optimization**: Two-column compact layout
  - Row 1: GPU name (summary stats moved to the section title)
  - Row 2: VRAM bar (left) + Util bar (right) on same line
  - Cuts GPU section from 4 rows down to 2 rows
  - Much more compact for multi-GPU systems

### v1.4.0 (2026-04-19)
- **CPU display optimization**: Two-column core layout
  - Cores displayed in two columns (left/right) instead of single column
  - Cuts vertical space in half for multi-core systems
  - Overall CPU usage line aligned with core lines
  - Progress bars start at same position for visual consistency
  - Much more compact display for 8+ core systems

### v1.3.0 (2026-04-19)
- **GPU process improvements**: Better command display
  - Shows full command line with arguments (from /proc/[pid]/cmdline)
  - Word-wraps long commands across multiple lines
  - Continuation lines properly aligned with command column
  - Auto-fits box width to terminal (85% of width, 80-120 chars)
  - Cleaner display: just command basename + args, no full paths

### v1.2.0 (2026-04-19)
- **UI improvements**: Compact, consistent display format
  - System memory: Single-line `Mem: 12.5GB/15.4G ████████  81.2%` format
  - VRAM: Now shows GB instead of MB for consistency (`38.6GB/24.0G`)
  - GPU utilization: Percentage aligns with VRAM numbers
  - Removed redundant "Total" and "Available" memory lines
  - All progress bars now start at same position for visual consistency

### v1.1.0 (2026-04-19)
- **New**: GPU process tracking (nvtop-style)
  - Shows top 5 GPU compute processes by VRAM usage
  - Displays PID, user, GPU memory, host memory, and command
  - Enriched with `/proc` data for user and host memory

### v1.0.0 (2026-04-15)
- **Initial release**: Production-ready system monitor
- **Features**: CPU, memory, swap, GPU monitoring
- **Design**: Clean ASCII box layout with color-coded progress bars

## License

MIT

## Author

Ifor Evans (@iforevans)  
Pair programmed with OpenClaw Agent Sparky ⚡
