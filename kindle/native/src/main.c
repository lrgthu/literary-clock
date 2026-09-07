#define _POSIX_C_SOURCE 200809L

#include "litclock.h"

#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void usage(FILE *stream) {
    (void)fprintf(
        stream,
        "usage: litclock-native [--release-root PATH] [--state PATH] [--timestamp SNAPSHOT]\n"
        "       [--display-helper PATH] [--log PATH] [--full-refresh-interval N]\n"
        "       [--fake-display-success | --fake-display-failure N] [--dry-run] [--version]\n"
    );
}

static bool copy_option(char *destination, size_t capacity, const char *value) {
    int written = snprintf(destination, capacity, "%s", value);
    return written >= 0 && (size_t)written < capacity;
}

static const char *environment(const char *name, const char *fallback) {
    const char *value = getenv(name);
    return value != NULL && *value != '\0' ? value : fallback;
}

static bool parse_unsigned(const char *text, uint64_t maximum, uint64_t *value) {
    uint64_t parsed;
    if (!lc_parse_u64(text, &parsed) || parsed > maximum) {
        return false;
    }
    *value = parsed;
    return true;
}

static int initialize_options(LcOptions *options, char *error) {
    const char *release_root = environment("LITCLOCK_RELEASE_ROOT", "");
    const char *state_root = environment("LITCLOCK_STATE_ROOT", "/var/local/literary-clock/state");
    const char *interval_text = getenv("LITCLOCK_FULL_REFRESH_INTERVAL");
    uint64_t parsed;
    int state_path_length;
    (void)memset(options, 0, sizeof(*options));
    options->fake_display_status = -1;
    options->full_refresh_interval = 15;
    state_path_length =
        snprintf(options->state_path, sizeof(options->state_path), "%s/state.tsv", state_root);
    if (!copy_option(options->release_root, sizeof(options->release_root), release_root) ||
        state_path_length < 0 || (size_t)state_path_length >= sizeof(options->state_path) ||
        !copy_option(
            options->log_path,
            sizeof(options->log_path),
            environment("LITCLOCK_LOG_FILE", "/var/local/literary-clock/literary-clock.log")
        ) ||
        !copy_option(
            options->snapshot_text,
            sizeof(options->snapshot_text),
            environment("LITCLOCK_TIMESTAMP_SNAPSHOT", "")
        ) ||
        !copy_option(
            options->scheduler_source,
            sizeof(options->scheduler_source),
            environment("LITCLOCK_SCHEDULER_SOURCE", "manual")
        )) {
        lc_set_error(error, "runtime option path is too long");
        return 2;
    }
    if (interval_text != NULL) {
        if (!parse_unsigned(interval_text, UINT64_MAX, &parsed) || parsed == 0) {
            lc_set_error(error, "full refresh interval is invalid");
            return 23;
        }
        options->full_refresh_interval = parsed;
    }
    if (getenv("LITCLOCK_FAKE_DISPLAY_STATUS") != NULL) {
        if (!parse_unsigned(getenv("LITCLOCK_FAKE_DISPLAY_STATUS"), 255, &parsed)) {
            lc_set_error(error, "fake display status is invalid");
            return 2;
        }
        options->fake_display_status = (int)parsed;
    }
    if (getenv("LITCLOCK_TEST_DELAY_BEFORE_DISPLAY") != NULL) {
        if (!parse_unsigned(getenv("LITCLOCK_TEST_DELAY_BEFORE_DISPLAY"), UINT_MAX, &parsed)) {
            lc_set_error(error, "pre-display delay is invalid");
            return 2;
        }
        options->delay_before_display = (unsigned int)parsed;
    }
    if (getenv("LITCLOCK_TEST_DELAY_BEFORE_COMMIT") != NULL) {
        if (!parse_unsigned(getenv("LITCLOCK_TEST_DELAY_BEFORE_COMMIT"), UINT_MAX, &parsed)) {
            lc_set_error(error, "pre-commit delay is invalid");
            return 2;
        }
        options->delay_before_commit = (unsigned int)parsed;
    }
    return 0;
}

