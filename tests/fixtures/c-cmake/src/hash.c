#include "kv/hash.h"

unsigned long hash_str(const char *s) {
    unsigned long h = 5381;
    while (*s) h = h * 33 + *s++;
    return h;
}
