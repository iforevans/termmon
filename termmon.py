#!/usr/bin/env python3
"""
termmon - Terminal System Monitor
==================================

A unified terminal-based system monitor combining `htop` (system RAM/CPU)
and GPU monitoring (nvidia-smi on Linux / powermetrics on macOS) into a
single dashboard.

Originally created to solve the problem of monitoring CPU/system RAM/swap
and GPU/VRAM usage from one window while testing local AI models on an
RTX 3090/24GB, now running on RTX A6000/48GB.

Features:
    - System memory monitoring (RAM + swap in GB)
    - Overall and per-core CPU utilization
    - NVIDIA GPU monitoring on Linux (VRAM, utilization, temperature, power)
    - Apple Silicon GPU monitoring on macOS (utilization, power, UMA)
    - GPU process tracking (top 5 processes by memory usage)
    - Color-coded progress bars
    - Auto-refresh every 2 seconds
    - Cross-platform (Linux + macOS)
    - Instant scroll response via non-blocking stats updates

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
import fcntl
import io
import json
import os
import platform
import signal
import shutil
import struct
import subprocess
import sys
import termios
import threading
from datetime import datetime
import logging
import time
from typing import Dict, List, Tuple, Any, Optional

logger = logging.getLogger(__name__)

try:
    import psutil
except ImportError:
    print("Error: psutil is required. Install with: pip3 install psutil", file=sys.stderr)
    sys.exit(1)

# Platform detection (set once at import time)
_SYSTEM = platform.system()  # 'Linux' or 'Darwin'
_IS_MACOS = _SYSTEM == "Darwin"
_IS_LINUX = _SYSTEM == "Linux"

__version__ = "1.16.2"
__author__ = "Ifor Evans"


# Layout configuration
BAR_WIDTH = 20         # Maximum width of progress bars
MIN_BAR_WIDTH = 5      # Bars never shrink below this before layout switches mode
MAX_BOX_WIDTH = 120    # Cap so the dashboard stays readable on ultra-wide terminals
MIN_BOX_WIDTH = 24     # Below this the terminal is too small to render anything useful
REFRESH_INTERVAL = 2   # Seconds between auto-refreshes

# Responsive breakpoints (box width in columns). Derived from measured format
# string lengths — see _draw_*_section for the per-section overhead arithmetic.
MEM_TWO_COL_MIN = 70   # Mem + Swap side by side
GPU_TWO_COL_MIN = 84   # Util + VRAM side by side
GPU_HEADER_TWO_COL_MIN = 62  # GPU name + temp/power on one row

# Color pair IDs
COLOR_TITLE = 1         # White - title and footer
COLOR_MEMORY = 2        # Green - RAM usage bar
COLOR_SWAP = 3          # Yellow - swap usage bar
COLOR_CPU = 4           # Cyan - CPU usage bar
COLOR_VRAM = 5          # Magenta - VRAM usage bar
COLOR_POPUP = 6         # White on blue - help popup

# NVIDIA GPU query fields (must match nvidia-smi output order)
GPU_QUERY_FIELDS = "index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw"
# GPU compute apps: pid, process_name, used_gpu_memory (limited fields available)
GPU_COMPUTE_QUERY = "pid,process_name,used_gpu_memory"

# Maximum GPU processes to display
MAX_GPU_PROCS = 5


class TermMon:
    """Terminal-based system monitor combining htop and nvidia-smi."""
    
    # Cached GPU process table fixed header (nvtop-style columns before Command)
    _GPU_PROCESS_FIXED_HEADER: str = (
        f"{'PID':<7} {'USER':<8} {'DEV':<3} {'TYPE':<4} "
        f"{'GPU':>5} {'GPU MEM':>8} {'CPU':>6} {'HOST MEM':>8} "
    )
    _GPU_PROCESS_FIXED_HEADER_LEN: int = len(_GPU_PROCESS_FIXED_HEADER)
    
    def __init__(self) -> None:
        """Initialize the TermMon application."""
        self.running: bool = True
        self.gpu_data: List[Dict[str, Any]] = []
        self.gpu_processes: List[Dict[str, Any]] = []
        self.system_data: Dict[str, Any] = {}
        self.core_count: int = self._get_core_count()
        self._resized: bool = False  # SIGWINCH flag
        self.process_scroll_x: int = 0  # Horizontal scroll offset for nvtop-style process table
        self._stats_lock = threading.Lock()  # Protects gpu_data, gpu_processes, system_data
        self._stats_thread = None
        self._stats_update_event = threading.Event()  # Signal for background thread
        self._box_width: int = 80  # Computed per-frame in _draw_frame
        self._gpu_data_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix='termmon-gpu'
        )
    
    def _on_resize(self, signum: int, frame: Any) -> None:
        """Handle terminal resize (SIGWINCH)."""
        self._resized = True

    @staticmethod
    def _true_terminal_size() -> Optional[Tuple[int, int]]:
        """
        Ask the kernel for the real window size as (rows, cols).

        curses caches LINES/COLS at initscr() time, so stdscr.getmaxyx() still
        reports the OLD geometry immediately after SIGWINCH. Feeding that stale
        value back into resizeterm() is a no-op, which leaves the app drawing at
        the previous width — content then wraps around and overwrites itself.
        Querying TIOCGWINSZ directly avoids that trap.
        """
        for stream in (sys.stdout, sys.stdin, sys.stderr):
            try:
                packed = fcntl.ioctl(stream.fileno(), termios.TIOCGWINSZ, b'\0' * 8)
                rows, cols = struct.unpack('HHHH', packed)[:2]
                if rows > 0 and cols > 0:
                    return rows, cols
            except (OSError, ValueError, AttributeError, io.UnsupportedOperation):
                continue
        try:
            size = os.get_terminal_size()
            return size.lines, size.columns
        except OSError:
            return None

    def _apply_resize(self, stdscr) -> None:
        """
        Re-synchronise curses with the real terminal size after SIGWINCH.

        Order matters: get the true size from the kernel, tell curses about it,
        then clear so no stale cells from the larger geometry survive.
        """
        size = self._true_terminal_size()
        if size is not None:
            rows, cols = size
        else:
            rows, cols = stdscr.getmaxyx()

        try:
            curses.resizeterm(rows, cols)
        except (curses.error, AttributeError):
            try:
                curses.update_lines_cols()
            except (OSError, AttributeError):
                pass

        # Drop every stale cell — a shrink leaves characters from the old,
        # wider frame behind otherwise.
        try:
            stdscr.clear()
        except curses.error:
            pass

    @staticmethod
    def _get_core_count() -> int:
        """Read CPU core count (cross-platform)."""
        return psutil.cpu_count() or 0
        
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
                if not gpu.strip():
                    continue
                # Split on comma — GPU names can contain commas, so parse
                # carefully: field 1 is always index (no comma), fields 3-8
                # are numeric (no commas). The name is everything between
                # field 1 and field 3.
                parts = gpu.split(',')
                if len(parts) < 8:
                    continue
                try:
                    idx = parts[0].strip()
                    # Last 6 fields are numeric: mem_total, mem_used, mem_free,
                    # gpu_util, temp, power
                    numeric = parts[-6:]
                    # Name is everything between idx and the first numeric field
                    name_parts = parts[1:len(parts) - 6]
                    name = ','.join(p.strip() for p in name_parts).strip()
                    gpus.append({
                        'idx': idx,
                        'name': name,
                        'mem_total': float(numeric[0].strip()),
                        'mem_used': float(numeric[1].strip()),
                        'mem_free': float(numeric[2].strip()),
                        'gpu_util': float(numeric[3].strip()),
                        'temp': float(numeric[4].strip()),
                        'power': float(numeric[5].strip()),
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

        # --- GPU model name and core count (cached one-shot) ---
        gpu_name, gpu_cores = self._apple_gpu_metadata

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

    @property
    def _apple_gpu_metadata(self) -> Tuple[str, int]:
        """Get GPU model name and core count, cached (hardware doesn't change at runtime).

        Returns (model_name, core_count) by parsing system_profiler JSON once
        and caching the result. Replaces the separate _apple_gpu_model() and
        _apple_gpu_cores() static methods which each ran system_profiler.
        """
        if not hasattr(self, '_cached_apple_gpu_metadata'):
            self._cached_apple_gpu_metadata = self._detect_apple_gpu_metadata()
        return self._cached_apple_gpu_metadata

    @staticmethod
    def _detect_apple_gpu_metadata() -> Tuple[str, int]:
        """Query system_profiler for GPU model name and core count."""
        # Try sysctl for model name first (fast), then system_profiler for cores
        gpu_name = 'Apple GPU'
        gpu_cores = 0

        # Try sysctl -n hw.gpu.model for model name (fast, Apple Silicon)
        for key in ('hw.gpu.model', 'hw.model'):
            try:
                result = subprocess.run(
                    ['sysctl', '-n', key],
                    capture_output=True, text=True, timeout=3
                )
                if result.returncode == 0:
                    model = result.stdout.strip()
                    if key == 'hw.gpu.model' and model:
                        gpu_name = model
                        break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # Get model name + core count from system_profiler (single call)
        try:
            result = subprocess.run(
                ['system_profiler', 'SPDisplaysDataType', '-json'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                gpus = data.get('SPDisplaysDataType', [])
                if gpus:
                    gpu = gpus[0]
                    # Use system_profiler for model name if sysctl didn't find it
                    if gpu_name == 'Apple GPU':
                        gpu_name = gpu.get('spdisplays_chipset', 'Apple GPU') or 'Apple GPU'
                    # sppci_cores is the GPU core count on Apple Silicon
                    cores_str = gpu.get('sppci_cores')
                    if cores_str:
                        gpu_cores = int(cores_str)
        except (FileNotFoundError, subprocess.TimeoutExpired,
                json.JSONDecodeError, ValueError):
            pass

        return gpu_name, gpu_cores

    @staticmethod
    def _apple_gpu_util_and_power() -> Tuple[float, float]:
        """
        Get GPU active percentage and power draw.

        Multi-tier fallback (all non-sudo):
          1. macmon    — actively maintained, reads IOReport + SMC (no sudo, best data)
          2. socpwrbud — archived but functional IOReport reader (no sudo)
          3. powermetrics — Apple's built-in tool (requires sudo on macOS 13+)
          4. Returns (0, 0) if none succeed

        Each tier returns Optional[Tuple[float, float]] — None means the tool
        is unavailable, (0.0, 0.0) means it ran but the GPU was genuinely idle.
        This distinguishes "tool missing" from "GPU at 0% utilization".
        """
        for tier_fn in (
            TermMon._apple_gpu_util_macmon,
            TermMon._apple_gpu_util_socpwrbud,
            TermMon._apple_gpu_util_powermetrics,
        ):
            result = tier_fn()
            if result is not None:
                return result
        return 0.0, 0.0

    @staticmethod
    def _apple_gpu_util_macmon() -> Optional[Tuple[float, float]]:
        """
        Get GPU utilization from macmon (sudoless IOReport + SMC reader).

        macmon is an actively maintained Rust tool (1.6k stars) that reads
        GPU performance counters from IOReport without sudo. Available via:
          - Homebrew:  brew install vladkens/tap/macmon
          - Release:   https://github.com/vladkens/macmon/releases

        Returns None if macmon is unavailable, (gpu_util_pct, gpu_power_watts)
        if it ran (even if both values are 0.0 for a truly idle GPU).
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
        return None

    @staticmethod
    def _apple_gpu_util_socpwrbud() -> Optional[Tuple[float, float]]:
        """
        Get GPU utilization from socpwrbud (sudoless IOReport reader).

        socpwrbud is a third-party tool that reads GPU performance counters
        directly from IOReport without requiring sudo. Archived but still
        functional on many Apple Silicon chips.

        Returns None if socpwrbud is unavailable, (gpu_util, 0.0) if it ran.
        Note: socpwrbud does not report power draw, so power is always 0.0
        when this tier succeeds.
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
                return gpu_util, 0.0
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    @staticmethod
    def _apple_gpu_util_powermetrics() -> Optional[Tuple[float, float]]:
        """
        Get GPU utilization from powermetrics (Apple's built-in tool).

        Note: On macOS 13+ (Ventura and later), powermetrics requires sudo
        to access GPU power sampler data. Without sudo, it returns zeros.
        However, the tool itself runs successfully (returncode 0) — it
        just doesn't have permission to read the counters, so we return
        (0.0, 0.0) to indicate "ran but no data" rather than None.

        Returns None if powermetrics binary is unavailable,
        (gpu_util, gpu_power) if it ran.
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
                            try:
                                gpu_util = float(val)
                            except ValueError:
                                pass
                    elif line.startswith('GPU power:'):
                        parts = line.split(':')
                        if len(parts) >= 2:
                            val = parts[-1].strip().split()[0]
                            try:
                                gpu_power = float(val)
                            except (ValueError, IndexError):
                                pass
                return gpu_util, gpu_power
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

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

    @staticmethod
    def _seed_cpu_percent(pids: List[int]) -> None:
        """Seed cpu_percent for a set of PIDs so the next read returns real values.

        psutil.Process.cpu_percent(interval=None) returns 0.0 on the first call
        because it needs a baseline. We seed all candidate PIDs here so that
        the enrichment pass below gets meaningful CPU percentages.
        """
        for pid in pids:
            try:
                psutil.Process(pid).cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

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
            pids: List[int] = []
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 3:
                    try:
                        pid = int(parts[0].strip())
                        pids.append(pid)
                    except (ValueError, IndexError):
                        pass

            # Seed cpu_percent so enrichment reads are accurate
            self._seed_cpu_percent(pids)

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

        Two-pass approach: first pass collects cheap metadata only, sorts,
        and picks top-N; second pass enriches those with cmdline() to avoid
        iterating all processes with expensive per-process calls.
        """
        processes = []
        try:
            # --- Pass 1: collect cheap metadata ---
            candidates = []
            for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
                try:
                    info = proc.info
                    pid = info['pid']
                    if pid is None:
                        continue

                    mem_info = info.get('memory_info')
                    host_mem = (mem_info.rss / 1024 / 1024) if mem_info else 0.0

                    candidates.append({
                        'pid': pid,
                        'host_mem': host_mem,
                        'process_name': info.get('name', 'unknown') or 'unknown',
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Sort by host memory as proxy for GPU activity, take top-N
            candidates.sort(key=lambda x: x['host_mem'], reverse=True)
            top_n = candidates[:MAX_GPU_PROCS]

            # Seed cpu_percent for the top-N PIDs so enrichment reads are accurate
            self._seed_cpu_percent([c['pid'] for c in top_n])

            # --- Pass 2: enrich top-N with user + cmdline ---
            for c in top_n:
                pid = c['pid']
                username = 'unknown'
                cpu_pct = 0.0
                cmdline = ''
                try:
                    p = psutil.Process(pid)
                    try:
                        username = p.username()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    try:
                        cpu_pct = p.cpu_percent(interval=None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    try:
                        cmdline = ' '.join(p.cmdline())
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                except psutil.NoSuchProcess:
                    pass

                processes.append({
                    'pid': pid,
                    'user': username,
                    'mem_used': 0.0,
                    'host_mem': c['host_mem'],
                    'cpu_pct': cpu_pct,
                    'process_name': c['process_name'],
                    'cmdline': cmdline,
                })

            self.gpu_processes = processes
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
                except (KeyError, psutil.NoSuchProcess):
                    pass

            # Host memory (RSS in MB)
            try:
                mem = p.memory_info()
                host_mem = mem.rss / 1024 / 1024
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            # CPU % — non-blocking; requires prior seeding call
            try:
                cpu_pct = p.cpu_percent(interval=None)
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
    
    def _stats_updater_thread(self) -> None:
        """Background thread that updates stats at regular intervals.

        Drives itself on a REFRESH_INTERVAL timer so stats stay fresh even
        when the main loop is blocked (e.g., help popup).  The event is an
        additional trigger for an immediate update (e.g., 'r' key).
        """
        last_update = time.monotonic()
        while self.running:
            # Wake on explicit signal OR after REFRESH_INTERVAL has elapsed.
            self._stats_update_event.wait(timeout=0.1)
            self._stats_update_event.clear()

            # Throttle: don't collect more often than REFRESH_INTERVAL.
            now = time.monotonic()
            if now - last_update < REFRESH_INTERVAL:
                continue
            last_update = now
            # Collect into local copies so we don't hold the lock during
            # expensive subprocess-adjacent system calls (nvidia-smi, etc.)
            new_sysdata = {}
            new_gpus = []
            new_procs = []

            # --- system stats (collected inline in background thread) ---
            try:
                vm = psutil.virtual_memory()
                swap = psutil.swap_memory()
                per_core_raw = psutil.cpu_percent(percpu=True)
                per_core_usage = list(enumerate(per_core_raw))
                new_sysdata = {
                    'total_mem_gb': vm.total / 1024**3,
                    'used_mem_gb': vm.used / 1024**3,
                    'avail_mem_gb': vm.available / 1024**3,
                    'mem_percent': vm.percent,
                    'swap_total_mb': swap.total / 1024**2,
                    'swap_used_mb': swap.used / 1024**2,
                    'swap_percent': swap.percent,
                    'cpu_usage': sum(per_core_raw) / max(len(per_core_raw), 1),
                    'core_count': self.core_count,
                    'per_core_usage': per_core_usage,
                }
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error("Failed to collect system stats: %s", e)
                new_sysdata = {}

            # --- GPU stats + processes (parallel) ---
            try:
                self._get_gpu_data_parallel()
                # Snapshot the just-written values (they live in self.* now)
                with self._stats_lock:
                    new_gpus = list(self.gpu_data)
                    new_procs = list(self.gpu_processes)
            except KeyboardInterrupt:
                raise
            except Exception:
                pass  # leave new_gpus / new_procs as []

            # --- Atomic swap under the lock ---
            with self._stats_lock:
                self.system_data = new_sysdata
                self.gpu_data = new_gpus
                self.gpu_processes = new_procs

    def update_stats(self) -> None:
        """Signal background thread to update stats (non-blocking)."""
        self._stats_update_event.set()
    
    def _get_gpu_data_parallel(self) -> None:
        """Run get_gpu_stats() and get_gpu_processes() concurrently."""
        try:
            futures = {
                self._gpu_data_executor.submit(self.get_gpu_stats): 'stats',
                self._gpu_data_executor.submit(self.get_gpu_processes): 'processes',
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
    
    def _safe_addstr(
            self, stdscr, y: int, x: int, text: str, attr: int = 0, max_x: int = 0
        ) -> None:
        """
        Write text at (y, x), clipped to terminal bounds and optionally to max_x.

        This is the single choke point that makes the UI responsive: curses
        wraps writes that run past the right edge onto the next line, which
        overwrites content already drawn there. Clipping every write kills that
        class of bug outright.

        Args:
            stdscr: Curses window
            y, x: Target position
            text: Text to write
            attr: Optional curses attribute bundle
            max_x: Optional exclusive right boundary (e.g. the box border
                   column) so content cannot leak past a box edge even when it
                   would still fit on the terminal.
        """
        try:
            h, w = stdscr.getmaxyx()
        except curses.error:
            return

        if y < 0 or y >= h or x >= w:
            return

        # Negative x: drop the leading chars that fall off the left edge.
        if x < 0:
            text = text[-x:]
            x = 0

        max_len = w - x
        if max_x > 0:
            max_len = min(max_len, max_x - x)
        if max_len <= 0:
            return

        if len(text) > max_len:
            text = text[:max_len]
        if not text:
            return

        try:
            if attr:
                stdscr.addstr(y, x, text, attr)
            else:
                stdscr.addstr(y, x, text)
        except curses.error:
            # Writing the terminal's very last cell legitimately raises.
            pass

    @staticmethod
    def _bar_width(bw: int, two_col: bool, overhead: int) -> int:
        """
        Compute a progress-bar width that actually fits the current box.

        Args:
            bw: Current box width
            two_col: True when the bar appears twice on the same row
            overhead: Chars on the row consumed by borders, labels, info
                      strings and gaps (everything that is not a bar)

        Returns:
            Bar width clamped to [MIN_BAR_WIDTH, BAR_WIDTH]
        """
        available = bw - 2 - overhead  # -2 for the box borders
        if two_col:
            available //= 2
        return max(MIN_BAR_WIDTH, min(BAR_WIDTH, available))

    def draw_bar(
            self, stdscr, y: int, x: int, percent: float, width: int,
            color_pair: int, max_x: int = 0
        ) -> None:
        """
        Draw a progress bar with filled and empty blocks.
        
        Args:
            stdscr: Curses window
            y, x: Position
            percent: Percentage (0-100)
            width: Number of blocks
            color_pair: Curses color pair ID
            max_x: Optional exclusive right boundary (box border column)
        
        Note: Shows at least 1 filled block if percent > 0.
        """
        percent = max(0, min(100, percent))
        
        if percent > 0:
            filled = max(1, int(percent / 100.0 * width))
        else:
            filled = 0
        filled = min(filled, width)
        empty = width - filled

        if filled > 0:
            self._safe_addstr(
                stdscr, y, x, '█' * filled,
                curses.color_pair(color_pair) | curses.A_BOLD, max_x,
            )
        if empty > 0:
            self._safe_addstr(stdscr, y, x + filled, '░' * empty, 0, max_x)
    
    def _show_help(self, stdscr) -> None:
        """Show a styled help popup (white-on-blue, blocking until key press)."""
        # Draw the dashboard underneath first
        self.draw(stdscr)
        
        h, w = stdscr.getmaxyx()

        help_lines = [
            " q  - Quit",
            " r  - Refresh now",
            " h  - Show help (this)",
            " ←→ - Scroll process table",
        ]

        # Popup must fit the terminal: shrink width, and drop help lines before
        # letting the box run off the bottom.
        box_w = min(36, max(0, w - 2))
        max_h = max(0, h - 2)
        box_h = min(len(help_lines) + 6, max_h)
        if box_w < 12 or box_h < 6:
            return  # No room for a legible popup — silently skip
        visible_lines = help_lines[:box_h - 6]
        
        start_y = max(0, (h - box_h) // 2)
        start_x = max(0, (w - box_w) // 2)
        
        popup_attr = curses.color_pair(COLOR_POPUP)  # White on blue
        
        try:
            stdscr.timeout(-1)  # Block on getch (run() uses timeout(50))
            
            # Draw colored background box
            for row in range(box_h):
                self._safe_addstr(stdscr, start_y + row, start_x, " " * box_w, popup_attr)
            
            # Draw border
            self._safe_addstr(
                stdscr, start_y, start_x, "+" + "-" * (box_w - 2) + "+", popup_attr,
            )
            self._safe_addstr(
                stdscr, start_y + box_h - 1, start_x, "+" + "-" * (box_w - 2) + "+", popup_attr,
            )
            for row in range(1, box_h - 1):
                self._safe_addstr(stdscr, start_y + row, start_x, "|", popup_attr)
                self._safe_addstr(stdscr, start_y + row, start_x + box_w - 1, "|", popup_attr)
            
            # Title - yellow bold on blue
            title = " KEYBINDINGS "
            title_x = start_x + max(0, (box_w - len(title)) // 2)
            self._safe_addstr(
                stdscr, start_y + 1, title_x, title,
                curses.color_pair(COLOR_SWAP) | curses.A_BOLD, start_x + box_w - 1,
            )
            
            # Divider
            self._safe_addstr(
                stdscr, start_y + 2, start_x + 1, "-" * (box_w - 2),
                popup_attr, start_x + box_w - 1,
            )
            
            # Help lines - white on blue background
            for i, line in enumerate(visible_lines):
                pad = " " + line.ljust(box_w - 3)
                self._safe_addstr(
                    stdscr, start_y + 3 + i, start_x + 1, pad[:box_w - 2],
                    popup_attr, start_x + box_w - 1,
                )
            
            # Footer prompt
            prompt = " Press any key ".center(box_w - 2)
            self._safe_addstr(
                stdscr, start_y + box_h - 2, start_x + 1, prompt[:box_w - 2],
                popup_attr, start_x + box_w - 1,
            )
            
            stdscr.refresh()
            stdscr.getch()  # Block until key press
        except curses.error:
            pass
        
        # Restore the 50ms timeout the main loop expects
        stdscr.timeout(50)
    
    def _draw_memory_section(self, stdscr, y: int, x: int, snapshot: Dict[str, Any], bw: int) -> int:
        """
        Draw the system memory monitoring section (Mem and Swap in 2 columns).

        Args:
            stdscr: Curses window
            y: Starting row position
            x: Column position
            snapshot: Thread-safe data snapshot
            bw: Box width for this frame

        Returns:
            Next y position after the section
        """
        sysdata = snapshot['system_data']
        right_edge = x + bw          # exclusive terminal-side boundary
        border_x = x + bw - 1        # column of the closing "│"

        # Box header
        self._safe_addstr(stdscr, y, x, "┌" + "─" * (bw - 2) + "┐", 0, right_edge)
        y += 1
        self._safe_addstr(stdscr, y, x, ("│ SYSTEM MEMORY").ljust(bw - 1) + "│", 0, right_edge)
        y += 1
        self._safe_addstr(stdscr, y, x, "│" + "─" * (bw - 2) + "│", 0, right_edge)
        y += 1

        mem_pct = sysdata.get('mem_percent', 0)
        used_gb = sysdata.get('used_mem_gb', 0)
        total_gb = sysdata.get('total_mem_gb', 0)
        swap_pct = sysdata.get('swap_percent', 0)
        swap_used_gb = sysdata.get('swap_used_mb', 0) / 1024
        swap_total_gb = sysdata.get('swap_total_mb', 0) / 1024

        mem_info = f" {used_gb:5.1f}GB/{total_gb:4.1f}G {mem_pct:5.1f}%"
        swap_info = f" {swap_used_gb:4.1f}/{swap_total_gb:4.1f}GB {swap_pct:5.1f}%"

        # Overhead = "│ Mem: "(7) + info + gap(2) + "Swap:"(5) + info + "│"(1)
        two_col = bw >= MEM_TWO_COL_MIN
        gap = 2

        if two_col:
            overhead = 7 + len(mem_info) + gap + 5 + len(swap_info) + 1
            bar_w = self._bar_width(bw, True, overhead)

            # Blank the row first so nothing from a previous frame survives.
            self._safe_addstr(stdscr, y, x, "│" + " " * (bw - 2) + "│", 0, right_edge)

            self._safe_addstr(stdscr, y, x, "│ Mem:", 0, right_edge)
            self.draw_bar(stdscr, y, x + 7, mem_pct, bar_w, COLOR_MEMORY, border_x)
            self._safe_addstr(stdscr, y, x + 7 + bar_w, mem_info, 0, border_x)

            # Right column starts immediately after the left column's content.
            right_col_start = x + 7 + bar_w + len(mem_info) + gap
            self._safe_addstr(stdscr, y, right_col_start, "Swap:", 0, border_x)
            self.draw_bar(stdscr, y, right_col_start + 5, swap_pct, bar_w, COLOR_SWAP, border_x)
            self._safe_addstr(stdscr, y, right_col_start + 5 + bar_w, swap_info, 0, border_x)

            self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
            y += 1
        else:
            # Narrow: stack Mem and Swap on their own rows. Shorten the info
            # strings progressively so values are never clipped mid-number.
            content_width = bw - 4
            if content_width < 7 + MIN_BAR_WIDTH + len(mem_info) - 2:
                mem_info = f" {used_gb:.1f}/{total_gb:.1f}G {mem_pct:.0f}%"
                swap_info = f" {swap_used_gb:.1f}/{swap_total_gb:.1f}G {swap_pct:.0f}%"
            if content_width < 7 + MIN_BAR_WIDTH + len(mem_info) - 2:
                mem_info = f" {mem_pct:.0f}%"
                swap_info = f" {swap_pct:.0f}%"

            overhead = 7 + max(len(mem_info), len(swap_info)) + 1
            bar_w = self._bar_width(bw, False, overhead)

            for label, pct, info, color in (
                ("│ Mem: ", mem_pct, mem_info, COLOR_MEMORY),
                ("│ Swap:", swap_pct, swap_info, COLOR_SWAP),
            ):
                self._safe_addstr(stdscr, y, x, "│" + " " * (bw - 2) + "│", 0, right_edge)
                self._safe_addstr(stdscr, y, x, label, 0, right_edge)
                self.draw_bar(stdscr, y, x + 7, pct, bar_w, color, border_x)
                self._safe_addstr(stdscr, y, x + 7 + bar_w, info, 0, border_x)
                self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
                y += 1

        # Box footer
        self._safe_addstr(stdscr, y, x, "└" + "─" * (bw - 2) + "┘", 0, right_edge)
        y += 2

        return y
    
    def _draw_cpu_section(self, stdscr, y: int, x: int, height: int, snapshot: Dict[str, Any], bw: int) -> int:
        """
        Draw the CPU monitoring section (overall + per-core in 2 columns).

        Args:
            stdscr: Curses window
            y: Starting row position
            x: Column position
            height: Terminal height (for bounds checking)
            snapshot: Thread-safe data snapshot
            bw: Box width for this frame

        Returns:
            Next y position after the section
        """
        sysdata = snapshot['system_data']
        right_edge = x + bw
        core_count = sysdata.get('core_count', 0)
        cpu_pct = sysdata.get('cpu_usage', 0)

        # Box header. Overall CPU usage lives in the title to save one row on
        # short iPad/mobile SSH terminals.
        self._safe_addstr(stdscr, y, x, "┌" + "─" * (bw - 2) + "┐", 0, right_edge)
        y += 1
        cpu_title = f" CPU ({core_count} cores, overall {cpu_pct:5.1f}%)"
        if len(cpu_title) > bw - 2:
            cpu_title = f" CPU {cpu_pct:5.1f}%"
        self._safe_addstr(stdscr, y, x, ("│" + cpu_title).ljust(bw - 1)[:bw - 1] + "│", 0, right_edge)
        y += 1
        self._safe_addstr(stdscr, y, x, "│" + "─" * (bw - 2) + "│", 0, right_edge)
        y += 1

        per_core = sysdata.get('per_core_usage', [])

        # Build each row as one complete string instead of several positioned
        # writes; this avoids stale characters and cursor drift on narrow
        # terminals, then overlay only the coloured filled blocks.
        content_width = bw - 4  # -2 for borders, -2 for side padding
        gap_width = 2

        # Keep labels aligned for Core 10+ without wasting a chunk of spaces
        # before the bar. The percentage sits after the bar.
        label_width = len(f"Core {max(0, core_count - 1)}:") + 1
        pct_len = len(f" {100.0:5.1f}%")

        # A cell needs label + a usable bar + percentage. If two of them don't
        # fit, drop to a single column of cores.
        min_cell = label_width + MIN_BAR_WIDTH + pct_len
        two_col = content_width >= (min_cell * 2 + gap_width)

        if two_col:
            col_width = (content_width - gap_width) // 2
            right_width = content_width - gap_width - col_width
            rows = (core_count + 1) // 2
        else:
            col_width = content_width
            right_width = 0
            rows = core_count

        def bar_parts(percent: float, width: int) -> Tuple[str, int]:
            pct = max(0, min(100, percent))
            if pct > 0:
                filled = max(1, int(pct / 100.0 * width))
            else:
                filled = 0
            filled = min(filled, width)
            return "█" * filled + "░" * (width - filled), filled

        def core_cell(core_id: int, core_pct: float, width: int) -> Tuple[str, int, int]:
            label = f"Core {core_id}:".ljust(label_width)
            pct_text = f" {core_pct:5.1f}%"
            # Very narrow cells: drop the label to a bare index, then the
            # percentage, before letting the bar disappear entirely.
            if width < len(label) + MIN_BAR_WIDTH + len(pct_text):
                label = f"{core_id}:".ljust(min(label_width, 4))
            if width < len(label) + MIN_BAR_WIDTH + len(pct_text):
                pct_text = ""
            bar_width = max(1, width - len(label) - len(pct_text))
            bar, filled = bar_parts(core_pct, bar_width)
            text = label + bar + pct_text
            return text[:width].ljust(width), len(label), filled

        cpu_attr = curses.color_pair(COLOR_CPU) | curses.A_BOLD

        for i in range(rows):
            if y >= height - 3:
                break  # Don't draw off-screen

            left = "".ljust(col_width)
            left_bar_start = 0
            left_filled = 0
            if i < len(per_core):
                core_id, core_pct = per_core[i]
                left, left_bar_start, left_filled = core_cell(core_id, core_pct, col_width)

            if two_col:
                right = "".ljust(right_width)
                right_bar_start = 0
                right_filled = 0
                right_idx = i + rows
                if right_idx < len(per_core):
                    core_id, core_pct = per_core[right_idx]
                    right, right_bar_start, right_filled = core_cell(core_id, core_pct, right_width)
                line = "│ " + left + " " * gap_width + right + " │"
            else:
                right_bar_start = right_filled = 0
                line = "│ " + left + " │"

            self._safe_addstr(stdscr, y, x, line[:bw], 0, right_edge)

            # Overlay only the filled bar blocks with the CPU colour. The +2
            # accounts for the border plus the 1-space left padding.
            if left_filled > 0:
                self._safe_addstr(
                    stdscr, y, x + 2 + left_bar_start, "█" * left_filled,
                    cpu_attr, x + bw - 1,
                )
            if two_col and right_filled > 0:
                right_x = x + 2 + col_width + gap_width + right_bar_start
                self._safe_addstr(
                    stdscr, y, right_x, "█" * right_filled, cpu_attr, x + bw - 1,
                )
            y += 1

        # Box footer
        self._safe_addstr(stdscr, y, x, "└" + "─" * (bw - 2) + "┘", 0, right_edge)
        y += 2

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

    def _draw_gpu_section(self, stdscr, y: int, x: int, height: int, snapshot: Dict[str, Any], bw: int) -> int:
        """
        Draw the GPU monitoring section (2-column layout).

        Handles both NVIDIA (with VRAM bar) and Apple Silicon (UMA — no
        separate VRAM, shows GPU cores instead).
        """
        gpu_data = snapshot['gpu_data']
        right_edge = x + bw
        border_x = x + bw - 1

        # Box header
        self._safe_addstr(stdscr, y, x, "┌" + "─" * (bw - 2) + "┐", 0, right_edge)
        y += 1
        self._safe_addstr(
            stdscr, y, x,
            ("│ " + self._gpu_section_title()).ljust(bw - 1)[:bw - 1] + "│", 0, right_edge,
        )
        y += 1
        self._safe_addstr(stdscr, y, x, "│" + "─" * (bw - 2) + "│", 0, right_edge)
        y += 1

        if not gpu_data:
            msg = " " + self._gpu_no_data_message()
            self._safe_addstr(
                stdscr, y, x, ("│" + msg).ljust(bw - 1)[:bw - 1] + "│", 0, right_edge,
            )
            y += 1
        else:
            for gpu in gpu_data:
                if y >= height - 3:
                    break
                is_uma = gpu.get('is_uma', False)
                gpu_cores = gpu.get('gpu_cores', 0)

                # --- Row 1: GPU name (left) | Temp + Power (right) ---
                if is_uma:
                    core_info = f" ({gpu_cores}-core GPU)" if gpu_cores else ""
                    gpu_name = f"{gpu['name']}{core_info}"
                else:
                    gpu_name = f"GPU {gpu['idx']}: {gpu['name']}"

                if gpu['temp'] > 0:
                    temp_power = f"Temp: {gpu['temp']:5.0f}°C  Power: {gpu['power']:6.1f}W"
                else:
                    temp_power = f"Power: {gpu['power']:6.1f}W"

                # Blank the row, then place name left and temp/power right.
                self._safe_addstr(stdscr, y, x, "│" + " " * (bw - 2) + "│", 0, right_edge)

                content_width = bw - 4  # borders + 1-space padding each side
                if bw >= GPU_HEADER_TWO_COL_MIN and len(temp_power) + 2 < content_width:
                    # Right-align temp/power against the inner right edge.
                    name_room = content_width - len(temp_power) - 2
                    self._safe_addstr(stdscr, y, x, "│ " + gpu_name[:name_room], 0, border_x)
                    self._safe_addstr(
                        stdscr, y, border_x - 1 - len(temp_power), temp_power, 0, border_x,
                    )
                else:
                    # Too narrow for both: name on this row, temp/power below.
                    self._safe_addstr(stdscr, y, x, "│ " + gpu_name[:content_width], 0, border_x)
                    self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
                    y += 1
                    if y >= height - 3:
                        break
                    self._safe_addstr(stdscr, y, x, "│" + " " * (bw - 2) + "│", 0, right_edge)
                    self._safe_addstr(stdscr, y, x, "│ " + temp_power[:content_width], 0, border_x)

                self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
                y += 1
                if y >= height - 3:
                    break

                # --- Row 2: Util (left) | VRAM or UMA info (right) ---
                util_info = f" {gpu['gpu_util']:6.1f}%"
                self._safe_addstr(stdscr, y, x, "│" + " " * (bw - 2) + "│", 0, right_edge)

                if is_uma:
                    uma_text = "UMA: shared w/ system memory"
                    # Overhead: "│ Util:"(7) + util_info + gap(2) + uma text + "│"(1)
                    overhead = 7 + len(util_info) + 2 + len(uma_text) + 1
                    two_col = bw - 2 - overhead >= MIN_BAR_WIDTH
                    bar_w = self._bar_width(bw, False, overhead if two_col else 7 + len(util_info) + 1)

                    self._safe_addstr(stdscr, y, x, "│ Util:", 0, right_edge)
                    self.draw_bar(stdscr, y, x + 7, gpu['gpu_util'], bar_w, COLOR_CPU, border_x)
                    self._safe_addstr(stdscr, y, x + 7 + bar_w, util_info, 0, border_x)
                    if two_col:
                        self._safe_addstr(
                            stdscr, y, x + 7 + bar_w + len(util_info) + 2, uma_text, 0, border_x,
                        )
                        self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
                        y += 1
                    else:
                        self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
                        y += 1
                        if y < height - 3:
                            self._safe_addstr(stdscr, y, x, "│" + " " * (bw - 2) + "│", 0, right_edge)
                            self._safe_addstr(stdscr, y, x, "│ " + uma_text[:bw - 4], 0, border_x)
                            self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
                            y += 1
                else:
                    mem_pct = (gpu['mem_used'] / gpu['mem_total']) * 100 if gpu['mem_total'] > 0 else 0
                    mem_used_gb = gpu['mem_used'] / 1024
                    mem_total_gb = gpu['mem_total'] / 1024
                    vram_info = f" {mem_used_gb:5.1f}GB/{mem_total_gb:4.1f}G {mem_pct:5.1f}%"

                    # Overhead = "│ Util:"(7) + util_info + gap(2) + "VRAM:"(5)
                    #            + vram_info + "│"(1)
                    two_col = bw >= GPU_TWO_COL_MIN
                    if two_col:
                        overhead = 7 + len(util_info) + 2 + 5 + len(vram_info) + 1
                        bar_w = self._bar_width(bw, True, overhead)
                    else:
                        overhead = 7 + len(util_info) + 1
                        bar_w = self._bar_width(bw, False, overhead)

                    self._safe_addstr(stdscr, y, x, "│ Util:", 0, right_edge)
                    self.draw_bar(stdscr, y, x + 7, gpu['gpu_util'], bar_w, COLOR_CPU, border_x)
                    self._safe_addstr(stdscr, y, x + 7 + bar_w, util_info, 0, border_x)

                    if two_col:
                        right_col_start = x + 7 + bar_w + len(util_info) + 2
                        self._safe_addstr(stdscr, y, right_col_start, "VRAM:", 0, border_x)
                        self.draw_bar(
                            stdscr, y, right_col_start + 5, mem_pct, bar_w, COLOR_VRAM, border_x,
                        )
                        self._safe_addstr(
                            stdscr, y, right_col_start + 5 + bar_w, vram_info, 0, border_x,
                        )
                        self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
                        y += 1
                    else:
                        self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
                        y += 1
                        if y < height - 3:
                            # VRAM gets its own row when narrow.
                            vram_overhead = 7 + len(vram_info) + 1
                            vram_bar_w = self._bar_width(bw, False, vram_overhead)
                            self._safe_addstr(stdscr, y, x, "│" + " " * (bw - 2) + "│", 0, right_edge)
                            self._safe_addstr(stdscr, y, x, "│ VRAM:", 0, right_edge)
                            self.draw_bar(
                                stdscr, y, x + 7, mem_pct, vram_bar_w, COLOR_VRAM, border_x,
                            )
                            self._safe_addstr(
                                stdscr, y, x + 7 + vram_bar_w, vram_info, 0, border_x,
                            )
                            self._safe_addstr(stdscr, y, border_x, "│", 0, right_edge)
                            y += 1

                # Separator between GPUs (if more GPUs and space available)
                if y < height - 3 and int(gpu['idx']) < len(gpu_data) - 1:
                    self._safe_addstr(stdscr, y, x, "│" + "─" * (bw - 2) + "│", 0, right_edge)
                    y += 1

        # Box footer
        self._safe_addstr(stdscr, y, x, "└" + "─" * (bw - 2) + "┘", 0, right_edge)
        y += 2

        return y
    
    def _process_command(self, proc: Dict[str, Any]) -> str:
        """Return the display command for a GPU process."""
        if proc.get('cmdline'):
            parts = proc['cmdline'].split()
            if parts:
                base_name = os.path.basename(parts[0])
                return base_name + (' ' + ' '.join(parts[1:]) if len(parts) > 1 else '')
        return os.path.basename(proc.get('process_name', 'unknown').split(',')[0].strip())

    def _gpu_process_table_header(self) -> str:
        """nvtop-style GPU process table header."""
        return self._GPU_PROCESS_FIXED_HEADER + "Command"

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

    def _draw_scrolled_process_line(self, stdscr, y: int, x: int, fixed: str, command: str, width: int) -> None:
        """Draw one bordered process table line with fixed columns and scrolled command."""
        view_width = max(1, width - 4)  # -2 for borders, -2 for side padding
        fixed_visible = fixed[:view_width]
        cmd_width = max(0, view_width - len(fixed_visible))
        scroll = max(0, self.process_scroll_x)
        visible = fixed_visible + command[scroll:scroll + cmd_width]
        self._safe_addstr(
            stdscr, y, x, "│ " + visible.ljust(view_width)[:view_width] + " │", 0, x + width,
        )

    def _max_process_scroll(self, bw: int, gpuprocs: List[Dict[str, Any]]) -> int:
        """Maximum horizontal scroll offset for the process table command column."""
        view_width = max(1, bw - 4)  # -2 for borders, -2 for side padding
        cmd_width = max(1, view_width - self._GPU_PROCESS_FIXED_HEADER_LEN)
        return max(0, max((len(self._process_command(proc)) for proc in gpuprocs), default=0) - cmd_width)

    def _draw_gpu_processes_section(self, stdscr, y: int, x: int, height: int, snapshot: Dict[str, Any], bw: int) -> int:
        """
        Draw the GPU processes section as an nvtop-style horizontally scrollable table.

        Args:
            stdscr: Curses window
            y: Starting row position
            x: Column position
            height: Terminal height (for bounds checking)
            snapshot: Thread-safe data snapshot
            bw: Box width for this frame

        Returns:
            Next y position after the section
        """
        gpuprocs = snapshot['gpu_processes']
        right_edge = x + bw
        self.process_scroll_x = min(max(0, self.process_scroll_x), self._max_process_scroll(bw, gpuprocs))

        # Box header
        self._safe_addstr(stdscr, y, x, "┌" + "─" * (bw - 2) + "┐", 0, right_edge)
        y += 1

        title = f" GPU PROCESSES  ←/→ scroll {self.process_scroll_x}"
        self._safe_addstr(stdscr, y, x, ("│" + title).ljust(bw - 1)[:bw - 1] + "│", 0, right_edge)
        y += 1

        self._safe_addstr(stdscr, y, x, "│" + "─" * (bw - 2) + "│", 0, right_edge)
        y += 1

        hdr = self._gpu_process_table_header()
        self._safe_addstr(stdscr, y, x, ("│ " + hdr).ljust(bw - 1)[:bw - 1] + "│", 0, right_edge)
        y += 1

        if y < height - 3:
            self._safe_addstr(stdscr, y, x, "│" + "─" * (bw - 2) + "│", 0, right_edge)
            y += 1

        if not gpuprocs:
            if y < height - 3:
                self._draw_scrolled_process_line(stdscr, y, x, "", "No active GPU compute processes", bw)
                y += 1
        else:
            for proc in gpuprocs:
                if y >= height - 3:
                    break  # Don't draw off-screen
                self._draw_scrolled_process_line(
                    stdscr, y, x, self._gpu_process_fixed_prefix(proc), self._process_command(proc), bw,
                )
                y += 1

        # Box footer
        self._safe_addstr(stdscr, y, x, "└" + "─" * (bw - 2) + "┘", 0, right_edge)
        y += 2

        return y
    
    def draw(self, stdscr) -> None:
        """Draw the complete UI with all monitoring sections."""
        # Take a thread-safe snapshot of the latest stats
        with self._stats_lock:
            snapshot = {
                'system_data': dict(self.system_data),
                'gpu_data': list(self.gpu_data),
                'gpu_processes': list(self.gpu_processes),
            }
        self._draw_frame(stdscr, snapshot)

    def _draw_frame(self, stdscr, snapshot: Dict[str, Any]) -> None:
        """Draw a single frame — called from draw() with a thread-safe snapshot."""
        try:
            curses.curs_set(0)
        except curses.error:
            pass

        height, width = stdscr.getmaxyx()

        stdscr.erase()

        # Terminal too small to render anything meaningful — say so rather than
        # drawing a mangled dashboard.
        if width < MIN_BOX_WIDTH or height < 6:
            self._safe_addstr(stdscr, 0, 0, "Terminal too small"[:max(0, width - 1)])
            if height > 1:
                self._safe_addstr(stdscr, 1, 0, f"{width}x{height}"[:max(0, width - 1)])
            stdscr.refresh()
            return

        # Box fills the available width (1-char margin each side), capped so the
        # layout stays readable on ultra-wide terminals. No fixed floor — a
        # floor is what caused content to wrap around and overwrite itself when
        # the terminal was made narrower than the box.
        self._box_width = max(MIN_BOX_WIDTH, min(MAX_BOX_WIDTH, width - 2))

        # Title
        title = f" termmon {__version__} - System Monitor | {datetime.now().strftime('%H:%M:%S')} | q:quit r:refresh h:help "
        if len(title) > width - 1:
            title = f" termmon {__version__} | {datetime.now().strftime('%H:%M:%S')} "
        self._safe_addstr(
            stdscr, 0, 0, title.ljust(width - 1)[:width - 1], curses.A_REVERSE,
        )

        y = 2
        x = max(1, (width - self._box_width) // 2)
        # Never let the box run past the right edge.
        if x + self._box_width > width:
            x = max(0, width - self._box_width)

        # Draw system memory section
        y = self._draw_memory_section(stdscr, y, x, snapshot, self._box_width)

        # Draw CPU section
        y = self._draw_cpu_section(stdscr, y, x, height, snapshot, self._box_width)

        # Draw GPU section
        y = self._draw_gpu_section(stdscr, y, x, height, snapshot, self._box_width)

        # Draw GPU processes section
        y = self._draw_gpu_processes_section(stdscr, y, x, height, snapshot, self._box_width)

        # Footer
        footer = f" Refresh: {REFRESH_INTERVAL}s | q:quit r:refresh h:help ←/→:process scroll "
        if len(footer) > width - 1:
            footer = " q:quit r:refresh h:help ←/→:scroll "
        if len(footer) > width - 1:
            footer = " q:quit h:help "
        self._safe_addstr(
            stdscr, height - 1, 0, footer.ljust(width - 1)[:width - 1], curses.A_REVERSE,
        )

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
        curses.init_pair(COLOR_POPUP, curses.COLOR_WHITE, curses.COLOR_BLUE)  # White on blue
        
        curses.cbreak()
        stdscr.keypad(True)
        # Use timeout instead of nodelay to avoid blocking on input
        # getch() will return -1 after 50ms if no input
        stdscr.timeout(50)
        
        # Handle terminal resize
        signal.signal(signal.SIGWINCH, self._on_resize)
        
        # Start background stats updater thread
        self._stats_thread = threading.Thread(target=self._stats_updater_thread, daemon=True)
        self._stats_thread.start()
        
        # Seed cpu_percent for all processes so first read returns real values
        for proc in psutil.process_iter():
            try:
                proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Initial stats update (wait for first update to complete)
        self._stats_update_event.set()
        time.sleep(0.5)

        last_refresh = time.monotonic()
        
        try:
            while self.running:
                current_time = time.monotonic()
                
                # Handle terminal resize
                if self._resized:
                    self._resized = False
                    self._apply_resize(stdscr)
                    self._stats_update_event.set()
                    last_refresh = current_time  # Force refresh on resize
                
                # Trigger stats update every 2 seconds
                if current_time - last_refresh >= REFRESH_INTERVAL:
                    self._stats_update_event.set()
                    last_refresh = current_time
                
                # Draw immediately (data may be stale but update is non-blocking)
                self.draw(stdscr)
                
                key = stdscr.getch()
                
                if key == ord('q') or key == ord('Q'):
                    self.running = False
                elif key == ord('r') or key == ord('R'):
                    self.update_stats()
                elif key == ord('h') or key == ord('H'):
                    self._show_help(stdscr)
                elif key == curses.KEY_RIGHT:
                    with self._stats_lock:
                        _gp = list(self.gpu_processes)
                    mx = self._max_process_scroll(self._box_width, _gp)
                    self.process_scroll_x = min(mx, self.process_scroll_x + 16)
                    self.draw(stdscr)
                elif key == curses.KEY_LEFT:
                    self.process_scroll_x = max(0, self.process_scroll_x - 16)
                    self.draw(stdscr)
        except KeyboardInterrupt:
            self.running = False
        finally:
            # Stop the background stats thread cleanly
            self.running = False
            if self._stats_thread is not None:
                self._stats_thread.join(timeout=2)
            try:
                curses.curs_set(1)
            except curses.error:
                pass
            curses.nocbreak()
            stdscr.keypad(False)
            curses.echo()
            curses.endwin()
            self._gpu_data_executor.shutdown(wait=False)
            # Restore the terminal: clear stale content and make the cursor visible.
            subprocess.run(['clear'], stdout=sys.stdout)
            os.write(sys.stderr.fileno(), b'\033[?25h\n')


if __name__ == "__main__":
    app = TermMon()

    # Warn on macOS if no GPU monitoring tool is available
    if _IS_MACOS:
        has_macmon = shutil.which('macmon') is not None
        has_socpwrbud = shutil.which('socpwrbud') is not None
        if not has_macmon and not has_socpwrbud:
            print(
                "Note: GPU utilization will show 0% without sudo on macOS 13+.\n"
                "      For accurate readings, install macmon (recommended):\n"
                "      https://github.com/vladkens/macmon/releases",
                file=sys.stderr,
            )

    app.run()
