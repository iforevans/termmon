/* Screen renderer: mirrors termmon.py's frame engine row-for-row so the
 * C build is a drop-in replacement (same layout, clipping and degradation
 * rules at every terminal size). */
#include "termmon.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>

#define BUF 1024
#define MAX_PARTS 5
#define GPU_TWO_COL_MIN 84

typedef struct {
    SysData sys;
    Gpu gpus[MAX_GPUS];
    int n_gpus;
    GpuProc procs[MAX_GPU_PROCS];
    int n_procs;
} Snapshot;

typedef struct {
    char text[64];
    int color;
} Part;

static void snapshot(App *app, Snapshot *snap)
{
    pthread_mutex_lock(&app->stats.lock);
    snap->sys = app->stats.sys;
    memcpy(snap->gpus, app->stats.gpus, sizeof snap->gpus);
    snap->n_gpus = app->stats.n_gpus;
    memcpy(snap->procs, app->stats.procs, sizeof snap->procs);
    snap->n_procs = app->stats.n_procs;
    pthread_mutex_unlock(&app->stats.lock);
}

static void safe_addstr(WINDOW *scr, int y, int x, const char *text,
                        chtype attr, int max_x)
{
    int h, w;
    char buf[BUF];

    getmaxyx(scr, h, w);
    if (y < 0 || y >= h || x >= w)
        return;
    if (x < 0) {
        text = u8_skip(text, -x);
        x = 0;
    }
    int max_len = w - x;
    if (max_x > 0 && max_x - x < max_len)
        max_len = max_x - x;
    if (max_len <= 0)
        return;
    u8_clip(buf, sizeof buf, text, max_len);
    if (buf[0] == '\0')
        return;
    if (attr)
        wattron(scr, attr);
    mvwaddnstr(scr, y, x, buf, (int)strlen(buf));
    if (attr)
        wattroff(scr, attr);
}

static void rep_utf8(char *dst, size_t cap, const char *unit, int n)
{
    size_t i = 0;
    size_t ulen = strlen(unit);
    for (int k = 0; k < n; k++) {
        if (i + ulen >= cap)
            break;
        memcpy(dst + i, unit, ulen);
        i += ulen;
    }
    dst[i] = '\0';
}

static void filled_blocks(char *dst, size_t cap, int n)
{
    rep_utf8(dst, cap, "█", n);
}

static void empty_blocks(char *dst, size_t cap, int n)
{
    rep_utf8(dst, cap, "░", n);
}

/* left + fill repeated n times + right, byte-budget safe (no snprintf) */
static void rule_row(char *dst, size_t cap, const char *left, const char *fill,
                     int fill_len, const char *right)
{
    size_t pos = 0;
    size_t llen = strlen(left), flen = strlen(fill), rlen = strlen(right);
    if (cap < llen + rlen + 1) {
        if (cap > 0)
            dst[0] = '\0';
        return;
    }
    memcpy(dst + pos, left, llen);
    pos += llen;
    for (int i = 0; i < fill_len && pos + flen + rlen < cap; i++) {
        memcpy(dst + pos, fill, flen);
        pos += flen;
    }
    memcpy(dst + pos, right, rlen);
    pos += rlen;
    dst[pos] = '\0';
}

static void draw_bar(WINDOW *scr, int y, int x, double percent, int width,
                     int color, int max_x)
{
    char buf[BUF];
    int filled = bar_filled(percent, width);
    if (filled > 0) {
        filled_blocks(buf, sizeof buf, filled);
        safe_addstr(scr, y, x, buf, COLOR_PAIR(color) | A_BOLD, max_x);
    }
    if (width - filled > 0) {
        empty_blocks(buf, sizeof buf, width - filled);
        safe_addstr(scr, y, x + filled, buf, 0, max_x);
    }
}

static void draw_stacked_bar(WINDOW *scr, int y, int x, const double *values,
                             const int *colors, int n, int width, int max_x)
{
    int wseg[4];
    char buf[BUF];
    int cx = x;

    stacked_segment_widths(values, n, width, wseg);
    for (int i = 0; i < n; i++) {
        if (wseg[i] > 0) {
            filled_blocks(buf, sizeof buf, wseg[i]);
            safe_addstr(scr, y, cx, buf, COLOR_PAIR(colors[i]) | A_BOLD,
                        max_x);
            cx += wseg[i];
        }
    }
    if (cx < x + width) {
        empty_blocks(buf, sizeof buf, x + width - cx);
        safe_addstr(scr, y, cx, buf, 0, max_x);
    }
}

