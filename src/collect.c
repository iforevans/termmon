#if defined(__APPLE__)
#define _DARWIN_C_SOURCE 1
#endif

#include "termmon.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#if defined(__APPLE__)
#include <libproc.h>
#include <mach/mach.h>
#include <mach/machine.h>
#include <mach/mach_time.h>
#include <sys/sysctl.h>

static void mac_cpu_pct_fill(SysData *s);
static void mac_mem_swap(SysData *s);
static void mac_collect_gpus(Gpu *gpus, int *n_gpus, GpuProc *procs,
                             int *n_procs);
static int mac_apple_gpu(void);
#endif

#if !defined(__APPLE__)

static char *trim(char *s)
{
    while (*s == ' ' || *s == '\t')
        s++;
    size_t len = strlen(s);
    while (len > 0 && (s[len - 1] == ' ' || s[len - 1] == '\t' ||
                       s[len - 1] == '\r' || s[len - 1] == '\n'))
        s[--len] = 0;
    return s;
}

static ssize_t read_whole(const char *path, char *buf, size_t cap)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return -1;
    size_t len = 0;
    while (len + 1 < cap) {
        ssize_t n = read(fd, buf + len, cap - 1 - len);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            close(fd);
            return -1;
        }
        if (n == 0)
            break;
        len += (size_t)n;
    }
    close(fd);
    buf[len] = 0;
    return (ssize_t)len;
}

static int parse_num(const char *s, double *out)
{
    char *end;
    double v = strtod(s, &end);
    while (*end == ' ' || *end == '\t')
        end++;
    if (end == s || *end != 0)
        return 0;
    *out = v;
    return 1;
}

/* ---------------- CPU via /proc/stat deltas ---------------- */

typedef struct {
    unsigned long long total;
    unsigned long long idle;
    int have;
} CpuPrev;

static CpuPrev g_cpu_prev[MAX_CORES + 1];

static void cpu_pct_fill(SysData *s)
{
    static char buf[65536];
    if (read_whole("/proc/stat", buf, sizeof buf) <= 0)
        return;

    int core = 0;
    double sum = 0.0;
    char *save = NULL;
    for (char *line = strtok_r(buf, "\n", &save); line != NULL;
         line = strtok_r(NULL, "\n", &save)) {
        if (strncmp(line, "cpu", 3) != 0 || line[3] == ' ')
            continue;
        if (line[3] < '0' || line[3] > '9')
            continue;
        int id = atoi(line + 3);
        if (id < 0 || id >= MAX_CORES)
            continue;

        char *p = strchr(line, ' ');
        if (p == NULL)
            continue;
        unsigned long long v[10];
        int nv = 0;
        char *end = p;
        while (nv < 10) {
            while (*end == ' ')
                end++;
            if (*end < '0' || *end > '9')
                break;
            v[nv] = strtoull(end, &end, 10);
            nv++;
        }
        if (nv < 4)
            continue;

        unsigned long long idle = v[3] + (nv > 4 ? v[4] : 0);
        unsigned long long total = 0;
        for (int i = 0; i < nv; i++)
            total += v[i];

        double pct = 0.0;
        CpuPrev *pr = &g_cpu_prev[id];
        if (pr->have && total >= pr->total) {
            unsigned long long dt = total - pr->total;
            unsigned long long di = idle - pr->idle;
            if (dt > 0) {
                pct = 100.0 * (double)(dt - di) / (double)dt;
                if (pct < 0.0)
                    pct = 0.0;
                if (pct > 100.0)
                    pct = 100.0;
            }
        }
        pr->total = total;
        pr->idle = idle;
        pr->have = 1;

        s->per_core[core] = pct;
        sum += pct;
        core++;
        if (core >= MAX_CORES)
            break;
    }
    s->core_count = core;
    s->cpu_usage = core > 0 ? sum / (double)core : 0.0;
}

/* ---------------- memory + swap via /proc/meminfo ---------------- */

static long long meminfo_field(const char *text, const char *name)
{
    size_t nlen = strlen(name);
    const char *p = text;
    while (p != NULL && *p != 0) {
        if (strncmp(p, name, nlen) == 0 && p[nlen] == ':')
            return strtoll(p + nlen + 1, NULL, 10);
        p = strchr(p, '\n');
        if (p != NULL)
            p++;
    }
    return -1;
}

static long long meminfo_get(const char *text, const char *name)
{
    long long v = meminfo_field(text, name);
    return v < 0 ? 0 : v;
}

static void collect_mem_swap(SysData *s)
{
    static char buf[16384];
    if (read_whole("/proc/meminfo", buf, sizeof buf) <= 0)
        return;
    if (meminfo_field(buf, "MemTotal") < 0)
        return;

    long long total = meminfo_field(buf, "MemTotal");
    long long free_ = meminfo_get(buf, "MemFree");
    long long cache = meminfo_get(buf, "Buffers") +
                      meminfo_get(buf, "Cached") +
                      meminfo_get(buf, "SReclaimable");
    long long avail = meminfo_field(buf, "MemAvailable");
    if (avail < 0)
        avail = free_ + cache;

    if (free_ < 0)
        free_ = 0;
    if (free_ > total)
        free_ = total;
    if (cache < 0)
        cache = 0;
    if (cache > total - free_)
        cache = total - free_;
    long long used = total - free_ - cache;
    if (avail < 0)
        avail = 0;
    if (avail > total)
        avail = total;

    const double kib_per_gib = 1024.0 * 1024.0;
    s->total_mem_gb = (double)total / kib_per_gib;
    s->used_mem_gb = (double)used / kib_per_gib;
    s->cache_mem_gb = (double)cache / kib_per_gib;
    s->free_mem_gb = (double)free_ / kib_per_gib;
    s->avail_mem_gb = (double)avail / kib_per_gib;
    s->mem_percent = total > 0 ? (double)used / (double)total * 100.0 : 0.0;

    long long st = meminfo_get(buf, "SwapTotal");
    long long sf = meminfo_get(buf, "SwapFree");
    s->swap_total_mb = (double)st / 1024.0;
    s->swap_used_mb = (double)(st - sf) / 1024.0;
    s->swap_percent = st > 0 ? (double)(st - sf) / (double)st * 100.0 : 0.0;
}

