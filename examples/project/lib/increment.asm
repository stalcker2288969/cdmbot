# Public entry point for the increment library.
rsect increment
increase>
    jsr add_delta
    rts
add_delta: ext
end.