static void draw_parts(WINDOW *scr, int row_y, int start_x, const Part *parts,
                       int nparts, int border_x)
{
    int cx = start_x;
    for (int i = 0; i < nparts; i++) {
        chtype attr = parts[i].color ? (chtype)(COLOR_PAIR(parts[i].color) | A_BOLD) : 0;
        safe_addstr(scr, row_y, cx, parts[i].text, attr, border_x);
        cx += u8_len(parts[i].text);
    }
}

static void blank_row(WINDOW *scr, int y, int x, int bw, int right_edge)
{
    char line[BUF];
    rule_row(line, sizeof line, "│", " ", bw - 2, "│");
    safe_addstr(scr, y, x, line, 0, right_edge);
}

static void box_header(WINDOW *scr, int y, int x, int bw, int right_edge)
{
    char line[BUF];
    rule_row(line, sizeof line, "┌", "─", bw - 2, "┐");
    safe_addstr(scr, y, x, line, 0, right_edge);
}

static void box_rule(WINDOW *scr, int y, int x, int bw, int right_edge)
{
    char line[BUF];
    rule_row(line, sizeof line, "│", "─", bw - 2, "│");
    safe_addstr(scr, y, x, line, 0, right_edge);
}

static void box_footer(WINDOW *scr, int y, int x, int bw, int right_edge)
{
    char line[BUF];
    rule_row(line, sizeof line, "└", "─", bw - 2, "┘");
    safe_addstr(scr, y, x, line, 0, right_edge);
}

/* ---- System memory section (mirrors _draw_memory_section) ---- */

static void legend_spec(int which, const MemSegs *m, char out[3][64])
{
    switch (which) {
    case 0:
        snprintf(out[0], 64, "Used: %5.1f GiB", m->used);
        snprintf(out[1], 64, "Cache: %5.1f GiB", m->cache);
        snprintf(out[2], 64, "Free: %5.1f GiB", m->free);
        break;
    case 1:
        snprintf(out[0], 64, "Used %5.1fG", m->used);
        snprintf(out[1], 64, "Cache %5.1fG", m->cache);
        snprintf(out[2], 64, "Free %5.1fG", m->free);
        break;
    case 2:
        snprintf(out[0], 64, "U %4.1f", m->used);
        snprintf(out[1], 64, "C %4.1f", m->cache);
        snprintf(out[2], 64, "F %4.1fG", m->free);
        break;
    default:
        snprintf(out[0], 64, "U%3.1f", m->used);
        snprintf(out[1], 64, "C%3.1f", m->cache);
        snprintf(out[2], 64, "F%3.1f", m->free);
        break;
    }
}

static int legend_parts(int which, const MemSegs *m, Part parts[MAX_PARTS])
{
    char txt[3][64];
    static const int seg_colors[3] = { COLOR_MEMORY, COLOR_MEM_CACHE,
                                       COLOR_MEM_FREE };
    const char *sep = which == 3 ? "  " : " | ";
    int n = 0;

    legend_spec(which, m, txt);
    for (int i = 0; i < 3; i++) {
        if (i) {
            snprintf(parts[n].text, sizeof parts[n].text, "%s", sep);
            parts[n].color = 0;
            n++;
        }
        snprintf(parts[n].text, sizeof parts[n].text, "%.63s", txt[i]);
        parts[n].color = seg_colors[i];
        n++;
    }
    return n;
}

static int parts_len(const Part *parts, int nparts)
{
    int total = 0;
    for (int i = 0; i < nparts; i++)
        total += u8_len(parts[i].text);
    return total;
}

typedef struct {
    enum { ROW_LEGEND, ROW_SUMMARY, ROW_SWAP } kind;
    Part parts[MAX_PARTS];
    int nparts;
    char text[160];
} MemRow;