/* ---------------- CPU temperature via /sys/class/hwmon ---------------- */

static double hwmon_read_max(const char *dir)
{
    DIR *d = opendir(dir);
    if (d == NULL)
        return -1.0;
    double maxv = -1.0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        const char *nm = e->d_name;
        if (strncmp(nm, "temp", 4) != 0)
            continue;
        const char *suf = strstr(nm, "_input");
        if (suf == NULL || suf[6] != 0)
            continue;
        char path[512];
        snprintf(path, sizeof path, "%s/%s", dir, nm);
        char val[32];
        if (read_whole(path, val, sizeof val) <= 0)
            continue;
        long milli = strtol(val, NULL, 10);
        if (milli > 0) {
            double v = (double)milli / 1000.0;
            if (v > maxv)
                maxv = v;
        }
    }
    closedir(d);
    return maxv;
}

static int hwmon_name(const char *dir, char *name, size_t cap)
{
    char path[512];
    snprintf(path, sizeof path, "%s/name", dir);
    char raw[128];
    if (read_whole(path, raw, sizeof raw) <= 0)
        return -1;
    char *t = trim(raw);
    snprintf(name, cap, "%s", t);
    return 0;
}

static double collect_cpu_temp(void)
{
    static const char *pref[] = { "coretemp", "cpu_thermal", "k10temp",
                                  "zenpower" };
    static const int npref = (int)(sizeof pref / sizeof pref[0]);
    char dirs[32][320];
    char names[32][32];
    int n = 0;

    DIR *d = opendir("/sys/class/hwmon");
    if (d == NULL)
        return -1.0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL && n < 32) {
        if (strncmp(e->d_name, "hwmon", 5) != 0)
            continue;
        char dir[320];
        snprintf(dir, sizeof dir, "/sys/class/hwmon/%s", e->d_name);
        char name[32];
        if (hwmon_name(dir, name, sizeof name) == 0) {
            snprintf(dirs[n], sizeof dirs[0], "%s", dir);
            snprintf(names[n], sizeof names[0], "%s", name);
            n++;
        }
    }
    closedir(d);

    for (int g = 0; g < npref; g++) {
        for (int i = 0; i < n; i++) {
            if (strcmp(names[i], pref[g]) != 0)
                continue;
            double v = hwmon_read_max(dirs[i]);
            if (v >= 0.0)
                return v;
        }
    }
    for (int i = 0; i < n; i++) {
        double v = hwmon_read_max(dirs[i]);
        if (v >= 0.0)
            return v;
    }
    return -1.0;
}

#endif /* !__APPLE__ */

void collect_sys(SysData *s)
{
#if defined(__APPLE__)
    mac_cpu_pct_fill(s);
    mac_mem_swap(s);
    s->has_temp = 0;
    s->cpu_temp = 0.0;
#else
    cpu_pct_fill(s);
    collect_mem_swap(s);
    double t = collect_cpu_temp();
    s->has_temp = t >= 0.0;
    s->cpu_temp = t >= 0.0 ? t : 0.0;
#endif
}

/* ---------------- external commands ---------------- */

static int run_cmd_capture(char *const argv[], char *buf, size_t cap,
                           int timeout_ms)
{
    int p[2];
    if (pipe(p) != 0)
        return -1;
    fcntl(p[0], F_SETFL, O_NONBLOCK);

    pid_t pid = fork();
    if (pid < 0) {
        close(p[0]);
        close(p[1]);
        return -1;
    }
    if (pid == 0) {
        close(p[0]);
        dup2(p[1], STDOUT_FILENO);
        close(p[1]);
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            dup2(devnull, STDERR_FILENO);
            if (devnull > 2)
                close(devnull);
        }
        execvp(argv[0], argv);
        _exit(127);
    }
    close(p[1]);

    size_t len = 0;
    int timed_out = 0;
    double t_end = now_mono() + (double)timeout_ms / 1000.0;
    for (;;) {
        double rem = (t_end - now_mono()) * 1000.0;
        if (rem <= 0.0) {
            timed_out = 1;
            break;
        }
        struct pollfd pfd = { p[0], POLLIN, 0 };
        int pr = poll(&pfd, 1, (int)rem);
        if (pr < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (pr == 0) {
            timed_out = 1;
            break;
        }
        for (;;) {
            ssize_t n = read(p[0], buf + len, cap - 1 - len);
            if (n > 0) {
                len += (size_t)n;
                if (len + 1 >= cap)
                    goto read_done;
                continue;
            }
            if (n == 0)
                goto read_done;
            if (errno == EAGAIN || errno == EWOULDBLOCK)
                break;
            goto read_done;
        }
    }
read_done:
    close(p[0]);
    if (timed_out)
        kill(pid, SIGKILL);
    int status;
    if (waitpid(pid, &status, 0) < 0)
        return -1;
    buf[len] = 0;
    if (timed_out || !WIFEXITED(status) || WEXITSTATUS(status) != 0)
        return -1;
    if (len == 0)
        return -1;
    return (int)len;
}

/* ---------------- macOS (Darwin) collection ---------------- */

#if defined(__APPLE__)

typedef struct {
    unsigned long long total;
    unsigned long long idle;
    int have;
} MacCpuPrev;

static MacCpuPrev g_mac_cpu_prev[MAX_CORES];

