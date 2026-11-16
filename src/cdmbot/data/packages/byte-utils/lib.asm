rsect byte_utils
# @param r0 Input byte
# @return r0 Input plus one, modulo 256
# @changed PS Flags; jsr/rts balance SP
byte_increment>
    inc r0
    rts
end
