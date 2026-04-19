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

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Manual refresh (immediate update) |
| `h` | Show help |

**Auto-refresh**: Every 2 seconds (no action needed)

## Display Layout

```
 termmon - System Monitor | HH:MM:SS | q:quit r:refresh h:help 
┌────────────────────────────────────────────────────────────────┐
│ SYSTEM MEMORY                                                  │
│────────────────────────────────────────────────────────────────│
│ Total:       15.4GB                                             │
│ Used:        8.6GB ████████████████████░░  55.8%               │
│ Available:    6.8GB                                             │
│ Swap:       2.5/4.0GB ████░░░░░░░░░░░░░░░░  62.5%              │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ CPU (8 cores)                                                  │
│────────────────────────────────────────────────────────────────│
│ Overall:     2.4% ████░░░░░░░░░░░░░░░░░░                       │
│ Core 0:      2.4% ████░░░░░░░░░░░░░░░░░░                       │
│ Core 1:      0.0% ░░░░░░░░░░░░░░░░░░░░░░                       │
│ ... (all cores)                                                │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ NVIDIA GPU(s)                                                  │
│────────────────────────────────────────────────────────────────│
│ GPU 0: NVIDIA GeForce RTX 3090                                 │
│ VRAM:    22547/24576MB ████████████████░░  91.7%              │
│ Util:     0.0% ░░░░░░░░░░░░░░░░░░░░░░                         │
│ Temp:     59°C  Power: 110.2W                                 │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ GPU PROCESSES (nvtop-style)                                    │
│────────────────────────────────────────────────────────────────│
│ PID    | USER       | GPU MEM    | HOST MEM   | Command        │
│────────────────────────────────────────────────────────────────│
│  86775 | iforevans  |   39506MB |    7768MB | /home/iforevans/...│
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