static void mac_cpu_pct_fill(SysData *s)
{
    natural_t num_cpus = 0;
    processor_info_array_t cpu_info = NULL;
    mach_msg_type_number_t count = 0;
    kern_return_t kr = host_processor_info(mach_host_self(),
                                           PROCESSOR_CPU_LOAD_INFO,
                                           &num_cpus, &cpu_info, &count);
    if (kr != KERN_SUCCESS || num_cpus == 0)
        return;
    int n = (int)num_cpus;
    if (n > MAX_CORES)
        n = MAX_CORES;

    double sum = 0.0;
    for (int i = 0; i < n; i++) {
        const natural_t *ticks =
            (const natural_t *)&cpu_info[i * CPU_STATE_MAX];
        unsigned long long idle = ticks[CPU_STATE_IDLE];
        unsigned long long total = 0;
        for (int j = 0; j < CPU_STATE_MAX; j++)
            total += ticks[j];

        double pct = 0.0;
        MacCpuPrev *pr = &g_mac_cpu_prev[i];
        if (pr->have && total >= pr->total) {
            unsigned long long dt = total - pr->total;
            unsigned long long di = idle - pr->idle;
            if (dt > 0) {
                pct = 100.0 * (double)(dt - di) / (double)dt;
                if (pct < 0.0)
                    pct = 0.0;
                if (pct > 100.0)
                    pct = 100.0;
            }
        }
        pr->total = total;
        pr->idle = idle;
        pr->have = 1;

        s->per_core[i] = pct;
        sum += pct;
    }
    s->core_count = n;
    s->cpu_usage = sum / (double)n;
    vm_deallocate(mach_task_self(), (vm_address_t)cpu_info,
                  count * (mach_msg_type_number_t)sizeof(integer_t));
}

static void mac_mem_swap(SysData *s)
{
    long long total = 0;
    size_t len = sizeof total;
    if (sysctlbyname("hw.memsize", &total, &len, NULL, 0) != 0 || total <= 0)
        return;

    mach_msg_type_number_t cnt = HOST_VM_INFO64_COUNT;
    vm_statistics64_data_t vm;
    memset(&vm, 0, sizeof vm);
    if (host_statistics64(mach_host_self(), HOST_VM_INFO64,
                          (host_info64_t)&vm, &cnt) != KERN_SUCCESS)
        return;

    /* Mirror the Python reference (psutil on macOS): used = active + wired,
     * free = free_count, cache absorbs the remainder so that
     * Used + Cache + Free == Total exactly. */
    const long long page = (long long)getpagesize();
    long long free_ = (long long)vm.free_count * page;
    long long used = (long long)vm.active_count * page +
                     (long long)vm.wire_count * page;
    long long inactive = (long long)vm.inactive_count * page;
    long long speculative = (long long)vm.speculative_count * page;

    if (used < 0)
        used = 0;
    if (used > total)
        used = total;
    if (free_ < 0)
        free_ = 0;
    if (free_ > total - used)
        free_ = total - used;
    long long cache = total - used - free_;
    if (cache < 0)
        cache = 0;
    long long avail = inactive + free_ + speculative;
    if (avail < 0)
        avail = 0;
    if (avail > total)
        avail = total;

    const double bytes_per_gib = 1024.0 * 1024.0 * 1024.0;
    s->total_mem_gb = (double)total / bytes_per_gib;
    s->used_mem_gb = (double)used / bytes_per_gib;
    s->cache_mem_gb = (double)cache / bytes_per_gib;
    s->free_mem_gb = (double)free_ / bytes_per_gib;
    s->avail_mem_gb = (double)avail / bytes_per_gib;
    s->mem_percent = total > 0 ? (double)used / (double)total * 100.0 : 0.0;

    struct xsw_usage xs;
    size_t xslen = sizeof xs;
    memset(&xs, 0, xslen);
    if (sysctlbyname("vm.swapusage", &xs, &xslen, NULL, 0) == 0 &&
        xs.xsu_total > 0) {
        s->swap_total_mb = (double)xs.xsu_total / 1048576.0;
        s->swap_used_mb = (double)xs.xsu_used / 1048576.0;
        s->swap_percent = (double)xs.xsu_used / (double)xs.xsu_total * 100.0;
    }
}

static int mac_apple_gpu(void)
{
    static int checked = 0;
    static int detected = 0;
    if (!checked) {
        checked = 1;
        char model[128];
        size_t len = sizeof model;
        if (sysctlbyname("hw.gpu.model", model, &len, NULL, 0) == 0 &&
            model[0]) {
            detected = 1;
        } else {
            uint64_t arm = 0;
            len = sizeof arm;
            if (sysctlbyname("hw.optional.arm64", &arm, &len, NULL, 0) == 0 &&
                arm) {
                detected = 1;
            }
        }
    }
    return detected;
}

/* Minimal JSON value extractors for the flat, single-embedded JSON emitted
 * by macmon/system_profiler. Never accepts a key prefix inside a string. */
static const char *json_find(const char *text, const char *key)
{
    char pat[96];
    snprintf(pat, sizeof pat, "\"%s\"", key);
    const char *p = strstr(text, pat);
    if (p == NULL)
        return NULL;
    p += strlen(pat);
    while (*p == ' ' || *p == '\t')
        p++;
    if (*p != ':')
        return NULL;
    p++;
    while (*p == ' ' || *p == '\t')
        p++;
    return p;
}

static double json_num(const char *text, const char *key, double dflt)
{
    const char *p = json_find(text, key);
    if (p == NULL)
        return dflt;
    char *end;
    double v = strtod(p, &end);
    if (end == p)
        return dflt;
    return v;
}

static int json_int(const char *text, const char *key, int dflt)
{
    const char *p = json_find(text, key);
    if (p == NULL)
        return dflt;
    if (*p == '"')
        p++;
    char *end;
    long v = strtol(p, &end, 10);
    if (end == p)
        return dflt;
    return (int)v;
}

static int json_str(const char *text, const char *key, char *dst, size_t cap)
{
    const char *p = json_find(text, key);
    if (p == NULL)
        return 0;
    if (*p == '"') {
        p++;
        size_t i = 0;
        while (*p != '\0' && *p != '"' && i + 1 < cap) {
            if (*p == '\\' && p[1] != '\0')
                p++; /* skip the escape for one char */
            dst[i++] = *p++;
        }
        dst[i] = '\0';
        return i > 0;
    }
    snprintf(dst, cap, "%s", p);
    return 1;
}

