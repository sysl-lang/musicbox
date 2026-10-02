# Prints the sine table literal in sh/sysl/musicbox/sine.sysl.
#
# One cycle of a sine in 1024 steps, each a Q1.31 value: round(sin(2 pi i / 1024) * (2^31 - 1)).
# The table is a literal rather than something computed at startup because a value built by a loop
# is module storage, and a freestanding target has nothing to run the loop before `main`. The
# package's tests recompute every entry and compare, so an edit here that is not pasted there fails.
import math

N = 1024
vals = [round(math.sin(2 * math.pi * i / N) * 2147483647) for i in range(N)]
rows = [", ".join(str(v) for v in vals[r:r + 8]) for r in range(0, N, 8)]
print(",\n".join("    " + row for row in rows))
