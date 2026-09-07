#include "litclock.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static void test_cksum(void) {
    static const unsigned char input[] = "123456789";
    assert(lc_posix_cksum(input, sizeof(input) - 1) == UINT32_C(930766865));
    assert(lc_posix_cksum((const unsigned char *)"", 0) == UINT32_MAX);
}

static void test_paths(void) {
    assert(lc_safe_relative("frames/q1.png"));
    assert(!lc_safe_relative("/frames/q1.png"));
    assert(!lc_safe_relative("frames/../q1.png"));
    assert(!lc_safe_relative("frames//q1.png"));
    assert(!lc_safe_relative("./q1.png"));
}

static void test_snapshot(void) {
    LcSnapshot snapshot = {0};
    char error[LC_ERROR_CAP] = {0};
    assert(
        lc_snapshot_capture("1788712440|2026-09-06|1234|0-09-06", &snapshot, error) == 0
    );
    assert(snapshot.epoch == INT64_C(1788712440));
    assert(snapshot.minute == 754);
    assert(strcmp(snapshot.minute_key, "0754") == 0);
    assert(strcmp(snapshot.date_key, "0-09-06") == 0);
}

static void test_pool_and_refresh(void) {
    LcPool pool = {0};
    char error[LC_ERROR_CAP] = {0};
    assert(lc_parse_csv_pool("17,2,999", &pool, error) == 0);
    assert(pool.count == 3);
    assert(pool.items[0].id == 17);
    assert(pool.items[1].id == 2);
    lc_pool_free(&pool);
    assert(strcmp(lc_refresh_mode(0, 1, 15, NULL, "2026-09-06"), "GC16") == 0);
    assert(
        strcmp(lc_refresh_mode(14, 15, 15, "2026-09-06", "2026-09-06"), "GC16") == 0
    );
    assert(
        strcmp(lc_refresh_mode(1, 2, 15, "2026-09-06", "2026-09-06"), "GL16") == 0
    );
}

static void test_selector_relaxation(void) {
    LcCandidate candidate = {
        .id = 1,
        .have_metadata = true,
        .have_quote_history = true,
        .have_book_history = true,
        .have_author_history = true,
        .last_quote = 990,
        .last_book = 990,
        .last_author = 990,
    };
    LcPool pool = {.items = &candidate, .count = 1};
    LcSnapshot snapshot = {.epoch = 1000};
    int level = -1;
    (void)snprintf(candidate.id_text, sizeof(candidate.id_text), "1");
    (void)snprintf(candidate.frame, sizeof(candidate.frame), "frames/q1.png");
    (void)snprintf(candidate.book, sizeof(candidate.book), "book1");
    (void)snprintf(candidate.author, sizeof(candidate.author), "author1");
    assert(lc_select(&pool, &snapshot, &level) == &candidate);
    assert(level == 7);
}

int main(void) {
    test_cksum();
    test_paths();
    test_snapshot();
    test_pool_and_refresh();
    test_selector_relaxation();
    (void)puts("native unit tests passed");
    return 0;
}