static int json_array2(const char *text, const char *key, double *a, double *b)
{
    const char *p = json_find(text, key);
    if (p == NULL || *p != '[')
        return 0;
    p++;
    char *end;
    double va = strtod(p, &end);
    if (end == p)
        return 0;
    p = end;
    while (*p == ' ' || *p == '\t' || *p == ',')
        p++;
    double vb = strtod(p, &end);
    if (end == p)
        return 0;
    *a = va;
    *b = vb;
    return 1;
}

static void mac_gpu_metadata(char *name, size_t cap, int *cores)
{
    static int done = 0;
    static char sname[80] = "Apple GPU";
    static int scores = 0;
    if (!done) {
        done = 1;
        char buf[256];
        size_t len = sizeof buf;
        /* Fast sysctl path (present on newer macOS); the model name that
         * hw.gpu.model would report is the marketing chip name. */
        if (sysctlbyname("hw.gpu.model", buf, &len, NULL, 0) == 0 &&
            buf[0]) {
            snprintf(sname, sizeof sname, "%s", buf);
        } else {
            /* One-shot system_profiler JSON: SPDisplaysDataType[0] holds
             * spdisplays_chipset (name) and sppci_cores (core count). */
            char out[32768];
            char *argv[] = { "/usr/sbin/system_profiler",
                             "SPDisplaysDataType", "-json", NULL };
            if (run_cmd_capture(argv, out, sizeof out, 8000) > 0) {
                char chip[80];
                if (json_str(out, "spdisplays_chipset", chip, sizeof chip) &&
                    chip[0]) {
                    snprintf(sname, sizeof sname, "%s", chip);
                }
                scores = json_int(out, "sppci_cores", 0);
            }
        }
    }
    snprintf(name, cap, "%s", sname);
    *cores = scores;
}

static int mac_gpu_macmon(double *util, double *power, double *temp)
{
    char out[65536];
    char *argv[] = { "macmon", "pipe", "-s", "1", NULL };
    if (run_cmd_capture(argv, out, sizeof out, 5000) <= 0)
        return -1;
    double freq, usage; /* usage is 0.0..1.0 (fraction of max) */
    *util = 0.0;
    *power = json_num(out, "gpu_power", 0.0);
    *temp = json_num(out, "gpu_temp_avg", 0.0);
    if (json_array2(out, "gpu_usage", &freq, &usage))
        *util = usage * 100.0;
    return 0;
}

static int mac_gpu_powermetrics(double *util, double *power)
{
    char out[32768];
    char *argv[] = { "/usr/bin/powermetrics", "--samplers", "gpu_power",
                     "-n", "1", "-i", "1000", NULL };
    if (run_cmd_capture(argv, out, sizeof out, 5000) <= 0)
        return -1;
    *util = 0.0;
    *power = 0.0;
    char *save = NULL;
    for (char *line = strtok_r(out, "\n", &save); line != NULL;
         line = strtok_r(NULL, "\n", &save)) {
        const char *colon;
        if ((colon = strstr(line, "GPU active percentage")) != NULL ||
            (colon = strstr(line, "GPU active residency")) != NULL) {
            colon = strchr(colon, ':');
            if (colon != NULL)
                *util = strtod(colon + 1, NULL);
        } else if ((colon = strstr(line, "GPU power")) != NULL) {
            colon = strchr(colon, ':');
            if (colon != NULL) {
                *power = strtod(colon + 1, NULL);
                if (strstr(colon, "mW")) /* powermetrics reports mW */
                    *power /= 1000.0;
            }
        }
    }
    return 0;
}

static void mac_gpu_util_power(double *util, double *power, double *temp)
{
    if (mac_gpu_macmon(util, power, temp) == 0)
        return;
    if (mac_gpu_powermetrics(util, power) == 0)
        return;
    *util = 0.0;
    *power = 0.0;
    *temp = 0.0;
}

/* Per-process CPU% across samples. pti_total_user/system are mach absolute
 * ticks; convert to seconds with mach_timebase_info like the rest of Darwin. */
typedef struct {
    int pid;
    unsigned long long cpu;
    double wall;
    int used;
} MacProcPrev;

static MacProcPrev g_mac_proc_prev[64];

static void mac_proc_prev_compact(int *alive, int n_alive)
{
    for (size_t i = 0; i < sizeof g_mac_proc_prev / sizeof g_mac_proc_prev[0];
         i++) {
        if (!g_mac_proc_prev[i].used)
            continue;
        int found = 0;
        for (int j = 0; j < n_alive; j++) {
            if (alive[j] == g_mac_proc_prev[i].pid) {
                found = 1;
                break;
            }
        }
        if (!found)
            g_mac_proc_prev[i].used = 0;
    }
}

static double mac_proc_cpu_percent(int pid, double now)
{
    struct proc_taskinfo pti;
    if (proc_pidinfo(pid, PROC_PIDTASKINFO, 0, &pti, (int)sizeof pti) !=
        (int)sizeof pti)
        return 0.0;
    unsigned long long cpu = pti.pti_total_user + pti.pti_total_system;

    MacProcPrev *slot = NULL;
    for (size_t i = 0; i < sizeof g_mac_proc_prev / sizeof g_mac_proc_prev[0];
         i++) {
        if (g_mac_proc_prev[i].used && g_mac_proc_prev[i].pid == pid) {
            slot = &g_mac_proc_prev[i];
            break;
        }
        if (!g_mac_proc_prev[i].used && slot == NULL)
            slot = &g_mac_proc_prev[i];
    }
    if (slot == NULL)
        slot = &g_mac_proc_prev[0];
    static mach_timebase_info_data_t s_tb;
    if (s_tb.denom == 0)
        mach_timebase_info(&s_tb);

    double pct = 0.0;
    if (slot->used) {
        double dt = now - slot->wall;
        unsigned long long dc = cpu >= slot->cpu ? cpu - slot->cpu : 0;
        if (dt > 0.0) {
            double secs = (double)dc * (double)s_tb.numer /
                          (double)s_tb.denom / 1e9;
            pct = secs / dt * 100.0;
        }
    }
    slot->used = 1;
    slot->pid = pid;
    slot->cpu = cpu;
    slot->wall = now;
    return pct;
}

