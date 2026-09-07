#define _POSIX_C_SOURCE 200809L

#include "litclock.h"

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static bool valid_minute_key(const char *text, int *minute) {
    size_t index;
    int value = 0;
    if (strlen(text) != 4) {
        return false;
    }
    for (index = 0; index < 4; index++) {
        if (text[index] < '0' || text[index] > '9') {
            return false;
        }
        value = value * 10 + text[index] - '0';
    }
    if (value > 1439) {
        return false;
    }
    *minute = value;
    return true;
}

static bool copy_field(char *destination, size_t capacity, const char *source) {
    size_t length = strlen(source);
    if (length == 0 || length >= capacity) {
        return false;
    }
    memcpy(destination, source, length + 1);
    return true;
}

int lc_state_peek_last(const char *path, LcLast *last, char *error) {
    FILE *stream = fopen(path, "r");
    char *line;
    int read_status;
    (void)memset(last, 0, sizeof(*last));
    if (stream == NULL) {
        if (errno == ENOENT) {
            return 0;
        }
        lc_set_error(error, "state cannot be read: %s", strerror(errno));
        return 37;
    }
    line = malloc(LC_LINE_CAP);
    if (line == NULL) {
        (void)fclose(stream);
        lc_set_error(error, "state allocation failed");
        return 37;
    }
    while ((read_status = lc_read_line(stream, line, LC_LINE_CAP)) > 0) {
        char *fields[6];
        int count = lc_split_tabs(line, fields, 6);
        int minute;
        if (count == 6 && strcmp(fields[0], "LAST") == 0 &&
            copy_field(last->iso_date, sizeof(last->iso_date), fields[1]) &&
            valid_minute_key(fields[2], &minute) &&
            copy_field(last->minute_key, sizeof(last->minute_key), fields[2]) &&
            copy_field(last->release_version, sizeof(last->release_version), fields[5])) {
            last->present = true;
            break;
        }
    }
    free(line);
    (void)fclose(stream);
    if (read_status < -1) {
        lc_set_error(error, "state contains an overlong row");
        return 37;
    }
    return 0;
}

static void add_history(LcState *state, char **fields) {
    LcHistory history = {0};
    int minute;
    int64_t epoch;
    uint64_t quote_id;
    if (!lc_parse_i64(fields[1], &epoch) || !valid_minute_key(fields[3], &minute) ||
        !lc_parse_u64(fields[4], &quote_id) ||
        !copy_field(history.epoch_text, sizeof(history.epoch_text), fields[1]) ||
        !copy_field(history.iso_date, sizeof(history.iso_date), fields[2]) ||
        !copy_field(history.minute_key, sizeof(history.minute_key), fields[3]) ||
        !copy_field(history.quote_id_text, sizeof(history.quote_id_text), fields[4]) ||
        !copy_field(history.book, sizeof(history.book), fields[5]) ||
        !copy_field(history.author, sizeof(history.author), fields[6])) {
        return;
    }
    history.epoch = epoch;
    history.quote_id = quote_id;
    if (state->history_count < LC_MAX_HISTORY) {
        size_t slot = (state->history_start + state->history_count) % LC_MAX_HISTORY;
        state->history[slot] = history;
        state->history_count++;
    } else {
        state->history[state->history_start] = history;
        state->history_start = (state->history_start + 1) % LC_MAX_HISTORY;
        state->truncated_history = true;
    }
}

static void add_bag(LcState *state, char **fields) {
    int minute;
    size_t bytes;
    char *copy;
    if (!valid_minute_key(fields[1], &minute) || state->bag_count == LC_MAX_BAGS) {
        return;
    }
    if (fields[2][0] != '\0' && !lc_validate_csv_ids(fields[2])) {
        return;
    }
    bytes = strlen(fields[2]) + 1;
    if (bytes > LC_MAX_BAG_BYTES - state->bag_bytes) {
        return;
    }
    copy = malloc(bytes);
    if (copy == NULL) {
        return;
    }
    memcpy(copy, fields[2], bytes);
    state->bags[state->bag_count].minute = minute;
    (void)snprintf(
        state->bags[state->bag_count].minute_key,
        sizeof(state->bags[state->bag_count].minute_key),
        "%s",
        fields[1]
    );
    state->bags[state->bag_count].remaining = copy;
    state->bag_count++;
    state->bag_bytes += bytes;
}

