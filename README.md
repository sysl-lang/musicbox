# musicbox

A music synthesizer in pure sysl. A **score** of notes, a fixed pool of **voices**, and 16-bit samples
**pulled** out at whatever rate the audio device runs at. No C, no audio API, no operating system: the
same code renders a WAV file on a laptop and feeds an I2S buffer on a Pico.

```hocon
dependencies {
  musicbox { git = "github.com/sysl-lang/musicbox", version = "0.1.1" }
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
- **`seek(sample)` lands where continuous play would have been.** A note sounding at the seek point
  plays on from it -- its envelope at the level and stage it had reached, released if its release
  point has passed, every partial at the phase it had turned to -- and a note that has died away stays
  silent. The samples after a seek are the ones play produces, to the bit, even past 32 voices: the
  notes before the point are started in order on the voices play would have given them, so a note that
  finds the pool full steals the voice that was quietest then. The work is done in `seek`, once:
  each earlier note's attack and sustain are crossed at once and its decay and release curves (the
  whole of a pluck) are stepped sample by sample, since an integer recurrence has no bit-exact closed
  form. `render` is unchanged. `rewind()` is `seek(0)`.
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

## Writing music as text

`parse(text) -> Result[Score, Diagnostic]` reads a score from a LilyPond-like notation. Everything the
music says about itself -- tempo, key, temperament, transposition, volume, instrument and voices -- is
written in the text, so the text is the whole of the input:

```musicbox
[tempo 96] [key g major] [instrument organ]
<< d'4 e' f' g'2 | <g, b, d>1 >>
```

```sysl
import sh.sysl.musicbox.*
import sysl.fs.write_bytes

val song = parse("[key g major] << d'4 e' f' g'2 | <g, b, d>1 >>").unwrap()
write_bytes("song.wav", render_wav(song, 44100).unwrap().view()).unwrap()
```

Every block marked `musicbox` in this README is parsed by the test suite.

### Notes

A note is a **letter**, then its **accidentals**, then its **octave marks**, then its **duration**,
**dots** and **articulation** -- all but the letter optional, and nothing between them.

```musicbox
c d e f g a b c'
```

- **Letters** `c d e f g a b` name the white keys from middle C up; `r` is a rest.
- **Accidentals**: `s` sharpens and `f` flattens, repeatable (`css` is a double sharp, `bff` a double
  flat); `n` is a natural. A written accidental *replaces* the key signature's, so `fn` in G major is
  F natural and `f` alone is F sharp.
- **Octave marks**: each `'` raises the note an octave and each `,` lowers it. An unmarked `c` is
  middle C (MIDI 60) until `[octave n]` moves it.
- **Durations** `1 2 4 8 16 32 64` are whole, half, quarter... notes. A duration is **sticky**: a note
  without one has the last one written, and the first note's is a quarter.
- **Dots**: `.` adds half the duration, `..` three quarters. Dots are not sticky.
- **Articulation**: `-.` is staccato (the note is let go at half its length), `--` tenuto (held for all
  of it). A plain note is held for nine tenths, the way a player separates notes.

```musicbox
cs df en bff c4 d e8 f g2. r4 c-. d-- e
```

### Groups

- **A chord** `<c e g>2` sounds its pitches together and takes its duration, dots and articulation
  after the `>`.
- **A slur** `( ... )` plays legato: each note in it is held a tenth past its end so the next starts
  under it, and the last note is let go as written. A note's own `-.` or `--` still wins.
- **A tuplet** `{n:m ... }` fits `n` notes in the time of `m`. Without `:m` it is the largest power of
  two below `n`: `{3` is a triplet in the time of two, `{5` a quintuplet in the time of four.

```musicbox
<c e g>2 <b, d g> (c4 d e f) g1 {3 c8 d e} f4 {5:4 g16 a b c' d'}
```

### Voices

`<< one | two | ... >>` sounds its voices at once, each starting where the `<<` stands. The music after
the `>>` starts when the longest voice ends. **Each voice begins with a copy of everything in force at
the `<<`** -- the key, the instrument, the sticky duration -- and a directive inside a voice is that
voice's alone, so after the `>>` the music goes on as it was before the `<<`. Voices nest.