static int draw_memory_section(App *app, WINDOW *scr, int y, int x, int height,
                               const Snapshot *snap, int bw)
{
    int right_edge = x + bw;
    int border_x = x + bw - 1;
    const SysData *s = &snap->sys;
    MemSegs m = mem_segments(s);
    double avail_gb = s->avail_mem_gb;
    double swap_pct = s->swap_percent;
    double swap_used_gb = s->swap_used_mb / 1024.0;
    double swap_total_gb = s->swap_total_mb / 1024.0;
    static const int seg_colors[3] = { COLOR_MEMORY, COLOR_MEM_CACHE,
                                       COLOR_MEM_FREE };
    char line[BUF];

    box_header(scr, y, x, bw, right_edge);
    y += 1;
    snprintf(line, sizeof line, "│ SYSTEM MEMORY");
    u8_fit(line, sizeof line, line, bw - 1);
    strncat(line, "│", BUF - strlen(line) - 1);
    safe_addstr(scr, y, x, line, 0, right_edge);
    y += 1;
    box_rule(scr, y, x, bw, right_edge);
    y += 1;

    Part inline_parts[MAX_PARTS];
    int n_inline = -1;
    Part legend_row_parts[MAX_PARTS];
    int n_legend = -1;
    int inline_len = 0;

    for (int spec = 0; spec < 4; spec++) {
        Part parts[MAX_PARTS];
        int n = legend_parts(spec, &m, parts);
        if (bw - 2 - 7 - 1 - parts_len(parts, n) >= MIN_BAR_WIDTH) {
            memcpy(inline_parts, parts, sizeof parts);
            n_inline = n;
            inline_len = parts_len(parts, n);
            break;
        }
    }
    int bar_w;
    if (n_inline >= 0) {
        bar_w = bar_width(bw, 0, 7 + 1 + inline_len);
    } else {
        bar_w = bar_width(bw, 0, 7 + 1);
        for (int spec = 0; spec < 4; spec++) {
            Part parts[MAX_PARTS];
            int n = legend_parts(spec, &m, parts);
            if (parts_len(parts, n) <= bw - 4) {
                memcpy(legend_row_parts, parts, sizeof parts);
                n_legend = n;
                break;
            }
        }
        if (n_legend < 0)
            n_legend = legend_parts(3, &m, legend_row_parts);
    }

    if (y <= height - 2) {
        blank_row(scr, y, x, bw, right_edge);
        safe_addstr(scr, y, x, "│ Mem:", 0, right_edge);
        double vals[3] = { m.used, m.cache, m.free };
        draw_stacked_bar(scr, y, x + 7, vals, seg_colors, 3, bar_w, border_x);
        if (n_inline >= 0)
            draw_parts(scr, y, x + 7 + bar_w + 1, inline_parts, n_inline,
                       border_x);
        safe_addstr(scr, y, border_x, "│", 0, right_edge);
        y += 1;
    }

    char summ[4][128];
    snprintf(summ[0], 128, "Total: %6.1f GiB | Available: %6.1f GiB", m.total,
             avail_gb);
    snprintf(summ[1], 128, "Total: %5.1fG | Available: %5.1fG", m.total,
             avail_gb);
    snprintf(summ[2], 128, "Total %5.1fG | Avail %5.1fG", m.total, avail_gb);
    snprintf(summ[3], 128, "T %4.1fG A %4.1fG", m.total, avail_gb);
    const char *summary = summ[3];
    for (int i = 0; i < 4; i++) {
        if ((int)strlen(summ[i]) <= bw - 4) {
            summary = summ[i];
            break;
        }
    }

    char sw[3][96];
    snprintf(sw[0], 96, " %4.1f/%4.1fGB %5.1f%%", swap_used_gb, swap_total_gb,
             swap_pct);
    snprintf(sw[1], 96, " %4.1f/%4.1fG %4.0f%%", swap_used_gb, swap_total_gb,
             swap_pct);
    snprintf(sw[2], 96, " %.0f%%", swap_pct);
    const char *swap_info = sw[2];
    for (int i = 0; i < 3; i++) {
        if (bw - 2 - 7 - 1 - (int)strlen(sw[i]) >= MIN_BAR_WIDTH) {
            swap_info = sw[i];
            break;
        }
    }
    int swap_bar_w = bar_width(bw, 0, 7 + 1 + (int)strlen(swap_info));

    MemRow rows[3];
    int nrows = 0;
    if (n_legend >= 0) {
        rows[nrows].kind = ROW_LEGEND;
        memcpy(rows[nrows].parts, legend_row_parts, sizeof legend_row_parts);
        rows[nrows].nparts = n_legend;
        nrows++;
    }
    rows[nrows].kind = ROW_SUMMARY;
    rows[nrows].nparts = 0;
    snprintf(rows[nrows].text, sizeof rows[nrows].text, "%s", summary);
    nrows++;
    rows[nrows].kind = ROW_SWAP;
    rows[nrows].nparts = 0;
    snprintf(rows[nrows].text, sizeof rows[nrows].text, "%s", swap_info);
    nrows++;

    int budget = (height - 2) - y;
    if (budget < 0)
        budget = 0;
    while (nrows > budget)
        nrows--;

    for (int r = 0; r < nrows; r++) {
        blank_row(scr, y, x, bw, right_edge);
        if (rows[r].kind == ROW_LEGEND) {
            draw_parts(scr, y, x + 2, rows[r].parts, rows[r].nparts, border_x);
        } else if (rows[r].kind == ROW_SUMMARY) {
            safe_addstr(scr, y, x + 2, rows[r].text, 0, border_x);
        } else {
            safe_addstr(scr, y, x, "│ Swap:", 0, right_edge);
            draw_bar(scr, y, x + 7, swap_pct, swap_bar_w, COLOR_SWAP,
                     border_x);
            safe_addstr(scr, y, x + 7 + swap_bar_w, swap_info, 0, border_x);
        }
        safe_addstr(scr, y, border_x, "│", 0, right_edge);
        y += 1;
    }

    box_footer(scr, y, x, bw, right_edge);
    y += 2;
    (void)app;
    return y;
}

