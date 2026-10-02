# musicbox

A music synthesizer in pure sysl. A **score** of notes, a fixed pool of **voices**, and 16-bit samples
**pulled** out at whatever rate the audio device runs at. No C, no audio API, no operating system: the
same code renders a WAV file on a laptop and feeds an I2S buffer on a Pico.

```hocon
dependencies {
  musicbox { git = "github.com/sysl-lang/musicbox", version = "0.1.0" }
}
```

```sysl
import sh.sysl.musicbox.*
import sysl.fs.write_bytes

val organ = instrument([Partial(1.0, 1.0), Partial(2.0, 0.5), Partial(3.0, 0.25)],
                       adsr(0.01, 0.1, 0.7, 0.3)).unwrap()
val harp = instrument([Partial(1.0, 1.0), Partial(2.0, 0.2)], pluck(1.5, 0.1)).unwrap()

val notes = [
    note(0.0, 1.0, pitch(60.0), 0.5),
    note(0.0, 1.0, pitch(64.0), 0.5),
    note(0.5, 0.5, pitch(72.0), 0.6, instrument = 1).held(0.2)]

val song = score(notes, [organ, harp]).unwrap()
write_bytes("chord.wav", render_wav(song, 44100).unwrap().view()).unwrap()
```

## The shape of it

- **A `Score` is immutable.** It is a view of note records the caller owns -- start, written length,
  release point, frequency, velocity, instrument -- in order of start time. Playing it changes
  nothing, so it can be rewound, replayed, or handed to two synths at once.
- **A `Synth` holds a fixed pool of 32 voices.** A note takes a voice when it starts; with every voice
  busy it takes the quietest one. Memory is proportional to polyphony, not to the length of the song.
- **The output is a pull.** `render(*self, out: []i16) -> bool` fills a buffer with mono samples at the
  rate the synth was built with, and answers false once the score has finished. It knows nothing about
  any audio device, so it fits a callback, a DMA ring or a file equally.
- **Instruments are plain data**: up to eight sine partials, each a ratio of the note's frequency and
  a share of its level, and an envelope -- `Adsr` or `Pluck`, an enum with payloads.
- **Articulation is a release point that is not the written length.** `note(...).held(seconds)` lets a
  note go early (staccato) or late (legato) without moving the notes after it.

## Fixed point, and where the floats are

Every float is spent before playback. Building a synth converts each instrument to Q1.31 at its sample
rate (partial ratios in Q16.16, amplitudes normalized so they sum to at most one, envelope coefficients);
starting a note converts its times and frequency with integer division. What runs per sample is a DDS
phase accumulator per partial (`u32`, wrapping), a lookup in a 1024-entry sine table with linear
interpolation, Q31 multiplies, and a mix that **saturates** to 16 bits rather than wrapping.

`Q31` is a struct over `i32` with `+`, `-`, `*` and unary `-`, all saturating. On Armv7-M and Armv8-M
its product compiles to the same `smull` and two shifts a hand-written `(int64_t)a * b >> 31` does; the
struct costs nothing. Armv6-M (the Pico's RP2040) has no 64-bit multiply and calls `__aeabi_lmul` either
way.

`q31(0.6)` inside a function folds to a constant. **A module-level `val` holding one does not** -- it is
filled by an initializer, which a freestanding target has nowhere to run, and `sysl build-c` for one
refuses it. Write such constants where they are used, or as a constructor over an integer literal
(`Q31(1288490189)`), which is laid straight into the object file.

## Envelopes

`adsr(attack, decay, sustain, release, curve = 0.001)`: a linear attack, then exponential segments in
the RC style -- each aims `curve` past its target, so it arrives in the time it was given instead of
approaching for ever. `0.001` is -60 dB, a classic analog release; smaller is straighter and more
percussive, larger rounder. A release time is the fall from full level.

`pluck(decay, release)`: full level at once, falling 60 dB over `decay` whether or not the note is
held, and over `release` once it is let go.

Every release is at least 20 ms: anything faster is a click whatever the instrument meant.

## Running on a board

`requires {}` is exact, and the package keeps no module storage an initializer would have to fill --
the sine table is a literal, printed by `scripts/sine_table.py` and checked entry by entry by a test.
The check is a probe program that calls `render`:

```
sysl build-c <probe dir> --target thumb-freestanding-softfp --lib <this repo>
sysl build-c <probe dir> --target thumbv6m-freestanding --lib <this repo>
```

Both build, and the disassembly of `Synth.render` has no floating-point instruction or call in it.
The only heap user is `render_wav`, which grows a buffer for a whole file; `wav_header` writes the
44-byte header into storage the caller supplies.

## Testing

```
sysl test .
```

The tests measure the rendered samples: pitch by counting zero crossings, the envelope by the peak of
each cycle, clicks by the largest step from one sample to the next, voice stealing by comparing against
a score that never had the stolen note (the mix is an exact integer sum, so the two are byte-identical
only if the quietest voice was taken), and saturation against twice a single note, clamped.
