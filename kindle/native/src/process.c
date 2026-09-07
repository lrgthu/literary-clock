#define _POSIX_C_SOURCE 200809L

#include "litclock.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

volatile sig_atomic_t lc_signal_received = 0;

static void on_signal(int signal_number) {
    lc_signal_received = signal_number;
}

int lc_install_signal_handlers(char *error) {
    struct sigaction action;
    (void)memset(&action, 0, sizeof(action));
    action.sa_handler = on_signal;
    (void)sigemptyset(&action.sa_mask);
    if (sigaction(SIGHUP, &action, NULL) != 0 || sigaction(SIGINT, &action, NULL) != 0 ||
        sigaction(SIGTERM, &action, NULL) != 0) {
        lc_set_error(error, "signal handler installation failed: %s", strerror(errno));
        return -1;
    }
    return 0;
}

int lc_sleep_interruptible(unsigned int seconds) {
    struct timespec remaining = {.tv_sec = (time_t)seconds, .tv_nsec = 0};
    while (remaining.tv_sec != 0 || remaining.tv_nsec != 0) {
        struct timespec next = {0};
        if (nanosleep(&remaining, &next) == 0) {
            return 0;
        }
        if (errno != EINTR) {
            return -1;
        }
        if (lc_signal_received != 0) {
            return -1;
        }
        remaining = next;
    }
    return 0;
}

int lc_lock_acquire(const char *state_path, LcLock *lock, char *error) {
    char directory[LC_PATH_CAP];
    struct flock operation;
    int flags;
    int lock_path_length;
    lock->fd = -1;
    if (!lc_parent_dir(directory, sizeof(directory), state_path)) {
        lc_set_error(error, "runtime lock path is too long");
        return 22;
    }
    lock_path_length =
        snprintf(lock->path, sizeof(lock->path), "%s/.litclock-native.lock", directory);
    if (lock_path_length < 0 || (size_t)lock_path_length >= sizeof(lock->path)) {
        lc_set_error(error, "runtime lock path is too long");
        return 22;
    }
    lock->fd = open(lock->path, O_RDWR | O_CREAT, 0644);
    if (lock->fd < 0) {
        lc_set_error(error, "runtime lock acquisition failed: %s", strerror(errno));
        return 22;
    }
    flags = fcntl(lock->fd, F_GETFD);
    if (flags >= 0) {
        (void)fcntl(lock->fd, F_SETFD, flags | FD_CLOEXEC);
    }
    (void)memset(&operation, 0, sizeof(operation));
    operation.l_type = F_WRLCK;
    operation.l_whence = SEEK_SET;
    if (fcntl(lock->fd, F_SETLK, &operation) != 0) {
        int lock_error = errno;
        (void)close(lock->fd);
        lock->fd = -1;
        if (lock_error == EACCES || lock_error == EAGAIN) {
            lc_set_error(error, "another Literary Clock update is active");
            return 20;
        }
        lc_set_error(error, "runtime lock acquisition failed: %s", strerror(lock_error));
        return 22;
    }
    if (ftruncate(lock->fd, 0) == 0) {
        char pid[32];
        int length = snprintf(pid, sizeof(pid), "%ld\n", (long)getpid());
        if (length > 0) {
            (void)write(lock->fd, pid, (size_t)length);
        }
    }
    return 0;
}

void lc_lock_release(LcLock *lock) {
    if (lock->fd >= 0) {
        (void)close(lock->fd);
        lock->fd = -1;
    }
}