/* ---- CPU section (mirrors _draw_cpu_section) ---- */

static void bar_string(char *dst, size_t cap, int filled, int total)
{
    size_t pos = 0;
    for (int i = 0; i < filled; i++) {
        if (pos + 3 >= cap)
            break;
        memcpy(dst + pos, "█", 3);
        pos += 3;
    }
    for (int i = filled; i < total; i++) {
        if (pos + 3 >= cap)
            break;
        memcpy(dst + pos, "░", 3);
        pos += 3;
    }
    dst[pos] = '\0';
}

static void core_cell(int core_id, double core_pct, int width,
                      int label_width, char *text, size_t cap, int *bar_start,
                      int *filled_out)
{
    char label[64];
    char pct_text[32];
    char bar[512];

    snprintf(label, sizeof label, "Core %d:", core_id);
    u8_fit(label, sizeof label, label, label_width);
    snprintf(pct_text, sizeof pct_text, " %5.1f%%", core_pct);
    if (width < (int)strlen(label) + MIN_BAR_WIDTH + (int)strlen(pct_text)) {
        snprintf(label, sizeof label, "%d:", core_id);
        u8_fit(label, sizeof label, label,
               label_width < 4 ? label_width : 4);
    }
    if (width < (int)strlen(label) + MIN_BAR_WIDTH + (int)strlen(pct_text))
        pct_text[0] = '\0';

    int bar_w = width - (int)strlen(label) - (int)strlen(pct_text);
    if (bar_w < 1)
        bar_w = 1;
    int filled = bar_filled(core_pct, bar_w);
    bar_string(bar, sizeof bar, filled, bar_w);
    snprintf(text, cap, "%.63s%s%.7s", label, bar, pct_text);
    u8_fit(text, cap, text, width);
    *bar_start = (int)strlen(label);
    *filled_out = filled;
}

