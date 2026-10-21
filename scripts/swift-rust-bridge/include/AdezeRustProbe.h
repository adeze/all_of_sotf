#ifndef ADEZE_RUST_PROBE_H
#define ADEZE_RUST_PROBE_H
#include <stdint.h>

/* Scalar values only: no allocated buffers, handles, callbacks or ownership transfer. */
uint32_t adeze_probe_rust_abi_version(void);
int32_t adeze_probe_rust_increment(int32_t value);
#endif
