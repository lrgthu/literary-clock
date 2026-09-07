#ifndef LITCLOCK_H
#define LITCLOCK_H

#include <stdbool.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define LC_RUNTIME_VERSION "0.1.0-scaffold"
#define LC_RELEASE_FORMAT_VERSION 1
#define LC_MANIFEST_FORMAT_VERSION 1
#define LC_BUNDLE_RUNTIME_VERSION 2

#define LC_PATH_CAP 1024
#define LC_FRAME_CAP 512
#define LC_IDENTITY_CAP 128
#define LC_ERROR_CAP 256
#define LC_LINE_CAP 131072
#define LC_MAX_CANDIDATES 4096
#define LC_MAX_HISTORY 2048
#define LC_MAX_BAGS 1440
#define LC_MAX_STATE_BYTES (4U * 1024U * 1024U)
#define LC_MAX_BAG_BYTES (2U * 1024U * 1024U)

typedef struct {
    int64_t epoch;
    char epoch_text[24];
    char iso_date[11];
    char hhmm[5];
    char minute_key[5];
    char date_key[16];
    int minute;
} LcSnapshot;

typedef struct {
    uint64_t id;
    char id_text[21];
    uint32_t shuffle_key;
    char frame[LC_FRAME_CAP];
    char book[LC_IDENTITY_CAP];
    char author[LC_IDENTITY_CAP];
    int64_t last_quote;
    int64_t last_book;
    int64_t last_author;
    bool have_metadata;
    bool have_quote_history;
    bool have_book_history;
    bool have_author_history;
} LcCandidate;

typedef struct {
    LcCandidate *items;
    size_t count;
} LcPool;

typedef struct {
    char frame[LC_FRAME_CAP];
    int x;
    int y;
} LcDateAsset;

typedef struct {
    int64_t epoch;
    char epoch_text[24];
    char iso_date[11];
    char minute_key[5];
    uint64_t quote_id;
    char quote_id_text[21];
    char book[LC_IDENTITY_CAP];
    char author[LC_IDENTITY_CAP];
} LcHistory;

typedef struct {
    int minute;
    char minute_key[5];
    char *remaining;
} LcBag;

typedef struct {
    bool present;
    char iso_date[11];
    char minute_key[5];
    char release_version[128];
} LcLast;

typedef struct {
    int version;
    uint64_t display_count;
    LcHistory history[LC_MAX_HISTORY];
    size_t history_count;
    size_t history_start;
    LcBag bags[LC_MAX_BAGS];
    size_t bag_count;
    size_t bag_bytes;
    bool existed;
    bool truncated_history;
} LcState;

typedef struct {
    char release_root[LC_PATH_CAP];
    char state_path[LC_PATH_CAP];
    char log_path[LC_PATH_CAP];
    char display_helper[LC_PATH_CAP];
    char snapshot_text[96];
    char scheduler_source[64];
    uint64_t full_refresh_interval;
    unsigned int delay_before_display;
    unsigned int delay_before_commit;
    int fake_display_status;
    bool dry_run;
} LcOptions;

typedef struct {
    int fd;
    char path[LC_PATH_CAP];
} LcLock;

extern volatile sig_atomic_t lc_signal_received;

void lc_set_error(char *error, const char *format, ...);
bool lc_parse_u64(const char *text, uint64_t *value);
bool lc_parse_i64(const char *text, int64_t *value);
bool lc_safe_relative(const char *path);
bool lc_join_path(char *output, size_t capacity, const char *root, const char *relative);
bool lc_parent_dir(char *output, size_t capacity, const char *path);
int lc_mkdir_p(const char *path, char *error);
uint64_t lc_monotonic_ms(void);
uint32_t lc_posix_cksum(const unsigned char *data, size_t length);
int lc_split_tabs(char *line, char **fields, size_t capacity);
int lc_read_line(FILE *stream, char *line, size_t capacity);
bool lc_validate_csv_ids(const char *csv);
int lc_parse_csv_pool(const char *csv, LcPool *pool, char *error);
void lc_pool_free(LcPool *pool);

int lc_snapshot_capture(const char *injected, LcSnapshot *snapshot, char *error);

int lc_validate_release(
    const char *release_root, char *release_version, size_t capacity, char *error
);
int lc_manifest_minute_pool(
    const char *release_root, int minute, LcPool *pool, char *error
);
int lc_manifest_quote_metadata(const char *release_root, LcPool *pool, char *error);
int lc_manifest_date(
    const char *release_root, const char *key, LcDateAsset *asset, char *error
);

int lc_state_peek_last(const char *path, LcLast *last, char *error);
int lc_state_load(const char *path, LcState *state, char *error);
const char *lc_state_bag(const LcState *state, int minute);
void lc_state_apply_history(const LcState *state, LcPool *pool);
int lc_state_commit(
    const char *path,
    const LcState *state,
    const LcSnapshot *snapshot,
    const LcCandidate *selected,
    const LcPool *remaining,
    uint64_t next_display_count,
    const char *release_version,
    char *error
);
void lc_state_free(LcState *state);

void lc_shuffle_pool(LcPool *pool, const char *release_version, const LcSnapshot *snapshot);
LcCandidate *lc_select(LcPool *pool, const LcSnapshot *snapshot, int *relaxation_level);
const char *lc_refresh_mode(
    uint64_t display_count,
    uint64_t next_display_count,
    uint64_t interval,
    const char *previous_date,
    const char *current_date
);

int lc_validate_png(const char *path);
int lc_display_frame(
    const LcOptions *options,
    const char *frame,
    const char *date_frame,
    int date_x,
    int date_y,
    const char *refresh_mode,
    char *error
);

int lc_lock_acquire(const char *state_path, LcLock *lock, char *error);
void lc_lock_release(LcLock *lock);
int lc_sleep_interruptible(unsigned int seconds);
int lc_install_signal_handlers(char *error);

#endif