static int parse_options(int argc, char **argv, LcOptions *options, char *error) {
    int index;
    bool display_helper_set = false;
    uint64_t parsed;
    for (index = 1; index < argc; index++) {
        const char *argument = argv[index];
        if (strcmp(argument, "--version") == 0) {
            (void)printf(
                "litclock-native %s release-format=%d manifest-format=%d runtime-format=%d\n",
                LC_RUNTIME_VERSION,
                LC_RELEASE_FORMAT_VERSION,
                LC_MANIFEST_FORMAT_VERSION,
                LC_BUNDLE_RUNTIME_VERSION
            );
            exit(0);
        }
        if (strcmp(argument, "--help") == 0) {
            usage(stdout);
            exit(0);
        }
        if (strcmp(argument, "--dry-run") == 0) {
            options->dry_run = true;
            continue;
        }
        if (strcmp(argument, "--fake-display-success") == 0) {
            options->fake_display_status = 0;
            continue;
        }
        if (index + 1 >= argc) {
            lc_set_error(error, "option requires a value: %s", argument);
            return 2;
        }
        if (strcmp(argument, "--release-root") == 0) {
            if (!copy_option(options->release_root, sizeof(options->release_root), argv[++index])) {
                return 2;
            }
        } else if (strcmp(argument, "--state") == 0) {
            if (!copy_option(options->state_path, sizeof(options->state_path), argv[++index])) {
                return 2;
            }
        } else if (strcmp(argument, "--timestamp") == 0) {
            if (!copy_option(options->snapshot_text, sizeof(options->snapshot_text), argv[++index])) {
                return 2;
            }
        } else if (strcmp(argument, "--display-helper") == 0) {
            if (!copy_option(options->display_helper, sizeof(options->display_helper), argv[++index])) {
                return 2;
            }
            display_helper_set = true;
        } else if (strcmp(argument, "--log") == 0) {
            if (!copy_option(options->log_path, sizeof(options->log_path), argv[++index])) {
                return 2;
            }
        } else if (strcmp(argument, "--full-refresh-interval") == 0) {
            if (!parse_unsigned(argv[++index], UINT64_MAX, &parsed) || parsed == 0) {
                lc_set_error(error, "full refresh interval is invalid");
                return 23;
            }
            options->full_refresh_interval = parsed;
        } else if (strcmp(argument, "--fake-display-failure") == 0) {
            if (!parse_unsigned(argv[++index], 255, &parsed) || parsed == 0) {
                lc_set_error(error, "fake display status is invalid");
                return 2;
            }
            options->fake_display_status = (int)parsed;
        } else {
            lc_set_error(error, "unknown option: %s", argument);
            return 2;
        }
    }
    if (options->release_root[0] == '\0') {
        lc_set_error(error, "release root is required (use --release-root or LITCLOCK_RELEASE_ROOT)");
        return 2;
    }
    if (!display_helper_set) {
        const char *configured = getenv("LITCLOCK_DISPLAY_HELPER");
        if (configured != NULL && *configured != '\0') {
            if (!copy_option(options->display_helper, sizeof(options->display_helper), configured)) {
                return 2;
            }
        } else {
            int helper_length = snprintf(
                       options->display_helper,
                       sizeof(options->display_helper),
                       "%s/bin/literary-clock-display.sh",
                       options->release_root
                   );
            if (helper_length < 0 || (size_t)helper_length >= sizeof(options->display_helper)) {
                lc_set_error(error, "display helper path is too long");
                return 2;
            }
        }
    }
    return 0;
}