static int draw_cpu_section(App *app, WINDOW *scr, int y, int x, int height,
                            const Snapshot *snap, int bw)
{
    int right_edge = x + bw;
    const SysData *s = &snap->sys;
    int core_count = s->core_count > MAX_CORES ? MAX_CORES : s->core_count;
    double cpu_pct = s->cpu_usage;
    char title[256];
    char line[BUF];

    box_header(scr, y, x, bw, right_edge);
    y += 1;

    if (s->has_temp)
        snprintf(title, sizeof title,
                 " CPU: %d Cores | Usage: %.1f%% | Temp: %.0f°C", core_count,
                 cpu_pct, s->cpu_temp);
    else
        snprintf(title, sizeof title, " CPU: %d Cores | Usage: %.1f%%",
                 core_count, cpu_pct);
    if (u8_len(title) > bw - 2)
        snprintf(title, sizeof title, " CPU: %d Cores | %.1f%%", core_count,
                 cpu_pct);
    if (u8_len(title) > bw - 2)
        snprintf(title, sizeof title, " CPU %.1f%%", cpu_pct);
    snprintf(line, sizeof line, "│%s", title);
    u8_fit(line, sizeof line, line, bw - 1);
    strncat(line, "│", BUF - strlen(line) - 1);
    safe_addstr(scr, y, x, line, 0, right_edge);
    y += 1;
    box_rule(scr, y, x, bw, right_edge);
    y += 1;

    int content_width = bw - 4;
    int gap_width = 2;
    char probe[64];
    snprintf(probe, sizeof probe, "Core %d:", core_count - 1 > 0 ? core_count - 1 : 0);
    int label_width = (int)strlen(probe) + 1;
    int pct_len = (int)strlen(" 100.0%");

    int min_cell = label_width + MIN_BAR_WIDTH + pct_len;
    int two_col = content_width >= (min_cell * 2 + gap_width);

    int col_width, right_width, rows;
    if (two_col) {
        col_width = (content_width - gap_width) / 2;
        right_width = content_width - gap_width - col_width;
        rows = (core_count + 1) / 2;
    } else {
        col_width = content_width;
        right_width = 0;
        rows = core_count;
    }

    chtype cpu_attr = COLOR_PAIR(COLOR_CPU) | A_BOLD;

    for (int i = 0; i < rows; i++) {
        if (y >= height - 3)
            break;
        char left[BUF] = "";
        int left_bar_start = 0, left_filled = 0;
        if (i < core_count)
            core_cell(i, s->per_core[i], col_width, label_width, left,
                      sizeof left, &left_bar_start, &left_filled);
        if (!left[0])
            u8_fit(left, sizeof left, "", col_width);

        int right_bar_start = 0, right_filled = 0;
        if (two_col) {
            char right[BUF] = "";
            int right_idx = i + rows;
            if (right_idx < core_count)
                core_cell(right_idx, s->per_core[right_idx], right_width,
                          label_width, right, sizeof right, &right_bar_start,
                          &right_filled);
            if (!right[0])
                u8_fit(right, sizeof right, "", right_width);
            snprintf(line, sizeof line, "│ %s  %s │", left, right);
        } else {
            snprintf(line, sizeof line, "│ %s │", left);
        }
        safe_addstr(scr, y, x, line, 0, right_edge);

        if (left_filled > 0) {
            char blocks[BUF];
            filled_blocks(blocks, sizeof blocks, left_filled);
            safe_addstr(scr, y, x + 2 + left_bar_start, blocks, cpu_attr,
                        x + bw - 1);
        }
        if (two_col && right_filled > 0) {
            char blocks[BUF];
            int right_x = x + 2 + col_width + gap_width + right_bar_start;
            filled_blocks(blocks, sizeof blocks, right_filled);
            safe_addstr(scr, y, right_x, blocks, cpu_attr, x + bw - 1);
        }
        y += 1;
    }

    box_footer(scr, y, x, bw, right_edge);
    y += 2;
    (void)app;
    return y;
}

/* ---- GPU section (mirrors _draw_gpu_section, NVIDIA only) ---- */

static const char *gpu_section_title(const Snapshot *snap)
{
    if (snap->n_gpus == 0)
        return gpu_backend_nvidia() ? "NVIDIA GPU(s)" : "GPU";
    return "GPUs";
}

static const char *gpu_no_data_message(void)
{
    return gpu_backend_nvidia() ? "No NVIDIA GPUs found or nvidia-smi not available"
                                : "No GPU detected or GPU monitoring unavailable";
}

