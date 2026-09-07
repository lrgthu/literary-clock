#define _POSIX_C_SOURCE 200809L

#include "litclock.h"

#include <ctype.h>
#include <string.h>
#include <time.h>

static bool digits(const char *text, size_t length) {
    size_t index;
    if (strlen(text) != length) {
        return false;
    }
    for (index = 0; index < length; index++) {
        if (!isdigit((unsigned char)text[index])) {
            return false;
        }
    }
    return true;
}

static bool valid_calendar_fields(const char *iso_date, const char *date_key) {
    int month;
    int day;
    if (strlen(iso_date) != 10 || iso_date[4] != '-' || iso_date[7] != '-' ||
        !isdigit((unsigned char)iso_date[0]) || !isdigit((unsigned char)iso_date[1]) ||
        !isdigit((unsigned char)iso_date[2]) || !isdigit((unsigned char)iso_date[3]) ||
        !isdigit((unsigned char)iso_date[5]) || !isdigit((unsigned char)iso_date[6]) ||
        !isdigit((unsigned char)iso_date[8]) || !isdigit((unsigned char)iso_date[9]) ||
        strlen(date_key) != 7 || date_key[1] != '-' || date_key[4] != '-' ||
        date_key[0] < '0' || date_key[0] > '6' ||
        !isdigit((unsigned char)date_key[2]) || !isdigit((unsigned char)date_key[3]) ||
        !isdigit((unsigned char)date_key[5]) || !isdigit((unsigned char)date_key[6])) {
        return false;
    }
    month = (iso_date[5] - '0') * 10 + iso_date[6] - '0';
    day = (iso_date[8] - '0') * 10 + iso_date[9] - '0';
    return month >= 1 && month <= 12 && day >= 1 && day <= 31 &&
           date_key[2] == iso_date[5] && date_key[3] == iso_date[6] &&
           date_key[5] == iso_date[8] && date_key[6] == iso_date[9];
}

int lc_snapshot_capture(const char *injected, LcSnapshot *snapshot, char *error) {
    char value[96];
    char *fields[4];
    int field_count;
    int64_t epoch;
    int hour;
    int minute;
    if (injected != NULL && *injected != '\0') {
        if (strlen(injected) >= sizeof(value)) {
            lc_set_error(error, "timestamp snapshot is malformed");
            return 33;
        }
        (void)snprintf(value, sizeof(value), "%s", injected);
    } else {
        time_t now = time(NULL);
        struct tm local;
        if (now == (time_t)-1 || localtime_r(&now, &local) == NULL) {
            lc_set_error(error, "local timestamp capture failed");
            return 32;
        }
        if (snprintf(
                value,
                sizeof(value),
                "%lld|%04d-%02d-%02d|%02d%02d|%d-%02d-%02d",
                (long long)now,
                local.tm_year + 1900,
                local.tm_mon + 1,
                local.tm_mday,
                local.tm_hour,
                local.tm_min,
                local.tm_wday,
                local.tm_mon + 1,
                local.tm_mday
            ) < 0) {
            lc_set_error(error, "local timestamp capture failed");
            return 32;
        }
    }
    {
        char *cursor;
        for (cursor = value; *cursor != '\0'; cursor++) {
            if (*cursor == '|') {
                *cursor = '\t';
            }
        }
    }
    field_count = lc_split_tabs(value, fields, 4);
    if (field_count != 4) {
        lc_set_error(error, "timestamp snapshot is malformed");
        return 33;
    }
    {
        size_t field_index;
        for (field_index = 0; field_index < 4; field_index++) {
            const char *cursor;
            for (cursor = fields[field_index]; *cursor != '\0'; cursor++) {
                if (!isdigit((unsigned char)*cursor) && *cursor != '-' && *cursor != ':') {
                    lc_set_error(error, "timestamp snapshot contains invalid characters");
                    return 34;
                }
            }
        }
    }
    if (!lc_parse_i64(fields[0], &epoch) || epoch < 0) {
        lc_set_error(error, "timestamp snapshot contains invalid characters");
        return 34;
    }
    if (strlen(fields[2]) != 4) {
        lc_set_error(error, "timestamp minute is malformed");
        return 35;
    }
    if (!digits(fields[2], 4) || !valid_calendar_fields(fields[1], fields[3])) {
        lc_set_error(error, "timestamp snapshot is malformed");
        return 33;
    }
    hour = (fields[2][0] - '0') * 10 + fields[2][1] - '0';
    minute = (fields[2][2] - '0') * 10 + fields[2][3] - '0';
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
        lc_set_error(error, "timestamp minute is outside range");
        return 36;
    }
    snapshot->epoch = epoch;
    snapshot->minute = hour * 60 + minute;
    (void)snprintf(snapshot->epoch_text, sizeof(snapshot->epoch_text), "%s", fields[0]);
    (void)snprintf(snapshot->iso_date, sizeof(snapshot->iso_date), "%s", fields[1]);
    (void)snprintf(snapshot->hhmm, sizeof(snapshot->hhmm), "%s", fields[2]);
    (void)snprintf(snapshot->minute_key, sizeof(snapshot->minute_key), "%04d", snapshot->minute);
    (void)snprintf(snapshot->date_key, sizeof(snapshot->date_key), "%s", fields[3]);
    return 0;
}