static void mac_proc_args(int pid, char *out, size_t cap)
{
    out[0] = '\0';
    int argmax = 0;
    size_t sz = sizeof argmax;
    int mib[3] = { CTL_KERN, KERN_ARGMAX };
    if (sysctl(mib, 2, &argmax, &sz, NULL, 0) != 0 || argmax <= 0)
        return;
    char *procargs = malloc((size_t)argmax);
    if (procargs == NULL)
        return;
    mib[0] = CTL_KERN;
    mib[1] = KERN_PROCARGS2;
    mib[2] = pid;
    size_t size = (size_t)argmax;
    if (sysctl(mib, 3, procargs, &size, NULL, 0) == 0 && size > 0) {
        char *end = procargs + size;
        char *sp = procargs;
        while (sp < end && *sp != '\0')
            sp++;
        while (sp < end && *sp == '\0')
            sp++;
        if ((size_t)(end - sp) > sizeof(int)) {
            int nargs;
            memcpy(&nargs, sp, sizeof nargs);
            sp += sizeof(int);
            size_t pos = 0;
            int first = 1;
            while (nargs-- > 0 && sp < end && pos + 1 < cap) {
                if (*sp != '\0') {
                    int w = snprintf(out + pos, cap - pos, "%s%s",
                                     first ? "" : " ", sp);
                    if (w < 0 || (size_t)w >= cap - pos)
                        break;
                    pos += (size_t)w;
                    first = 0;
                }
                while (sp < end && *sp != '\0')
                    sp++;
                if (sp < end)
                    sp++;
            }
        }
    }
    free(procargs);
}

static void mac_proc_extra(GpuProc *gp)
{
    struct proc_taskallinfo t;
    if (proc_pidinfo(gp->pid, PROC_PIDTASKALLINFO, 0, &t, (int)sizeof t) ==
        (int)sizeof t) {
        struct passwd *pw = getpwuid(t.pbsd.pbi_uid);
        if (pw != NULL && pw->pw_name != NULL)
            snprintf(gp->user, sizeof gp->user, "%s", pw->pw_name);
        if (t.pbsd.pbi_comm[0] != '\0')
            snprintf(gp->process_name, sizeof gp->process_name, "%s",
                     t.pbsd.pbi_comm);
    } else {
        char nm[128];
        if (proc_name(gp->pid, nm, sizeof nm) > 0 && nm[0])
            snprintf(gp->process_name, sizeof gp->process_name, "%s", nm);
    }
    char argv[512];
    mac_proc_args(gp->pid, argv, sizeof argv);
    if (argv[0] != '\0')
        snprintf(gp->cmdline, sizeof gp->cmdline, "%s", argv);
    else if (gp->process_name[0] != '\0')
        snprintf(gp->cmdline, sizeof gp->cmdline, "%s", gp->process_name);
}

static void mac_gpu_procs(GpuProc *procs, int *n_procs)
{
    *n_procs = 0;
    int all = proc_listallpids(NULL, 0);
    if (all <= 0)
        return;
    int cap = all < 512 ? all : 512;
    pid_t pids[512];
    int n = proc_listallpids(pids, (int)(cap * sizeof(pid_t)));
    if (n <= 0)
        return;
    if (n > cap)
        n = cap;

    typedef struct {
        int pid;
        double rss;
    } Cand;
    Cand cand[512];
    int nc = 0;
    double now = now_mono();
    for (int i = 0; i < n; i++) {
        struct proc_taskinfo pti;
        if (proc_pidinfo(pids[i], PROC_PIDTASKINFO, 0, &pti,
                         (int)sizeof pti) != (int)sizeof pti)
            continue;
        cand[nc].pid = (int)pids[i];
        cand[nc].rss = (double)pti.pti_resident_size / 1048576.0;
        nc++;
    }

    /* Top-N by host memory as a proxy for GPU activity (UMA: no separate
     * per-process VRAM query without privileges). Mirrors the Python
     * reference's macOS process pass. */
    for (int i = 1; i < nc; i++) {
        Cand key = cand[i];
        int j = i - 1;
        while (j >= 0 && cand[j].rss < key.rss) {
            cand[j + 1] = cand[j];
            j--;
        }
        cand[j + 1] = key;
    }

    int keep = nc < MAX_GPU_PROCS ? nc : MAX_GPU_PROCS;
    int alive[MAX_GPU_PROCS];
    for (int i = 0; i < keep; i++) {
        GpuProc *gp = &procs[i];
        memset(gp, 0, sizeof *gp);
        gp->pid = cand[i].pid;
        gp->host_mem_mb = cand[i].rss;
        gp->cpu_pct = mac_proc_cpu_percent(gp->pid, now);
        mac_proc_extra(gp);
        alive[i] = gp->pid;
    }
    *n_procs = keep;
    mac_proc_prev_compact(alive, keep);
}

static void mac_collect_gpus(Gpu *gpus, int *n_gpus, GpuProc *procs,
                             int *n_procs)
{
    *n_gpus = 0;
    *n_procs = 0;
    if (!mac_apple_gpu())
        return;

    Gpu g;
    memset(&g, 0, sizeof g);
    snprintf(g.idx, sizeof g.idx, "0");
    int cores = 0;
    mac_gpu_metadata(g.name, sizeof g.name, &cores);
    g.is_uma = 1;
    g.gpu_cores = cores;
    mac_gpu_util_power(&g.gpu_util, &g.power, &g.temp);
    gpus[0] = g;
    *n_gpus = 1;

    mac_gpu_procs(procs, n_procs);
}

#endif /* __APPLE__ */

#if !defined(__APPLE__)

/* ---------------- nvidia-smi ---------------- */

