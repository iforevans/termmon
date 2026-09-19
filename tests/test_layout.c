/* Unit tests for the pure layout math (layout.c). No curses, no terminal:
 * these lock the invariants that keep the box borders closed at any width
 * (largest-remainder stacking, clamped bar widths, codepoint-exact UTF-8
 * clipping) — the same math the Python oracle performs. */
#include "termmon.h"

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;

#define CHECK(cond)                                                       \
    do {                                                                  \
        if (!(cond)) {                                                    \
            failures++;                                                   \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__,       \
                    #cond);                                               \
        }                                                                 \
    } while (0)

static void test_utf8_helpers(void)
{
    char buf[64];

    CHECK(u8_len("abc") == 3);
    CHECK(u8_len("┌──┐") == 4);
    CHECK(u8_len("←/→") == 3);
    CHECK(u8_len("") == 0);

    /* clip stops exactly at maxcp codepoints, never a partial sequence */
    u8_clip(buf, sizeof buf, "┌──┐", 3);
    CHECK(strcmp(buf, "┌──") == 0);
    u8_clip(buf, sizeof buf, "┌──┐", 0);
    CHECK(strcmp(buf, "") == 0);
    u8_clip(buf, sizeof buf, "┌──┐", 10);
    CHECK(strcmp(buf, "┌──┐") == 0);

    u8_slice(buf, sizeof buf, "llama-server --host 1.2.3.4", 6, 6);
    CHECK(strcmp(buf, "server") == 0);

    u8_fit(buf, sizeof buf, "┌x", 5);
    CHECK(u8_len(buf) == 5 && strcmp(buf, "┌x   ") == 0);
    u8_fit(buf, sizeof buf, "abcdef", 3);
    CHECK(strcmp(buf, "abc") == 0);

    /* CPython str.center: extra pad on the LEFT when both gaps are odd */
    u8_center(buf, sizeof buf, "ab", 5);
    CHECK(strcmp(buf, "  ab ") == 0);
    u8_center(buf, sizeof buf, " Press any key ", 34);
    CHECK(u8_len(buf) == 34);
    u8_center(buf, sizeof buf, "012345678901234567890", 10); /* width <= len */
    CHECK(strcmp(buf, "012345678901234567890") == 0);
}

static void test_bar_width(void)
{
    CHECK(bar_width(120, 0, 8) == BAR_WIDTH);   /* plenty → capped at 20  */
    CHECK(bar_width(30, 0, 8) == 20);           /* 30-2-8=20              */
    CHECK(bar_width(26, 0, 8) == 16);           /* 26-2-8=16              */
    CHECK(bar_width(20, 0, 8) == 10);           /* 20-2-8=10              */
    CHECK(bar_width(8, 0, 2) == MIN_BAR_WIDTH); /* 8-2-2=4 → clamps up    */
    /* two_col halves the available space before clamping */
    CHECK(bar_width(120, 1, 30) == BAR_WIDTH);  /* (120-2-30)/2=44→20     */
    CHECK(bar_width(70, 1, 30) == 19);          /* (70-2-30)/2=19         */
    CHECK(bar_width(60, 1, 40) == 9);           /* (60-2-40)/2=9          */
    CHECK(bar_width(16, 1, 8) == MIN_BAR_WIDTH);/* (16-2-8)/2=3 → clamps  */
}

static void test_bar_filled(void)
{
    CHECK(bar_filled(0.0, 20) == 0);
    CHECK(bar_filled(0.1, 20) == 1);   /* percent>0 ⇒ at least one block */
    CHECK(bar_filled(100.0, 20) == 20);
    CHECK(bar_filled(120.0, 20) == 20); /* clamps high                   */
    CHECK(bar_filled(-5.0, 20) == 0);   /* clamps low                    */
    CHECK(bar_filled(23.4, 20) == 4);   /* truncation, not rounding      */
    CHECK(bar_filled(59.9, 20) == 11);
}

static void test_stacked_widths(void)
{
    int out[3];
    double vals[3] = { 12.5, 2.4, 0.5 };

    stacked_segment_widths(vals, 3, 20, out);
    CHECK(out[0] + out[1] + out[2] == 20); /* invariant: always sums      */
    CHECK(out[0] == 16 && out[1] == 3 && out[2] == 1);

    double zero[3] = { 0, 0, 0 };
    stacked_segment_widths(zero, 3, 20, out);
    CHECK(out[0] == 0 && out[1] == 0 && out[2] == 0);

    double neg[3] = { -5, 0, 3 };
    stacked_segment_widths(neg, 3, 10, out);
    CHECK(out[0] + out[1] + out[2] == 10);
    CHECK(out[0] == 0);

    /* ties on fractional parts resolve to the earlier segment */
    double tie[3] = { 1, 1, 1 };
    stacked_segment_widths(tie, 3, 5, out);
    CHECK(out[0] + out[1] + out[2] == 5);
    CHECK(out[0] == 2 && out[1] == 2 && out[2] == 1);

    double even[3] = { 1, 1, 1 };
    stacked_segment_widths(even, 3, 9, out);
    CHECK(out[0] == 3 && out[1] == 3 && out[2] == 3);

    stacked_segment_widths(vals, 3, 0, out);
    CHECK(out[0] == 0 && out[1] == 0 && out[2] == 0);
}

