"""Writing music, and writing it down.

Two things live here and they are not the same kind of thing.

`midi.py` renders an arrangement to a Standard MIDI File — the documented,
portable, importable format. It exists because "make me something for FL
Studio" has an honest answer (a `.mid`) and an dishonest one (a `.flp`, which
is a proprietary binary no one outside Image Line can write).

`compose.py` is the part a language model is good at: deciding what the music
is. A chord progression, a bass line that follows the harmony, a piece that
turns before the end. That is judgement about music, it is text, and it is
worth delegating. The rendering is not judgement and does not belong to a
model.
"""
