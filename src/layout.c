/* Pure, side-effect-free layout math and UTF-8 text ops.
 * Mirrors the Python implementation codepoint-for-codepoint so the C
 * renderer produces byte-identical screens. All functions here are unit
 * testable without curses or a terminal. */
#include "termmon.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#define MAX_SEGMENTS 8

int u8_len(const char *s)
{
    int n = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; p++)
        if ((*p & 0xC0) != 0x80)
            n++;
    return n;
}

const char *u8_skip(const char *s, int n)
{
    const unsigned char *p = (const unsigned char *)s;
    while (*p && n > 0) {
        do {
            p++;
        } while (*p && (*p & 0xC0) == 0x80);
        n--;
    }
    return (const char *)p;
}

void u8_clip(char *dst, size_t cap, const char *src, int maxcp)
{
    size_t i = 0;
    int cp = 0;
    const unsigned char *p = (const unsigned char *)src;
    if (cap == 0)
        return;
    while (*p && cp < maxcp) {
        const unsigned char *q = p;
        size_t j = i;
        do {
            if (j + 1 >= cap) /* codepoint would not fit the buffer */
                goto done;
            q++;
            j++;
        } while (*q && (*q & 0xC0) == 0x80);
        memcpy(dst + i, p, (size_t)(q - p));
        i = j;
        p = q;
        cp++;
    }
done:
    dst[i] = '\0';
}

void u8_slice(char *dst, size_t cap, const char *src, int skip, int count)
{
    u8_clip(dst, cap, u8_skip(src, skip > 0 ? skip : 0), count);
}

void u8_fit(char *dst, size_t cap, const char *src, int n)
{
    size_t i;
    int cp;
    if (cap == 0)
        return;
    u8_clip(dst, cap, src, n);
    i = strlen(dst);
    cp = u8_len(dst);
    while (cp < n && i + 1 < cap) {
        dst[i++] = ' ';
        cp++;
    }
    dst[i] = '\0';
}

void u8_center(char *dst, size_t cap, const char *src, int width)
{
    size_t i = 0;
    int len = u8_len(src);
    int blen = (int)strlen(src);
    if (cap == 0)
        return;
    if (width <= len) {
        size_t ncopy = (size_t)blen < cap - 1 ? (size_t)blen : cap - 1;
        memcpy(dst, src, ncopy);
        dst[ncopy] = '\0';
        return;
    }
    int marg = width - len;
    int p = marg / 2 + (marg & width & 1); /* CPython str.center rule */
    for (int k = 0; k < p && i + 1 < cap; k++)
        dst[i++] = ' ';
    for (int k = 0; k < blen && i + 1 < cap; k++)
        dst[i++] = src[k];
    for (int k = marg - p; k > 0 && i + 1 < cap; k--)
        dst[i++] = ' ';
    dst[i] = '\0';
}

int bar_width(int bw, int two_col, int overhead)
{
    int available = bw - 2 - overhead;
    if (two_col)
        available /= 2;
    if (available < MIN_BAR_WIDTH)
        available = MIN_BAR_WIDTH;
    if (available > BAR_WIDTH)
        available = BAR_WIDTH;
    return available;
}

int bar_filled(double percent, int width)
{
    if (percent < 0)
        percent = 0;
    if (percent > 100)
        percent = 100;
    int filled = percent > 0 ? (int)(percent / 100.0 * width) : 0;
    if (percent > 0 && filled < 1)
        filled = 1;
    if (filled > width)
        filled = width;
    return filled;
}

void stacked_segment_widths(const double *values, int n, int width, int *out)
{
    double shares[MAX_SEGMENTS];
    double total = 0.0;
    int order[MAX_SEGMENTS];
    int sum_floor = 0;

    for (int i = 0; i < n; i++)
        out[i] = 0;
    if (width <= 0 || n <= 0 || n > MAX_SEGMENTS)
        return;
    for (int i = 0; i < n; i++)
        if (values[i] > 0)
            total += values[i];
    if (total <= 0)
        return;
    for (int i = 0; i < n; i++) {
        double v = values[i] > 0 ? values[i] : 0.0;
        shares[i] = v / total * (double)width;
        out[i] = (int)shares[i];
        sum_floor += out[i];
        order[i] = i;
    }
    int leftover = width - sum_floor;
    /* sort by (fraction desc, index asc) — Python key (frac, -i), reverse */
    for (int a = 1; a < n; a++) {
        int key = order[a];
        int b = a - 1;
        double fkey = shares[key] - (double)out[key];
        while (b >= 0) {
            int cand = order[b];
            double fcand = shares[cand] - (double)out[cand];
            int after = fcand < fkey || (fcand == fkey && cand > key);
            if (!after)
                break;
            order[b + 1] = order[b];
            b--;
        }
        order[b + 1] = key;
    }
    if (leftover > n)
        leftover = n;
    for (int k = 0; k < leftover; k++)
        out[order[k]] += 1;
}

