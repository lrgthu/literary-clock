#define _POSIX_C_SOURCE 200809L

#include "litclock.h"

#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static bool metadata_value(
    const char *path, const char *key, char *value, size_t capacity, char *error
) {
    FILE *stream = fopen(path, "r");
    char line[4096];
    bool found = false;
    if (stream == NULL) {
        lc_set_error(error, "metadata is missing: %s", path);
        return false;
    }
    while (fgets(line, sizeof(line), stream) != NULL) {
        char *tab;
        size_t length = strlen(line);
        if (length > 0 && line[length - 1] == '\n') {
            line[length - 1] = '\0';
        } else if (feof(stream) == 0) {
            lc_set_error(error, "metadata row is too long: %s", path);
            (void)fclose(stream);
            return false;
        }
        tab = strchr(line, '\t');
        if (tab == NULL || strchr(tab + 1, '\t') != NULL) {
            lc_set_error(error, "metadata row is malformed: %s", path);
            (void)fclose(stream);
            return false;
        }
        *tab = '\0';
        if (strcmp(line, key) == 0 && !found) {
            if (strlen(tab + 1) >= capacity) {
                lc_set_error(error, "metadata value is too long: %s", key);
                (void)fclose(stream);
                return false;
            }
            (void)snprintf(value, capacity, "%s", tab + 1);
            found = true;
        }
    }
    if (ferror(stream) != 0) {
        lc_set_error(error, "cannot read metadata: %s", strerror(errno));
        found = false;
    }
    (void)fclose(stream);
    return found;
}

static bool required_file(const char *root, const char *relative) {
    char path[LC_PATH_CAP];
    return lc_join_path(path, sizeof(path), root, relative) && access(path, R_OK) == 0;
}

static bool safe_release_version(const char *version) {
    return version[0] != '\0' && strchr(version, '/') == NULL && strstr(version, "..") == NULL;
}

int lc_validate_release(
    const char *release_root, char *release_version, size_t capacity, char *error
) {
    char release_meta[LC_PATH_CAP];
    char bundle_meta[LC_PATH_CAP];
    char value[128];
    if (!lc_join_path(release_meta, sizeof(release_meta), release_root, "release.meta") ||
        !lc_join_path(bundle_meta, sizeof(bundle_meta), release_root, "bundle/bundle.meta")) {
        lc_set_error(error, "release path is too long");
        return 24;
    }
    if (access(release_meta, R_OK) != 0) {
        lc_set_error(error, "release metadata is missing");
        return 24;
    }
    if (!metadata_value(release_meta, "release_format_version", value, sizeof(value), error) ||
        strcmp(value, "1") != 0) {
        lc_set_error(error, "release format is incompatible");
        return 25;
    }
    if (!metadata_value(release_meta, "runtime_version", value, sizeof(value), error) ||
        strcmp(value, "2") != 0) {
        lc_set_error(error, "runtime version is incompatible");
        return 26;
    }
    if (!metadata_value(release_meta, "release_version", value, sizeof(value), error) ||
        !safe_release_version(value) || strlen(value) >= capacity) {
        lc_set_error(error, "release version is invalid");
        return 27;
    }
    (void)snprintf(release_version, capacity, "%s", value);
    {
        static const char *const required[] = {
            "bundle/bundle.meta", "bundle/minutes.tsv", "bundle/quotes.tsv", "bundle/dates.tsv"
        };
        size_t index;
        for (index = 0; index < sizeof(required) / sizeof(required[0]); index++) {
            if (!required_file(release_root, required[index])) {
                lc_set_error(error, "bundle index is missing: %s", required[index] + 7);
                return 28;
            }
        }
    }
    if (!metadata_value(bundle_meta, "format_version", value, sizeof(value), error) ||
        strcmp(value, "1") != 0) {
        lc_set_error(error, "manifest format is incompatible");
        return 29;
    }
    if (!metadata_value(bundle_meta, "release_format_version", value, sizeof(value), error) ||
        strcmp(value, "1") != 0) {
        lc_set_error(error, "release/bundle format mismatch");
        return 30;
    }
    if (!metadata_value(bundle_meta, "runtime_version", value, sizeof(value), error) ||
        strcmp(value, "2") != 0) {
        lc_set_error(error, "release/bundle runtime mismatch");
        return 31;
    }
    return 0;
}