static void append_log(const LcOptions *options, const char *format, ...) {
    char directory[LC_PATH_CAP];
    struct stat details;
    FILE *stream;
    va_list arguments;
    char ignored[LC_ERROR_CAP] = {0};
    if (!lc_parent_dir(directory, sizeof(directory), options->log_path)) {
        return;
    }
    (void)lc_mkdir_p(directory, ignored);
    if (stat(options->log_path, &details) == 0 && details.st_size > 262144) {
        stream = fopen(options->log_path, "w");
    } else {
        stream = fopen(options->log_path, "a");
    }
    if (stream == NULL) {
        return;
    }
    va_start(arguments, format);
    (void)vfprintf(stream, format, arguments);
    va_end(arguments);
    (void)fputc('\n', stream);
    (void)fclose(stream);
}

static int finish_error(
    int code,
    const char *message,
    const LcOptions *options,
    const LcSnapshot *snapshot,
    const char *quote_id,
    uint64_t display_count,
    const char *refresh_mode,
    uint64_t started
) {
    uint64_t duration = lc_monotonic_ms() - started;
    (void)fprintf(stderr, "%s\n", message);
    append_log(
        options,
        "timestamp=%s local_date=%s minute=%s quote_id=%s display_count=%llu "
        "display_result=error-%d refresh_mode=%s history_commit=no duration_ms=%llu",
        snapshot->epoch_text[0] != '\0' ? snapshot->epoch_text : "0",
        snapshot->iso_date[0] != '\0' ? snapshot->iso_date : "none",
        snapshot->minute_key[0] != '\0' ? snapshot->minute_key : "none",
        quote_id,
        (unsigned long long)display_count,
        code,
        refresh_mode,
        (unsigned long long)duration
    );
    return code;
}

