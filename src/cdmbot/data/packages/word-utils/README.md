# word-utils

CdM-16 source library. Import `@word-utils/lib.asm`, declare `word_increment: ext`, and call with `jsr word_increment`. Input/output: r0; changed: PS. The caller initializes an even SP. The routine balances its two-byte return address.
