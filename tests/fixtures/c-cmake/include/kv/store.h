#ifndef KV_STORE_H
#define KV_STORE_H
#include <stddef.h>
#include "kv/hash.h"

typedef struct {
    size_t size;
} Store;

Store *store_new(void);
int store_put(Store *s, const char *key, const char *value);
#endif
