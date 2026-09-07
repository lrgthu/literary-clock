#define _POSIX_C_SOURCE 200809L

#include "litclock.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

void lc_set_error(char *error, const char *format, ...) {
    va_list arguments;
    va_start(arguments, format);
    (void)vsnprintf(error, LC_ERROR_CAP, format, arguments);
    va_end(arguments);
}

bool lc_parse_u64(const char *text, uint64_t *value) {
    char *end = NULL;
    unsigned long long parsed;
    if (text == NULL || *text == '\0' || (*text == '0' && text[1] != '\0')) {
        return false;
    }
    errno = 0;
    parsed = strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') {
        return false;
    }
    *value = (uint64_t)parsed;
    return true;
}

bool lc_parse_i64(const char *text, int64_t *value) {
    char *end = NULL;
    long long parsed;
    if (text == NULL || *text == '\0') {
        return false;
    }
    errno = 0;
    parsed = strtoll(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') {
        return false;
    }
    *value = (int64_t)parsed;
    return true;
}

bool lc_safe_relative(const char *path) {
    const char *part;
    if (path == NULL || *path == '\0' || path[0] == '/') {
        return false;
    }
    part = path;
    while (*part != '\0') {
        const char *slash = strchr(part, '/');
        size_t length = slash == NULL ? strlen(part) : (size_t)(slash - part);
        if (length == 0 || (length == 1 && part[0] == '.') ||
            (length == 2 && part[0] == '.' && part[1] == '.')) {
            return false;
        }
        if (slash == NULL) {
            break;
        }
        part = slash + 1;
    }
    return true;
}

bool lc_join_path(char *output, size_t capacity, const char *root, const char *relative) {
    int written;
    if (!lc_safe_relative(relative)) {
        return false;
    }
    written = snprintf(output, capacity, "%s/%s", root, relative);
    return written >= 0 && (size_t)written < capacity;
}

bool lc_parent_dir(char *output, size_t capacity, const char *path) {
    char *slash;
    size_t length = strlen(path);
    if (length == 0 || length >= capacity) {
        return false;
    }
    memcpy(output, path, length + 1);
    slash = strrchr(output, '/');
    if (slash == NULL) {
        return snprintf(output, capacity, ".") > 0;
    }
    if (slash == output) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }
    return true;
}

int lc_mkdir_p(const char *path, char *error) {
    char copy[LC_PATH_CAP];
    char *cursor;
    size_t length = strlen(path);
    if (length == 0 || length >= sizeof(copy)) {
        lc_set_error(error, "state directory path is too long");
        return -1;
    }
    memcpy(copy, path, length + 1);
    for (cursor = copy + 1; *cursor != '\0'; cursor++) {
        if (*cursor != '/') {
            continue;
        }
        *cursor = '\0';
        if (mkdir(copy, 0755) != 0 && errno != EEXIST) {
            lc_set_error(error, "cannot create state directory: %s", strerror(errno));
            return -1;
        }
        *cursor = '/';
    }
    if (mkdir(copy, 0755) != 0 && errno != EEXIST) {
        lc_set_error(error, "cannot create state directory: %s", strerror(errno));
        return -1;
    }
    return 0;
}

uint64_t lc_monotonic_ms(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) {
        return 0;
    }
    return (uint64_t)value.tv_sec * UINT64_C(1000) + (uint64_t)value.tv_nsec / UINT64_C(1000000);
}

uint32_t lc_posix_cksum(const unsigned char *data, size_t length) {
    uint32_t crc = 0;
    size_t index;
    uint64_t remaining = (uint64_t)length;
    for (index = 0; index < length; index++) {
        unsigned int bit;
        crc ^= (uint32_t)data[index] << 24;
        for (bit = 0; bit < 8; bit++) {
            crc = (crc & UINT32_C(0x80000000)) != 0
                      ? (crc << 1) ^ UINT32_C(0x04c11db7)
                      : crc << 1;
        }
    }
    while (remaining != 0) {
        unsigned int bit;
        crc ^= (uint32_t)(remaining & UINT64_C(0xff)) << 24;
        for (bit = 0; bit < 8; bit++) {
            crc = (crc & UINT32_C(0x80000000)) != 0
                      ? (crc << 1) ^ UINT32_C(0x04c11db7)
                      : crc << 1;
        }
        remaining >>= 8;
    }
    return ~crc;
}

int lc_split_tabs(char *line, char **fields, size_t capacity) {
    size_t count = 0;
    char *cursor = line;
    if (capacity == 0) {
        return -1;
    }
    fields[count++] = cursor;
    while (*cursor != '\0') {
        if (*cursor == '\t') {
            *cursor = '\0';
            if (count == capacity) {
                return -1;
            }
            fields[count++] = cursor + 1;
        }
        cursor++;
    }
    return (int)count;
}

int lc_read_line(FILE *stream, char *line, size_t capacity) {
    size_t length;
    int ch;
    if (fgets(line, (int)capacity, stream) == NULL) {
        return feof(stream) != 0 ? 0 : -1;
    }
    length = strlen(line);
    if (length > 0 && line[length - 1] == '\n') {
        line[length - 1] = '\0';
        return 1;
    }
    if (feof(stream) != 0) {
        return 1;
    }
    do {
        ch = fgetc(stream);
    } while (ch != '\n' && ch != EOF);
    return -2;
}

static bool csv_id_count(const char *csv, size_t *result) {
    const char *cursor = csv;
    size_t count = 0;
    if (csv == NULL || *csv == '\0') {
        return false;
    }
    while (*cursor != '\0') {
        const char *comma = strchr(cursor, ',');
        size_t length = comma == NULL ? strlen(cursor) : (size_t)(comma - cursor);
        char id_text[21];
        uint64_t id = 0;
        if (count == LC_MAX_CANDIDATES || length == 0 || length >= sizeof(id_text)) {
            return false;
        }
        memcpy(id_text, cursor, length);
        id_text[length] = '\0';
        if (!lc_parse_u64(id_text, &id)) {
            return false;
        }
        count++;
        if (comma == NULL) {
            break;
        }
        cursor = comma + 1;
        if (*cursor == '\0') {
            return false;
        }
    }
    *result = count;
    return true;
}

bool lc_validate_csv_ids(const char *csv) {
    size_t count;
    return csv_id_count(csv, &count);
}

int lc_parse_csv_pool(const char *csv, LcPool *pool, char *error) {
    const char *cursor = csv;
    size_t count;
    size_t index = 0;
    LcCandidate *items;
    if (!csv_id_count(csv, &count)) {
        lc_set_error(error, "candidate list exceeds bounds or contains an invalid ID");
        return -1;
    }
    items = calloc(count, sizeof(*items));
    if (items == NULL) {
        lc_set_error(error, "candidate allocation failed");
        return -1;
    }
    while (*cursor != '\0') {
        const char *comma = strchr(cursor, ',');
        size_t length = comma == NULL ? strlen(cursor) : (size_t)(comma - cursor);
        char id_text[21];
        uint64_t id;
        memcpy(id_text, cursor, length);
        id_text[length] = '\0';
        if (!lc_parse_u64(id_text, &id)) {
            free(items);
            lc_set_error(error, "candidate list changed during parsing");
            return -1;
        }
        items[index].id = id;
        memcpy(items[index].id_text, id_text, length + 1);
        index++;
        if (comma == NULL) {
            break;
        }
        cursor = comma + 1;
    }
    pool->items = items;
    pool->count = count;
    return 0;
}

void lc_pool_free(LcPool *pool) {
    free(pool->items);
    pool->items = NULL;
    pool->count = 0;
}
