#include <stdio.h>
#include "kv/store.h"
#include "missing_config.h"

int main(void) {
    Store *s = store_new();
    store_put(s, "a", "b");
    printf("ok\n");
    return 0;
}
