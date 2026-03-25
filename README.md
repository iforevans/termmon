# termmon

A terminal-based system monitor combining `htop` and `nvidia-smi` into a single unified dashboard.

## Features

- **System Memory**: Total, used, available, and swap with progress bars
- **CPU Usage**: Real-time per-system CPU utilization with delta-based calculation
- **GPU Stats**: NVIDIA GPU monitoring (VRAM usage, GPU utilization, temperature, power draw)
- **Auto-refresh**: Updates every 2 seconds
- **Color-coded**: Visual progress bars with intuitive color coding
- **No dependencies**: Pure Python + curses (no external packages required)

## Screenshot

```
 termmon - System Monitor | 09:55:30 | q:quit r:refresh h:help 
┌────────────────────────────────────────────────────────────────────────┐
│ SYSTEM MEMORY                                                          │
│────────────────────────────────────────────────────────────────────────│
│ Total:        15.4 GB                                                   │
│ Used:       8.8GB ████████████████████░░  57.6%                        │
│ Available:     6.5 GB                                                   │
│ Swap: 676.9/4096.0MB ████░░░░░░░░░░░░░░░░  16.5%                       │
└────────────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────────┐
│ CPU (8 cores)                                                          │
│────────────────────────────────────────────────────────────────────────│
│ Usage:     1.4% █░░░░░░░░░░░░░░░░░░░░░                                  │
└────────────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────────┐
│ NVIDIA GPU(s)                                                          │
│────────────────────────────────────────────────────────────────────────│
│ GPU 0: NVIDIA GeForce RTX 3090                                         │
│ VRAM:  22313/24576MB ████████████████░░  90.8%                        │
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

- **Memory section**: Shows total RAM, used RAM with progress bar and percentage, available RAM, and swap usage
- **CPU section**: Shows overall CPU usage with progress bar (delta-based calculation for accurate real-time usage)
- **GPU section**: Per-GPU display showing VRAM usage, GPU utilization, temperature, and power consumption

## How It Works

- Reads system stats from `/proc/meminfo` and `/proc/stat`
- Calculates CPU usage using delta between samples (not cumulative from boot)
- Queries NVIDIA GPUs via `nvidia-smi` in CSV format
- Renders a fixed-width TUI using Python's `curses` library
- Updates every 2 seconds with smooth rendering (no flicker)

## Future Enhancements

- [ ] Per-core CPU visualization
- [ ] Process list with GPU tags
- [ ] Network I/O monitoring
- [ ] Disk I/O monitoring
- [ ] Configurable refresh rate
- [ ] Rust port using ratatui

## License

MIT License - see LICENSE file for details.

## Author

Ifor Evans - [@iforevans](https://github.com/iforevans)
