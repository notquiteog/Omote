# The two modes

Not tabs over a shared workspace. A conversation and a place to make things
are genuinely different kinds of attention, and one at a time is the honest
arrangement. Everything else this app can do is a *tool*, and the argument for
that is below rather than assumed.

There were seven of these once: Code, Talk, Live, Autopilot, Studio, Search and
Mail. Reducing them to two was not tidying. Each of the other five was a place
a decision had been pushed out to, and each push cost something real.

* **Talk and Live** were modes because a microphone is a different kind of
  attention. They still are — which is why there is a call surface that gives
  the composer back rather than covering it, and an `Escape` that ends it. But
  they are not *places*, and treating them as places meant a rail of seven
  buttons where two could only be reached through a third: a "talk" button that
  had to switch mode before it could open the microphone. There are two buttons
  in the bar now. Each opens the microphone and starts listening; each pressed
  again ends the call. You are still in the conversation, and you can read what
  it said when the call stops.
* **Search and Mail** were modes because "look this up" and "read my inbox"
  were not expressible as messages. They are: they are tools, and they have
  always been tools. What the modes added was a *second* door onto one store,
  and two doors is two places for it to be wrong — worse, the two could not
  see each other. The panel read your inbox without the model knowing you had,
  so "what came in today" was something you *did* and then something you had to
  *ask* about, and the second conversation could not build on the first.

  So the panels are gone. The model searches and reads, and the results are
  drawn into the tool card, in the place they were asked about. There is one
  door and it is the tool.

  Two things survived the move and are worth stating:

  * **The tool is offered even with no mailbox.** It used to be hidden until an
    account existed, so on a fresh install the model had no `mail` tool at all
    and could not tell you it could not read your mail — from its side the
    capability did not exist. Offered, the refusal is a sentence naming the
    three settings that would fix it.
  * **Nothing in the browser can send.** The model drafts; a send is the tool,
    and it is graded `MESSAGE` and asked for in every mode. A panel with a send
    button is a second, quieter way for a message to leave without anybody
    reading it.

* **Autopilot** was a mode because somebody had to press the button, and that is
  the one thing the harness is better at. It is the `autopilot` tool now: the
  model decides a long job needs working on unattended, exactly as it decides
  anything else, and it competes on the same terms as reading a file or
  running the tests. The run is a turn, so it is in the transcript and can be
  read afterwards — which a mode's live view could never offer.

## Chat

A transcript, tool cards with the risk grade on them, inline diffs, approve and
deny, and a receipt at the end of each turn saying what changed and offering to
put it back.

The controls that qualify *the next thing you say* — approval mode, model,
working root, provider — sit under the box you type in rather than in a title
bar three hundred pixels away.

Beside them are two buttons:

**Talk** opens the microphone and starts listening. Press it again and the call
ends. Barge-in works the way it does everywhere else in openmirror: talk over
it and the synthesis is killed server-side, the client drops what it has
buffered, and the history records only what you actually heard.

*It speaks its questions, and says when it is waiting.* This was the mode's
worst failure for a while: only assistant prose was spoken, so when the agent
asked something or stopped for an approval the call simply went quiet, and
somebody not looking at a screen had no way to know the machine was waiting on
them. Both are now said out loud, with the options read out — on a screen they
are buttons, and in a voice call they are the only way to know what answers
are accepted.

The reply goes back to the thing that is waiting rather than starting a new
turn. Answering out loud while it is still reading the question out stops the
speech and keeps the turn, because cancelling there would throw away the work
the answer was about to unblock.

*Nothing is ever guessed.* "Go" and "no" are one phoneme apart, and "don't do
it" contains "do it" — so a refusal wins whenever both appear, and anything
that is neither is repeated back rather than resolved to the likelier reading.
Refusing an ambiguous utterance costs a repeat; approving one costs whatever
the tool was about to do.

*Money and secrets need a word that is not a mishearing.* A bare "yes" will not
spend anything: those need "confirm", which is not a word that turns up in
conversation by accident and is not a plausible mistranscription of "no". It
says so when it asks, not after.

`Ctrl+Shift+T` from anywhere. `Escape` stops whatever is talking.

**Live** is next to it: OpenAI's realtime API, speech to speech, one model, no
text in the middle. What you gain is latency and prosody — the model hears *how*
something was said and answers before a transcript would have existed. What you
give up is choice, and the button says so: it is one vendor's model, it cannot
be pointed at your own hardware, and everything said in the room goes to them
while it is open. Ordinary Talk routes dictation, thinking and speech
separately, so each of those can stay on your machine.