int lc_state_load(const char *path, LcState *state, char *error) {
    FILE *stream;
    struct stat details;
    char *line;
    int read_status;
    bool version_seen = false;
    bool count_seen = false;
    bool count_invalid = false;
    uint64_t v1_history_count = 0;
    (void)memset(state, 0, sizeof(*state));
    state->version = 1;
    stream = fopen(path, "r");
    if (stream == NULL) {
        if (errno == ENOENT) {
            return 0;
        }
        lc_set_error(error, "state cannot be read: %s", strerror(errno));
        return 37;
    }
    state->existed = true;
    if (fstat(fileno(stream), &details) != 0 || details.st_size < 0 ||
        (uint64_t)details.st_size > LC_MAX_STATE_BYTES) {
        (void)fclose(stream);
        lc_set_error(error, "state file exceeds its bounded size");
        return 37;
    }
    line = malloc(LC_LINE_CAP);
    if (line == NULL) {
        (void)fclose(stream);
        lc_set_error(error, "state allocation failed");
        return 37;
    }
    while ((read_status = lc_read_line(stream, line, LC_LINE_CAP)) > 0) {
        char *fields[8];
        int count = lc_split_tabs(line, fields, 8);
        if (count < 0) {
            continue;
        }
        if (count == 2 && strcmp(fields[0], "VERSION") == 0 && !version_seen) {
            uint64_t version;
            if (!lc_parse_u64(fields[1], &version) || version > (uint64_t)INT32_MAX) {
                free(line);
                (void)fclose(stream);
                lc_set_error(error, "state version is incompatible");
                return 37;
            }
            state->version = (int)version;
            version_seen = true;
        } else if (count == 2 && strcmp(fields[0], "DISPLAY_COUNT") == 0 && !count_seen) {
            count_seen = true;
            count_invalid = !lc_parse_u64(fields[1], &state->display_count);
        } else if (count == 7 && strcmp(fields[0], "H") == 0) {
            v1_history_count++;
            add_history(state, fields);
        } else if (count == 3 && strcmp(fields[0], "BAG") == 0) {
            add_bag(state, fields);
        }
    }
    free(line);
    (void)fclose(stream);
    if (read_status < 0) {
        lc_state_free(state);
        lc_set_error(error, "state contains an overlong or unreadable row");
        return 37;
    }
    if (state->version != 1 && state->version != 2) {
        lc_state_free(state);
        lc_set_error(error, "state version is incompatible");
        return 37;
    }
    if (state->version == 1) {
        state->display_count = v1_history_count;
    } else if (count_invalid) {
        lc_state_free(state);
        lc_set_error(error, "display counter is invalid");
        return 38;
    }
    return 0;
}

const char *lc_state_bag(const LcState *state, int minute) {
    size_t index;
    for (index = 0; index < state->bag_count; index++) {
        if (state->bags[index].minute == minute) {
            return state->bags[index].remaining;
        }
    }
    return NULL;
}

void lc_state_apply_history(const LcState *state, LcPool *pool) {
    size_t candidate_index;
    size_t history_index;
    for (candidate_index = 0; candidate_index < pool->count; candidate_index++) {
        LcCandidate *candidate = &pool->items[candidate_index];
        if (!candidate->have_metadata) {
            continue;
        }
        for (history_index = 0; history_index < state->history_count; history_index++) {
            size_t slot = (state->history_start + history_index) % LC_MAX_HISTORY;
            const LcHistory *history = &state->history[slot];
            if (history->quote_id == candidate->id &&
                (!candidate->have_quote_history || history->epoch > candidate->last_quote)) {
                candidate->last_quote = history->epoch;
                candidate->have_quote_history = true;
            }
            if (strcmp(candidate->book, "-") != 0 &&
                strcmp(history->book, candidate->book) == 0 &&
                (!candidate->have_book_history || history->epoch > candidate->last_book)) {
                candidate->last_book = history->epoch;
                candidate->have_book_history = true;
            }
            if (strcmp(candidate->author, "-") != 0 &&
                strcmp(history->author, candidate->author) == 0 &&
                (!candidate->have_author_history || history->epoch > candidate->last_author)) {
                candidate->last_author = history->epoch;
                candidate->have_author_history = true;
            }
        }
    }
}

static int flush_state(FILE *stream, int fd, char *error) {
    if (fflush(stream) != 0 || fsync(fd) != 0) {
        lc_set_error(error, "state flush failed: %s", strerror(errno));
        return -1;
    }
    return 0;
}

static int sync_directory(const char *directory, char *error) {
    int fd = open(directory, O_RDONLY);
    int result;
    if (fd < 0) {
        lc_set_error(error, "state directory cannot be opened: %s", strerror(errno));
        return -1;
    }
    result = fsync(fd);
    if (result != 0) {
        lc_set_error(error, "state directory flush failed: %s", strerror(errno));
    }
    (void)close(fd);
    return result;
}

