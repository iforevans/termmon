#!/usr/bin/env python3
"""
termmon - Terminal System Monitor
==================================

A unified terminal-based system monitor combining `htop` (system RAM/CPU) 
and `nvidia-smi` (GPU/VRAM) functionality into a single dashboard.

Originally created to solve the problem of monitoring CPU/system RAM/swap 
and GPU/VRAM usage from one window while testing local AI models on an 
RTX3090/24GB.

Features:
    - System memory monitoring (RAM + swap in GB)
    - Overall and per-core CPU utilization
    - NVIDIA GPU monitoring (VRAM, utilization, temperature, power)
    - GPU process tracking (top 5 processes by VRAM usage)
    - Color-coded progress bars
    - Auto-refresh every 2 seconds
    - Pure Python with no external dependencies

Usage:
    termmon

Keybindings:
    q - Quit
    r - Refresh now
    h - Show help

Author:
    Ifor Evans (@iforevans)
    Pair programmed with OpenClaw Agent Sparky ⚡

License:
    MIT
"""

import curses
import subprocess
from datetime import datetime
import time
from typing import Dict, List, Tuple, Any, Optional

__version__ = "1.5.5"
__author__ = "Ifor Evans"


# Layout configuration
BOX_WIDTH = 0          # Will be auto-calculated based on terminal width (80% of terminal)
BAR_WIDTH = 20         # Width of progress bars
LABEL_WIDTH = 22       # Width of label column
REFRESH_INTERVAL = 2   # Seconds between auto-refreshes