static int draw_gpu_section(App *app, WINDOW *scr, int y, int x, int height,
                            const Snapshot *snap, int bw)
{
    int right_edge = x + bw;
    int border_x = x + bw - 1;
    char line[BUF];

    box_header(scr, y, x, bw, right_edge);
    y += 1;
    snprintf(line, sizeof line, "│ %s", gpu_section_title(snap));
    u8_fit(line, sizeof line, line, bw - 1);
    strncat(line, "│", BUF - strlen(line) - 1);
    safe_addstr(scr, y, x, line, 0, right_edge);
    y += 1;
    box_rule(scr, y, x, bw, right_edge);
    y += 1;

    if (snap->n_gpus == 0) {
        snprintf(line, sizeof line, "│ %s", gpu_no_data_message());
        u8_fit(line, sizeof line, line, bw - 1);
        strncat(line, "│", BUF - strlen(line) - 1);
        safe_addstr(scr, y, x, line, 0, right_edge);
        y += 1;
    } else {
        for (int g = 0; g < snap->n_gpus; g++) {
            const Gpu *gpu = &snap->gpus[g];
            if (y >= height - 3)
                break;

            /* Row 1: name | Util | Temp | Power, dropping segments to fit */
            char segs[4][160];
            int nsegs = 0;
            snprintf(segs[nsegs++], 160, "GPU %s: %s", gpu->idx, gpu->name);
            snprintf(segs[nsegs++], 160, "Util: %.1f%%", gpu->gpu_util);
            if (gpu->temp > 0)
                snprintf(segs[nsegs++], 160, "Temp: %.0f°C", gpu->temp);
            if (gpu->power > 0)
                snprintf(segs[nsegs++], 160, "Power: %.1fW", gpu->power);

            int content_width = bw - 3;
            char joined[512];
            for (;;) {
                joined[0] = '\0';
                size_t pos = 0;
                for (int i = 0; i < nsegs; i++) {
                    int w = snprintf(joined + pos, sizeof joined - pos,
                                     i ? " | %s" : "%s", segs[i]);
                    if (w > 0 && (size_t)w < sizeof joined - pos)
                        pos += (size_t)w;
                }
                if (nsegs <= 1 || u8_len(joined) <= content_width)
                    break;
                nsegs--;
            }
            char header[512];
            u8_clip(header, sizeof header, joined, content_width);

            blank_row(scr, y, x, bw, right_edge);
            snprintf(line, sizeof line, "│ %s", header);
            safe_addstr(scr, y, x, line, 0, border_x);
            safe_addstr(scr, y, border_x, "│", 0, right_edge);
            y += 1;
            if (y >= height - 3)
                break;

            /* Row 2: Util bar (left) | VRAM (right, when wide) */
            char util_info[32];
            snprintf(util_info, sizeof util_info, " %6.1f%%", gpu->gpu_util);
            double mem_pct = gpu->mem_total > 0
                                 ? gpu->mem_used / gpu->mem_total * 100.0
                                 : 0.0;
            double mem_used_gb = gpu->mem_used / 1024.0;
            double mem_total_gb = gpu->mem_total / 1024.0;
            char vram_info[64];
            snprintf(vram_info, sizeof vram_info, " %5.1fGB/%4.1fG %5.1f%%",
                     mem_used_gb, mem_total_gb, mem_pct);

            int two_col = bw >= GPU_TWO_COL_MIN;
            int bar_w;
            if (two_col) {
                int overhead = 7 + (int)strlen(util_info) + 2 + 5 +
                               (int)strlen(vram_info) + 1;
                bar_w = bar_width(bw, 1, overhead);
            } else {
                int overhead = 7 + (int)strlen(util_info) + 1;
                bar_w = bar_width(bw, 0, overhead);
            }

            blank_row(scr, y, x, bw, right_edge);
            safe_addstr(scr, y, x, "│ Util:", 0, right_edge);
            draw_bar(scr, y, x + 7, gpu->gpu_util, bar_w, COLOR_CPU, border_x);
            safe_addstr(scr, y, x + 7 + bar_w, util_info, 0, border_x);

            if (two_col) {
                int rcs = x + 7 + bar_w + (int)strlen(util_info) + 2;
                safe_addstr(scr, y, rcs, "VRAM:", 0, border_x);
                draw_bar(scr, y, rcs + 5, mem_pct, bar_w, COLOR_VRAM,
                         border_x);
                safe_addstr(scr, y, rcs + 5 + bar_w, vram_info, 0, border_x);
                safe_addstr(scr, y, border_x, "│", 0, right_edge);
                y += 1;
            } else {
                safe_addstr(scr, y, border_x, "│", 0, right_edge);
                y += 1;
                if (y < height - 3) {
                    int vram_overhead = 7 + (int)strlen(vram_info) + 1;
                    int vram_bar_w = bar_width(bw, 0, vram_overhead);
                    blank_row(scr, y, x, bw, right_edge);
                    safe_addstr(scr, y, x, "│ VRAM:", 0, right_edge);
                    draw_bar(scr, y, x + 7, mem_pct, vram_bar_w, COLOR_VRAM,
                             border_x);
                    safe_addstr(scr, y, x + 7 + vram_bar_w, vram_info, 0,
                                border_x);
                    safe_addstr(scr, y, border_x, "│", 0, right_edge);
                    y += 1;
                }
            }

            if (y < height - 3 && atoi(gpu->idx) < snap->n_gpus - 1) {
                box_rule(scr, y, x, bw, right_edge);
                y += 1;
            }
        }
    }

    box_footer(scr, y, x, bw, right_edge);
    y += 2;
    (void)app;
    return y;
}

/* ---- GPU processes section (mirrors _draw_gpu_processes_section) ---- */

