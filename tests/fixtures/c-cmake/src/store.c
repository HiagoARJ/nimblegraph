#include <stdlib.h>
#include "kv/store.h"

static void grow(Store *s) { s->size *= 2; }

Store *store_new(void) { return calloc(1, sizeof(Store)); }

int store_put(Store *s, const char *key, const char *value) {
    grow(s);
    return (int)hash_str(key);
}
