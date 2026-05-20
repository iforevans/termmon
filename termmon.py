#!/usr/bin/env python3
"""
termmon - Terminal System Monitor
==================================

A unified terminal-based system monitor combining `htop` (system RAM/CPU)
and GPU monitoring (nvidia-smi on Linux / powermetrics on macOS) into a
single dashboard.

Originally created to solve the problem of monitoring CPU/system RAM/swap
and GPU/VRAM usage from one window while testing local AI models on an
RTX3090/24GB.

Features:
    - System memory monitoring (RAM + swap in GB)
    - Overall and per-core CPU utilization
    - NVIDIA GPU monitoring on Linux (VRAM, utilization, temperature, power)
    - Apple Silicon GPU monitoring on macOS (utilization, power, UMA)
    - GPU process tracking (top 5 processes by memory usage)
    - Color-coded progress bars
    - Auto-refresh every 2 seconds
    - Cross-platform (Linux + macOS)

Usage:
    termmon

Keybindings:
    q - Quit
    r - Refresh now
    h - Show help
    ←/→ - Scroll GPU process command column

Author:
    Ifor Evans (@iforevans)
    Pair programmed with OpenClaw Agent Sparky ⚡

License:
    MIT
"""

import curses
import concurrent.futures
import json
import os
import platform
import pwd
import signal
import subprocess
import sys
from datetime import datetime
import time
from typing import Dict, List, Tuple, Any, Optional

try:
    import psutil
except ImportError:
    print("Error: psutil is required. Install with: pip3 install psutil", file=sys.stderr)
    sys.exit(1)

# Platform detection (set once at import time)
_SYSTEM = platform.system()  # 'Linux' or 'Darwin'
_IS_MACOS = _SYSTEM == "Darwin"
_IS_LINUX = _SYSTEM == "Linux"

__version__ = "1.8.1"
__author__ = "Ifor Evans"


# Layout configuration
BOX_WIDTH = 0          # Will be auto-calculated based on terminal width (80% of terminal)
BAR_WIDTH = 20         # Width of progress bars
REFRESH_INTERVAL = 2   # Seconds between auto-refreshes

# Color pair IDs
COLOR_TITLE = 1         # White - title and footer
COLOR_MEMORY = 2        # Green - RAM usage bar
COLOR_SWAP = 3          # Yellow - swap usage bar
COLOR_CPU = 4           # Cyan - CPU usage bar
COLOR_VRAM = 5          # Magenta - VRAM usage bar
COLOR_ERROR = 6         # Red - error messages
COLOR_PROCESS = 7       # Blue - GPU process list
COLOR_POPUP = 8         # White on blue - help popup

# NVIDIA GPU query fields (must match nvidia-smi output order)
GPU_QUERY_FIELDS = "index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw"
# GPU compute apps: pid, process_name, used_gpu_memory (limited fields available)
GPU_COMPUTE_QUERY = "pid,process_name,used_gpu_memory"

# Maximum GPU processes to display
MAX_GPU_PROCS = 5


