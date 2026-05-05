# termmon - Terminal System Monitor

A unified terminal-based system monitor combining `htop` (system RAM/CPU) and `nvidia-smi` (GPU/VRAM) functionality into a single dashboard.

Originally created to solve the problem of monitoring CPU/system RAM/swap and GPU/VRAM usage from one window while testing local AI models on an RTX3090/24GB.

## Features

- **System Memory**: Total, used, available RAM + swap (in GB) with progress bars
- **CPU Usage**: Overall and per-core real-time utilization
- **NVIDIA GPU Monitoring**: VRAM usage, GPU utilization, temperature, power draw
- **GPU Process Tracking**: Top 5 active GPU compute processes (nvtop-style)
  - Shows PID, user, GPU memory, host memory, and command
  - Sorted by VRAM usage (descending)
- **Color-coded progress bars**: Visual feedback for resource usage
- **Auto-refresh**: Updates every 2 seconds
- **Pure Python**: No external dependencies (stdlib only)

## Requirements

- Python 3.9+
- Linux system (reads from `/proc`)
- NVIDIA drivers with `nvidia-smi` (for GPU monitoring)

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

```
 termmon - System Monitor | HH:MM:SS | q:quit r:refresh h:help
┌────────────────────────────────────────────────────────────────┐
│ SYSTEM MEMORY                                                  │
│────────────────────────────────────────────────────────────────│
│ Mem:  ████████████████████░░░░  12.5GB/15.4G  81.2%           │
│ Swap: ██████████████████░░░░░░   2.5/4.0GB  62.5%             │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ CPU (8 cores, overall  23.4%)                                  │
│────────────────────────────────────────────────────────────────│
│Core 0:  █████░░░░░░░░░░░░░░░░░  23.4%  Core 4:  ██░░░░░░   9.8%│
│Core 1:  ███████░░░░░░░░░░░░░░░  31.0%  Core 5:  ███░░░░░  15.2%│
│ ... (all cores)                                                │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ NVIDIA GPU(s)                                                  │
│────────────────────────────────────────────────────────────────│
│ GPU 0: NVIDIA GeForce RTX 3090                  Temp:  59°C  Power: 110.2W │
│ Util: ████████░░░░░░░░░░░░░░░░  80.0%   VRAM: ████████░░ 22.0GB/24.0G  91.7% │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ GPU PROCESSES (nvtop-style)                                    │
│────────────────────────────────────────────────────────────────│
│PID     USER     DEV TYPE   GPU  GPU MEM    CPU HOST MEM Command│
│────────────────────────────────────────────────────────────────│
│86775   iforevan 0   C       --   39506M  12.3%    7768M llama-s│
└────────────────────────────────────────────────────────────────┘
```

## Color Scheme

- 🟢 **Green**: System memory usage
- 🟡 **Yellow**: Swap usage
- 🔵 **Cyan**: CPU usage
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

- **Built with**: Python + curses
- **Dependencies**: None (pure stdlib)
- **GPU Detection**: Uses `nvidia-smi` CLI tool
- **CPU Stats**: Reads from `/proc/stat`
- **Memory Stats**: Reads from `/proc/meminfo`
- **Process Info**: Reads from `/proc/[pid]/status`
- **Refresh Rate**: 2 seconds (configurable in source)

## Development Timeline

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
  - Row 1: GPU name (left) + Temp/Power (right) on same line
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