# Color pair IDs
COLOR_TITLE = 1         # White - title and footer
COLOR_MEMORY = 2        # Green - RAM usage bar
COLOR_SWAP = 3          # Yellow - swap usage bar
COLOR_CPU = 4           # Cyan - CPU usage bar
COLOR_VRAM = 5          # Magenta - VRAM usage bar
COLOR_ERROR = 6         # Red - error messages
COLOR_PROCESS = 7       # Blue - GPU process list

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
        self.last_cpu_stats: Optional[Tuple[int, int]] = None
        self.last_per_core_stats: Dict[int, Tuple[int, int]] = {}
        
    def get_system_stats(self) -> None:
        """
        Read system memory and CPU statistics from /proc filesystem.
        
        Parses /proc/meminfo for memory/swap statistics and /proc/stat for
        CPU utilization. Calculates CPU usage using delta between samples
        for accurate real-time measurement (not cumulative from boot).
        """
        try:
            with open('/proc/meminfo', 'r') as f:
                meminfo = {}
                for line in f:
                    if ':' in line:
                        key, value = line.split(':')
                        meminfo[key.strip()] = int(value.split()[0])
            
            total_mem = meminfo.get('MemTotal', 0)
            avail_mem = meminfo.get('MemAvailable', 0)
            used_mem = total_mem - avail_mem
            swap_total = meminfo.get('SwapTotal', 0)
            swap_free = meminfo.get('SwapFree', 0)
            swap_used = swap_total - swap_free
            
            total_mem_gb = total_mem / 1024 / 1024
            used_mem_gb = used_mem / 1024 / 1024
            avail_mem_gb = avail_mem / 1024 / 1024
            swap_total_mb = swap_total / 1024
            swap_used_mb = swap_used / 1024
            
            # Overall CPU - parse /proc/stat
            # Format: cpu <user> <nice> <system> <idle> <iowait> <irq> <softirq> <steal> <guest> <guest_nice>
            # Fields:  [0]   [1]    [2]      [3]       [4]      [5]     [6]       [7]      [8]       [9]        [10]
            with open('/proc/stat', 'r') as f:
                lines = f.readlines()
            
            # Parse overall CPU (first line)
            cpu_line = lines[0]
            cpu_values = [int(v) for v in cpu_line.split()[1:12]]  # Skip 'cpu' label, take 11 fields
            idle = cpu_values[3] + cpu_values[4]  # idle + iowait
            total = sum(cpu_values)
            
            # Calculate CPU usage using delta (difference from last sample)
            # This gives real-time usage, not cumulative since boot
            cpu_usage = 0.0
            if self.last_cpu_stats is not None:
                prev_idle, prev_total = self.last_cpu_stats
                idle_delta = idle - prev_idle
                total_delta = total - prev_total
                if total_delta > 0:
                    cpu_usage = (1 - idle_delta / total_delta) * 100
            
            self.last_cpu_stats = (idle, total)
            
            # Parse per-core stats (cpu0, cpu1, cpu2, ...)
            # Each line has same format as overall CPU line
            per_core_usage = []
            for line in lines[1:]:
                if line.startswith('cpu'):
                    parts = line.split()
                    if len(parts) >= 11:  # Need core name + 10 stat fields
                        core_id = int(parts[0][3:])  # Extract number from 'cpu0', 'cpu1', etc.
                        core_values = [int(v) for v in parts[1:11]]  # Take 10 stat fields
                        core_idle = core_values[3] + core_values[4]  # idle + iowait
                        core_total = sum(core_values)
                        
                        # Calculate per-core usage using delta
                        core_usage = 0.0
                        if core_id in self.last_per_core_stats:
                            prev_idle, prev_total = self.last_per_core_stats[core_id]
                            idle_delta = core_idle - prev_idle
                            total_delta = core_total - prev_total
                            if total_delta > 0:
                                core_usage = (1 - idle_delta / total_delta) * 100
                        
                        self.last_per_core_stats[core_id] = (core_idle, core_total)
                        per_core_usage.append((core_id, core_usage))
            
            # Sort by core ID
            per_core_usage.sort(key=lambda x: x[0])
            
            with open('/proc/cpuinfo', 'r') as f:
                core_count = len([l for l in f.read().split('\n') if l.startswith('processor')])
            
            mem_percent = (used_mem / total_mem) * 100 if total_mem > 0 else 0
            swap_percent = (swap_used / swap_total) * 100 if swap_total > 0 else 0
            
            self.system_data = {
                'total_mem_gb': total_mem_gb,
                'used_mem_gb': used_mem_gb,
                'avail_mem_gb': avail_mem_gb,
                'mem_percent': mem_percent,
                'swap_total_mb': swap_total_mb,
                'swap_used_mb': swap_used_mb,
                'swap_percent': swap_percent,
                'cpu_usage': cpu_usage,
                'core_count': core_count,
                'per_core_usage': per_core_usage
            }
        except Exception as e:
            self.system_data['error'] = str(e)
    
    def get_gpu_stats(self) -> None:
        """
        Read NVIDIA GPU statistics using nvidia-smi.
        
        Queries GPU index, name, memory (total/used/free), utilization,
        temperature, and power draw.
        """
        try:
            result = subprocess.run(
                [
                    'nvidia-smi',
                    f'--query-gpu={GPU_QUERY_FIELDS}',
                    '--format=csv,noheader,nounits'
                ],
                capture_output=True,
                text=True,
                timeout=5
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
                                'power': float(parts[7])
                            })
                        except (ValueError, IndexError):
                            pass
                self.gpu_data = gpus
            else:
                self.gpu_data = []
        except Exception as e:
            self.gpu_data = []
    
    def get_gpu_processes(self) -> None:
        """
        Read GPU compute applications using nvidia-smi.
        
        Queries active GPU processes: PID, process name, and memory used.
        Enriches with user and host memory from /proc filesystem.
        Returns top processes sorted by VRAM usage.
        """
        try:
            result = subprocess.run(
                [
                    'nvidia-smi',
                    f'--query-compute-apps={GPU_COMPUTE_QUERY}',
                    '--format=csv,noheader,nounits'
                ],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                processes = []
                for line in result.stdout.strip().split('\n'):
                    if not line.strip():
                        continue
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) >= 3:
                        try:
                            # Order: pid, process_name, used_gpu_memory
                            pid = int(parts[0].strip())
                            # Process name might have commas, memory is last
                            mem_used = float(parts[-1].strip())
                            # Process name is everything in between
                            process_name = ','.join(parts[1:-1]).strip()
                            
                            # Enrich with user and host memory from /proc
                            user = "unknown"
                            host_mem = 0.0
                            cmdline = ""
                            try:
                                # Get UID from /proc/[pid]/status
                                with open(f'/proc/{pid}/status', 'r') as f:
                                    for proc_line in f:
                                        if proc_line.startswith('Uid:'):
                                            uid = proc_line.split()[1]
                                            # Look up username from UID
                                            import pwd
                                            try:
                                                user = pwd.getpwuid(int(uid)).pw_name
                                            except (KeyError, ValueError):
                                                user = uid
                                            break
                                
                                # Get RSS (resident set size) from /proc/[pid]/status
                                with open(f'/proc/{pid}/status', 'r') as f:
                                    for proc_line in f:
                                        if proc_line.startswith('VmRSS:'):
                                            # VmRSS is in kB
                                            host_mem = float(proc_line.split()[1]) / 1024  # Convert to MB
                                            break
                                
                                # Get command line from /proc/[pid]/cmdline
                                try:
                                    with open(f'/proc/{pid}/cmdline', 'r') as f:
                                        # cmdline is null-separated
                                        cmdline = f.read().replace('\0', ' ').strip()
                                except (FileNotFoundError, PermissionError, IOError):
                                    pass
                            except (FileNotFoundError, PermissionError, IOError):
                                # Process may have exited or no permission
                                pass
                            
                            processes.append({
                                'pid': pid,
                                'user': user,
                                'mem_used': mem_used,
                                'host_mem': host_mem,
                                'process_name': process_name,
                                'cmdline': cmdline
                            })
                        except (ValueError, IndexError):
                            pass
                
                # Sort by memory usage (descending) and take top N
                processes.sort(key=lambda x: x['mem_used'], reverse=True)
                self.gpu_processes = processes[:MAX_GPU_PROCS]
            else:
                self.gpu_processes = []
        except Exception as e:
            self.gpu_processes = []
    
    def update_stats(self) -> None:
        """Update all system and GPU statistics."""
        self.get_system_stats()
        self.get_gpu_stats()
        self.get_gpu_processes()
    
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
            # Box header
            core_count = self.system_data.get('core_count', 0)
            stdscr.addstr(y, x, "┌" + "─" * (BOX_WIDTH - 2) + "┐")
            y += 1
            stdscr.addstr(y, x, f"│ CPU ({core_count} cores)".ljust(BOX_WIDTH - 1) + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1
            
            # Overall CPU usage (align with core lines)
            cpu_pct = self.system_data.get('cpu_usage', 0)
            label = f"│ Overall:".ljust(11) + f"{cpu_pct:6.1f}%".rjust(8)
            stdscr.addstr(y, x, label)
            self.draw_bar(stdscr, y, x + 11 + 8, cpu_pct, BAR_WIDTH, COLOR_CPU)
            # Close the box
            stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
            y += 1
            
            # Per-core usage in TWO columns
            per_core = self.system_data.get('per_core_usage', [])
            
            # Calculate split point (half the cores in each column)
            mid_point = (core_count + 1) // 2
            
            # Calculate column widths
            # Left column: "│ Core N:" (11 chars) + percentage (8 chars) + bar (20 chars) = 39 chars
            # Right column starts after that + separator
            left_col_width = 11 + 8 + BAR_WIDTH + 2  # ~41 chars
            right_col_start = x + left_col_width
            
            # Draw cores in two columns
            for i in range(mid_point):
                if y >= height - 3:
                    break  # Don't draw off-screen
                
                # Left column
                if i < len(per_core):
                    core_id, core_pct = per_core[i]
                    label = f"│ Core {core_id}:".ljust(11) + f"{core_pct:6.1f}%".rjust(8)
                    try:
                        stdscr.addstr(y, x, label)
                        self.draw_bar(stdscr, y, x + 11 + 8, core_pct, BAR_WIDTH, COLOR_CPU)
                    except curses.error:
                        pass
                
                # Right column (if we have enough cores)
                right_idx = i + mid_point
                if right_idx < len(per_core):
                    core_id, core_pct = per_core[right_idx]
                    right_label = f"Core {core_id}:".ljust(11) + f"{core_pct:6.1f}%".rjust(8)
                    try:
                        stdscr.addstr(y, right_col_start, right_label)
                        self.draw_bar(stdscr, y, right_col_start + 11 + 8, core_pct, BAR_WIDTH, COLOR_CPU)
                        # Close the right side of the box
                        right_end = right_col_start + 11 + 8 + BAR_WIDTH
                        if right_end < BOX_WIDTH - 1:
                            stdscr.addstr(y, right_end, " " * (BOX_WIDTH - 1 - right_end))
                        stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                    except curses.error:
                        pass
                else:
                    # Just close the box if no right column
                    try:
                        stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                    except curses.error:
                        pass
                
                y += 1
            
            # Box footer
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass
        
        return y
    
    def _draw_gpu_section(self, stdscr, y: int, x: int, height: int) -> int:
        """
        Draw the NVIDIA GPU monitoring section (2-column layout).
        
        Args:
            stdscr: Curses window
            y: Starting row position
            x: Column position
            height: Terminal height (for bounds checking)
            
        Returns:
            Next y position after the section
        """
        try:
            # Box header
            stdscr.addstr(y, x, "┌" + "─" * (BOX_WIDTH - 2) + "┐")
            y += 1
            stdscr.addstr(y, x, "│ NVIDIA GPU(s)".ljust(BOX_WIDTH - 1) + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1
            
            if not self.gpu_data:
                line = "│ No GPUs found or nvidia-smi not available"
                stdscr.addstr(y, x, (line + " " * (BOX_WIDTH - len(line) - 1))[:BOX_WIDTH-1] + "│")
                y += 1
            else:
                for gpu in self.gpu_data:
                    mem_pct = (gpu['mem_used'] / gpu['mem_total']) * 100 if gpu['mem_total'] > 0 else 0
                    mem_used_gb = gpu['mem_used'] / 1024
                    mem_total_gb = gpu['mem_total'] / 1024
                    
                    # Calculate column positions
                    left_col_width = 42  # GPU name takes ~42 chars
                    right_col_start = x + left_col_width
                    
                    # Row 1: GPU name (left) | Temp + Power (right) - SAME LINE
                    gpu_name = f"GPU {gpu['idx']}: {gpu['name'][:35]}"
                    temp_power = f"Temp: {gpu['temp']:5.0f}°C  Power: {gpu['power']:6.1f}W"
                    
                    # Build the combined line
                    line = f"│ {gpu_name}"
                    stdscr.addstr(y, x, line)
                    
                    # Add temp/power on the right
                    stdscr.addstr(y, right_col_start, temp_power)
                    stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                    y += 1
                    
                    # Row 2: Util (left) | VRAM (right)
                    # Left column: Util
                    left_label = "│ Util:"
                    stdscr.addstr(y, x, left_label)
                    self.draw_bar(stdscr, y, x + 6, gpu['gpu_util'], BAR_WIDTH, COLOR_CPU)
                    util_info = f" {gpu['gpu_util']:6.1f}%"
                    stdscr.addstr(y, x + 6 + BAR_WIDTH, util_info)
                    
                    # Right column: VRAM
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
    
    def _draw_gpu_processes_section(self, stdscr, y: int, x: int, height: int) -> int:
        """
        Draw the GPU processes section (top N by VRAM usage).
        
        Args:
            stdscr: Curses window
            y: Starting row position
            x: Column position
            height: Terminal height (for bounds checking)
            
        Returns:
            Next y position after the section
        """
        try:
            # Box header
            stdscr.addstr(y, x, "┌" + "─" * (BOX_WIDTH - 2) + "┐")
            y += 1
            stdscr.addstr(y, x, "│ GPU PROCESSES (nvtop-style)".ljust(BOX_WIDTH - 1) + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1
            
            # Column headings
            heading = "│ PID    | USER       | GPU MEM    | HOST MEM   | Command"
            stdscr.addstr(y, x, (heading.ljust(BOX_WIDTH - 1))[:BOX_WIDTH-1] + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1
            
            if not self.gpu_processes:
                line = "│ No active GPU compute processes"
                stdscr.addstr(y, x, (line + " " * (BOX_WIDTH - len(line) - 1))[:BOX_WIDTH-1] + "│")
                y += 1
            else:
                # Calculate column widths for wrapping
                pid_width = 6
                user_width = 10
                gpu_mem_width = 10
                host_mem_width = 10
                separators = 4  # " | " appears 4 times
                cmd_width = BOX_WIDTH - pid_width - user_width - gpu_mem_width - host_mem_width - separators - 2
                
                for proc in self.gpu_processes:
                    if y >= height - 3:
                        break  # Don't draw off-screen
                    
                    mem_mb = proc['mem_used']
                    host_mem_mb = proc['host_mem']
                    user = proc['user'][:10]
                    
                    # Get command (use cmdline if available)
                    if proc.get('cmdline'):
                        import os
                        parts = proc['cmdline'].split()
                        if parts:
                            base_name = os.path.basename(parts[0])
                            full_cmd = base_name + ' ' + ' '.join(parts[1:])
                        else:
                            full_cmd = "unknown"
                    else:
                        import os
                        full_cmd = os.path.basename(proc['process_name'].split(',')[0].strip())
                    
               # Word-wrap the command if needed
                    # First, check if we have any words that are too long (like paths)
                    # and split them on / before doing word wrapping
                    cmd_words = full_cmd.split(' ')
                    expanded_words = []
                    
                    # Split long paths (>50 chars) on / for better readability
                    MAX_WORD_LEN = 50
                    
                    for word in cmd_words:
                        if len(word) > MAX_WORD_LEN and '/' in word:
                            # Split long paths on / but keep track of path boundaries
                            parts = [p for p in word.split('/') if p]  # Skip empty parts
                            if parts:
                                # Add each part as a special "path segment" that rejoins with /
                                for part in parts:
                                    expanded_words.append(f"__PATHSEG__{part}")
                            else:
                                expanded_words.append(word)
                        else:
                            expanded_words.append(word)
                    
                    # Pre-process: combine flag-value pairs to keep them together
                    # A flag is a word starting with - or --, followed by a non-flag value
                    paired_words = []
                    i = 0
                    while i < len(expanded_words):
                        word = expanded_words[i]
                        # Check if this is a flag (starts with - but not a path segment)
                        if (word.startswith('-') and not word.startswith('__PATHSEG__') and 
                            i + 1 < len(expanded_words)):
                            next_word = expanded_words[i + 1]
                            # Check if next word is NOT a flag (doesn't start with -)
                            if not next_word.startswith('-') and not next_word.startswith('__PATHSEG__'):
                                # Combine flag and value with a special separator
                                paired_words.append(f"{word}__FLAGVAL__{next_word}")
                                i += 2  # Skip both words
                                continue
                        paired_words.append(word)
                        i += 1
                    
                    # Now do word wrapping with the (possibly paired) words
                    lines = []
                    current_cmd = ""
                    in_path_context = False  # Track if we're building a path
                    
                    for word in paired_words:
                        # Check if this is a path segment
                        is_path_seg = word.startswith("__PATHSEG__")
                        # Check if this is a flag-value pair
                        is_flag_pair = "__FLAGVAL__" in word
                        
                        if is_path_seg:
                            actual_word = word[11:]  # Remove __PATHSEG__ prefix
                        elif is_flag_pair:
                            # Split and rejoin with space for flag-value pairs
                            parts = word.split("__FLAGVAL__")
                            actual_word = parts[0] + " " + parts[1]
                        else:
                            actual_word = word
                        
                        if not current_cmd:
                            current_cmd = actual_word
                            in_path_context = is_path_seg
                        elif len(current_cmd) + 1 + len(actual_word) <= cmd_width:
                            # Join with / only if both current and new are in path context
                            if in_path_context and is_path_seg:
                                current_cmd += "/" + actual_word
                            else:
                                current_cmd += " " + actual_word
                                in_path_context = is_path_seg
                        else:
                            if current_cmd:
                                lines.append(current_cmd)
                            current_cmd = actual_word
                            in_path_context = is_path_seg
                    
                    if current_cmd:
                        lines.append(current_cmd)
                    
                    # Final pass: handle any remaining lines that are still too long
                    # (shouldn't happen now, but just in case)
                    final_lines = []
                    for line in lines:
                        if len(line) > cmd_width:
                            # Chunk at fixed width as last resort
                            for i in range(0, len(line), cmd_width):
                                final_lines.append(line[i:i+cmd_width])
                        else:
                            final_lines.append(line)
                    lines = final_lines
                    
                    # Draw first line with all columns
                    if lines and y < height - 3:
                        line = f"│ {proc['pid']:6} | {user:10} | {mem_mb:8.0f}MB | {host_mem_mb:8.0f}MB | {lines[0]}"
                        stdscr.addstr(y, x, (line.ljust(BOX_WIDTH - 1))[:BOX_WIDTH-1] + "│")
                        y += 1
                    
                    # Draw continuation lines (just the command, indented)
                    for continuation in lines[1:]:
                        if y >= height - 3:
                            break
                        # Just show the continuation, aligned with command column
                        # PID(6) + " | "(3) + USER(10) + " | "(3) + GPU_MEM(10) + " | "(3) + HOST_MEM(10) + " | "(3) = 48
                        indent = "│" + " " * 47  # 47 spaces to align with command column start
                        # Ensure continuation doesn't exceed available width
                        avail_width = BOX_WIDTH - 48  # After indent and closing │
                        if len(continuation) > avail_width:
                            # Split continuation into multiple lines if needed
                            chunks = [continuation[i:i+avail_width] for i in range(0, len(continuation), avail_width)]
                            for chunk_idx, chunk in enumerate(chunks):
                                if y >= height - 3:
                                    break
                                line = f"{indent}{chunk}"
                                stdscr.addstr(y, x, (line.ljust(BOX_WIDTH - 1))[:BOX_WIDTH-1] + "│")
                                y += 1
                        else:
                            line = f"{indent}{continuation}"
                            stdscr.addstr(y, x, (line.ljust(BOX_WIDTH - 1))[:BOX_WIDTH-1] + "│")
                            y += 1
                    
                    # Add blank separator line between processes (if space)
                    if y < height - 3:
                        line = "│" + " " * (BOX_WIDTH - 2) + "│"
                        stdscr.addstr(y, x, line)
                        y += 1
            
            # Box footer
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass
        
        return y
    
    def draw(self, stdscr) -> None:
        """Draw the complete UI with all monitoring sections."""
        curses.curs_set(0)
        
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
            footer = f" Refresh: {REFRESH_INTERVAL}s | q:quit r:refresh h:help "
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
        
        curses.cbreak()
        stdscr.keypad(True)
        stdscr.nodelay(True)
        
        self.update_stats()
        time.sleep(0.5)
        self.update_stats()
        
        last_refresh = 0
        
        try:
            while self.running:
                current_time = time.time()
                
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
                    try:
                        help_y = max(0, (curses.LINES - 9) // 2)
                        help_x = max(0, (curses.COLS - 30) // 2)
                        stdscr.addstr(help_y, help_x, "╔════════════════════════════╗")
                        stdscr.addstr(help_y + 1, help_x, "║        KEYBINDINGS         ║")
                        stdscr.addstr(help_y + 2, help_x, "║═══════════════════════════║")
                        stdscr.addstr(help_y + 3, help_x, "║ q  - Quit                  ║")
                        stdscr.addstr(help_y + 4, help_x, "║ r  - Refresh now           ║")
                        stdscr.addstr(help_y + 5, help_x, "║ h  - Show help (this)      ║")
                        stdscr.addstr(help_y + 6, help_x, "╚════════════════════════════╝")
                    except curses.error:
                        pass
                    time.sleep(2)
                
                time.sleep(0.05)
        finally:
            curses.nocbreak()
            stdscr.keypad(False)
            curses.echo()
            curses.endwin()


if __name__ == "__main__":
    app = TermMon()
    app.run()
