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
import concurrent.futures
import os
import pwd
import signal
import subprocess
from datetime import datetime
import time
from typing import Dict, List, Tuple, Any, Optional

__version__ = "1.6.3"
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
        self.last_cpu_stats: Optional[Tuple[int, int]] = None
        self.last_per_core_stats: Dict[int, Tuple[int, int]] = {}
        self.core_count: int = self._get_core_count()
        self._resized: bool = False  # SIGWINCH flag
    
    def _on_resize(self, signum: int, frame: Any) -> None:
        """Handle terminal resize (SIGWINCH)."""
        self._resized = True

    @staticmethod
    def _get_core_count() -> int:
        """Read CPU core count from /proc/cpuinfo (called once at init)."""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                return len([l for l in f.read().split('\n') if l.startswith('processor')])
        except (FileNotFoundError, IOError):
            return 0
        
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
                'core_count': self.core_count,
                'per_core_usage': per_core_usage
            }
        except KeyboardInterrupt:
            raise
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
        except KeyboardInterrupt:
            raise
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
                                # Get UID and RSS from /proc/[pid]/status (single read)
                                with open(f'/proc/{pid}/status', 'r') as f:
                                    for proc_line in f:
                                        if proc_line.startswith('Uid:'):
                                            uid = proc_line.split()[1]
                                            # Look up username from UID
                                            try:
                                                user = pwd.getpwuid(int(uid)).pw_name
                                            except (KeyError, ValueError):
                                                user = uid
                                        elif proc_line.startswith('VmRSS:'):
                                            # VmRSS is in kB
                                            host_mem = float(proc_line.split()[1]) / 1024  # Convert to MB
                                
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
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.gpu_processes = []
    
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
        box_h = 9
        
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
            
            # Compact two-line process layout.
            # Old layout burned 50 columns before the command (PID | USER | GPU | HOST),
            # leaving only ~29 chars on 80-col/iPad terminals. Put metadata on its own
            # line and give command lines almost the full box width instead.
            heading = "│ PID/USER/GPU/HOST on first line; command gets full width below"
            stdscr.addstr(y, x, (heading.ljust(BOX_WIDTH - 1))[:BOX_WIDTH-1] + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1
            
            if not self.gpu_processes:
                line = "│ No active GPU compute processes"
                stdscr.addstr(y, x, (line + " " * (BOX_WIDTH - len(line) - 1))[:BOX_WIDTH-1] + "│")
                y += 1
            else:
                cmd_prefix = "│ CMD: "
                cmd_indent = "│      "
                cmd_width = max(10, BOX_WIDTH - len(cmd_prefix) - 1)  # keep room for closing │
                
                for proc in self.gpu_processes:
                    if y >= height - 3:
                        break  # Don't draw off-screen
                    
                    mem_mb = proc['mem_used']
                    host_mem_mb = proc['host_mem']
                    user = proc['user'][:18]
                    
                    # Get command (use cmdline if available)
                    if proc.get('cmdline'):
                        parts = proc['cmdline'].split()
                        if parts:
                            base_name = os.path.basename(parts[0])
                            full_cmd = base_name + ' ' + ' '.join(parts[1:])
                        else:
                            full_cmd = "unknown"
                    else:
                        full_cmd = os.path.basename(proc['process_name'].split(',')[0].strip())

                    meta = f"│ PID {proc['pid']}  USER {user}  GPU {mem_mb:.0f}MB  HOST {host_mem_mb:.0f}MB"
                    stdscr.addstr(y, x, (meta.ljust(BOX_WIDTH - 1))[:BOX_WIDTH-1] + "│")
                    y += 1

                    # Word-wrap the command across nearly the full box width.
                    lines = self._wrap_command(full_cmd, cmd_width)
                    for idx, command_part in enumerate(lines):
                        if y >= height - 3:
                            break
                        prefix = cmd_prefix if idx == 0 else cmd_indent
                        line = f"{prefix}{command_part}"
                        stdscr.addstr(y, x, (line.ljust(BOX_WIDTH - 1))[:BOX_WIDTH-1] + "│")
                        y += 1
                    
                    # Add separator line between processes (if space)
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
                
                time.sleep(0.05)
        finally:
            curses.nocbreak()
            stdscr.keypad(False)
            curses.echo()
            curses.endwin()


if __name__ == "__main__":
    app = TermMon()
    app.run()