static int nvidia_available(void)
{
    return access("/dev/nvidia0", F_OK) == 0 ||
           access("/proc/driver/nvidia", F_OK) == 0;
}

static size_t split_trim(char *line, char **f, size_t max)
{
    size_t n = 0;
    char *p = line;
    for (;;) {
        char *c = strchr(p, ',');
        if (n < max)
            f[n++] = p;
        if (c == NULL)
            break;
        *c = 0;
        p = c + 1;
    }
    for (size_t i = 0; i < n; i++)
        f[i] = trim(f[i]);
    return n;
}

static void join_fields(char *dst, size_t cap, char **f, size_t from,
                        size_t to)
{
    size_t pos = 0;
    dst[0] = 0;
    for (size_t i = from; i < to && i < from + 64; i++) {
        int w = snprintf(dst + pos, cap - pos, "%s%s", i > from ? "," : "",
                         f[i]);
        if (w < 0 || (size_t)w >= cap - pos)
            break;
        pos += (size_t)w;
    }
}

static void parse_gpu_stats(char *text, Gpu *gpus, int *count)
{
    int n = 0;
    char *save = NULL;
    for (char *line = strtok_r(text, "\n", &save); line != NULL;
         line = strtok_r(NULL, "\n", &save)) {
        if (*line == 0)
            continue;
        char *f[64];
        size_t nf = split_trim(line, f, 64);
        if (nf < 8)
            continue;
        double nums[6];
        int ok = 1;
        for (int i = 0; i < 6; i++) {
            if (!parse_num(f[nf - 6 + i], &nums[i])) {
                ok = 0;
                break;
            }
        }
        if (!ok)
            continue;
        Gpu *g = &gpus[n];
        memset(g, 0, sizeof *g);
        snprintf(g->idx, sizeof g->idx, "%s", f[0]);
        join_fields(g->name, sizeof g->name, f, 1, nf - 6);
        g->mem_total = nums[0];
        g->mem_used = nums[1];
        g->mem_free = nums[2];
        g->gpu_util = nums[3];
        g->temp = nums[4];
        g->power = nums[5];
        n++;
        if (n >= MAX_GPUS)
            break;
    }
    *count = n;
}

/* ---------------- GPU process enrichment ---------------- */

typedef struct {
    int pid;
    unsigned long long cpu;
    double wall;
    int used;
} ProcPrev;

static ProcPrev g_proc_prev[64];

static unsigned long long proc_cpu_jiffies(int pid)
{
    char path[64];
    snprintf(path, sizeof path, "/proc/%d/stat", pid);
    static char buf[4096];
    if (read_whole(path, buf, sizeof buf) <= 0)
        return 0;
    char *close_paren = strrchr(buf, ')');
    if (close_paren == NULL)
        return 0;
    char *p = close_paren + 1;
    unsigned long long v[13];
    int nv = 0;
    while (nv < 13) {
        while (*p == ' ')
            p++;
        if (*p < '0' || *p > '9')
            break;
        v[nv++] = strtoull(p, &p, 10);
    }
    if (nv < 13)
        return 0;
    return v[11] + v[12];
}

static double proc_cpu_percent(int pid, double now)
{
    unsigned long long cpu = proc_cpu_jiffies(pid);
    long hz = sysconf(_SC_CLK_TCK);
    if (hz <= 0)
        hz = 100;

    ProcPrev *slot = NULL;
    for (size_t i = 0; i < sizeof g_proc_prev / sizeof g_proc_prev[0]; i++) {
        if (g_proc_prev[i].used && g_proc_prev[i].pid == pid) {
            slot = &g_proc_prev[i];
            break;
        }
        if (!g_proc_prev[i].used && slot == NULL)
            slot = &g_proc_prev[i];
    }
    if (slot == NULL) {
        slot = &g_proc_prev[0];
    }
    double pct = 0.0;
    if (slot->used) {
        double dt = now - slot->wall;
        unsigned long long dc = cpu >= slot->cpu ? cpu - slot->cpu : 0;
        if (dt > 0.0)
            pct = (double)dc / (double)hz / dt * 100.0;
    }
    slot->used = 1;
    slot->pid = pid;
    slot->cpu = cpu;
    slot->wall = now;
    return pct;
}

static void proc_prev_compact(int *alive, int n_alive)
{
    for (size_t i = 0; i < sizeof g_proc_prev / sizeof g_proc_prev[0]; i++) {
        if (!g_proc_prev[i].used)
            continue;
        int found = 0;
        for (int j = 0; j < n_alive; j++) {
            if (alive[j] == g_proc_prev[i].pid) {
                found = 1;
                break;
            }
        }
        if (!found)
            g_proc_prev[i].used = 0;
    }
}

static void enrich_proc(GpuProc *gp)
{
    snprintf(gp->user, sizeof gp->user, "unknown");

    char path[64];
    snprintf(path, sizeof path, "/proc/%d/status", gp->pid);
    static char buf[8192];
    if (read_whole(path, buf, sizeof buf) > 0) {
        const char *uid = strstr(buf, "\nUid:");
        if (uid != NULL) {
            uid += 5;
            while (*uid == ' ' || *uid == '\t')
                uid++;
            unsigned uidv = (unsigned)strtoul(uid, NULL, 10);
            struct passwd *pw = getpwuid(uidv);
            if (pw != NULL && pw->pw_name != NULL)
                snprintf(gp->user, sizeof gp->user, "%s", pw->pw_name);
        }
    }

    snprintf(path, sizeof path, "/proc/%d/statm", gp->pid);
    char statm[128];
    if (read_whole(path, statm, sizeof statm) > 0) {
        char tok1[32], tok2[32];
        unsigned long long resident = 0;
        if (sscanf(statm, "%31s %31s", tok1, tok2) == 2) {
            resident = strtoull(tok2, NULL, 10);
            long pages = sysconf(_SC_PAGESIZE);
            if (pages > 0)
                gp->host_mem_mb =
                    (double)resident * (double)pages / 1048576.0;
        }
    }

    snprintf(path, sizeof path, "/proc/%d/cmdline", gp->pid);
    char cmd[512];
    ssize_t n = read_whole(path, cmd, sizeof cmd);
    if (n > 0) {
        for (ssize_t i = 0; i < n - 1; i++) {
            if (cmd[i] == 0)
                cmd[i] = ' ';
        }
        cmd[n - 1] = 0;
        char *t = cmd;
        while (*t == ' ')
            t++;
        snprintf(gp->cmdline, sizeof gp->cmdline, "%s", t);
    }
}