```musicbox
% a melody over a bass line
[instrument clarinet]
<< e'4 d' c' d' e' e' e'2
 | [instrument pluck] [octave 3] c1 g >>
```

### Directives

A directive is `[name arguments]`, and changes what the notes after it inherit.

| directive | argument | default |
|---|---|---|
| `[tempo 96]` | quarter notes a minute, above zero | 120 |
| `[key g major]` | a tonic (`c`, `fs`, `bf`...) and `major` or `minor`; all 30 signatures | `c major` |
| `[temperament werckmeister]` | `equal` or `werckmeister` (Werckmeister III, C4 where equal puts it) | `equal` |
| `[transpose -2]` | semitones, -127 to 127 | 0 |
| `[volume 0.6]` | a note's velocity, 0 to 1 | 1 |
| `[instrument bell]` | an instrument's name | `sine` |
| `[octave 5]` | the octave an unmarked `c` starts, 0 to 9 | 4 |

```musicbox
[tempo 72] [key ef major] [temperament werckmeister] [transpose -2] [volume 0.6]
[instrument bell] [octave 5] e g b e'2
```

The built-in instruments are `sine`, `organ` (four harmonics), `clarinet` (odd harmonics), `bell`
(inharmonic partials, plucked) and `pluck`. A program offers its own with
`parse_with(text, [Named("kazoo", kazoo)])`; a registered name is looked up first, so it may replace a
built-in one. A score uses at most 16 instruments.

`%` starts a comment that runs to the end of the line.

### The grammar

```
score     = { item } ;
item      = directive | voices | slur | tuplet | event ;
directive = "[" name { argument } "]" ;
voices    = "<<" { item } { "|" { item } } ">>" ;
slur      = "(" { item } ")" ;
tuplet    = "{" count [ ":" count ] { item } "}" ;
event     = ( pitch | "r" | "<" { pitch } ">" ) [ duration ] { "." } [ "-." | "--" ] ;
pitch     = letter [ "n" | { "s" | "f" } ] { "'" | "," } ;
letter    = "a" | "b" | "c" | "d" | "e" | "f" | "g" ;
duration  = "1" | "2" | "4" | "8" | "16" | "32" | "64" ;
```

Whitespace and comments separate items and may not split a note.

### Errors

A mistake is a `Diagnostic` from `sh.sysl.parsing` whose span indexes the text; `explain(text, d)`
renders it the way the sysl compiler prints its own:

```
error: a duration is 1, 2, 4, 8, 16, 32 or 64
 --> score:1:5
  |
1 | c4 d3 e
  |     ^ found `3`
```

A misspelt directive or instrument is answered with the nearest name (`did you mean `tempo`?`), a
group left open points at where it was opened, and a key needing eight sharps says so.

## Running on a board

`requires {}` is exact, and the package keeps no module storage an initializer would have to fill --
the sine table is a literal, printed by `scripts/sine_table.py` and checked entry by entry by a test.
The check is a probe program that calls `render`:

```
sysl build-c <probe dir> --target thumb-freestanding-softfp --lib <this repo>
sysl build-c <probe dir> --target thumbv6m-freestanding --lib <this repo>
```

Both build, and the disassembly of `Synth.render` has no floating-point instruction or call in it.
The heap users are `render_wav`, which grows a buffer for a whole file (`wav_header` writes the 44-byte
header into storage the caller supplies), and `parse`, which grows the score's notes and reads its
numbers through `sh.sysl.parsing`. A probe calling `parse` builds for both targets too; linking it
needs what a libc supplies -- `malloc` and `free`, `strtod` for a directive's number, `pow` for a
frequency -- so it runs on a board with newlib (the Pico SDK, Zephyr, FreeRTOS), and a bare one plays
scores built with `note` and `score` instead.

## Testing

```
sysl test .
```

The tests measure the rendered samples: pitch by counting zero crossings, the envelope by the peak of
each cycle, clicks by the largest step from one sample to the next, voice stealing by comparing against
a score that never had the stolen note (the mix is an exact integer sum, so the two are byte-identical
only if the quietest voice was taken), and saturation against twice a single note, clamped. Seeking is
held to continuous play sample for sample, at points mid-attack, mid-decay, mid-sustain, mid-release,
on a note's start, on a release point and between notes, deep into a long sustain, and past the voice
pool.
