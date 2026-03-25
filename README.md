# termmon

While testing local models on my RTX3090/24GB I constantly found myself with a terminal window open for htop and another to keep refreshing 'nvidia-smi' to see VRAM usage. This is a solution to that problem. Monitor CPU/System RAM/SWP, and GPU/VRAM usage from one little htop clone.

A terminal-based system monitor combining `htop` and `nvidia-smi` into a single unified dashboard.

## Features

- **System Memory**: Total, used, available, and swap (in GB) with progress bars
- **CPU Usage**: Overall and per-core real-time utilization with delta-based calculation
- **GPU Stats**: NVIDIA GPU monitoring (VRAM usage, GPU utilization, temperature, power draw)
- **Auto-refresh**: Updates every 2 seconds
- **Color-coded**: Visual progress bars (green=memory, yellow=swap, cyan=CPU, magenta=VRAM)
- **No dependencies**: Pure Python + curses (no external packages required)

## Screenshot

```
 termmon - System Monitor | 09:55:30 | q:quit r:refresh h:help 
┌────────────────────────────────────────────────────────────────────────┐
│ SYSTEM MEMORY                                                          │
│────────────────────────────────────────────────────────────────────────│
│ Total:       15.4GB                                                     │
│ Used:        8.6GB ████████████████████░░  55.8%                        │
│ Available:    6.8GB                                                     │
│ Swap:       2.5/4.0GB ████░░░░░░░░░░░░░░░░  62.5%                       │
└────────────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────────┐
│ CPU (8 cores)                                                          │
│────────────────────────────────────────────────────────────────────────│
│ Overall:     2.4% ████░░░░░░░░░░░░░░░░░░                               │
│ Core 0:      2.4% ████░░░░░░░░░░░░░░░░░░                               │
│ Core 1:      0.0% ░░░░░░░░░░░░░░░░░░░░░░                               │
│ Core 2:      4.8% ██████░░░░░░░░░░░░░░░░                               │
│ Core 3:      0.0% ░░░░░░░░░░░░░░░░░░░░░░                               │
│ Core 4:      2.4% ████░░░░░░░░░░░░░░░░░░                               │
│ Core 5:      0.0% ░░░░░░░░░░░░░░░░░░░░░░                               │
│ Core 6:      0.0% ░░░░░░░░░░░░░░░░░░░░░░                               │
│ Core 7:      2.4% ████░░░░░░░░░░░░░░░░░░                               │
└────────────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────────┐
│ NVIDIA GPU(s)                                                          │
│────────────────────────────────────────────────────────────────────────│
│ GPU 0: NVIDIA GeForce RTX 3090                                         │
│ VRAM:    22547/24576MB ████████████████░░  91.7%                        │
│ Util:     0.0% ░░░░░░░░░░░░░░░░░░░░░░                                  │
│ Temp:     59°C  Power: 110.2W                                         │
└────────────────────────────────────────────────────────────────────────┘
 Refresh: 2s | q:quit r:refresh h:help 
```

## Requirements

- Python 3.6+
- Linux/Unix system (uses `/proc` filesystem)
- NVIDIA GPU (optional, for GPU monitoring)
- `nvidia-smi` command (for GPU stats)

## Installation

```bash
# Clone the repository
git clone https://github.com/iforevans/termmon.git
cd termmon

# Make executable (optional)
chmod +x termmon.py
```

## Usage

```bash
# Run with python3
python3 termmon.py

# Or if made executable
./termmon.py
```

## Keybindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Refresh now |
| `h` | Show help |

## Display Format

- **Memory section**: Total, used, and available RAM in GB; swap in GB with progress bars and percentages
- **CPU section**: Overall usage plus per-core breakdown with aligned progress bars
- **GPU section**: Per-GPU VRAM usage, utilization, temperature, and power draw with color-coded bars

## How It Works

- Reads system stats from `/proc/meminfo` and `/proc/stat`
- Calculates CPU usage using delta between samples (not cumulative from boot)
- Queries NVIDIA GPUs via `nvidia-smi` in CSV format
- Renders a fixed-width TUI using Python's `curses` library
- Updates every 2 seconds with smooth rendering (no flicker)

## Future Enhancements

- [x] Per-core CPU visualization
- [ ] Process list with GPU tags
- [ ] Network I/O monitoring
- [ ] Disk I/O monitoring
- [ ] Configurable refresh rate
- [ ] Rust port using ratatui

## License

MIT License - see LICENSE file for details.

## Author

Ifor Evans - [@iforevans](https://github.com/iforevans)
