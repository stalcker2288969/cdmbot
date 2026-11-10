rsect delta
add_delta>
    ldi r1, 2       # Amount to add.
    add r1, r0
    bz wrapped
    rts
wrapped:
    ldi r2, 1
    rts
end.
