# Local upstream correction

Base: the commit in `REVISION`.

`native/scorebot.cpp` explicitly casts the no-player enum to `uint32_t` in
`scorebot_v1_pair_peer`. GCC on the target Pi rejects the mixed enum/integer
conditional under `-Wextra -Werror`. The result value and ABI are unchanged.