static void draw_scrolled_process_line(WINDOW *scr, int y, int x, int scroll_x,
                                       const char *fixed, const char *command,
                                       int width)
{
    int view_width = width - 4 > 1 ? width - 4 : 1;
    char fixed_visible[160];
    char cmd_slice[512];
    char visible[700];
    char line[BUF];

    u8_clip(fixed_visible, sizeof fixed_visible, fixed, view_width);
    int cmd_width = view_width - u8_len(fixed_visible);
    if (cmd_width < 0)
        cmd_width = 0;
    int scroll = scroll_x > 0 ? scroll_x : 0;
    u8_slice(cmd_slice, sizeof cmd_slice, command, scroll, cmd_width);
    snprintf(visible, sizeof visible, "%.159s%.511s", fixed_visible,
             cmd_slice);
    u8_fit(visible, sizeof visible, visible, view_width);
    snprintf(line, sizeof line, "│ %s │", visible);
    safe_addstr(scr, y, x, line, 0, x + width);
}

static int draw_gpu_processes_section(App *app, WINDOW *scr, int y, int x,
                                      int height, const Snapshot *snap, int bw)
{
    int right_edge = x + bw;
    char line[BUF];
    char hdr[128];

    int max_scroll = max_process_scroll(bw, snap->procs, snap->n_procs);
    if (app->process_scroll_x < 0)
        app->process_scroll_x = 0;
    if (app->process_scroll_x > max_scroll)
        app->process_scroll_x = max_scroll;

    box_header(scr, y, x, bw, right_edge);
    y += 1;

    snprintf(line, sizeof line,
             "│ GPU PROCESSES  ←/→ scroll %d", app->process_scroll_x);
    u8_fit(line, sizeof line, line, bw - 1);
    strncat(line, "│", BUF - strlen(line) - 1);
    safe_addstr(scr, y, x, line, 0, right_edge);
    y += 1;

    box_rule(scr, y, x, bw, right_edge);
    y += 1;

    gpu_process_header(hdr, sizeof hdr);
    snprintf(line, sizeof line, "│ %s", hdr);
    u8_fit(line, sizeof line, line, bw - 1);
    strncat(line, "│", BUF - strlen(line) - 1);
    safe_addstr(scr, y, x, line, 0, right_edge);
    y += 1;

    if (y < height - 3) {
        box_rule(scr, y, x, bw, right_edge);
        y += 1;
    }

    if (snap->n_procs == 0) {
        if (y < height - 3) {
            draw_scrolled_process_line(scr, y, x, app->process_scroll_x, "",
                                       "No active GPU compute processes", bw);
            y += 1;
        }
    } else {
        for (int i = 0; i < snap->n_procs; i++) {
            char fixed[128];
            char command[1024];
            if (y >= height - 3)
                break;
            gpu_process_fixed_prefix(fixed, sizeof fixed, &snap->procs[i]);
            proc_command(command, sizeof command, &snap->procs[i]);
            draw_scrolled_process_line(scr, y, x, app->process_scroll_x, fixed,
                                       command, bw);
            y += 1;
        }
    }

    box_footer(scr, y, x, bw, right_edge);
    y += 2;
    return y;
}

/* ---- frame (mirrors _draw_frame) ---- */

void draw(App *app, WINDOW *scr)
{
    Snapshot snap;
    snapshot(app, &snap);

    int height, width;
    getmaxyx(scr, height, width);

    curs_set(0);
    werase(scr);

    if (width < MIN_BOX_WIDTH || height < 6) {
        char msg[64];
        snprintf(msg, sizeof msg, "Terminal too small");
        u8_clip(msg, sizeof msg, msg, width - 1 > 0 ? width - 1 : 0);
        safe_addstr(scr, 0, 0, msg, 0, 0);
        if (height > 1) {
            snprintf(msg, sizeof msg, "%dx%d", width, height);
            u8_clip(msg, sizeof msg, msg, width - 1 > 0 ? width - 1 : 0);
            safe_addstr(scr, 1, 0, msg, 0, 0);
        }
        wrefresh(scr);
        return;
    }

    int bw = width - 2;
    if (bw > MAX_BOX_WIDTH)
        bw = MAX_BOX_WIDTH;
    if (bw < MIN_BOX_WIDTH)
        bw = MIN_BOX_WIDTH;
    app->box_width = bw;

    time_t t = time(NULL);
    struct tm *tm = localtime(&t);
    char clockstr[16];
    strftime(clockstr, sizeof clockstr, "%H:%M:%S", tm);

    char title[256];
    snprintf(title, sizeof title,
             " termmon %s - System Monitor | %s | q:quit r:rate h:help ",
             TERMMON_VERSION, clockstr);
    if (u8_len(title) > width - 1)
        snprintf(title, sizeof title, " termmon %s | %s ", TERMMON_VERSION,
                 clockstr);
    u8_fit(title, sizeof title, title, width - 1);
    safe_addstr(scr, 0, 0, title, A_REVERSE, 0);

    int y = 2;
    int x = (width - bw) / 2;
    if (x < 1)
        x = 1;
    if (x + bw > width) {
        x = width - bw;
        if (x < 0)
            x = 0;
    }

    y = draw_memory_section(app, scr, y, x, height, &snap, bw);
    y = draw_cpu_section(app, scr, y, x, height, &snap, bw);
    y = draw_gpu_section(app, scr, y, x, height, &snap, bw);
    y = draw_gpu_processes_section(app, scr, y, x, height, &snap, bw);

    char footer[256];
    char iv[32];
    fmt_interval(iv, sizeof iv, g_refresh_interval);
    snprintf(footer, sizeof footer,
             " Refresh: %ss | q:quit r:rate h:help ←/→:process scroll ", iv);
    if (u8_len(footer) > width - 1)
        snprintf(footer, sizeof footer, " q:quit r:rate h:help ←/→:scroll ");
    if (u8_len(footer) > width - 1)
        snprintf(footer, sizeof footer, " q:quit h:help ");
    u8_fit(footer, sizeof footer, footer, width - 1);
    safe_addstr(scr, height - 1, 0, footer, A_REVERSE, 0);

    wrefresh(scr);
}