int main(int argc, char **argv) {
    LcOptions options;
    LcSnapshot snapshot = {0};
    LcLast last = {0};
    LcState state = {0};
    LcPool pool = {0};
    LcPool minute_pool = {0};
    LcDateAsset date_asset = {0};
    LcCandidate *selected = NULL;
    LcLock lock = {.fd = -1};
    char release_version[128] = {0};
    char frame[LC_PATH_CAP];
    char date_frame[LC_PATH_CAP];
    char state_directory[LC_PATH_CAP];
    char error[LC_ERROR_CAP] = {0};
    const char *saved_bag;
    const char *refresh = "none";
    const char *previous_date = NULL;
    uint64_t started = lc_monotonic_ms();
    uint64_t next_display_count = 0;
    uint64_t display_started;
    uint64_t display_ms;
    uint64_t state_read_started;
    uint64_t state_read_ms = 0;
    uint64_t manifest_started;
    uint64_t manifest_ms = 0;
    uint64_t selector_started;
    uint64_t selector_ms = 0;
    uint64_t date_started;
    uint64_t state_write_started;
    uint64_t state_write_ms = 0;
    int relaxation_level = -1;
    int result;

    result = initialize_options(&options, error);
    if (result == 0) {
        result = parse_options(argc, argv, &options, error);
    }
    if (result != 0) {
        usage(stderr);
        (void)fprintf(stderr, "%s\n", error);
        return result;
    }
    if (!lc_parent_dir(state_directory, sizeof(state_directory), options.state_path) ||
        lc_mkdir_p(state_directory, error) != 0) {
        return finish_error(22, error, &options, &snapshot, "none", 0, refresh, started);
    }
    result = lc_lock_acquire(options.state_path, &lock, error);
    if (result != 0) {
        return finish_error(result, error, &options, &snapshot, "none", 0, refresh, started);
    }
    if (lc_install_signal_handlers(error) != 0) {
        result = 22;
        goto failed;
    }
    result = lc_validate_release(
        options.release_root, release_version, sizeof(release_version), error
    );
    if (result != 0) {
        goto failed;
    }
    result = lc_snapshot_capture(options.snapshot_text, &snapshot, error);
    if (result != 0) {
        goto failed;
    }
    result = lc_state_peek_last(options.state_path, &last, error);
    if (result != 0) {
        goto failed;
    }
    if (last.present && strcmp(last.iso_date, snapshot.iso_date) == 0 &&
        strcmp(last.minute_key, snapshot.minute_key) == 0 &&
        strcmp(last.release_version, release_version) == 0) {
        append_log(
            &options,
            "timestamp=%s local_date=%s minute=%s quote_id=none display_count=unchanged "
            "selection_reason=already-current display_result=skipped refresh_mode=none "
            "history_commit=no duration_ms=0 scheduler_source=%s",
            snapshot.epoch_text,
            snapshot.iso_date,
            snapshot.minute_key,
            options.scheduler_source
        );
        (void)printf(
            "result=skipped minute=%s selection_reason=already-current history_commit=no\n",
            snapshot.minute_key
        );
        result = 0;
        goto done;
    }
    state_read_started = lc_monotonic_ms();
    result = lc_state_load(options.state_path, &state, error);
    state_read_ms = lc_monotonic_ms() - state_read_started;
    if (result != 0) {
        goto failed;
    }
    if (state.display_count == UINT64_MAX) {
        lc_set_error(error, "display counter is invalid");
        result = 38;
        goto failed;
    }
    next_display_count = state.display_count + 1;
    manifest_started = lc_monotonic_ms();
    result = lc_manifest_minute_pool(options.release_root, snapshot.minute, &minute_pool, error);
    if (result != 0) {
        goto failed;
    }
    saved_bag = lc_state_bag(&state, snapshot.minute);
    if (saved_bag != NULL && *saved_bag != '\0') {
        result = lc_parse_csv_pool(saved_bag, &pool, error);
        if (result != 0) {
            result = 40;
            goto failed;
        }
        lc_pool_free(&minute_pool);
    } else {
        pool = minute_pool;
        minute_pool.items = NULL;
        minute_pool.count = 0;
        lc_shuffle_pool(&pool, release_version, &snapshot);
    }
    if (pool.count == 0) {
        lc_set_error(error, "shuffle bag initialization failed");
        result = 40;
        goto failed;
    }
    result = lc_manifest_quote_metadata(options.release_root, &pool, error);
    if (result != 0) {
        goto failed;
    }
    manifest_ms = lc_monotonic_ms() - manifest_started;
    selector_started = lc_monotonic_ms();
    lc_state_apply_history(&state, &pool);
    selected = lc_select(&pool, &snapshot, &relaxation_level);
    selector_ms = lc_monotonic_ms() - selector_started;
    if (selected == NULL) {
        lc_set_error(error, "no selectable quote metadata remains");
        result = 41;
        goto failed;
    }
    date_started = lc_monotonic_ms();
    result = lc_manifest_date(options.release_root, snapshot.date_key, &date_asset, error);
    manifest_ms += lc_monotonic_ms() - date_started;
    if (result != 0) {
        goto failed;
    }
    if (!lc_join_path(frame, sizeof(frame), options.release_root, "bundle") ||
        !lc_join_path(date_frame, sizeof(date_frame), options.release_root, "bundle")) {
        lc_set_error(error, "bundle path is invalid");
        result = 41;
        goto failed;
    }
    {
        char bundle_root[LC_PATH_CAP];
        (void)snprintf(bundle_root, sizeof(bundle_root), "%s", frame);
        if (!lc_join_path(frame, sizeof(frame), bundle_root, selected->frame) ||
            !lc_join_path(date_frame, sizeof(date_frame), bundle_root, date_asset.frame)) {
            lc_set_error(error, "asset path is invalid");
            result = 41;
            goto failed;
        }
    }
    if (last.present) {
        previous_date = last.iso_date;
    }
    refresh = lc_refresh_mode(
        state.display_count,
        next_display_count,
        options.full_refresh_interval,
        previous_date,
        snapshot.iso_date
    );
    if (options.dry_run) {
        if (lc_validate_png(frame) != 0 || lc_validate_png(date_frame) != 0) {
            lc_set_error(error, "dry-run asset validation failed");
            result = 3;
            goto failed;
        }
        (void)printf(
            "result=dry-run minute=%s quote_id=%s selection_reason=%d refresh_mode=%s "
            "display_count=%llu history_commit=no\n",
            snapshot.minute_key,
            selected->id_text,
            relaxation_level,
            refresh,
            (unsigned long long)state.display_count
        );
        result = 0;
        goto done;
    }
    if (options.delay_before_display != 0 &&
        lc_sleep_interruptible(options.delay_before_display) != 0) {
        result = lc_signal_received != 0 ? 128 + lc_signal_received : 44;
        lc_set_error(error, "pre-display delay interrupted");
        goto failed;
    }
    if (lc_signal_received != 0) {
        result = 128 + lc_signal_received;
        lc_set_error(error, "runtime interrupted before display");
        goto failed;
    }
    display_started = lc_monotonic_ms();
    result = lc_display_frame(
        &options, frame, date_frame, date_asset.x, date_asset.y, refresh, error
    );
    display_ms = lc_monotonic_ms() - display_started;
    if (result != 0) {
        append_log(
            &options,
            "timestamp=%s local_date=%s minute=%s quote_id=%s display_count=%llu "
            "selection_reason=%d display_result=failed-%d refresh_mode=%s history_commit=no "
            "display_ms=%llu duration_ms=%llu scheduler_source=%s",
            snapshot.epoch_text,
            snapshot.iso_date,
            snapshot.minute_key,
            selected->id_text,
            (unsigned long long)state.display_count,
            relaxation_level,
            result,
            refresh,
            (unsigned long long)display_ms,
            (unsigned long long)(lc_monotonic_ms() - started),
            options.scheduler_source
        );
        (void)fprintf(stderr, "%s\n", error);
        goto done;
    }
    if (options.delay_before_commit != 0 &&
        lc_sleep_interruptible(options.delay_before_commit) != 0) {
        result = lc_signal_received != 0 ? 128 + lc_signal_received : 45;
        lc_set_error(error, "pre-commit delay interrupted");
        goto failed;
    }
    if (lc_signal_received != 0) {
        result = 128 + lc_signal_received;
        lc_set_error(error, "runtime interrupted before commit");
        goto failed;
    }
    state_write_started = lc_monotonic_ms();
    result = lc_state_commit(
        options.state_path,
        &state,
        &snapshot,
        selected,
        &pool,
        next_display_count,
        release_version,
        error
    );
    state_write_ms = lc_monotonic_ms() - state_write_started;
    if (result != 0) {
        goto failed;
    }
    append_log(
        &options,
        "timestamp=%s local_date=%s minute=%s quote_id=%s display_count=%llu frame=%s "
        "selection_reason=%d display_result=success refresh_mode=%s history_commit=yes "
        "state_read_ms=%llu manifest_ms=%llu selector_ms=%llu display_ms=%llu state_ms=%llu "
        "duration_ms=%llu scheduler_source=%s",
        snapshot.epoch_text,
        snapshot.iso_date,
        snapshot.minute_key,
        selected->id_text,
        (unsigned long long)next_display_count,
        selected->frame,
        relaxation_level,
        refresh,
        (unsigned long long)state_read_ms,
        (unsigned long long)manifest_ms,
        (unsigned long long)selector_ms,
        (unsigned long long)display_ms,
        (unsigned long long)state_write_ms,
        (unsigned long long)(lc_monotonic_ms() - started),
        options.scheduler_source
    );
    (void)printf(
        "displayed minute=%s quote_id=%s count=%llu refresh=%s selection_reason=%d "
        "history_commit=yes duration_ms=%llu\n",
        snapshot.minute_key,
        selected->id_text,
        (unsigned long long)next_display_count,
        refresh,
        relaxation_level,
        (unsigned long long)(lc_monotonic_ms() - started)
    );
    result = 0;
    goto done;

failed:
    result = finish_error(
        result,
        error,
        &options,
        &snapshot,
        selected != NULL ? selected->id_text : "none",
        state.display_count,
        refresh,
        started
    );
done:
    lc_pool_free(&minute_pool);
    lc_pool_free(&pool);
    lc_state_free(&state);
    lc_lock_release(&lock);
    return result;
}