static void test_mem_segments(void)
{
    SysData s;
    memset(&s, 0, sizeof s);
    s.total_mem_gb = 15.4;
    s.used_mem_gb = 12.5;
    s.cache_mem_gb = 2.4;
    MemSegs m = mem_segments(&s);
    CHECK(fabs(m.free - 0.5) < 1e-9);
    CHECK(m.used + m.cache + m.free == m.total ||
          fabs((m.used + m.cache + m.free) - m.total) < 1e-9);

    s.used_mem_gb = 20.0; /* over-total used clamps to total */
    m = mem_segments(&s);
    CHECK(m.used == 15.4 && m.cache == 0.0 && m.free == 0.0);

    s.used_mem_gb = 12.5;
    s.cache_mem_gb = 99.0; /* cache clamps to the residual */
    m = mem_segments(&s);
    CHECK(fabs(m.cache - 2.9) < 1e-9 && m.free == 0.0);

    s.total_mem_gb = 0.0; /* no total ⇒ everything zero */
    m = mem_segments(&s);
    CHECK(m.used == 0 && m.cache == 0 && m.free == 0 && m.total == 0);
}

static void test_proc_command(void)
{
    GpuProc p;
    char cmd[256];

    memset(&p, 0, sizeof p);
    snprintf(p.cmdline, sizeof p.cmdline,
             "/usr/bin/llama-server   --host 127.0.0.1  --port 8080");
    proc_command(cmd, sizeof cmd, &p);
    CHECK(strcmp(cmd, "llama-server --host 127.0.0.1 --port 8080") == 0);

    snprintf(p.cmdline, sizeof p.cmdline, "python3 -m foo");
    proc_command(cmd, sizeof cmd, &p);
    CHECK(strcmp(cmd, "python3 -m foo") == 0);

    p.cmdline[0] = '\0';
    snprintf(p.process_name, sizeof p.process_name, "some-app,sibling");
    proc_command(cmd, sizeof cmd, &p);
    CHECK(strcmp(cmd, "some-app") == 0);

    p.cmdline[0] = '\0';
    p.process_name[0] = '\0';
    proc_command(cmd, sizeof cmd, &p);
    CHECK(strcmp(cmd, "unknown") == 0);
}

static void test_process_table(void)
{
    GpuProc p;
    char prefix[128];
    char hdr[128];

    CHECK(gpu_process_header_len() == 57); /* fixed columns, no "Command" */
    int hlen = gpu_process_header(hdr, sizeof hdr);
    CHECK(hlen == 64); /* 57 + strlen("Command") */
    CHECK(strcmp(hdr, "PID     USER     DEV TYPE   GPU  GPU MEM    CPU "
                      "HOST MEM Command") == 0);

    memset(&p, 0, sizeof p);
    p.pid = 54321;
    snprintf(p.user, sizeof p.user, "iforevan");
    p.gpu_mem_mb = 39506.0;
    p.host_mem_mb = 7768.0;
    p.cpu_pct = 12.0;
    snprintf(p.cmdline, sizeof p.cmdline, "llama-server --host 127.0.0.1");
    int plen = gpu_process_fixed_prefix(prefix, sizeof prefix, &p);
    CHECK(plen == 57);
    CHECK(strcmp(prefix, "54321   iforevan 0   C       --   39506M  12.0% "
                         "   7768M ") == 0);

    /* scroll clamp: view = bw-4, cmd area = view-57 clamped to >= 1 */
    snprintf(p.cmdline, sizeof p.cmdline,
             "llama-server --this-command-is-long-enough-to-need-scrolling-"
             "padding-abcdef-0123456789-abcdefghijklmnop");
    char cmd[512];
    proc_command(cmd, sizeof cmd, &p);
    int longlen = u8_len(cmd);
    GpuProc procs[1];
    memcpy(procs, &p, sizeof procs);
    int ms = max_process_scroll(120, procs, 1); /* view 116, area 59 */
    CHECK(ms == (longlen > 59 ? longlen - 59 : 0));
    ms = max_process_scroll(30, procs, 1); /* view 26 < header → area 1 */
    CHECK(ms == (longlen > 1 ? longlen - 1 : 0));
}

static void test_fmt_interval(void)
{
    char b[32];
    fmt_interval(b, sizeof b, 1.0);
    CHECK(strcmp(b, "1") == 0);
    fmt_interval(b, sizeof b, 2.0);
    CHECK(strcmp(b, "2") == 0);
    fmt_interval(b, sizeof b, 0.5);
    CHECK(strcmp(b, "0.5") == 0);
    fmt_interval(b, sizeof b, 1.5);
    CHECK(strcmp(b, "1.5") == 0);
    fmt_interval(b, sizeof b, 0.2);
    CHECK(strcmp(b, "0.2") == 0);
    fmt_interval(b, sizeof b, 60.0);
    CHECK(strcmp(b, "60") == 0);
}

static void test_next_refresh_interval(void)
{
    CHECK(next_refresh_interval(1.0) == 2.0);
    CHECK(next_refresh_interval(2.0) == 5.0);
    CHECK(next_refresh_interval(5.0) == 10.0);
    CHECK(next_refresh_interval(10.0) == 30.0);
    CHECK(next_refresh_interval(30.0) == 60.0);
    CHECK(next_refresh_interval(60.0) == 0.5);  /* wraps to the floor */
    CHECK(next_refresh_interval(0.5) == 1.0);
    CHECK(next_refresh_interval(0.2) == 0.5);   /* below ladder */
    CHECK(next_refresh_interval(2.5) == 5.0);   /* custom value */
    CHECK(next_refresh_interval(59.9) == 60.0);
}

int main(void)
{
    test_utf8_helpers();
    test_bar_width();
    test_bar_filled();
    test_stacked_widths();
    test_mem_segments();
    test_proc_command();
    test_process_table();
    test_fmt_interval();
    test_next_refresh_interval();
    if (failures == 0) {
        printf("layout unit tests: all passed\n");
        return 0;
    }
    printf("layout unit tests: %d FAILED\n", failures);
    return 1;
}