#endif /* !__APPLE__ */

void collect_gpus(Gpu *gpus, int *n_gpus, GpuProc *procs, int *n_procs)
{
#if defined(__APPLE__)
    mac_collect_gpus(gpus, n_gpus, procs, n_procs);
#else
    *n_gpus = 0;
    *n_procs = 0;
    if (!nvidia_available())
        return;

    static char buf[16384];
    char *smi[] = { "nvidia-smi",
                    "--query-gpu=index,name,memory.total,memory.used,"
                    "memory.free,utilization.gpu,temperature.gpu,"
                    "power.draw",
                    "--format=csv,noheader,nounits",
                    NULL };
    if (run_cmd_capture(smi, buf, sizeof buf, 5000) > 0)
        parse_gpu_stats(buf, gpus, n_gpus);

    char *appq[] = { "nvidia-smi",
                     "--query-compute-apps=pid,process_name,"
                     "used_gpu_memory",
                     "--format=csv,noheader,nounits",
                     NULL };
    if (run_cmd_capture(appq, buf, sizeof buf, 5000) <= 0) {
        proc_prev_compact(NULL, 0);
        return;
    }

    GpuProc tmp[MAX_GPU_PROCS * 4];
    int count = 0;
    int alive[MAX_GPU_PROCS * 4];
    char *save = NULL;
    double now = now_mono();
    for (char *line = strtok_r(buf, "\n", &save); line != NULL;
         line = strtok_r(NULL, "\n", &save)) {
        if (*line == 0)
            continue;
        char *f[64];
        size_t nf = split_trim(line, f, 64);
        if (nf < 3)
            continue;
        if (count >= MAX_GPU_PROCS * 4)
            break;
        GpuProc *gp = &tmp[count];
        memset(gp, 0, sizeof *gp);
        gp->pid = atoi(f[0]);
        join_fields(gp->process_name, sizeof gp->process_name, f, 1, nf - 1);
        gp->gpu_mem_mb = strtod(f[nf - 1], NULL);
        gp->cpu_pct = proc_cpu_percent(gp->pid, now);
        enrich_proc(gp);
        alive[count] = gp->pid;
        count++;
    }
    proc_prev_compact(alive, count);

    for (int i = 1; i < count; i++) {
        GpuProc key = tmp[i];
        int j = i - 1;
        while (j >= 0 && tmp[j].gpu_mem_mb < key.gpu_mem_mb) {
            tmp[j + 1] = tmp[j];
            j--;
        }
        tmp[j + 1] = key;
    }
    int keep = count < MAX_GPU_PROCS ? count : MAX_GPU_PROCS;
    for (int i = 0; i < keep; i++)
        procs[i] = tmp[i];
    *n_procs = keep;
#endif /* __APPLE__ */
}

/* ---------------- background update thread ---------------- */

void stats_init(Stats *st)
{
    pthread_mutex_init(&st->lock, NULL);
    pthread_cond_init(&st->cond, NULL);
    st->pending = 0;
    st->running = 0;
    memset(&st->sys, 0, sizeof st->sys);
    st->n_gpus = 0;
    st->n_procs = 0;
}

static void *stats_thread(void *arg)
{
    Stats *st = (Stats *)arg;
    double last = now_mono() - g_refresh_interval;

    for (;;) {
        pthread_mutex_lock(&st->lock);
        if (!st->pending && st->running) {
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            ts.tv_nsec += 100 * 1000 * 1000;
            if (ts.tv_nsec >= 1000000000L) {
                ts.tv_nsec -= 1000000000L;
                ts.tv_sec += 1;
            }
            pthread_cond_timedwait(&st->cond, &st->lock, &ts);
        }
        int running = st->running;
        st->pending = 0;
        pthread_mutex_unlock(&st->lock);
        if (!running)
            break;

        double now = now_mono();
        if (now - last < g_refresh_interval)
            continue;
        last = now;

        SysData s;
        memset(&s, 0, sizeof s);
        collect_sys(&s);
        s.valid = 1;

        Gpu gpus[MAX_GPUS];
        GpuProc procs[MAX_GPU_PROCS];
        int n_gpus = 0, n_procs = 0;
        collect_gpus(gpus, &n_gpus, procs, &n_procs);

        pthread_mutex_lock(&st->lock);
        st->sys = s;
        memcpy(st->gpus, gpus, sizeof gpus);
        st->n_gpus = n_gpus;
        memcpy(st->procs, procs, sizeof procs);
        st->n_procs = n_procs;
        pthread_mutex_unlock(&st->lock);
    }
    return NULL;
}

int stats_start(Stats *st)
{
    pthread_mutex_lock(&st->lock);
    st->running = 1;
    pthread_mutex_unlock(&st->lock);
    if (pthread_create(&st->tid, NULL, stats_thread, st) != 0) {
        pthread_mutex_lock(&st->lock);
        st->running = 0;
        pthread_mutex_unlock(&st->lock);
        return -1;
    }
    st->thread_started = 1;
    return 0;
}

void stats_request(Stats *st)
{
    pthread_mutex_lock(&st->lock);
    st->pending = 1;
    pthread_cond_signal(&st->cond);
    pthread_mutex_unlock(&st->lock);
}

void stats_stop(Stats *st)
{
    pthread_mutex_lock(&st->lock);
    st->running = 0;
    st->pending = 1;
    pthread_cond_broadcast(&st->cond);
    pthread_mutex_unlock(&st->lock);
    if (st->thread_started) {
        pthread_join(st->tid, NULL);
        st->thread_started = 0;
    }
}