class TermMon:
    """Terminal-based system monitor combining htop and nvidia-smi."""
    
    def __init__(self) -> None:
        """Initialize the TermMon application."""
        self.running: bool = True
        self.gpu_data: List[Dict[str, Any]] = []
        self.gpu_processes: List[Dict[str, Any]] = []
        self.system_data: Dict[str, Any] = {}
        self.core_count: int = self._get_core_count()
        self._resized: bool = False  # SIGWINCH flag
        self.process_scroll_x: int = 0  # Horizontal scroll offset for nvtop-style process table
    
    def _on_resize(self, signum: int, frame: Any) -> None:
        """Handle terminal resize (SIGWINCH)."""
        self._resized = True

    @staticmethod
    def _get_core_count() -> int:
        """Read CPU core count (cross-platform)."""
        return psutil.cpu_count() or 0
        
    def get_system_stats(self) -> None:
        """
        Read system memory and CPU statistics (cross-platform via psutil).

        Uses psutil for virtual memory, swap, and per-core CPU utilization.
        Calculates CPU usage using delta between samples for accurate
        real-time measurement.
        """
        try:
            # Memory via psutil (cross-platform)
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()

            total_mem_gb = vm.total / 1024 / 1024 / 1024
            used_mem_gb = vm.used / 1024 / 1024 / 1024
            avail_mem_gb = vm.available / 1024 / 1024 / 1024
            mem_percent = vm.percent

            # Swap - some platforms report swap as percentage directly,
            # others as bytes. Normalise to MB.
            swap_total_mb = swap.total / 1024 / 1024
            swap_used_mb = swap.used / 1024 / 1024
            swap_percent = swap.percent

            # Per-core CPU usage (cross-platform)
            # psutil.cpu_percent(percpu=True) returns a list of per-core
            # percentages. First call returns 0.0; subsequent calls give
            # the real delta-based reading.
            per_core_raw = psutil.cpu_percent(percpu=True)
            per_core_usage = list(enumerate(per_core_raw))

            self.system_data = {
                'total_mem_gb': total_mem_gb,
                'used_mem_gb': used_mem_gb,
                'avail_mem_gb': avail_mem_gb,
                'mem_percent': mem_percent,
                'swap_total_mb': swap_total_mb,
                'swap_used_mb': swap_used_mb,
                'swap_percent': swap_percent,
                'cpu_usage': sum(per_core_raw) / max(len(per_core_raw), 1),
                'core_count': self.core_count,
                'per_core_usage': per_core_usage
            }
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.system_data['error'] = str(e)
    
    # ------------------------------------------------------------------ #
    #  GPU backends — auto-detected at init, swappable for testing       #
    # ------------------------------------------------------------------ #

    @property
    def _gpu_backend(self) -> str:
        """Return 'nvidia', 'apple', or 'none' based on platform detection."""
        if not hasattr(self, '_cached_backend'):
            self._cached_backend = self._detect_gpu_backend()
        return self._cached_backend

    @staticmethod
    def _detect_gpu_backend() -> str:
        """Detect available GPU backend without sudo."""
        if _IS_LINUX:
            try:
                subprocess.run(
                    ['nvidia-smi', '--query-gpu=index', '--format=csv,noheader'],
                    capture_output=True, timeout=3
                )
                return 'nvidia'
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass
        if _IS_MACOS:
            # Check for Apple Silicon GPU via sysctl
            try:
                result = subprocess.run(
                    ['sysctl', '-n', 'hw.gpu.model'],
                    capture_output=True, text=True, timeout=3
                )
                if result.returncode == 0 and result.stdout.strip():
                    return 'apple'
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass
            # Fallback: check for Intel/AMD discrete GPUs via system_profiler
            try:
                result = subprocess.run(
                    ['system_profiler', 'SPDisplaysDataType', '-json'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    return 'apple'
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass
        return 'none'

    # ---- NVIDIA backend (Linux) ----------------------------------------

    def get_gpu_stats(self) -> None:
        """
        Read GPU statistics.

        Linux (NVIDIA): uses nvidia-smi for VRAM, utilization, temperature, power.
        macOS (Apple):  uses macmon (preferred), socpwrbud, or powermetrics + sysctl
        for GPU utilization and metadata. macmon reads IOReport counters directly
        without sudo; falls back through socpwrbud then powermetrics.
        Falls back gracefully if the backend is unavailable.
        """
        try:
            if self._gpu_backend == 'nvidia':
                self._get_gpu_stats_nvidia()
            elif self._gpu_backend == 'apple':
                self._get_gpu_stats_apple()
            else:
                self.gpu_data = []
        except KeyboardInterrupt:
            raise
        except Exception:
            self.gpu_data = []

    def _get_gpu_stats_nvidia(self) -> None:
        """Read NVIDIA GPU statistics using nvidia-smi."""
        result = subprocess.run(
            [
                'nvidia-smi',
                f'--query-gpu={GPU_QUERY_FIELDS}',
                '--format=csv,noheader,nounits'
            ],
            capture_output=True, text=True, timeout=5
        )

        if result.returncode == 0:
            gpus = []
            for gpu in result.stdout.strip().split('\n'):
                parts = [p.strip() for p in gpu.split(',')]
                if len(parts) >= 8:
                    try:
                        gpus.append({
                            'idx': parts[0],
                            'name': parts[1],
                            'mem_total': float(parts[2]),
                            'mem_used': float(parts[3]),
                            'mem_free': float(parts[4]),
                            'gpu_util': float(parts[5]),
                            'temp': float(parts[6]),
                            'power': float(parts[7]),
                            'gpu_cores': 0,
                            'is_uma': False,
                        })
                    except (ValueError, IndexError):
                        pass
            self.gpu_data = gpus
        else:
            self.gpu_data = []

    def _get_gpu_stats_apple(self) -> None:
        """
        Read Apple Silicon GPU statistics.

        Uses powermetrics for GPU active percentage and power draw
        (single call, best effort — requires entitlement on newer macOS;
        falls back to 0 if denied).

        Uses sysctl/system_profiler for GPU model name and core count.
        Unified Memory Architecture means there is no separate VRAM pool.
        """
        gpus = []

        # --- GPU model name and core count (one-shot cache) ---
        gpu_name = self._apple_gpu_model()
        gpu_cores = self._apple_gpu_cores()

        # --- GPU utilization + power via single powermetrics call ---
        gpu_util, gpu_power = self._apple_gpu_util_and_power()

        # --- Temperature (best effort — usually not available without sudo) ---
        gpu_temp = 0.0  # Not available without sudo/IOKit entitlement

        gpus.append({
            'idx': '0',
            'name': gpu_name or 'Apple GPU',
            'mem_total': 0,     # UMA — no separate VRAM
            'mem_used': 0,
            'mem_free': 0,
            'gpu_util': gpu_util,
            'temp': gpu_temp,
            'power': gpu_power,
            # Extra fields for the draw layer
            'gpu_cores': gpu_cores,
            'is_uma': True,     # flag: Unified Memory Architecture
        })

        self.gpu_data = gpus

    @staticmethod
    def _apple_gpu_model() -> str:
        """Get GPU model name from sysctl or system_profiler."""
        # Try sysctl first (fast)
        for key in ('hw.gpu.model', 'hw.model'):
            try:
                result = subprocess.run(
                    ['sysctl', '-n', key],
                    capture_output=True, text=True, timeout=3
                )
                if result.returncode == 0:
                    model = result.stdout.strip()
                    if key == 'hw.gpu.model' and model:
                        return model
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # Fallback: parse system_profiler
        try:
            result = subprocess.run(
                ['system_profiler', 'SPDisplaysDataType', '-json'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data.get('SPDisplaysDataType'):
                    # First GPU entry
                    gpu = data['SPDisplaysDataType'][0]
                    return gpu.get('spdisplays_chipset', 'Apple GPU')
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

        return 'Apple GPU'

    @staticmethod
    def _apple_gpu_cores() -> int:
        """Get GPU core count from sysctl."""
        try:
            result = subprocess.run(
                ['sysctl', '-n', 'hw.gpus'],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                return int(result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return 0

    @staticmethod
    def _apple_gpu_util_and_power() -> Tuple[float, float]:
        """
        Get GPU active percentage and power draw.

        Multi-tier fallback (all non-sudo):
          1. macmon    — actively maintained, reads IOReport + SMC (no sudo, best data)
          2. socpwrbud — archived but functional IOReport reader (no sudo)
          3. powermetrics — Apple's built-in tool (requires sudo on macOS 13+)
          4. Returns (0, 0) if none succeed

        macmon pipe JSON example:
          {
            "gpu_usage": [1200, 0.75],  // [freq_mhz, percent_from_max]
            "gpu_power": 5.2,            // Watts
            "temp": { "gpu_temp_avg": 45.5 }
          }

        socpwrbud output example:
          Integrated Graphics
              Average frequency: 609 mhz
              Average voltage:   692 mv
              Active residency:  2.46 %
              Idle residency:    97.54 %

        powermetrics output example:
          GPU active percentage: 12.3%
          GPU power:            5.2 Watts
        """
        # --- Tier 1: macmon (preferred — actively maintained, no sudo) ---
        result = TermMon._apple_gpu_util_macmon()
        if result != (0.0, 0.0):
            return result

        # --- Tier 2: socpwrbud (fallback — archived but works on many chips) ---
        result = TermMon._apple_gpu_util_socpwrbud()
        if result != (0.0, 0.0):
            return result

        # --- Tier 3: powermetrics (last resort — requires sudo on macOS 13+) ---
        return TermMon._apple_gpu_util_powermetrics()

    @staticmethod
    def _apple_gpu_util_macmon() -> Tuple[float, float]:
        """
        Get GPU utilization from macmon (sudoless IOReport + SMC reader).

        macmon is an actively maintained Rust tool (1.6k stars) that reads
        GPU performance counters from IOReport without sudo. Available via:
          - Homebrew:  brew install vladkens/tap/macmon
          - Release:   https://github.com/vladkens/macmon/releases

        Returns (gpu_util_pct, gpu_power_watts) as floats.
        """
        try:
            # macmon pipe -s 1 gives one JSON line then exits
            result = subprocess.run(
                ['macmon', 'pipe', '-s', '1'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                gpu_util = 0.0
                gpu_power = 0.0

                # gpu_usage: [freq_mhz, percent_from_max]  — 0.0 to 1.0
                gpu_usage = data.get('gpu_usage')
                if gpu_usage and len(gpu_usage) >= 2:
                    gpu_util = gpu_usage[1] * 100  # convert fraction to percentage

                # gpu_power: Watts
                gpu_power = float(data.get('gpu_power', 0.0))

                return gpu_util, gpu_power
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError,
                json.JSONDecodeError, TypeError):
            pass
        return 0.0, 0.0

    @staticmethod
    def _apple_gpu_util_socpwrbud() -> Tuple[float, float]:
        """
        Get GPU utilization from socpwrbud (sudoless IOReport reader).

        socpwrbud is a third-party tool that reads GPU performance counters
        directly from IOReport without requiring sudo. Archived but still
        functional on many Apple Silicon chips.

        Returns (gpu_util, gpu_power) as floats.
        """
        try:
            result = subprocess.run(
                ['socpwrbud', '-i', '1000', '-s', '1', '-m', 'active,idle,freq,volts'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                gpu_util = 0.0
                for line in result.stdout.split('\n'):
                    line = line.strip()
                    if 'Active residency' in line:
                        try:
                            val = line.split(':')[-1].strip().rstrip('%')
                            gpu_util = float(val)
                        except (ValueError, IndexError):
                            pass
                if gpu_util > 0:
                    return gpu_util, 0.0
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return 0.0, 0.0

    @staticmethod
    def _apple_gpu_util_powermetrics() -> Tuple[float, float]:
        """
        Get GPU utilization from powermetrics (Apple's built-in tool).

        Note: On macOS 13+ (Ventura and later), powermetrics requires sudo
        to access GPU power sampler data. Without sudo, it returns zeros.

        Returns (gpu_util, gpu_power) as floats.
        """
        try:
            result = subprocess.run(
                ['powermetrics', '--samplers', 'gpu_power', '-n', '1', '-i', '1000'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                gpu_util = 0.0
                gpu_power = 0.0
                for line in result.stdout.split('\n'):
                    line = line.strip()
                    if 'GPU active percentage' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            val = parts[-1].strip().rstrip('%')
                            gpu_util = float(val)
                    elif line.startswith('GPU power:'):
                        parts = line.split(':')
                        if len(parts) >= 2:
                            val = parts[-1].strip().split()[0]
                            gpu_power = float(val)
                return gpu_util, gpu_power
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return 0.0, 0.0

    # ---- GPU processes ------------------------------------------------

    def get_gpu_processes(self) -> None:
        """
        Read active GPU processes.

        Linux (NVIDIA): uses nvidia-smi --query-compute-apps, enriches with
        psutil for user, host memory, and command line.
        macOS (Apple): queries psutil for processes using GPU-accelerated
        frameworks (Metal/OpenCL) — best-effort via /proc-like process info.
        """
        try:
            if self._gpu_backend == 'nvidia':
                self._get_gpu_processes_nvidia()
            elif self._gpu_backend == 'apple':
                self._get_gpu_processes_apple()
            else:
                self.gpu_processes = []
        except KeyboardInterrupt:
            raise
        except Exception:
            self.gpu_processes = []

    def _get_gpu_processes_nvidia(self) -> None:
        """Get NVIDIA GPU compute apps via nvidia-smi, enriched with psutil."""
        result = subprocess.run(
            [
                'nvidia-smi',
                f'--query-compute-apps={GPU_COMPUTE_QUERY}',
                '--format=csv,noheader,nounits'
            ],
            capture_output=True, text=True, timeout=5
        )

        if result.returncode == 0:
            processes = []
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 3:
                    try:
                        pid = int(parts[0].strip())
                        mem_used = float(parts[-1].strip())
                        process_name = ','.join(parts[1:-1]).strip()
                        enriched = self._enrich_process(pid, process_name, mem_used)
                        processes.append(enriched)
                    except (ValueError, IndexError):
                        pass

            processes.sort(key=lambda x: x['mem_used'], reverse=True)
            self.gpu_processes = processes[:MAX_GPU_PROCS]
        else:
            self.gpu_processes = []

    def _get_gpu_processes_apple(self) -> None:
        """
        Get GPU-active processes on macOS.

        Uses psutil to find processes, then filters for those with GPU
        activity (best-effort). On macOS, there's no per-process GPU memory
        query without sudo. We show the top CPU-intensive processes as a
        proxy, since GPU-bound workloads also show CPU activity.
        """
        processes = []
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
                try:
                    info = proc.info
                    pid = info['pid']
                    if pid is None:
                        continue

                    # Try to get user
                    try:
                        username = proc.username()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        username = 'unknown'

                    # Memory
                    mem_info = info.get('memory_info')
                    host_mem = (mem_info.rss / 1024 / 1024) if mem_info else 0.0

                    # CPU
                    cpu_pct = info.get('cpu_percent') or 0.0

                    # Command line
                    try:
                        cmdline = ' '.join(proc.cmdline())
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        cmdline = ''

                    # GPU memory: not available per-process on Apple Silicon
                    # without sudo. We use host RSS as a proxy.
                    gpu_mem = 0.0

                    processes.append({
                        'pid': pid,
                        'user': username,
                        'mem_used': gpu_mem,
                        'host_mem': host_mem,
                        'cpu_pct': cpu_pct,
                        'process_name': info.get('name', 'unknown') or 'unknown',
                        'cmdline': cmdline,
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Sort by host memory as proxy for GPU activity
            processes.sort(key=lambda x: x['host_mem'], reverse=True)
            self.gpu_processes = processes[:MAX_GPU_PROCS]
        except Exception:
            self.gpu_processes = []

    @staticmethod
    def _enrich_process(pid: int, process_name: str, gpu_mem: float) -> Dict[str, Any]:
        """Enrich a GPU process with user, host memory, CPU %, and cmdline via psutil."""
        user = "unknown"
        host_mem = 0.0
        cpu_pct = 0.0
        cmdline = ""

        try:
            p = psutil.Process(pid)

            # User
            try:
                user = p.username().split('/')[-1]  # Handle 'domain\\user' on Windows
            except (psutil.AccessDenied, AttributeError):
                # Fallback: try pwd lookup on Linux
                try:
                    import pwd as _pwd
                    uid = p.uids().real
                    user = _pwd.getpwuid(uid).pw_name
                except (KeyError, NameError, psutil.NoSuchProcess):
                    pass

            # Host memory (RSS in MB)
            try:
                mem = p.memory_info()
                host_mem = mem.rss / 1024 / 1024
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            # CPU %
            try:
                cpu_pct = p.cpu_percent(interval=0.1)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            # Command line
            try:
                cmdline = ' '.join(p.cmdline())
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

        except psutil.NoSuchProcess:
            pass

        return {
            'pid': pid,
            'user': user,
            'mem_used': gpu_mem,
            'host_mem': host_mem,
            'cpu_pct': cpu_pct,
            'process_name': process_name,
            'cmdline': cmdline,
        }
    
    def update_stats(self) -> None:
        """Update all system and GPU statistics.
        
        System stats are read sequentially; GPU stats and GPU processes
        are queried in parallel since they run independent nvidia-smi calls.
        """
        self.get_system_stats()
        self._get_gpu_data_parallel()
    
    def _get_gpu_data_parallel(self) -> None:
        """Run get_gpu_stats() and get_gpu_processes() concurrently."""
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = {
                    pool.submit(self.get_gpu_stats): 'stats',
                    pool.submit(self.get_gpu_processes): 'processes',
                }
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except KeyboardInterrupt:
                        raise
                    except Exception:
                        # Errors handled inside each method
                        pass
        except KeyboardInterrupt:
            raise
    
    def draw_bar(
            self, stdscr, y: int, x: int, percent: float, width: int, color_pair: int
        ) -> None:
        """
        Draw a progress bar with filled and empty blocks.
        
        Args:
            stdscr: Curses window
            y, x: Position
            percent: Percentage (0-100)
            width: Number of blocks
            color_pair: Curses color pair ID
        
        Note: Shows at least 1 filled block if percent > 0.
        """
        percent = max(0, min(100, percent))
        
        if percent > 0:
            filled = max(1, int(percent / 100.0 * width))
        else:
            filled = 0
        filled = min(filled, width)
        empty = width - filled
        
        try:
            stdscr.attron(curses.color_pair(color_pair) | curses.A_BOLD)
            if filled > 0:
                stdscr.addstr(y, x, '█' * filled)
            stdscr.attroff(curses.color_pair(color_pair) | curses.A_BOLD)
            
            if empty > 0:
                stdscr.addstr(y, x + filled, '░' * empty)
        except curses.error:
            pass
    
    def _show_help(self, stdscr) -> None:
        """Show a styled help popup (white-on-blue, blocking until key press)."""
        # Draw the dashboard underneath first
        self.draw(stdscr)
        
        h, w = stdscr.getmaxyx()
        box_w = min(36, w - 2)
        box_h = 10
        
        start_y = max(0, (h - box_h) // 2)
        start_x = max(0, (w - box_w) // 2)
        
        popup_attr = curses.color_pair(COLOR_POPUP)  # White on blue
        
        try:
            stdscr.nodelay(False)  # Block on getch
            
            # Draw colored background box
            for row in range(box_h):
                stdscr.attron(popup_attr)
                stdscr.addnstr(start_y + row, start_x, " " * box_w, box_w)
            stdscr.attroff(popup_attr)
            
            # Draw border
            stdscr.attron(popup_attr)
            stdscr.addnstr(start_y, start_x, "+" + "-" * (box_w - 2) + "+", box_w)
            stdscr.addnstr(start_y + box_h - 1, start_x, "+" + "-" * (box_w - 2) + "+", box_w)
            for row in range(1, box_h - 1):
                stdscr.addnstr(start_y + row, start_x, "|", 1)
                stdscr.addnstr(start_y + row, start_x + box_w - 1, "|", 1)
            stdscr.attroff(popup_attr)
            
            # Title - yellow bold on blue
            title = " KEYBINDINGS "
            title_x = start_x + (box_w - len(title)) // 2
            stdscr.attron(curses.color_pair(COLOR_SWAP) | curses.A_BOLD)
            stdscr.addnstr(start_y + 1, title_x, title, box_w)
            stdscr.attroff(curses.color_pair(COLOR_SWAP) | curses.A_BOLD)
            
            # Divider
            stdscr.attron(popup_attr)
            stdscr.addnstr(start_y + 2, start_x + 1, "-" * (box_w - 2), box_w - 2)
            stdscr.attroff(popup_attr)
            
            # Help lines - white on blue background
            help_lines = [
                " q  - Quit",
                " r  - Refresh now",
                " h  - Show help (this)",
                " ←→ - Scroll process table",
            ]
            for i, line in enumerate(help_lines):
                pad = " " + line.ljust(box_w - 3)
                stdscr.attron(popup_attr)
                stdscr.addnstr(start_y + 3 + i, start_x + 1, pad, box_w - 2)
                stdscr.attroff(popup_attr)
            
            # Footer prompt
            prompt = " Press any key ".center(box_w - 2)
            stdscr.attron(popup_attr)
            stdscr.addnstr(start_y + box_h - 2, start_x + 1, prompt, box_w - 2)
            stdscr.attroff(popup_attr)
            
            stdscr.refresh()
            stdscr.getch()  # Block until key press
        except curses.error:
            pass
        
        # Restore nodelay for main loop
        stdscr.nodelay(True)
    
    def _wrap_command(self, cmd: str, width: int) -> List[str]:
        """
        Word-wrap a command string to fit within `width` columns.

        Handles:
        - Long paths split on '/' with proper rejoining across lines
        - Flag-value pairing (--flag value stays together on one line)
        - Flag + multi-segment path (continuation segments tracked via __PCONT__)
        - Multiple/extra spaces normalized away
        - Single words exceeding width are hard-chunked as last resort

        Args:
            cmd: The command string to wrap
            width: Maximum line width in columns

        Returns:
            List of lines, each <= width characters
        """
        if not cmd or not cmd.strip():
            return [""]

        # Short internal markers (prefix length = len(marker))
        PSG = "__PSG__"
        PCONT = "__PCONT__"
        FV = "__FLAGVAL__"

        # Split on whitespace (handles multiple spaces, tabs, etc.)
        cmd_words = cmd.split()

        # Phase 1: Split long path-like words on '/' for readability
        MAX_WORD_LEN = 50
        expanded_words: List[str] = []
        for word in cmd_words:
            if len(word) > MAX_WORD_LEN and '/' in word:
                parts = [p for p in word.split('/') if p]
                if parts:
                    for part in parts:
                        expanded_words.append(f"{PSG}{part}")
                else:
                    expanded_words.append(word)
            else:
                expanded_words.append(word)

        # Phase 2: Flag-value pairing
        # A flag starts with '-'. It pairs with the next non-flag token.
        # For flag + path segments: pair with first segment, mark rest as PCONT
        # so they can wrap independently while maintaining / joins.
        paired_words: List[str] = []
        i = 0
        while i < len(expanded_words):
            word = expanded_words[i]
            is_flag = word.startswith('-') and not word.startswith(PSG)
            if is_flag and i + 1 < len(expanded_words):
                next_word = expanded_words[i + 1]
                next_is_flag = next_word.startswith('-') and not next_word.startswith(PSG)
                if not next_is_flag:
                    if next_word.startswith(PSG):
                        val = next_word[len(PSG):]
                        paired_words.append(f"{word}{FV}{val}")
                        j = i + 2
                        while j < len(expanded_words) and expanded_words[j].startswith(PSG):
                            paired_words.append(f"{PCONT}{expanded_words[j][len(PSG):]}")
                            j += 1
                        i = j
                    else:
                        paired_words.append(f"{word}{FV}{next_word}")
                        i += 2
                    continue
            paired_words.append(word)
            i += 1

        # Phase 3: Word wrapping with path context tracking
        lines: List[str] = []
        current_line = ""
        in_path = False

        for token in paired_words:
            is_psg = token.startswith(PSG)
            is_pcont = token.startswith(PCONT)
            is_flag_pair = FV in token and not is_psg

            # Resolve display text and whether this token is a path segment
            token_in_path = False
            if is_psg:
                display = token[len(PSG):]
                token_in_path = True
            elif is_pcont:
                display = token[len(PCONT):]
                token_in_path = True
            elif is_flag_pair:
                parts = token.split(FV, 1)
                val = parts[1]
                was_path = val.startswith(PSG)
                if was_path:
                    val = val[len(PSG):]
                display = parts[0] + " " + val
                token_in_path = was_path  # True if value came from path segments
            else:
                display = token

            if not current_line:
                current_line = display
                in_path = token_in_path
            elif in_path and token_in_path:
                # Both in path context: join with /
                if len(current_line) + 1 + len(display) <= width:
                    current_line += "/" + display
                else:
                    lines.append(current_line)
                    current_line = display
                    in_path = True
            elif len(current_line) + 1 + len(display) <= width:
                # Normal fit — PCONT after a path start should join with /
                if token_in_path and is_pcont:
                    current_line += "/" + display
                else:
                    current_line += " " + display
                    in_path = token_in_path
            else:
                lines.append(current_line)
                current_line = display
                in_path = token_in_path

        if current_line:
            lines.append(current_line)

        # Phase 4: Hard-chunk any remaining lines still exceeding width
        final_lines: List[str] = []
        for line in lines:
            if len(line) > width:
                for chunk_start in range(0, len(line), width):
                    final_lines.append(line[chunk_start:chunk_start + width])
            else:
                final_lines.append(line)

        return final_lines

    def _draw_memory_section(self, stdscr, y: int, x: int) -> int:
        """
        Draw the system memory monitoring section (Mem and Swap in 2 columns).
        
        Args:
            stdscr: Curses window
            y: Starting row position
            x: Column position
            
        Returns:
            Next y position after the section
        """
        try:
            # Box header
            stdscr.addstr(y, x, "┌" + "─" * (BOX_WIDTH - 2) + "┐")
            y += 1
            stdscr.addstr(y, x, "│ SYSTEM MEMORY".ljust(BOX_WIDTH - 1) + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1
            
            # Calculate column positions
            # Left: "│ Mem:" (6) + bar (20) + info (~15) = ~41 chars
            # Right starts after that
            left_col_width = 6 + BAR_WIDTH + 16  # ~42 chars
            right_col_start = x + left_col_width
            
            # Memory (left column)
            mem_pct = self.system_data.get('mem_percent', 0)
            used_gb = self.system_data.get('used_mem_gb', 0)
            total_gb = self.system_data.get('total_mem_gb', 0)
            
            label = "│ Mem:"
            stdscr.addstr(y, x, label)
            self.draw_bar(stdscr, y, x + 6, mem_pct, BAR_WIDTH, COLOR_MEMORY)
            mem_info = f" {used_gb:5.1f}GB/{total_gb:4.1f}G {mem_pct:5.1f}%"
            stdscr.addstr(y, x + 6 + BAR_WIDTH, mem_info)
            
            # Swap (right column)
            swap_pct = self.system_data.get('swap_percent', 0)
            swap_used_gb = self.system_data.get('swap_used_mb', 0) / 1024
            swap_total_gb = self.system_data.get('swap_total_mb', 0) / 1024
            
            right_label = "Swap:"
            stdscr.addstr(y, right_col_start, right_label)
            self.draw_bar(stdscr, y, right_col_start + 5, swap_pct, BAR_WIDTH, COLOR_SWAP)
            swap_info = f" {swap_used_gb:4.1f}/{swap_total_gb:4.1f}GB {swap_pct:5.1f}%"
            stdscr.addstr(y, right_col_start + 5 + BAR_WIDTH, swap_info)
            
            # Close the box
            stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
            y += 1
            
            # Box footer
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass
        
        return y
    
    def _draw_cpu_section(self, stdscr, y: int, x: int, height: int) -> int:
        """
        Draw the CPU monitoring section (overall + per-core in 2 columns).
        
        Args:
            stdscr: Curses window
            y: Starting row position
            x: Column position
            height: Terminal height (for bounds checking)
            
        Returns:
            Next y position after the section
        """
        try:
            # Box header. Put overall CPU usage in the title to save one row on
            # short iPad/mobile SSH terminals.
            core_count = self.system_data.get('core_count', 0)
            cpu_pct = self.system_data.get('cpu_usage', 0)
            stdscr.addstr(y, x, "┌" + "─" * (BOX_WIDTH - 2) + "┐")
            y += 1
            cpu_title = f" CPU ({core_count} cores, overall {cpu_pct:5.1f}%)"
            stdscr.addstr(y, x, ("│" + cpu_title).ljust(BOX_WIDTH - 1) + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1
            
            # Per-core usage in TWO columns
            per_core = self.system_data.get('per_core_usage', [])
            
            # Calculate split point (half the cores in each column)
            mid_point = (core_count + 1) // 2
            
            # Draw cores in two columns. Build each row as one complete string
            # instead of several positioned addstr/draw_bar calls; this avoids
            # stale characters and cursor-position weirdness on narrow/mobile
            # terminals where partial curses writes can visually drift.
            content_width = BOX_WIDTH - 4  # -2 for borders, -2 for side padding
            gap_width = 2
            col_width = (content_width - gap_width) // 2
            right_width = content_width - gap_width - col_width

            def bar_parts(percent: float, width: int) -> Tuple[str, int]:
                pct = max(0, min(100, percent))
                if pct > 0:
                    filled = max(1, int(pct / 100.0 * width))
                else:
                    filled = 0
                filled = min(filled, width)
                return "█" * filled + "░" * (width - filled), filled

            # Keep labels aligned for Core 10+ but do not waste a whole chunk of
            # spaces before the bar. The percentage moves after the bar, so the
            # utilization graphic starts almost immediately after `Core n:`.
            label_width = len(f"Core {max(0, core_count - 1)}:") + 1

            def core_cell(core_id: int, core_pct: float, width: int) -> Tuple[str, int, int]:
                label = f"Core {core_id}:".ljust(label_width)
                pct_text = f" {core_pct:5.1f}%"
                bar_width = max(1, width - len(label) - len(pct_text))
                bar, filled = bar_parts(core_pct, bar_width)
                text = label + bar + pct_text
                return text[:width].ljust(width), len(label), filled

            for i in range(mid_point):
                if y >= height - 3:
                    break  # Don't draw off-screen

                left = "".ljust(col_width)
                left_bar_start = 0
                left_filled = 0
                if i < len(per_core):
                    core_id, core_pct = per_core[i]
                    left, left_bar_start, left_filled = core_cell(core_id, core_pct, col_width)

                right = "".ljust(right_width)
                right_bar_start = 0
                right_filled = 0
                right_idx = i + mid_point
                if right_idx < len(per_core):
                    core_id, core_pct = per_core[right_idx]
                    right, right_bar_start, right_filled = core_cell(core_id, core_pct, right_width)

                line = "│ " + left + " " * gap_width + right + " │"
                stdscr.addstr(y, x, line[:BOX_WIDTH])

                # Restore colored CPU utilization bars without returning to the
                # old many-position write pattern. Draw the full stable row once,
                # then overlay only the filled bar blocks with the CPU color.
                cpu_attr = curses.color_pair(COLOR_CPU) | curses.A_BOLD
                if left_filled > 0:
                    stdscr.addstr(y, x + 2 + left_bar_start, "█" * left_filled, cpu_attr)
                if right_filled > 0:
                    right_x = x + 2 + col_width + gap_width + right_bar_start
                    stdscr.addstr(y, right_x, "█" * right_filled, cpu_attr)
                y += 1
            
            # Box footer
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass
        
        return y
    
    def _gpu_section_title(self) -> str:
        """Return a dynamic GPU section title based on the active backend."""
        if self._gpu_backend == 'nvidia':
            return 'NVIDIA GPU(s)'
        if self._gpu_backend == 'apple':
            return 'Apple GPU'
        return 'GPU'

    def _gpu_no_data_message(self) -> str:
        """Return a helpful 'no data' message for the active backend."""
        if self._gpu_backend == 'nvidia':
            return 'No NVIDIA GPUs found or nvidia-smi not available'
        if self._gpu_backend == 'apple':
            return 'No GPU data available (powermetrics may need entitlements)'
        return 'No GPU detected or GPU monitoring unavailable'

    def _draw_gpu_section(self, stdscr, y: int, x: int, height: int) -> int:
        """
        Draw the GPU monitoring section (2-column layout).

        Handles both NVIDIA (with VRAM bar) and Apple Silicon (UMA — no
        separate VRAM, shows GPU cores instead).
        """
        try:
            # Box header
            stdscr.addstr(y, x, "┌" + "─" * (BOX_WIDTH - 2) + "┐")
            y += 1
            stdscr.addstr(y, x, ("│ " + self._gpu_section_title()).ljust(BOX_WIDTH - 1) + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1

            if not self.gpu_data:
                msg = "│ " + self._gpu_no_data_message()
                stdscr.addstr(y, x, (msg + " " * (BOX_WIDTH - len(msg) - 1))[:BOX_WIDTH-1] + "│")
                y += 1
            else:
                for gpu in self.gpu_data:
                    is_uma = gpu.get('is_uma', False)
                    gpu_cores = gpu.get('gpu_cores', 0)

                    # Calculate column positions
                    left_col_width = 42  # GPU name takes ~42 chars
                    right_col_start = x + left_col_width

                    # Row 1: GPU name (left) | Temp + Power (right) - SAME LINE
                    if is_uma:
                        # Apple Silicon: show name + core count
                        core_info = f" ({gpu_cores}-core GPU)" if gpu_cores else ""
                        gpu_name = f" {gpu['name'][:35]}{core_info}"
                    else:
                        gpu_name = f"GPU {gpu['idx']}: {gpu['name'][:35]}"

                    if gpu['temp'] > 0:
                        temp_power = f"Temp: {gpu['temp']:5.0f}°C  Power: {gpu['power']:6.1f}W"
                    else:
                        temp_power = f"Power: {gpu['power']:6.1f}W"

                    # Build the combined line
                    line = f"│ {gpu_name}"
                    stdscr.addstr(y, x, line)

                    # Add temp/power on the right
                    stdscr.addstr(y, right_col_start, temp_power)
                    stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                    y += 1

                    # Row 2: Util (left) | VRAM or UMA info (right)
                    # Left column: Util
                    left_label = "│ Util:"
                    stdscr.addstr(y, x, left_label)
                    self.draw_bar(stdscr, y, x + 6, gpu['gpu_util'], BAR_WIDTH, COLOR_CPU)
                    util_info = f" {gpu['gpu_util']:6.1f}%"
                    stdscr.addstr(y, x + 6 + BAR_WIDTH, util_info)

                    # Right column: VRAM (NVIDIA) or UMA info (Apple)
                    if is_uma:
                        right_label = "UMA:"
                        stdscr.addstr(y, right_col_start, right_label)
                        uma_info = " shared w/ system memory"
                        stdscr.addstr(y, right_col_start + 4, uma_info)
                    else:
                        mem_pct = (gpu['mem_used'] / gpu['mem_total']) * 100 if gpu['mem_total'] > 0 else 0
                        mem_used_gb = gpu['mem_used'] / 1024
                        mem_total_gb = gpu['mem_total'] / 1024

                        right_label = "VRAM:"
                        stdscr.addstr(y, right_col_start, right_label)
                        self.draw_bar(stdscr, y, right_col_start + 5, mem_pct, BAR_WIDTH, COLOR_VRAM)
                        vram_info = f" {mem_used_gb:5.1f}GB/{mem_total_gb:4.1f}G {mem_pct:5.1f}%"
                        stdscr.addstr(y, right_col_start + 5 + BAR_WIDTH, vram_info)

                    # Close the box
                    stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                    y += 1

                    # Separator between GPUs (if more GPUs and space available)
                    if y < height - 3 and int(gpu['idx']) < len(self.gpu_data) - 1:
                        stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
                        y += 1

            # Box footer
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass

        return y
    
    def _process_command(self, proc: Dict[str, Any]) -> str:
        """Return the display command for a GPU process."""
        if proc.get('cmdline'):
            parts = proc['cmdline'].split()
            if parts:
                base_name = os.path.basename(parts[0])
                return base_name + (' ' + ' '.join(parts[1:]) if len(parts) > 1 else '')
        return os.path.basename(proc.get('process_name', 'unknown').split(',')[0].strip())

    def _gpu_process_fixed_header(self) -> str:
        """Fixed nvtop-style GPU process table columns before Command."""
        return (
            f"{'PID':<7} {'USER':<8} {'DEV':<3} {'TYPE':<4} "
            f"{'GPU':>5} {'GPU MEM':>8} {'CPU':>6} {'HOST MEM':>8} "
        )

    def _gpu_process_table_header(self) -> str:
        """nvtop-style GPU process table header."""
        return self._gpu_process_fixed_header() + "Command"

    def _gpu_process_fixed_prefix(self, proc: Dict[str, Any]) -> str:
        """Format fixed nvtop-style GPU process columns before Command."""
        pid = proc.get('pid', 0)
        user = str(proc.get('user', 'unknown'))[:8]
        dev = str(proc.get('dev', '0'))[:3]
        proc_type = str(proc.get('type', 'C'))[:4]
        gpu_pct = proc.get('gpu_pct')
        gpu_text = f"{gpu_pct:5.1f}%" if isinstance(gpu_pct, (int, float)) else "--"
        gpu_mem = f"{proc.get('mem_used', 0):.0f}M"
        cpu_text = f"{proc.get('cpu_pct', 0.0):5.1f}%"
        host_mem = f"{proc.get('host_mem', 0):.0f}M"
        return (
            f"{pid:<7} {user:<8} {dev:<3} {proc_type:<4} "
            f"{gpu_text:>5} {gpu_mem:>8} {cpu_text:>6} {host_mem:>8} "
        )

    def _gpu_process_table_row(self, proc: Dict[str, Any]) -> str:
        """Format one nvtop-style GPU process row without horizontal clipping."""
        return self._gpu_process_fixed_prefix(proc) + self._process_command(proc)

    def _draw_scrolled_process_line(self, stdscr, y: int, x: int, fixed: str, command: str, width: int) -> None:
        """Draw one bordered process table line with fixed columns and scrolled command."""
        view_width = max(1, width - 4)  # -2 for borders, -2 for side padding
        fixed_visible = fixed[:view_width]
        cmd_width = max(0, view_width - len(fixed_visible))
        scroll = max(0, self.process_scroll_x)
        visible = fixed_visible + command[scroll:scroll + cmd_width]
        stdscr.addstr(y, x, "│ " + visible.ljust(view_width) + " │")

    def _max_process_scroll(self) -> int:
        """Maximum horizontal scroll offset for the process table command column."""
        view_width = max(1, BOX_WIDTH - 4)  # -2 for borders, -2 for side padding
        cmd_width = max(1, view_width - len(self._gpu_process_fixed_header()))
        return max(0, max((len(self._process_command(proc)) for proc in self.gpu_processes), default=0) - cmd_width)

    def _draw_gpu_processes_section(self, stdscr, y: int, x: int, height: int) -> int:
        """
        Draw the GPU processes section as an nvtop-style horizontally scrollable table.
        
        Args:
            stdscr: Curses window
            y: Starting row position
            x: Column position
            height: Terminal height (for bounds checking)
            
        Returns:
            Next y position after the section
        """
        try:
            self.process_scroll_x = min(max(0, self.process_scroll_x), self._max_process_scroll())

            # Box header
            stdscr.addstr(y, x, "┌" + "─" * (BOX_WIDTH - 2) + "┐")
            y += 1

            title = f" GPU PROCESSES  ←/→ scroll {self.process_scroll_x}"
            stdscr.addstr(y, x, ("│" + title).ljust(BOX_WIDTH - 1)[:BOX_WIDTH-1] + "│")
            y += 1

            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1

            hdr = self._gpu_process_table_header()
            stdscr.addstr(y, x, ("│ " + hdr).ljust(BOX_WIDTH - 1)[:BOX_WIDTH-1] + "│")
            y += 1

            if y < height - 3:
                stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
                y += 1
            
            if not self.gpu_processes:
                if y < height - 3:
                    self._draw_scrolled_process_line(stdscr, y, x, "", "No active GPU compute processes", BOX_WIDTH)
                    y += 1
            else:
                for proc in self.gpu_processes:
                    if y >= height - 3:
                        break  # Don't draw off-screen
                    self._draw_scrolled_process_line(stdscr, y, x, self._gpu_process_fixed_prefix(proc), self._process_command(proc), BOX_WIDTH)
                    y += 1
            
            # Box footer
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass
        
        return y
    
    def draw(self, stdscr) -> None:
        """Draw the complete UI with all monitoring sections."""
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        
        height, width = stdscr.getmaxyx()
        
        # Calculate box width dynamically (80% of terminal width, min 80, max 120)
        global BOX_WIDTH
        BOX_WIDTH = max(80, min(120, int(width * 0.85)))
        
        stdscr.erase()
        
        # Title
        title = f" termmon {__version__} - System Monitor | {datetime.now().strftime('%H:%M:%S')} | q:quit r:refresh h:help "
        try:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addstr(0, 0, title[:width-1].ljust(width-1)[:width-1])
            stdscr.attroff(curses.A_REVERSE)
        except curses.error:
            pass
        
        y = 2
        x = (width - BOX_WIDTH) // 2
        if x < 1:
            x = 1
        
        # Draw system memory section
        y = self._draw_memory_section(stdscr, y, x)
        
        # Draw CPU section
        y = self._draw_cpu_section(stdscr, y, x, height)
        
        # Draw GPU section
        y = self._draw_gpu_section(stdscr, y, x, height)
        
        # Draw GPU processes section
        y = self._draw_gpu_processes_section(stdscr, y, x, height)
        
        # Footer
        try:
            footer = f" Refresh: {REFRESH_INTERVAL}s | q:quit r:refresh h:help ←/→:process scroll "
            stdscr.attron(curses.A_REVERSE)
            stdscr.addstr(height - 1, 0, footer[:width-1].ljust(width-1)[:width-1])
            stdscr.attroff(curses.A_REVERSE)
        except curses.error:
            pass
        
        stdscr.refresh()
    
    def run(self) -> None:
        """
        Main application loop.
        
        Initializes curses, sets up colors, enters main loop with
        auto-refresh and keyboard input handling.
        
        Keybindings:
            q/Q - Quit
            r/R - Force refresh
            h/H - Show help
            ←/→ - Horizontally scroll GPU process table command column
        """
        stdscr = curses.initscr()
        
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(COLOR_TITLE, curses.COLOR_WHITE, -1)
        curses.init_pair(COLOR_MEMORY, curses.COLOR_GREEN, -1)
        curses.init_pair(COLOR_SWAP, curses.COLOR_YELLOW, -1)
        curses.init_pair(COLOR_CPU, curses.COLOR_CYAN, -1)
        curses.init_pair(COLOR_VRAM, curses.COLOR_MAGENTA, -1)
        curses.init_pair(COLOR_ERROR, curses.COLOR_RED, -1)
        curses.init_pair(COLOR_PROCESS, curses.COLOR_BLUE, -1)
        curses.init_pair(COLOR_POPUP, curses.COLOR_WHITE, curses.COLOR_BLUE)  # White on blue
        
        curses.cbreak()
        stdscr.keypad(True)
        stdscr.nodelay(True)
        
        # Handle terminal resize
        signal.signal(signal.SIGWINCH, self._on_resize)
        
        self.update_stats()
        time.sleep(0.5)
        self.update_stats()
        
        last_refresh = 0
        
        try:
            while self.running:
                current_time = time.time()
                
                # Handle terminal resize
                if self._resized:
                    self._resized = False
                    try:
                        curses.update_lines_cols()
                    except (OSError, AttributeError):
                        pass  # Some platforms don't support update_lines_cols
                    self.update_stats()
                    last_refresh = current_time  # Force refresh on resize
                
                if current_time - last_refresh >= 2:
                    self.update_stats()
                    last_refresh = current_time
                
                self.draw(stdscr)
                
                key = stdscr.getch()
                
                if key == ord('q') or key == ord('Q'):
                    self.running = False
                elif key == ord('r') or key == ord('R'):
                    self.update_stats()
                elif key == ord('h') or key == ord('H'):
                    self._show_help(stdscr)
                elif key == curses.KEY_RIGHT:
                    self.process_scroll_x = min(self._max_process_scroll(), self.process_scroll_x + 16)
                elif key == curses.KEY_LEFT:
                    self.process_scroll_x = max(0, self.process_scroll_x - 16)
                
                time.sleep(0.05)
        finally:
            curses.nocbreak()
            stdscr.keypad(False)
            curses.echo()
            curses.endwin()


if __name__ == "__main__":
    app = TermMon()

    # Warn on macOS if no GPU monitoring tool is available
    if _IS_MACOS:
        has_macmon = False
        has_socpwrbud = False
        try:
            subprocess.run(['which', 'macmon'], capture_output=True, timeout=3)
            has_macmon = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        try:
            subprocess.run(['which', 'socpwrbud'], capture_output=True, timeout=3)
            has_socpwrbud = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        if not has_macmon and not has_socpwrbud:
            print(
                "Note: GPU utilization will show 0% without sudo on macOS 13+.\n"
                "      For accurate readings, install macmon (recommended):\n"
                "      https://github.com/vladkens/macmon/releases",
                file=sys.stderr,
            )

    app.run()