static bool four_digit_minute(const char *text, int *minute) {
    size_t index;
    int value = 0;
    if (strlen(text) != 4) {
        return false;
    }
    for (index = 0; index < 4; index++) {
        if (!isdigit((unsigned char)text[index])) {
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

int lc_manifest_minute_pool(
    const char *release_root, int minute, LcPool *pool, char *error
) {
    char path[LC_PATH_CAP];
    char *line;
    FILE *stream;
    int read_status;
    bool found = false;
    if (!lc_join_path(path, sizeof(path), release_root, "bundle/minutes.tsv")) {
        lc_set_error(error, "minutes manifest path is too long");
        return 39;
    }
    stream = fopen(path, "r");
    line = malloc(LC_LINE_CAP);
    if (stream == NULL || line == NULL) {
        lc_set_error(error, "cannot open minutes manifest");
        free(line);
        if (stream != NULL) {
            (void)fclose(stream);
        }
        return 39;
    }
    while ((read_status = lc_read_line(stream, line, LC_LINE_CAP)) > 0) {
        char *fields[2];
        int row_minute;
        if (lc_split_tabs(line, fields, 2) != 2 || !four_digit_minute(fields[0], &row_minute) ||
            !lc_validate_csv_ids(fields[1])) {
            lc_set_error(error, "minutes manifest contains a malformed row");
            read_status = -1;
            break;
        }
        if (row_minute == minute) {
            if (found) {
                lc_set_error(error, "minutes manifest contains a duplicate active row");
                read_status = -1;
                break;
            }
            if (lc_parse_csv_pool(fields[1], pool, error) != 0) {
                read_status = -1;
                break;
            }
            found = true;
        }
    }
    if (read_status < 0 || ferror(stream) != 0) {
        lc_pool_free(pool);
        if (error[0] == '\0') {
            lc_set_error(error, "minutes manifest cannot be read safely");
        }
        found = false;
    }
    free(line);
    (void)fclose(stream);
    if (!found) {
        if (error[0] == '\0') {
            lc_set_error(error, "active bundle has no candidates for requested minute");
        }
        return 39;
    }
    return 0;
}

static bool hex_digest(const char *text) {
    size_t index;
    if (strlen(text) != 64) {
        return false;
    }
    for (index = 0; index < 64; index++) {
        if (!isxdigit((unsigned char)text[index])) {
            return false;
        }
    }
    return true;
}

static bool positive_decimal(const char *text) {
    uint64_t value;
    return lc_parse_u64(text, &value) && value > 0;
}

int lc_manifest_quote_metadata(const char *release_root, LcPool *pool, char *error) {
    char path[LC_PATH_CAP];
    char *line;
    FILE *stream;
    int read_status;
    size_t metadata_count = 0;
    if (!lc_join_path(path, sizeof(path), release_root, "bundle/quotes.tsv")) {
        lc_set_error(error, "quote manifest path is too long");
        return 41;
    }
    stream = fopen(path, "r");
    line = malloc(LC_LINE_CAP);
    if (stream == NULL || line == NULL) {
        lc_set_error(error, "cannot open quote manifest");
        free(line);
        if (stream != NULL) {
            (void)fclose(stream);
        }
        return 41;
    }
    while ((read_status = lc_read_line(stream, line, LC_LINE_CAP)) > 0) {
        char *fields[8];
        uint64_t quote_id;
        size_t index;
        if (lc_split_tabs(line, fields, 8) != 8 || !lc_parse_u64(fields[0], &quote_id) ||
            !lc_safe_relative(fields[1]) || strlen(fields[1]) >= LC_FRAME_CAP ||
            fields[2][0] == '\0' || strlen(fields[2]) >= LC_IDENTITY_CAP ||
            fields[3][0] == '\0' || strlen(fields[3]) >= LC_IDENTITY_CAP ||
            !hex_digest(fields[4]) || !positive_decimal(fields[5]) ||
            !positive_decimal(fields[6]) || !positive_decimal(fields[7])) {
            lc_set_error(error, "quote manifest contains a malformed row");
            read_status = -1;
            break;
        }
        for (index = 0; index < pool->count; index++) {
            LcCandidate *candidate = &pool->items[index];
            if (candidate->id != quote_id) {
                continue;
            }
            if (candidate->have_metadata) {
                lc_set_error(error, "quote manifest contains a duplicate candidate row");
                read_status = -1;
                break;
            }
            (void)snprintf(candidate->frame, sizeof(candidate->frame), "%s", fields[1]);
            (void)snprintf(candidate->book, sizeof(candidate->book), "%s", fields[2]);
            (void)snprintf(candidate->author, sizeof(candidate->author), "%s", fields[3]);
            candidate->have_metadata = true;
            metadata_count++;
        }
        if (read_status < 0) {
            break;
        }
    }
    free(line);
    (void)fclose(stream);
    if (read_status < 0 || metadata_count == 0) {
        if (error[0] == '\0') {
            lc_set_error(error, "no selectable quote metadata remains");
        }
        return 41;
    }
    return 0;
}

static bool parse_nonnegative_int(const char *text, int *value) {
    uint64_t parsed;
    if (!lc_parse_u64(text, &parsed) || parsed > (uint64_t)INT_MAX) {
        return false;
    }
    *value = (int)parsed;
    return true;
}

int lc_manifest_date(
    const char *release_root, const char *key, LcDateAsset *asset, char *error
) {
    char path[LC_PATH_CAP];
    char *line;
    FILE *stream;
    int read_status;
    bool found = false;
    if (!lc_join_path(path, sizeof(path), release_root, "bundle/dates.tsv")) {
        lc_set_error(error, "date manifest path is too long");
        return 42;
    }
    stream = fopen(path, "r");
    line = malloc(LC_LINE_CAP);
    if (stream == NULL || line == NULL) {
        lc_set_error(error, "cannot open date manifest");
        free(line);
        if (stream != NULL) {
            (void)fclose(stream);
        }
        return 42;
    }
    while ((read_status = lc_read_line(stream, line, LC_LINE_CAP)) > 0) {
        char *fields[8];
        int x;
        int y;
        if (lc_split_tabs(line, fields, 8) != 8 || fields[0][0] == '\0' ||
            !lc_safe_relative(fields[1]) || strlen(fields[1]) >= LC_FRAME_CAP ||
            !parse_nonnegative_int(fields[2], &x) || !parse_nonnegative_int(fields[3], &y) ||
            !hex_digest(fields[4]) || !positive_decimal(fields[5]) ||
            !positive_decimal(fields[6]) || !positive_decimal(fields[7])) {
            lc_set_error(error, "date manifest contains a malformed row");
            read_status = -1;
            break;
        }
        if (strcmp(fields[0], key) == 0) {
            if (found) {
                lc_set_error(error, "date manifest contains a duplicate active row");
                read_status = -1;
                break;
            }
            (void)snprintf(asset->frame, sizeof(asset->frame), "%s", fields[1]);
            asset->x = x;
            asset->y = y;
            found = true;
        }
    }
    free(line);
    (void)fclose(stream);
    if (read_status < 0 || !found) {
        if (error[0] == '\0') {
            lc_set_error(error, "bundle has no date overlay for requested key");
        }
        return 42;
    }
    return 0;
}