int gpu_backend_nvidia(void)
{
#if defined(__APPLE__)
    return 0;
#else
    return nvidia_available();
#endif
}

int gpu_backend_apple(void)
{
#if defined(__APPLE__)
    return mac_apple_gpu();
#else
    return 0;
#endif
}

/* ---------------- deterministic fixture (golden tests) ----------------
 * TERMMON_FIXTURE=<file> loads frozen stats instead of live collection so
 * the C screen can be diffed byte-for-byte against the Python oracle. */

static int fixture_kw(const char *line, const char *kw, const char **rest)
{
    size_t n = strlen(kw);
    if (strncmp(line, kw, n) == 0 && (line[n] == ' ' || line[n] == '\t')) {
        *rest = line + n + strspn(line + n, " \t");
        return 1;
    }
    return 0;
}

int stats_load_fixture(Stats *st, const char *path)
{
    FILE *f = fopen(path, "r");
    if (f == NULL)
        return -1;

    SysData s;
    memset(&s, 0, sizeof s);
    double cores[MAX_CORES];
    memset(cores, 0, sizeof cores);
    int max_core = -1;
    Gpu gpus[MAX_GPUS];
    int n_gpus = 0;
    GpuProc procs[MAX_GPU_PROCS];
    int n_procs = 0;

    char line[1024];
    while (fgets(line, sizeof line, f) != NULL) {
        size_t len = strlen(line);
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r'))
            line[--len] = '\0';
        const char *rest;
        if (fixture_kw(line, "SYS", &rest)) {
            char key[64];
            double v;
            if (sscanf(rest, "%63s %lf", key, &v) != 2)
                continue;
            if (strcmp(key, "total_mem_gb") == 0)
                s.total_mem_gb = v;
            else if (strcmp(key, "used_mem_gb") == 0)
                s.used_mem_gb = v;
            else if (strcmp(key, "cache_mem_gb") == 0)
                s.cache_mem_gb = v;
            else if (strcmp(key, "free_mem_gb") == 0)
                s.free_mem_gb = v;
            else if (strcmp(key, "avail_mem_gb") == 0)
                s.avail_mem_gb = v;
            else if (strcmp(key, "mem_percent") == 0)
                s.mem_percent = v;
            else if (strcmp(key, "swap_total_mb") == 0)
                s.swap_total_mb = v;
            else if (strcmp(key, "swap_used_mb") == 0)
                s.swap_used_mb = v;
            else if (strcmp(key, "swap_percent") == 0)
                s.swap_percent = v;
            else if (strcmp(key, "cpu_usage") == 0)
                s.cpu_usage = v;
            else if (strcmp(key, "cpu_temp") == 0) {
                s.cpu_temp = v;
                s.has_temp = 1;
            }
        } else if (fixture_kw(line, "CORE", &rest)) {
            int idx;
            double pct;
            if (sscanf(rest, "%d %lf", &idx, &pct) == 2 && idx >= 0 &&
                idx < MAX_CORES) {
                cores[idx] = pct;
                if (idx > max_core)
                    max_core = idx;
            }
        } else if (fixture_kw(line, "GPU", &rest)) {
            char tmp[512];
            snprintf(tmp, sizeof tmp, "%s", rest);
            char *fields[8] = { NULL };
            char *p = tmp;
            int nf = 0;
            while (nf < 8) {
                fields[nf++] = p;
                char *bar = strchr(p, '|');
                if (bar == NULL)
                    break;
                *bar = '\0';
                p = bar + 1;
            }
            if (nf >= 8 && n_gpus < MAX_GPUS) {
                Gpu *g = &gpus[n_gpus++];
                memset(g, 0, sizeof *g);
                snprintf(g->idx, sizeof g->idx, "%s", fields[0]);
                snprintf(g->name, sizeof g->name, "%s", fields[1]);
                g->mem_total = strtod(fields[2], NULL);
                g->mem_used = strtod(fields[3], NULL);
                g->mem_free = strtod(fields[4], NULL);
                g->gpu_util = strtod(fields[5], NULL);
                g->temp = strtod(fields[6], NULL);
                g->power = strtod(fields[7], NULL);
            }
        } else if (fixture_kw(line, "PROC", &rest)) {
            char tmp[700];
            snprintf(tmp, sizeof tmp, "%s", rest);
            char *fields[5] = { NULL };
            char *p = tmp;
            int nf = 0;
            while (nf < 5) {
                fields[nf] = p;
                char *bar = strchr(p, '|');
                if (bar == NULL) {
                    nf = -1;
                    break;
                }
                *bar = '\0';
                p = bar + 1;
                nf++;
            }
            if (nf == 5 && n_procs < MAX_GPU_PROCS) {
                GpuProc *gp = &procs[n_procs++];
                memset(gp, 0, sizeof *gp);
                gp->pid = atoi(fields[0]);
                snprintf(gp->user, sizeof gp->user, "%s", fields[1]);
                gp->gpu_mem_mb = strtod(fields[2], NULL);
                gp->host_mem_mb = strtod(fields[3], NULL);
                gp->cpu_pct = strtod(fields[4], NULL);
                const char *base = strrchr(p, '/');
                snprintf(gp->process_name, sizeof gp->process_name, "%s",
                         base ? base + 1 : p);
                snprintf(gp->cmdline, sizeof gp->cmdline, "%s", p);
            }
        }
    }
    fclose(f);

    if (max_core >= 0) {
        s.core_count = max_core + 1;
        for (int i = 0; i <= max_core; i++)
            s.per_core[i] = cores[i];
    }
    s.valid = 1;

    pthread_mutex_lock(&st->lock);
    st->sys = s;
    memcpy(st->gpus, gpus, sizeof gpus);
    st->n_gpus = n_gpus;
    memcpy(st->procs, procs, sizeof procs);
    st->n_procs = n_procs;
    pthread_mutex_unlock(&st->lock);
    return 0;
}