/* ---- help popup (mirrors _show_help) ---- */

void show_help(App *app, WINDOW *scr)
{
    static const char *help_lines[] = {
        " q  - Quit",
        " r  - Cycle refresh rate",
        " h  - Show help (this)",
        " ←→ - Scroll process table",
    };
    const int n_help = 4;

    draw(app, scr);

    int h, w;
    getmaxyx(scr, h, w);

    int box_w = w - 2 > 0 ? w - 2 : 0;
    if (box_w > 36)
        box_w = 36;
    int max_h = h - 2 > 0 ? h - 2 : 0;
    int box_h = n_help + 6 < max_h ? n_help + 6 : max_h;
    if (box_w < 12 || box_h < 6)
        return;
    int visible = box_h - 6;

    int start_y = (h - box_h) / 2;
    if (start_y < 0)
        start_y = 0;
    int start_x = (w - box_w) / 2;
    if (start_x < 0)
        start_x = 0;

    chtype popup_attr = COLOR_PAIR(COLOR_POPUP);

    timeout(-1);

    char blanks[64];
    char border[64];
    rep_utf8(blanks, sizeof blanks, " ", box_w);
    for (int row = 0; row < box_h; row++)
        safe_addstr(scr, start_y + row, start_x, blanks, popup_attr, 0);

    rep_utf8(border, sizeof border, "-", box_w - 2);
    char bt[72];
    snprintf(bt, sizeof bt, "+%s+", border);
    safe_addstr(scr, start_y, start_x, bt, popup_attr, 0);
    safe_addstr(scr, start_y + box_h - 1, start_x, bt, popup_attr, 0);
    for (int row = 1; row < box_h - 1; row++) {
        safe_addstr(scr, start_y + row, start_x, "|", popup_attr, 0);
        safe_addstr(scr, start_y + row, start_x + box_w - 1, "|", popup_attr,
                    0);
    }

    const char *title = " KEYBINDINGS ";
    int title_x = start_x + (box_w - u8_len(title)) / 2;
    if ((box_w - u8_len(title)) / 2 < 0)
        title_x = start_x;
    safe_addstr(scr, start_y + 1, title_x, title,
                COLOR_PAIR(COLOR_SWAP) | A_BOLD, start_x + box_w - 1);

    safe_addstr(scr, start_y + 2, start_x + 1, border, popup_attr,
                start_x + box_w - 1);

    for (int i = 0; i < visible && i < n_help; i++) {
        char pad[128];
        char tmp[128];
        snprintf(tmp, sizeof tmp, " %s", help_lines[i]);
        u8_fit(pad, sizeof pad, tmp, box_w - 2);
        safe_addstr(scr, start_y + 3 + i, start_x + 1, pad, popup_attr,
                    start_x + box_w - 1);
    }

    char prompt[128];
    char centered[128];
    u8_center(centered, sizeof centered, " Press any key ", box_w - 2);
    u8_fit(prompt, sizeof prompt, centered, box_w - 2);
    safe_addstr(scr, start_y + box_h - 2, start_x + 1, prompt, popup_attr,
                start_x + box_w - 1);

    wrefresh(scr);
    wgetch(scr);

    timeout(50);
}