Its tool calls go through the same approval path as the typed loop. If the
policy says a human decides, the model waits — and it is told, in the tool
result, that it is waiting on a person, so it says so out loud rather than going
quiet.

### Autopilot, as a card

When the model decides a job wants working on unattended, it calls the
`autopilot` tool. What comes back is a card in the transcript: the screen it is
driving while it runs, one line per thing it did, and a stop button.

It gets a screen of its own by default — see [AUTONOMY.md](AUTONOMY.md) — so you
keep using your computer while you watch. Running on *your* screen is possible
and has to be asked for in words; the refusal happens before anything moves.

The picture and the narration come down separate channels, which is not an
accident. The narration is small, ordered and replayed to a client that
reconnects; the video is large, continuous and worthless five seconds later. On
one channel you would either store an hour of stale frames to replay, or lose
the record of what happened when someone reloads the page.

When the run ends the card folds itself away and leaves the narration, which is
still true. That is the one thing a mode's live view could not do.

If it stops to ask something, the question appears in the transcript with the
full context around it.

## Studio

Images, video and music, with every control the backend actually has.

The panel is drawn from what the server says: ask an A1111 and its own samplers,
schedulers, upscalers and LoRA names come back as real dropdowns, each with a
sentence explaining what it does. Nothing in the client knows what a sampler
is, so installing one on the diffusion box adds it here with no release in
between.

"Everything else" is a fold, not a lock. Every parameter is present and
settable; the ones you need most days are open and the other twenty are one
click away. Hiding one behind a preference would mean the person who wants it
has to find out it exists first.

Video is a job rather than a request — minutes, not seconds — so it starts,
reports how long it has been going, and can be stopped. There is no progress
bar because ComfyUI behind Perch exposes polling and nothing else, and a
percentage would be a fiction.

Every result is stored as a file plus a JSON sidecar holding the prompt, the
seed the backend *actually used*, and every setting. A picture you cannot
reproduce is a picture you cannot iterate on.

### Music

A third kind, and not a flag on video: a music model is asked for a length in
bars and returns a `.wav`, a video model is asked for a resolution and returns
an `.mp4`, and the two lists have almost nothing in common. So the picker offers
what the connected host actually has for music — Stable Audio, ACE-Step,
DiffRhythm and the rest through fal or Replicate, which is also how you reach
Suno-hosted models, since Suno has no API of its own.

It is a job, like video, because a piece takes minutes.

**Scores rather than recordings.** `compose_music` is the tool for anything you
are going to finish yourself. You give it a key and a progression — "A minor,
ii–V–I" — and it writes a chord part held a bar at a time with a bass
underneath, ending back on the key so a loop lands somewhere, as a Standard MIDI
File. FL Studio, Ableton, Logic, Reaper and Cubase all import that.

It does not write FL Studio's `.flp` format. That is a proprietary binary, and
there is no honest way to produce one from outside the application; a tool that
claimed to would either be reverse-engineering a format that changes or
shelling out to an FL Studio that may not be installed. It also does not host
plugins, because that is a C++ audio graph and a different product. The `.mid`
is what the format allows, and it is the same either way whether you have FL
Studio or not.

Deciding the music is the part worth delegating — a chord progression, a bass
line that follows the harmony, a piece that turns before the end — and writing
the file is the part that must not be. So the model arranges and
`openmirror/music/midi.py` renders, in a hundred lines of standard library with
a round-trip test, because a MIDI file nothing else can open is not a file.

### Bringing a ComfyUI workflow

None ship, because a template names the checkpoints and custom nodes your
ComfyUI has. Bringing yours is one command — see
[openmirror/providers/workflows/README.md](../openmirror/providers/workflows/README.md).
The prompt, seed, size and sampler settings are found and turned into
controls, and what it found is reported back so you can see whether it worked.


## Search and mail, as cards in the conversation

### Search

The agent's search backend, without the agent.

Worth having for a reason that is not obvious: that backend is configured
once, with a key, and is usually better than a browser tab — a self-hosted
SearxNG, or a Brave key that does not track you. Looking something up should
not have to cost a model turn, and someone who only wanted the links gets the
links.

The hits are drawn into the tool card, with a "read it here" on each — through
the same fetch the agent uses, so the private-address guard applies to a person
clicking a link, not only to a model following one.

### Mail

Your inbox, in the conversation, as a card.

* **The agent reads the same inbox you are looking at.** One door: the `mail`
  tool, and a renderer that draws its result. "What came in today" and "what did
  the Acme thread say about the invoice" are one conversation now, which is the
  thing that was impossible when the panel and the tool could not see each other.
