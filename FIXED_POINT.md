# Fixed point in sysl — what musicbox has measured

musicbox is where sysl's fixed-point question is being answered: whether a fixed-point number can be an
ordinary library type, or whether the language has to know about it. Everything fixed-point lives in this
package (`sh/sysl/musicbox/fixed.sysl`) until the answer is settled. Whatever the language or the standard
library eventually gains goes into the self-hosted compiler.

`Q31` is a struct over an `i32` with saturating `+`, `-`, `*` and negation, and `q31(real)` converts from a
float. Each finding below was measured from emitted IR or disassembly, not reasoned about.

## 1. A float literal folds inside a function, and not at module level

| written | result |
|---|---|
| `(Q31(a) * q31(0.6)).raw` in a function body | folds: `movw/movt #0x4ccccccc`, then `smull` — no float work left |
| `val X: Q31 = q31(0.6)` at module level | **does not fold**: a zero-initialised global plus a store before `main`; `build-c --target thumbv6m-freestanding` refuses it as module storage |
| `val X: Q31 = Q31(1288490189)` at module level | folds, and builds for a board |

**This is the one real gap.** A named fixed-point constant at module level, which is the natural way to
write an instrument's parameters, has to be spelled as its raw integer to reach a microcontroller. What
would close it is module-level constant evaluation of a pure function over literals (`q31(0.6)` folding the
way it already does inside a body). It is a self-hosted design item, not a reason for a fixed-point type in
the language.

## 2. The multiply is tight, and the struct costs nothing

- **Armv8-M (`thumb-freestanding-softfp`)**: the struct, a raw `i32` and the generic `Fixed[16]` compile to
  identical code — `smull`, then `lsrs #31; orr r1, lsl #1`. It is not `smmul`, because Q1.31 shifts by 31,
  not by 32.
- **Armv6-M (`thumbv6m-freestanding`)**: no 64-bit multiply in hardware, so the struct and the raw form both
  call `__aeabi_lmul`, again identically.
- The whole of `Synth.render` contains no floating-point instruction and no float call. Its one runtime
  helper is `__aeabi_uldivmod`, which runs once when a note starts, not once per sample.

## 3. Value generics work

`struct Fixed[const F: u32]` with `impl[const F: u32] Mul for Fixed[F]` compiles, and `Fixed[16]`
monomorphizes to an `smull` with a `>>16`. So one generic type can cover Q1.31, Q16.16 and the rest.
musicbox keeps the plain `Q31` because that is all it needs.

## So far

A library type is enough for the arithmetic and for code quality: the wrapper costs nothing and value
generics cover every format. The gap is constant evaluation at module level, which is a general language
feature that fixed point happens to need, not a fixed-point feature.

## Observed along the way

- **Converting a float to an integer saturates.** The compiler emits `llvm.fptosi.sat`, so an out-of-range
  float clamps instead of producing poison. `reference/expressions.md` says only that the conversion
  "truncates toward zero". `q31` relies on the saturation, and a test pins it.
- **An out-of-bounds slice inside a test is reported only as "exit status 133"**, at the test's opening
  line, with no source line for the slice that failed.
