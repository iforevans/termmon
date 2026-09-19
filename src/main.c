#include "termmon.h"

#include <locale.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/ioctl.h>

static volatile sig_atomic_t g_resized = 0;
static volatile sig_atomic_t g_sigint = 0;

static void on_sigwinch(int sig)
{
    (void)sig;
    g_resized = 1;
}

static void on_sigint(int sig)
{
    (void)sig;
    g_sigint = 1;
}

double now_mono(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

int true_terminal_size(int *rows, int *cols)
{
    static const int fds[3] = { STDOUT_FILENO, STDIN_FILENO, STDERR_FILENO };
    for (size_t i = 0; i < sizeof fds / sizeof fds[0]; i++) {
        struct winsize ws;
        if (ioctl(fds[i], TIOCGWINSZ, &ws) == 0 &&
            ws.ws_row > 0 && ws.ws_col > 0) {
            *rows = (int)ws.ws_row;
            *cols = (int)ws.ws_col;
            return 1;
        }
    }
    return 0;
}

static void apply_resize(WINDOW *scr)
{
    int rows = 0, cols = 0;
    if (!true_terminal_size(&rows, &cols))
        getmaxyx(scr, rows, cols);
    resizeterm(rows, cols);
    werase(scr);
}

int main(void)
{
    setlocale(LC_ALL, "");

    App app;
    memset(&app, 0, sizeof app);
    stats_init(&app.stats);
    app.box_width = 80;

    WINDOW *scr = initscr();
    start_color();
    use_default_colors();
    init_pair(COLOR_TITLE, COLOR_WHITE, -1);
    init_pair(COLOR_MEMORY, COLOR_GREEN, -1);
    init_pair(COLOR_SWAP, COLOR_YELLOW, -1);
    init_pair(COLOR_CPU, COLOR_CYAN, -1);
    init_pair(COLOR_VRAM, COLOR_MAGENTA, -1);
    init_pair(COLOR_POPUP, COLOR_WHITE, COLOR_BLUE);
    init_pair(COLOR_MEM_CACHE, COLOR_CYAN, -1);
    init_pair(COLOR_MEM_FREE, COLOR_WHITE, -1);
    cbreak();
    noecho();
    keypad(scr, TRUE);
    timeout(50);

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_sigwinch;
    sigaction(SIGWINCH, &sa, NULL);
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_sigint;
    sigaction(SIGINT, &sa, NULL);

    const char *fixture = getenv("TERMMON_FIXTURE");
    if (fixture == NULL || stats_load_fixture(&app.stats, fixture) != 0)
        stats_start(&app.stats);

    int quit = 0;
    double last_refresh = now_mono();

    while (!quit) {
        if (g_sigint)
            break;

        double now = now_mono();

        if (g_resized) {
            g_resized = 0;
            apply_resize(scr);
            last_refresh = now;
        }

        if (now - last_refresh >= REFRESH_INTERVAL) {
            last_refresh = now;
        }

        draw(&app, scr);

        int key = getch();

        if (g_sigint)
            break;

        switch (key) {
        case 'q':
        case 'Q':
            quit = 1;
            break;
        case 'r':
        case 'R':
            stats_request(&app.stats);
            break;
        case 'h':
        case 'H':
            show_help(&app, scr);
            break;
        case KEY_RIGHT:
            app.process_scroll_x += 16;
            draw(&app, scr);
            break;
        case KEY_LEFT:
            app.process_scroll_x -= 16;
            if (app.process_scroll_x < 0)
                app.process_scroll_x = 0;
            draw(&app, scr);
            break;
        default:
            break;
        }
    }

    stats_stop(&app.stats);

    curs_set(1);
    nocbreak();
    keypad(scr, FALSE);
    echo();
    endwin();

    fputs("\x1b[H\x1b[2J", stdout);
    fflush(stdout);
    ssize_t ignored = write(STDERR_FILENO, "\x1b[?25h\n", 6);
    (void)ignored;
    return 0;
}
