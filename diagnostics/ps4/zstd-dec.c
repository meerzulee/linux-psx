// SPDX-License-Identifier: MIT
// Small stdout decompressor for the pinned zstd single-file decoder.
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
#include "zstd.h"
int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s FILE\n", argv[0]); return 2; }
    FILE *in = fopen(argv[1], "rb");
    if (!in) { perror("open"); return 3; }
    ZSTD_DStream *ds = ZSTD_createDStream();
    if (!ds) { fclose(in); return 4; }
    size_t in_size = ZSTD_DStreamInSize(), out_size = ZSTD_DStreamOutSize();
    void *ib = malloc(in_size), *ob = malloc(out_size);
    if (!ib || !ob) { fclose(in); ZSTD_freeDStream(ds); return 5; }
    ZSTD_initDStream(ds);
    size_t read, last = 0;
    while ((read = fread(ib, 1, in_size, in)) > 0) {
        ZSTD_inBuffer input = { ib, read, 0 };
        while (input.pos < input.size) {
            ZSTD_outBuffer output = { ob, out_size, 0 };
            size_t ret = ZSTD_decompressStream(ds, &output, &input);
            if (ZSTD_isError(ret)) { fprintf(stderr, "zstd: %s\n", ZSTD_getErrorName(ret)); fclose(in); ZSTD_freeDStream(ds); free(ib); free(ob); return 6; }
            if (output.pos && fwrite(ob, 1, output.pos, stdout) != output.pos) { fclose(in); ZSTD_freeDStream(ds); free(ib); free(ob); return 7; }
            last = ret;
        }
    }
    fclose(in); ZSTD_freeDStream(ds); free(ib); free(ob);
    return last == 0 ? 0 : 8;
}