* **A listing does not mark anything read.** That is the server's `BODY.PEEK`,
  and it is why expanding a list cannot clear your unread badge behind your back.
  Reading is a separate call, and it is what marks a message seen.
* **A draft says it has not been sent.** It is not going to be, by anything you
  can press.

## Where the commit bar lives

Not a mode — it is in the Chat header, and it only appears when
your working root is a git repository with something in it. It counts *changed files* rather
than lines, because that is the number that answers "is there anything to do
here", and a diffstat of a reformat is large and says nothing.

The two buttons are the design. "Write it for me" reads the **staged** diff —
what a commit would contain, not everything that changed, and usually not the
half that does not compile yet — and puts a draft in the message field.
"Commit" sends the message in the field at that moment, which may be that
draft, an edit of it, or something typed from nothing.

Nothing is staged for you. `git add` is a checkbox per file and there is a
button for all of them, and both of those are things you press.

## Updates, the context meter, and `@`

Not modes, and not a tab. Three small things that live in the places you would
look for them.

**The update dot** is on the Updates tab in Settings, and nowhere else. A
banner would be more noticeable and would also be dismissed without being
read, which for a message that says "a new version exists" is the most likely
outcome. The tab dot says the same thing without taking anything away, and
Settings → Updates is where the release notes are.

**The context meter** is under the composer, beside the thing that would
change it. The number is exact; the bar appears only when there is a
denominator. It fills towards the point the conversation will be summarised
rather than towards the model's window, because that is the one the session
will actually hit. Clicking it is the same as typing `/compact`.

**`@` in the composer** opens the files in the working root, ranked. Enter
completes a partial name and sends a finished one — the same rule the slash
menu uses and for the same reason: `@app` then Enter means the file, and
`@src/app.js` then Enter means send it. Escape closes the list and keeps the
text.

## Review, and the queue

Neither is a mode, and both are worth a paragraph because each is a decision
rather than a place.

**Review changes** is beside Rewind in the rail, not inside it. They are
different acts: rewind throws a whole turn away, review keeps the parts you
agree with. Every block in the diff is a choice, and the button says how many
hunks in how many files it is about to put back before you press it.

The file is rebuilt from the turn's recorded before- and after-content rather
than patched. That is why applying a second choice cannot land in the wrong
place — a patch applied to a file an earlier patch already changed is a patch
applied to text that is no longer what it was computed against.

If a file was edited by hand after the turn, the review says so and the apply
is refused until you confirm: those hunks are not what is in the file, and
writing over it would lose the edit.

**The composer takes a message while a turn is running**, and holds it until
that turn finishes. The send button stays on screen and says what it will do;
before, it was replaced by Stop, so the server could queue but nothing could
reach it. An interrupt still discards the queue — cancelling is cancelling.

## Leave

Not a mode either, and not a tab. It is a `hr` tool over a local ledger, and
the interface to it is a conversation: you ask about a request, it tells you
what your own record says, and you decide.

There is nothing to click because there is nothing to click. The ledger is
behind the model, which is the point — see [LEAVE.md](LEAVE.md) for why the
assessment answers with evidence and a band rather than a verdict, and for
when it declines to answer at all.

## Sessions that outlive the daemon, worktrees, and settings

Three things that are not modes, and are worth a paragraph each because each
answers "what happens when…".

**A restart.** Every turn is written to `data/sessions/`, and the stored
conversations are listed separately from the live ones — after a restart the
live list is empty and the stored list is everything. Reopening rebuilds the
session and replays the messages into it. What it does not restore is the live
state of a turn that was running: an in-flight tool call, a suspended
approval, the queue. A transcript does not contain a Future, so a session
interrupted mid-turn comes back to *just before* it with a note saying so.

**A worktree.** A second checkout of the repository, on its own branch, that
a session works in. The point is not safety — a worktree shares the repository's
history and its remotes, so a force push in one is the same force push. The
point is that a turn in it cannot touch the tree you have open, which is the
kind of damage that actually happens. `unconfined` answers the same problem
with a flag that opens the whole disk, and a flag that opens everything is
either left on and feared or turned off and needed.

**A settings file.** Every knob was an environment variable, which is right for
a container and wrong for a laptop. Three filenames are read, and a project
file can lower the safety settings and never raise them — see
[HOOKS.md](HOOKS.md) for why that rule is the same one, and
[LEAVE.md](LEAVE.md) for a feature that depends on it being true.
