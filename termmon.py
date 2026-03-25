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

__version__ = "1.0.0"
__author__ = "Ifor Evans"


# Layout configuration
BOX_WIDTH = 80          # Width of each section box
BAR_WIDTH = 20          # Width of progress bars
LABEL_WIDTH = 22        # Width of label column
REFRESH_INTERVAL = 2    # Seconds between auto-refreshes

# Color pair IDs
COLOR_TITLE = 1         # White - title and footer
COLOR_MEMORY = 2        # Green - RAM usage bar
COLOR_SWAP = 3          # Yellow - swap usage bar
COLOR_CPU = 4           # Cyan - CPU usage bar
COLOR_VRAM = 5          # Magenta - VRAM usage bar
COLOR_ERROR = 6         # Red - error messages

# NVIDIA GPU query fields (must match nvidia-smi output order)
GPU_QUERY_FIELDS = "index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw"


class TermMon:
    """Terminal-based system monitor combining htop and nvidia-smi."""
    
    def __init__(self) -> None:
        """Initialize the TermMon application."""
        self.running: bool = True
        self.gpu_data: List[Dict[str, Any]] = []
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
    
    def update_stats(self) -> None:
        """Update all system and GPU statistics."""
        self.get_system_stats()
        self.get_gpu_stats()
    
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
        Draw the system memory monitoring section.
        
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
            
            # Total memory
            total_gb = self.system_data.get('total_mem_gb', 0)
            line = f"│ Total:       {total_gb:5.1f}GB"
            stdscr.addstr(y, x, (line + " " * (BOX_WIDTH - len(line) - 1))[:BOX_WIDTH-1] + "│")
            y += 1
            
            # Used memory with bar
            mem_pct = self.system_data.get('mem_percent', 0)
            used_gb = self.system_data.get('used_mem_gb', 0)
            label = "│ Used:".ljust(8) + f"{used_gb:6.1f}GB".rjust(LABEL_WIDTH - 8)
            stdscr.addstr(y, x, label)
            self.draw_bar(stdscr, y, x + LABEL_WIDTH, mem_pct, BAR_WIDTH, COLOR_MEMORY)
            pct_str = f" {mem_pct:5.1f}%"
            remaining = BOX_WIDTH - LABEL_WIDTH - BAR_WIDTH - 1
            stdscr.addstr(y, x + LABEL_WIDTH + BAR_WIDTH, pct_str.ljust(remaining)[:remaining])
            stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
            y += 1
            
            # Available memory
            avail_gb = self.system_data.get('avail_mem_gb', 0)
            line = f"│ Available:  {avail_gb:6.1f}GB"
            stdscr.addstr(y, x, (line + " " * (BOX_WIDTH - len(line) - 1))[:BOX_WIDTH-1] + "│")
            y += 1
            
            # Swap with bar
            swap_pct = self.system_data.get('swap_percent', 0)
            swap_used_gb = self.system_data.get('swap_used_mb', 0) / 1024
            swap_total_gb = self.system_data.get('swap_total_mb', 0) / 1024
            label = "│ Swap:".ljust(8) + f"{swap_used_gb:4.1f}/{swap_total_gb:4.1f}GB".rjust(LABEL_WIDTH - 8)
            stdscr.addstr(y, x, label)
            self.draw_bar(stdscr, y, x + LABEL_WIDTH, swap_pct, BAR_WIDTH, COLOR_SWAP)
            pct_str = f" {swap_pct:5.1f}%"
            remaining = BOX_WIDTH - LABEL_WIDTH - BAR_WIDTH - 1
            stdscr.addstr(y, x + LABEL_WIDTH + BAR_WIDTH, pct_str.ljust(remaining)[:remaining])
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
        Draw the CPU monitoring section (overall + per-core).
        
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
            
            # Overall CPU usage
            cpu_pct = self.system_data.get('cpu_usage', 0)
            label = f"│ Overall:".ljust(12) + f"{cpu_pct:6.1f}%".rjust(LABEL_WIDTH - 12)
            stdscr.addstr(y, x, label)
            self.draw_bar(stdscr, y, x + LABEL_WIDTH, cpu_pct, BAR_WIDTH, COLOR_CPU)
            stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
            y += 1
            
            # Per-core usage
            per_core = self.system_data.get('per_core_usage', [])
            for core_id, core_pct in per_core:
                if y >= height - 3:
                    break  # Don't draw off-screen
                
                label = f"│ Core {core_id}:".ljust(11) + f"{core_pct:6.1f}%".rjust(LABEL_WIDTH - 11)
                try:
                    stdscr.addstr(y, x, label)
                    self.draw_bar(stdscr, y, x + LABEL_WIDTH, core_pct, BAR_WIDTH, COLOR_CPU)
                    stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                except curses.error:
                    pass  # Skip if can't draw
                y += 1
            
            # Box footer
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass
        
        return y
    
    def _draw_gpu_section(self, stdscr, y: int, x: int, height: int) -> int:
        """
        Draw the NVIDIA GPU monitoring section.
        
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
                    
                    # GPU name (truncated if needed)
                    name_line = f"│ GPU {gpu['idx']}: {gpu['name'][:32]}"
                    stdscr.addstr(y, x, (name_line + " " * (BOX_WIDTH - len(name_line) - 1))[:BOX_WIDTH-1] + "│")
                    y += 1
                    
                    # VRAM usage
                    label = "│ VRAM:".ljust(8) + f"{gpu['mem_used']:5.0f}/{gpu['mem_total']:5.0f}MB".rjust(LABEL_WIDTH - 8)
                    stdscr.addstr(y, x, label)
                    self.draw_bar(stdscr, y, x + LABEL_WIDTH, mem_pct, BAR_WIDTH, COLOR_VRAM)
                    pct_str = f" {mem_pct:5.1f}%"
                    remaining = BOX_WIDTH - LABEL_WIDTH - BAR_WIDTH - 1
                    stdscr.addstr(y, x + LABEL_WIDTH + BAR_WIDTH, pct_str.ljust(remaining)[:remaining])
                    stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                    y += 1
                    
                    # GPU utilization
                    label = "│ Util:".ljust(8) + f"{gpu['gpu_util']:6.1f}%".rjust(LABEL_WIDTH - 8)
                    stdscr.addstr(y, x, label)
                    self.draw_bar(stdscr, y, x + LABEL_WIDTH, gpu['gpu_util'], BAR_WIDTH, COLOR_CPU)
                    stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                    y += 1
                    
                    # Temperature and power
                    temp_line = f"│ Temp: {gpu['temp']:5.0f}°C  Power: {gpu['power']:6.1f}W"
                    stdscr.addstr(y, x, (temp_line + " " * (BOX_WIDTH - len(temp_line) - 1))[:BOX_WIDTH-1] + "│")
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
    
    def draw(self, stdscr) -> None:
        """Draw the complete UI with all monitoring sections."""
        curses.curs_set(0)
        
        height, width = stdscr.getmaxyx()
        
        stdscr.erase()
        
        # Title
        title = f" termmon - System Monitor | {datetime.now().strftime('%H:%M:%S')} | q:quit r:refresh h:help "
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
