#include "litclock.h"

#include <string.h>

const char *lc_refresh_mode(
    uint64_t display_count,
    uint64_t next_display_count,
    uint64_t interval,
    const char *previous_date,
    const char *current_date
) {
    if (display_count == 0 || previous_date == NULL || strcmp(previous_date, current_date) != 0 ||
        next_display_count % interval == 0) {
        return "GC16";
    }
    return "GL16";
}
