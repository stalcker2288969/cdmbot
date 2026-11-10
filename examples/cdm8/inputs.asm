asect 0
    pop r0
    ldi r1, start_label
    ld r1, r2
    add r2, r0
    pop r3
    ldi r1, text_label
    ld r1, r2
    halt

asect 0x80
start_label: ds 1
text_label: ds 8
end.
