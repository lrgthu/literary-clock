#define _POSIX_C_SOURCE 200809L

#include "litclock.h"

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

int lc_validate_png(const char *path) {
    static const unsigned char signature[8] = {0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a};
    unsigned char actual[8];
    struct stat details;
    FILE *stream;
    size_t bytes;
    if (stat(path, &details) != 0 || details.st_size <= 8 || !S_ISREG(details.st_mode)) {
        return -1;
    }
    stream = fopen(path, "rb");
    if (stream == NULL) {
        return -1;
    }
    bytes = fread(actual, 1, sizeof(actual), stream);
    (void)fclose(stream);
    return bytes == sizeof(actual) && memcmp(actual, signature, sizeof(signature)) == 0 ? 0 : -1;
}

int lc_display_frame(
    const LcOptions *options,
    const char *frame,
    const char *date_frame,
    int date_x,
    int date_y,
    const char *refresh_mode,
    char *error
) {
    pid_t child;
    struct stat details;
    int status;
    char x_text[16];
    char y_text[16];
    if (lc_validate_png(frame) != 0) {
        lc_set_error(error, "invalid or missing base PNG: %s", frame);
        return 3;
    }
    if (lc_validate_png(date_frame) != 0) {
        lc_set_error(error, "invalid or missing date PNG: %s", date_frame);
        return 4;
    }
    if (options->fake_display_status >= 0) {
        if (options->fake_display_status != 0) {
            lc_set_error(error, "fake display failed with status %d", options->fake_display_status);
        }
        return options->fake_display_status;
    }
    if (stat(options->display_helper, &details) != 0 || !S_ISREG(details.st_mode)) {
        lc_set_error(error, "display helper is missing: %s", options->display_helper);
        return 5;
    }
    (void)snprintf(x_text, sizeof(x_text), "%d", date_x);
    (void)snprintf(y_text, sizeof(y_text), "%d", date_y);
    child = fork();
    if (child < 0) {
        lc_set_error(error, "display helper cannot be started: %s", strerror(errno));
        return 5;
    }
    if (child == 0) {
        (void)signal(SIGHUP, SIG_DFL);
        (void)signal(SIGINT, SIG_DFL);
        (void)signal(SIGTERM, SIG_DFL);
        execl(
            options->display_helper,
            options->display_helper,
            frame,
            date_frame,
            x_text,
            y_text,
            refresh_mode,
            (char *)NULL
        );
        _exit(5);
    }
    for (;;) {
        pid_t waited = waitpid(child, &status, 0);
        if (waited == child) {
            break;
        }
        if (waited < 0 && errno == EINTR) {
            if (lc_signal_received != 0) {
                (void)kill(child, SIGTERM);
                while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
                }
                return 128 + lc_signal_received;
            }
            continue;
        }
        lc_set_error(error, "display helper wait failed: %s", strerror(errno));
        return 5;
    }
    if (WIFEXITED(status)) {
        int result = WEXITSTATUS(status);
        if (result != 0) {
            lc_set_error(error, "display helper failed with status %d", result);
        }
        return result;
    }
    if (WIFSIGNALED(status)) {
        int signal_number = WTERMSIG(status);
        lc_set_error(error, "display helper terminated by signal %d", signal_number);
        return 128 + signal_number;
    }
    lc_set_error(error, "display helper ended unexpectedly");
    return 5;
}
