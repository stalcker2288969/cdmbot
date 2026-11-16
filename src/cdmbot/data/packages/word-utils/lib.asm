rsect word_utils
# @param r0 Input word
# @return r0 Input plus one, modulo 65536
# @changed PS Flags; jsr/rts balance SP
word_increment>
    inc r0
    rts
end
