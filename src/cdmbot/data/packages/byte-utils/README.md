# byte-utils

CdM-8 source library. Import `@byte-utils/lib.asm`, declare `byte_increment: ext`, and call with `jsr byte_increment`. Input/output: r0; changed: PS. The caller initializes SP. The routine balances the return address.
