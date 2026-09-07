#include "litclock.h"

#include <stdlib.h>
#include <string.h>

static int shuffled_compare(const void *left, const void *right) {
    const LcCandidate *a = left;
    const LcCandidate *b = right;
    if (a->shuffle_key < b->shuffle_key) {
        return -1;
    }
    if (a->shuffle_key > b->shuffle_key) {
        return 1;
    }
    return strcmp(a->id_text, b->id_text);
}

void lc_shuffle_pool(LcPool *pool, const char *release_version, const LcSnapshot *snapshot) {
    size_t index;
    for (index = 0; index < pool->count; index++) {
        char source[256];
        int written = snprintf(
            source,
            sizeof(source),
            "%s:%s:%s:%s",
            release_version,
            snapshot->epoch_text,
            snapshot->minute_key,
            pool->items[index].id_text
        );
        if (written > 0 && (size_t)written < sizeof(source)) {
            pool->items[index].shuffle_key =
                lc_posix_cksum((const unsigned char *)source, (size_t)written);
        }
    }
    qsort(pool->items, pool->count, sizeof(*pool->items), shuffled_compare);
}

static bool elapsed(int64_t now, int64_t last, int64_t cooldown) {
    return now >= last && now - last >= cooldown;
}

LcCandidate *lc_select(LcPool *pool, const LcSnapshot *snapshot, int *relaxation_level) {
    int level;
    size_t index;
    for (level = 0; level <= 7; level++) {
        for (index = 0; index < pool->count; index++) {
            LcCandidate *candidate = &pool->items[index];
            bool quote_ready;
            bool book_ready;
            bool author_ready;
            bool accepted;
            if (!candidate->have_metadata) {
                continue;
            }
            quote_ready = !candidate->have_quote_history ||
                          elapsed(snapshot->epoch, candidate->last_quote, INT64_C(86400));
            book_ready = strcmp(candidate->book, "-") == 0 || !candidate->have_book_history ||
                         elapsed(snapshot->epoch, candidate->last_book, INT64_C(43200));
            author_ready = strcmp(candidate->author, "-") == 0 ||
                           !candidate->have_author_history ||
                           elapsed(snapshot->epoch, candidate->last_author, INT64_C(21600));
            accepted = (level == 0 && quote_ready && book_ready && author_ready) ||
                       (level == 1 && quote_ready && book_ready) ||
                       (level == 2 && quote_ready && author_ready) ||
                       (level == 3 && quote_ready) ||
                       (level == 4 && book_ready && author_ready) ||
                       (level == 5 && book_ready) || (level == 6 && author_ready) || level == 7;
            if (accepted) {
                *relaxation_level = level;
                return candidate;
            }
        }
    }
    return NULL;
}
