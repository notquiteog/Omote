# Memory

Omote can remember things about you between conversations. It is off until you
switch it on, and switching it on is two decisions rather than one.

## The two switches

**Remember things between conversations.** Off by default. Off means nothing is
written — not written-and-ignored, not written-pending-consent. The text never
even reaches the embedding model. A toggle that only stopped the *reading*
would leave a record of someone who believed they had opted out.

**Also pick things up automatically.** A separate switch, because passively
learning from what you say is a bigger step than storing what you explicitly
asked to be stored. It cannot outlive the first switch: turning memory off
turns this off too, rather than leaving it armed for whenever memory is next
enabled.

Turning memory off stops storing and stops recalling. It does *not* delete what
is already there — and the UI says so, with the count, rather than implying the
data is gone.

## Inspecting and controlling saved information

The **Brain** view in the sidebar is available by default. Set `OMOTE_MEMORY=false` to
turn the feature off for the installation; otherwise each user still opts in.
The dialog lists text, kind, project/subject, source, timestamp, and recall count,
with pagination, corrections, a full JSON export, per-item deletion, and a wipe.
Search covers all saved entries, including pages not yet loaded. Filter by
personal/project scope, kind, or a saved project/subject. Browsing and text search
are local: they work while paused, need no embedding connection, and do not
increment recall counts. Search matches literal text and subject names; semantic
recall remains a separate operation that needs an embedding model.
Automatic chat context searches personal memories and memories whose subject is
the chat's project root. It filters before ranking, so unrelated projects cannot
crowd out relevant results or appear in the prompt. Explicit recall can still
search other subjects. A project label entered manually must match the project
root to participate in that project's automatic recall.
Pausing does not hide existing entries. Corrections save locally even when the
embedding provider is offline, invalidate the old vector immediately, and can be
re-indexed later. Paused users' entries are not sent for re-embedding.
Manual additions also save locally when the embedding connection is unavailable.
They remain pending and appear in Brain immediately. Once a connection is ready,
open **Memory settings & search connection → Index pending memories** to enable
recall for those entries. New additions still require memory to be enabled.

| Endpoint | Behavior |
|---|---|
| `GET /api/memory?limit=100&offset=0` | A page of entries; optional `q`, `scope` (`all`, `personal`, `projects`), `kind`, and `subject` filters. `total` counts all the user's entries; `matched` counts filtered entries. `catalog` contains user-scoped filter choices and counts. |
| `POST /api/memory` | Save `text`, `kind`, and optional `subject` locally after consent; returns `indexed: false` if embedding is pending. |
| `PUT /api/memory/{id}` | Correct an entry and invalidate its old vector; returns whether it was indexed. |
| `GET /api/memory/export` | Export every saved entry and consent settings, including while paused. Excludes vectors and keys. |
| `DELETE /api/memory/{id}` | Delete one entry and its search vector. |
| `DELETE /api/memory` | Delete all the user's entries, vectors, and consent settings. |

## How it is stored

Memory lives in a local SQLite database. Source text is **readable plaintext**.
Vectors are projected to a bounded width (at most 256 dimensions) and quantised
to int8 with a per-vector scale. Similarity search uses `sqlite-vec` when
available and a Python scan otherwise. Both paths restrict results to the user,
embedding model, and requested kind/subject.

A per-user key makes each user's vector projection different. This is **not
at-rest encryption**: both memory text and the projection key live in the same
database. Protect the file with filesystem permissions or disk encryption when
needed. Switching embedding models invalidates incompatible vectors rather than
comparing unrelated spaces.

A wipe removes the user's active rows, vector-index entries, and key. It does not
securely erase disk sectors or independently stored backups and exported JSON.
Restoring a complete database backup also restores its saved text and keys.

## What gets remembered automatically

Only sentences that read like a durable fact about you or your setup — "I
always run the tests before pushing", "my deploy script lives in bin/release".
At most three per message, so one conversation cannot fill memory with a single
afternoon's opinion.
Repeated facts with the same subject are skipped before embedding (case-insensitive
exact matching after whitespace normalization). Disabling automatic learning also
stops a pending capture from saving after its embedding request completes.
Consent changes apply to existing chats on their next model request. Tools are
hidden while paused, and execution rechecks availability if a model proposes an
older memory tool call. A chat does not have to be restarted after opting in.
Personal preferences are classified as preferences; clearly project-related
statements are stored under the session's working folder. This conservative
pattern-based capture does not summarize every model action or reconcile
contradictions. The agent can also explicitly use `remember` while working.

Anything transient is skipped: "today", "right now", "currently". A fact that
was only true this afternoon is a liability next week, when it will be recalled
as though it were still current.

## What it looks like to the model

Relevant memories are put in front of the model before the first request of a
turn, framed as background rather than instruction — it is what you told it
before, not what you are asking for now. There are also `remember` and `recall`
tools, because automatic recall only ever sees the opening message: an agent
that reads three files and *then* realises it needs to know how you like
migrations written has no other way to ask.

`remember` is graded as a **write**, not a read. Storing something in your
long-term memory is a side effect, and you get to say no to it like any other.

The tools are only offered when memory is on for that person. A model shown a
`remember` tool it will always be refused for using wastes a step every turn.

## Failing safely

If the embedding provider is down, recall returns nothing and the turn
continues. An assistant that refuses to answer because it could not remember is
worse than one that simply does not remember.

## Changing the embedding model

A vector only means anything relative to the model that made it. Change the
embedding model and every stored vector is a coordinate in a space that no
longer exists — and the failure is silent, which is the whole problem. Old
vectors do not *fail* against a new query; they score. Cosine against unrelated
geometry returns plausible middling numbers, so an unreset store keeps answering
questions, just with whichever old memory happened to land near the new query.
Nothing logs and nothing raises.

So Omote notices instead. The store records which embedder its vectors are
under, and compares it at every open and before every embedding — the embedding
route can be re-pointed while Omote is running, so a start-up-only check would
leave the rest of that session comparing against the old model. When it has
moved, **every vector is discarded and every text is kept**, and the memories are
embedded again in the background. Recall is briefly empty and says so, rather
than being briefly wrong and saying nothing.

What counts as "the same embedder" includes the width: `text-embedding-3-large`
asked for 512 dimensions and the same model at its full width are two different
embedders, because their vectors are no more comparable than two different
models' would be. The stored width (`src_dim`) already separated models of
different sizes; what it could not catch is two *different* models of the *same*
size, which is the case this exists for.

## Controlling the index by hand

The vector index is derived data: the memory is the text, and the vectors and
the search index are both things Omote can make again. Everything here is
therefore safe to reach for, which `DELETE /api/memory` — the wipe — is not.

| | |
|---|---|
| `GET /api/memory/index` | Which embedder, how many memories, how many searchable, how many still pending, and whether `sqlite-vec` is answering or the scan is. |
| `POST /api/memory/index/rebuild` | Rebuild the search index from the vectors already stored. Local, no provider call. For an index that was interrupted, or never built because the extension arrived later. |
| `POST /api/memory/index/reset` | Throw away every vector and make them again from the texts. The manual form of what a changed model does automatically. Pass `{"reembed": false}` to do the wipe alone. |
| `POST /api/memory/index/reembed` | Embed whatever is still pending. Safe to call repeatedly. |

A re-embed is batched and stops at the first failed batch rather than hammering
a provider that is down. The rows it did not reach stay pending, so the next
call — or the next start-up — resumes exactly where it left off.
