#ifndef TERMMON_H
#define TERMMON_H

#include <ncurses.h>
#include <pthread.h>
#include <time.h>

#define TERMMON_VERSION "1.22.0"

#define BAR_WIDTH 20
#define MIN_BAR_WIDTH 5
#define MAX_BOX_WIDTH 120
#define MIN_BOX_WIDTH 24
#define REFRESH_INTERVAL_DEFAULT 1.0
#define REFRESH_INTERVAL_MIN 0.5
#define REFRESH_INTERVAL_MAX 60.0

extern double g_refresh_interval;

#define MAX_GPU_PROCS 5
#define MAX_CORES 256
#define MAX_GPUS 8

#define COLOR_TITLE 1
#define COLOR_MEMORY 2
#define COLOR_SWAP 3
#define COLOR_CPU 4
#define COLOR_VRAM 5
#define COLOR_POPUP 6
#define COLOR_MEM_CACHE 7
#define COLOR_MEM_FREE 8

typedef struct {
    int valid;
    double total_mem_gb;
    double used_mem_gb;
    double cache_mem_gb;
    double free_mem_gb;
    double avail_mem_gb;
    double mem_percent;
    double swap_total_mb;
    double swap_used_mb;
    double swap_percent;
    double cpu_usage;
    double cpu_temp;
    int has_temp;
    int core_count;
    double per_core[MAX_CORES];
} SysData;

typedef struct {
    char idx[8];
    char name[80];
    double mem_total;
    double mem_used;
    double mem_free;
    double gpu_util;
    double temp;
    double power;
} Gpu;

typedef struct {
    int pid;
    double gpu_mem_mb;
    double host_mem_mb;
    double cpu_pct;
    char user[64];
    char process_name[128];
    char cmdline[512];
} GpuProc;

typedef struct {
    pthread_mutex_t lock;
    pthread_cond_t cond;
    pthread_t tid;
    int thread_started;
    int pending;
    int running;
    SysData sys;
    Gpu gpus[MAX_GPUS];
    int n_gpus;
    GpuProc procs[MAX_GPU_PROCS];
    int n_procs;
} Stats;

typedef struct {
    Stats stats;
    int box_width;
    int process_scroll_x;
} App;

double now_mono(void);

int true_terminal_size(int *rows, int *cols);

void stats_init(Stats *st);
int stats_start(Stats *st);
void stats_request(Stats *st);
void stats_stop(Stats *st);
int stats_load_fixture(Stats *st, const char *path);

void collect_sys(SysData *s);
void collect_gpus(Gpu *gpus, int *n_gpus, GpuProc *procs, int *n_procs);

int gpu_backend_nvidia(void);

/* pure layout helpers (layout.c) */
int u8_len(const char *s);
void u8_clip(char *dst, size_t cap, const char *src, int maxcp);
void u8_slice(char *dst, size_t cap, const char *src, int skip, int count);
const char *u8_skip(const char *s, int n);
void u8_fit(char *dst, size_t cap, const char *src, int n);
void u8_center(char *dst, size_t cap, const char *src, int width);
int bar_width(int bw, int two_col, int overhead);
int bar_filled(double percent, int width);
void stacked_segment_widths(const double *values, int n, int width, int *out);
typedef struct {
    double used;
    double cache;
    double free;
    double total;
} MemSegs;
MemSegs mem_segments(const SysData *s);
int proc_command(char *dst, size_t cap, const GpuProc *p);
int gpu_process_fixed_prefix(char *dst, size_t cap, const GpuProc *p);
int gpu_process_header(char *dst, size_t cap);
int gpu_process_header_len(void);
void fmt_interval(char *dst, size_t cap, double v);
double next_refresh_interval(double cur);
int max_process_scroll(int bw, const GpuProc *procs, int n_procs);

void draw(App *app, WINDOW *scr);
void show_help(App *app, WINDOW *scr);

#endif