MemSegs mem_segments(const SysData *s)
{
    MemSegs m = { 0.0, 0.0, 0.0, 0.0 };
    double total = s->total_mem_gb;
    double used = s->used_mem_gb;
    double cache = s->cache_mem_gb;
    if (total <= 0)
        return m;
    used = fmin(fmax(0.0, used), total);
    cache = fmin(fmax(0.0, cache), total - used);
    m.used = used;
    m.cache = cache;
    m.free = fmax(0.0, total - used - cache);
    m.total = total;
    return m;
}

static const char *basename_of(const char *s)
{
    const char *slash = strrchr(s, '/');
    return slash ? slash + 1 : s;
}

int proc_command(char *dst, size_t cap, const GpuProc *p)
{
    char tmp[1024];
    size_t pos = 0;
    char copy[544];

    if (p->cmdline[0] != '\0') {
        snprintf(copy, sizeof copy, "%s", p->cmdline);
        /* tokenize on whitespace runs, like Python str.split() */
        char *tokv[64];
        int ntok = 0;
        char *save = NULL;
        for (char *t = strtok_r(copy, " \t\n\v\f\r", &save);
             t != NULL && ntok < 64; t = strtok_r(NULL, " \t\n\v\f\r", &save))
            tokv[ntok++] = t;
        if (ntok > 0) {
            const char *base = basename_of(tokv[0]);
            int w = snprintf(tmp + pos, sizeof tmp - pos, "%s", base);
            if (w < 0 || (size_t)w >= sizeof tmp - pos)
                goto trunc;
            pos += (size_t)w;
            for (int i = 1; i < ntok; i++) {
                w = snprintf(tmp + pos, sizeof tmp - pos, " %s", tokv[i]);
                if (w < 0 || (size_t)w >= sizeof tmp - pos)
                    goto trunc;
                pos += (size_t)w;
            }
        trunc:
            snprintf(dst, cap, "%s", tmp);
            return u8_len(dst);
        }
    }
    {
        char name[160];
        const char *comma;
        size_t keeplen;
        snprintf(name, sizeof name, "%s",
                 p->process_name[0] ? p->process_name : "unknown");
        comma = strchr(name, ',');
        keeplen = comma ? (size_t)(comma - name) : strlen(name);
        name[keeplen < sizeof name ? keeplen : sizeof name - 1] = '\0';
        /* strip surrounding whitespace (Python .strip() on the first field) */
        char *start = name;
        while (*start == ' ' || *start == '\t')
            start++;
        size_t slen = strlen(start);
        while (slen > 0 && (start[slen - 1] == ' ' || start[slen - 1] == '\t'))
            start[--slen] = '\0';
        snprintf(dst, cap, "%s", basename_of(start));
        return u8_len(dst);
    }
}

int gpu_process_fixed_prefix(char *dst, size_t cap, const GpuProc *p)
{
    char user8[96];
    char memtxt[32];
    char cputxt[32];
    char hosttxt[32];

    u8_fit(user8, sizeof user8, p->user, 8);
    snprintf(memtxt, sizeof memtxt, "%.0fM", p->gpu_mem_mb);
    snprintf(cputxt, sizeof cputxt, "%5.1f%%", p->cpu_pct);
    snprintf(hosttxt, sizeof hosttxt, "%.0fM", p->host_mem_mb);
    snprintf(dst, cap,
             "%-7d %-8s %-3s %-4s %5s %8s %6s %8s ",
             p->pid, user8, "0", "C", "--", memtxt, cputxt, hosttxt);
    return (int)strlen(dst);
}

int gpu_process_header(char *dst, size_t cap)
{
    snprintf(dst, cap,
             "%-7s %-8s %-3s %-4s %5s %8s %6s %8s %s",
             "PID", "USER", "DEV", "TYPE", "GPU", "GPU MEM", "CPU",
             "HOST MEM", "Command");
    return (int)strlen(dst);
}

int gpu_process_header_len(void)
{
    return 57;
}

int max_process_scroll(int bw, const GpuProc *procs, int n_procs)
{
    int view_width = bw - 4 > 1 ? bw - 4 : 1;
    int cmd_width = view_width - gpu_process_header_len();
    if (cmd_width < 1)
        cmd_width = 1;
    int best = 0;
    for (int i = 0; i < n_procs; i++) {
        char cmd[1024];
        proc_command(cmd, sizeof cmd, &procs[i]);
        int len = u8_len(cmd);
        if (len > best)
            best = len;
    }
    return best - cmd_width > 0 ? best - cmd_width : 0;
}