int lc_state_commit(
    const char *path,
    const LcState *state,
    const LcSnapshot *snapshot,
    const LcCandidate *selected,
    const LcPool *remaining,
    uint64_t next_display_count,
    const char *release_version,
    char *error
) {
    char directory[LC_PATH_CAP];
    char temporary[LC_PATH_CAP];
    int fd;
    FILE *stream;
    size_t index;
    size_t history_begin = state->history_count == LC_MAX_HISTORY ? 1 : 0;
    int64_t cutoff = snapshot->epoch - INT64_C(90000);
    bool first = true;
    int temporary_length;
    if (!lc_parent_dir(directory, sizeof(directory), path)) {
        lc_set_error(error, "state temporary path is too long");
        return 43;
    }
    temporary_length =
        snprintf(temporary, sizeof(temporary), "%s/state.%ld.tmp", directory, (long)getpid());
    if (temporary_length < 0 || (size_t)temporary_length >= sizeof(temporary)) {
        lc_set_error(error, "state temporary path is too long");
        return 43;
    }
    fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL, 0644);
    if (fd < 0) {
        lc_set_error(error, "state temporary file cannot be created: %s", strerror(errno));
        return 43;
    }
    stream = fdopen(fd, "w");
    if (stream == NULL) {
        (void)close(fd);
        (void)unlink(temporary);
        lc_set_error(error, "state temporary stream cannot be opened");
        return 43;
    }
    if (fprintf(stream, "VERSION\t2\nDISPLAY_COUNT\t%llu\n", (unsigned long long)next_display_count) < 0) {
        goto write_failed;
    }
    for (index = history_begin; index < state->history_count; index++) {
        size_t slot = (state->history_start + index) % LC_MAX_HISTORY;
        const LcHistory *history = &state->history[slot];
        if (history->epoch >= cutoff &&
            fprintf(
                stream,
                "H\t%s\t%s\t%s\t%s\t%s\t%s\n",
                history->epoch_text,
                history->iso_date,
                history->minute_key,
                history->quote_id_text,
                history->book,
                history->author
            ) < 0) {
            goto write_failed;
        }
    }
    for (index = 0; index < state->bag_count; index++) {
        const LcBag *bag = &state->bags[index];
        if (bag->minute != snapshot->minute &&
            fprintf(stream, "BAG\t%s\t%s\n", bag->minute_key, bag->remaining) < 0) {
            goto write_failed;
        }
    }
    if (fprintf(
            stream,
            "H\t%s\t%s\t%s\t%s\t%s\t%s\nBAG\t%s\t",
            snapshot->epoch_text,
            snapshot->iso_date,
            snapshot->minute_key,
            selected->id_text,
            selected->book,
            selected->author,
            snapshot->minute_key
        ) < 0) {
        goto write_failed;
    }
    for (index = 0; index < remaining->count; index++) {
        if (remaining->items[index].id == selected->id) {
            continue;
        }
        if (fprintf(stream, "%s%s", first ? "" : ",", remaining->items[index].id_text) < 0) {
            goto write_failed;
        }
        first = false;
    }
    if (fprintf(
            stream,
            "\nLAST\t%s\t%s\t%s\t%s\t%s\n",
            snapshot->iso_date,
            snapshot->minute_key,
            snapshot->epoch_text,
            selected->id_text,
            release_version
        ) < 0 ||
        flush_state(stream, fd, error) != 0) {
        goto write_failed;
    }
    if (fclose(stream) != 0) {
        stream = NULL;
        lc_set_error(error, "state close failed: %s", strerror(errno));
        goto close_failed;
    }
    stream = NULL;
    if (rename(temporary, path) != 0) {
        lc_set_error(error, "atomic history activation failed after display: %s", strerror(errno));
        goto close_failed;
    }
    if (sync_directory(directory, error) != 0) {
        return 43;
    }
    return 0;

write_failed:
    if (error[0] == '\0') {
        lc_set_error(error, "state write failed: %s", strerror(errno));
    }
    if (stream != NULL) {
        (void)fclose(stream);
        stream = NULL;
    }
close_failed:
    (void)unlink(temporary);
    return 43;
}

void lc_state_free(LcState *state) {
    size_t index;
    for (index = 0; index < state->bag_count; index++) {
        free(state->bags[index].remaining);
        state->bags[index].remaining = NULL;
    }
    state->bag_count = 0;
    state->history_count = 0;
    state->history_start = 0;
    state->bag_bytes = 0;
}
