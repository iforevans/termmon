#!/usr/bin/env python3
"""
termmon - Terminal system monitor (htop + nvidia-smi in one)
Pure Python, no external dependencies
"""

import curses
import subprocess
from datetime import datetime
import time

class TermMon:
    def __init__(self):
        self.running = True
        self.gpu_data = []
        self.system_data = {}
        self.last_cpu_stats = None
        self.last_per_core_stats = {}  # {core_id: (idle, total)}
        
    def get_system_stats(self):
        """Read system memory and CPU stats"""
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
            
            # Overall CPU
            with open('/proc/stat', 'r') as f:
                lines = f.readlines()
            
            # Parse overall CPU
            cpu_line = lines[0]
            cpu_values = [int(v) for v in cpu_line.split()[1:12]]
            idle = cpu_values[3] + cpu_values[4]
            total = sum(cpu_values)
            
            cpu_usage = 0.0
            if self.last_cpu_stats is not None:
                prev_idle, prev_total = self.last_cpu_stats
                idle_delta = idle - prev_idle
                total_delta = total - prev_total
                if total_delta > 0:
                    cpu_usage = (1 - idle_delta / total_delta) * 100
            
            self.last_cpu_stats = (idle, total)
            
            # Parse per-core stats (cpu0, cpu1, cpu2, ...)
            per_core_usage = []
            for line in lines[1:]:
                if line.startswith('cpu'):
                    parts = line.split()
                    if len(parts) >= 12:
                        core_id = int(parts[0][3:])  # Extract number from 'cpu0', 'cpu1', etc.
                        core_values = [int(v) for v in parts[1:12]]
                        core_idle = core_values[3] + core_values[4]
                        core_total = sum(core_values)
                        
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
    
    def get_gpu_stats(self):
        """Read NVIDIA GPU stats"""
        try:
            result = subprocess.run(
                [
                    'nvidia-smi',
                    '--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw',
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
    
    def update_stats(self):
        """Update all stats"""
        self.get_system_stats()
        self.get_gpu_stats()
    
    def draw_bar(self, stdscr, y, x, percent, width, color_pair):
        """Draw a progress bar with at least 1 block if percent > 0"""
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
    
    def draw(self, stdscr):
        """Draw the UI with fixed-width columns"""
        curses.curs_set(0)
        
        height, width = stdscr.getmaxyx()
        
        BOX_WIDTH = 80
        BAR_WIDTH = 20
        LABEL_WIDTH = 22
        
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
        
        # System Memory
        try:
            stdscr.addstr(y, x, "┌" + "─" * (BOX_WIDTH - 2) + "┐")
            y += 1
            stdscr.addstr(y, x, "│ SYSTEM MEMORY".ljust(BOX_WIDTH - 1) + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1
            
            # Total
            line = f"│ Total:    {self.system_data.get('total_mem_gb', 0):6.1f} GB"
            stdscr.addstr(y, x, (line + " " * (BOX_WIDTH - len(line) - 1))[:BOX_WIDTH-1] + "│")
            y += 1
            
            # Used with bar and %
            mem_pct = self.system_data.get('mem_percent', 0)
            used_gb = self.system_data.get('used_mem_gb', 0)
            label = "│ Used:".ljust(8) + f"{used_gb:6.1f}GB".rjust(LABEL_WIDTH - 8)
            stdscr.addstr(y, x, label)
            self.draw_bar(stdscr, y, x + LABEL_WIDTH, mem_pct, BAR_WIDTH, 2)
            pct_str = f" {mem_pct:5.1f}%"
            remaining = BOX_WIDTH - LABEL_WIDTH - BAR_WIDTH - 1
            stdscr.addstr(y, x + LABEL_WIDTH + BAR_WIDTH, pct_str.ljust(remaining)[:remaining])
            stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
            y += 1
            
            # Available
            line = f"│ Available: {self.system_data.get('avail_mem_gb', 0):6.1f} GB"
            stdscr.addstr(y, x, (line + " " * (BOX_WIDTH - len(line) - 1))[:BOX_WIDTH-1] + "│")
            y += 1
            
            # Swap with bar and %
            swap_pct = self.system_data.get('swap_percent', 0)
            swap_used = self.system_data.get('swap_used_mb', 0)
            swap_total = self.system_data.get('swap_total_mb', 0)
            label = "│ Swap:".ljust(8) + f"{swap_used:6.1f}/{swap_total:6.1f}MB".rjust(LABEL_WIDTH - 8)
            stdscr.addstr(y, x, label)
            self.draw_bar(stdscr, y, x + LABEL_WIDTH, swap_pct, BAR_WIDTH, 3)
            pct_str = f" {swap_pct:5.1f}%"
            remaining = BOX_WIDTH - LABEL_WIDTH - BAR_WIDTH - 1
            stdscr.addstr(y, x + LABEL_WIDTH + BAR_WIDTH, pct_str.ljust(remaining)[:remaining])
            stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
            y += 1
            
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass
        
        # CPU - Overall + Per-Core
        try:
            stdscr.addstr(y, x, "┌" + "─" * (BOX_WIDTH - 2) + "┐")
            y += 1
            stdscr.addstr(y, x, f"│ CPU ({self.system_data.get('core_count', 0)} cores)".ljust(BOX_WIDTH - 1) + "│")
            y += 1
            stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
            y += 1
            
            # Overall CPU
            cpu_pct = self.system_data.get('cpu_usage', 0)
            label = "│ Overall:".ljust(8) + f"{cpu_pct:6.1f}%".rjust(LABEL_WIDTH - 8)
            stdscr.addstr(y, x, label)
            self.draw_bar(stdscr, y, x + LABEL_WIDTH, cpu_pct, BAR_WIDTH, 4)
            stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
            y += 1
            
            # Per-core usage
            per_core = self.system_data.get('per_core_usage', [])
            for core_id, core_pct in per_core:
                label = f"│ Core {core_id}:".ljust(8) + f"{core_pct:6.1f}%".rjust(LABEL_WIDTH - 8)
                stdscr.addstr(y, x, label)
                self.draw_bar(stdscr, y, x + LABEL_WIDTH, core_pct, BAR_WIDTH, 4)
                stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                y += 1
            
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass
        
        # GPU
        try:
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
                    
                    name_line = f"│ GPU {gpu['idx']}: {gpu['name'][:32]}"
                    stdscr.addstr(y, x, (name_line + " " * (BOX_WIDTH - len(name_line) - 1))[:BOX_WIDTH-1] + "│")
                    y += 1
                    
                    # VRAM
                    label = "│ VRAM:".ljust(8) + f"{gpu['mem_used']:7.0f}/{gpu['mem_total']:7.0f}MB".rjust(LABEL_WIDTH - 8)
                    stdscr.addstr(y, x, label)
                    self.draw_bar(stdscr, y, x + LABEL_WIDTH, mem_pct, BAR_WIDTH, 5)
                    pct_str = f" {mem_pct:5.1f}%"
                    remaining = BOX_WIDTH - LABEL_WIDTH - BAR_WIDTH - 1
                    stdscr.addstr(y, x + LABEL_WIDTH + BAR_WIDTH, pct_str.ljust(remaining)[:remaining])
                    stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                    y += 1
                    
                    # Util
                    label = "│ Util:".ljust(8) + f"{gpu['gpu_util']:6.1f}%".rjust(LABEL_WIDTH - 8)
                    stdscr.addstr(y, x, label)
                    self.draw_bar(stdscr, y, x + LABEL_WIDTH, gpu['gpu_util'], BAR_WIDTH, 4)
                    stdscr.addstr(y, x + BOX_WIDTH - 1, "│")
                    y += 1
                    
                    temp_line = f"│ Temp: {gpu['temp']:5.0f}°C  Power: {gpu['power']:6.1f}W"
                    stdscr.addstr(y, x, (temp_line + " " * (BOX_WIDTH - len(temp_line) - 1))[:BOX_WIDTH-1] + "│")
                    y += 1
                    
                    if y < height - 3:
                        stdscr.addstr(y, x, "│" + "─" * (BOX_WIDTH - 2) + "│")
                        y += 1
            
            stdscr.addstr(y, x, "└" + "─" * (BOX_WIDTH - 2) + "┘")
            y += 2
        except curses.error:
            pass
        
        # Footer
        try:
            footer = " Refresh: 2s | q:quit r:refresh h:help "
            stdscr.attron(curses.A_REVERSE)
            stdscr.addstr(height - 1, 0, footer[:width-1].ljust(width-1)[:width-1])
            stdscr.attroff(curses.A_REVERSE)
        except curses.error:
            pass
        
        stdscr.refresh()
    
    def run(self):
        """Main loop"""
        stdscr = curses.initscr()
        
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)
        curses.init_pair(6, curses.COLOR_RED, -1)
        
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
