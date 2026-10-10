# Workspace setup

Omote runs on the user's machine. It provides the agent runtime and interface;
model inference, ComfyUI, and desktop applications must be configured separately.
Settings → Connections selects a provider and model independently for chat,
embeddings, dictation, speech, images, and video. The default system theme uses
opaque light/dark surfaces, a session sidebar, task starters, and a central composer.
Existing themes are still available in Settings.

## Images, video, and the full ComfyUI editor

Use **Studio → Generate** for provider-specific image and video forms, reference
images, jobs, cancellation, and the generated-media gallery. Connect an image/video
provider first. ComfyUI generation in this form requires an API-format workflow
template matching the checkpoints and custom nodes installed on its server.

Use **Studio → Advanced · ComfyUI** to edit the actual ComfyUI graph. Connect ComfyUI
in Settings, select it in the advanced workspace, and press **Open editor**. This
loads the installation's own frontend rather than translating its graph into a
limited editor. Node connections, custom widgets, subgraphs, workflow import/export,
queues, and image/video previews remain ComfyUI features. Switching back to Generate
or Chat retains the mounted editor and its unsaved graph. Save the graph in ComfyUI
before changing connections, reloading, or closing Omote.

**Nodes 2.0:** install a ComfyUI frontend version that supports Nodes 2.0 and enable
it in ComfyUI's menu. Omote does not replace or pin that renderer. The legacy
renderer remains usable as well. See the [ComfyUI Nodes 2.0 announcement](https://blog.comfy.org/p/comfyui-node-2-0).

The browser must be able to reach the editor address. For Perch/API gateways, set
the optional **ComfyUI editor address** in Connections to the actual frontend URL.
API keys are never put into the frame URL. Authenticate through the frontend's own
login if needed. URLs containing credentials or query tokens are rejected.
An HTTPS Omote page cannot embed an HTTP frontend; use HTTPS on both or
**Open in tab**. That link also handles servers which disallow embedding. ComfyUI
must use a separate origin from Omote. Tor connections are not embedded because
the browser frame cannot inherit the backend's Tor transport. Local-only mode refuses
remote editor connections.

Graphs and outputs made in the native editor belong to ComfyUI's storage. They do
not automatically appear in Omote's media gallery. To use a graph with the
simple form and gallery, export it in API format and **Import a workflow** in Generate.
Normal workflow JSON belongs in the native editor; the simple importer explains this
instead of storing an unusable template. ComfyUI editor actions run under ComfyUI's
own controls, outside the chat agent's approval policy.

## Agent, code, browser, and desktop work

Create a chat session against a tool-capable model and the desired working folder.
The existing agent tools cover file editing, patching, shell commands, tests, Git,
review, language servers, browser search and navigation, desktop screenshots/input,
MCP, background tasks, and delegated work. Tool calls, results, questions, and
approvals appear in the conversation. Choose the session's approval policy before
unattended work. Background commands have status and Stop controls; the autopilot
tool supports longer desktop jobs. See [Autonomy](AUTONOMY.md) and [Installation](INSTALL.md).

Browser automation needs the browser extra and its browser installation. Desktop
control needs the desktop extra and the platform's input/screenshot permissions.
An application such as **Photoshop** must be installed and accessible on that
desktop. The bundled `/photoshop` skill guides the agent through opening a document,
editing with desktop tools, preserving layers, saving, and checking the export.
It is a desktop-control workflow, not an Adobe cloud API or bundled Photoshop license.
Use the model's visible tool results to verify each task's outcome.

## Voice

**Talk** routes STT → chat LLM → TTS independently. Select all three connections
and models in Settings; they may run locally or through different providers. This
pipeline supports streamed replies, interruption, and agent tools when attached to
a session. The browser needs microphone permission and a secure context (HTTPS or
localhost).

**Live** uses the OpenAI Realtime API, with GA nested audio configuration, 24 kHz
PCM audio, transcripts, and the same agent tool approval path. Configure an OpenAI
API key and the realtime model/voice settings. Local-only mode refuses Live and
directs the user to Talk. This is API access; it does not sign into or embed ChatGPT
Instant or use a ChatGPT subscription automatically. See the [official Realtime
conversation guide](https://developers.openai.com/api/docs/guides/realtime-conversations).

## User and project memory

Open **Memory** to opt in. Automatic capture is a separate switch. Select an embedding
model in Connections; the chat model is not automatically used for embeddings.
The dialog shows the embedding model, index status, every saved item's text, kind,
project/subject, source, creation time, and recall count. **Load more** reaches entries
beyond the first page; **Export all** includes the complete collection.

You can add facts or project details, correct existing entries, forget individual
items, or delete everything. Pausing stops capture and recall and keeps saved entries
visible. Corrections work while paused or offline: the old vector is immediately
invalidated and the corrected entry waits for indexing. **Rebuild pending embeddings**
retries when memory and the provider are available. Paused users are not re-embedded.

The memory database stores readable text on disk. Embedding vectors are compressed
and keyed, but this is not encryption. Export files also contain readable memory
text. Deleting entries removes their active database/index rows; it does not delete
independent backups or exports. See [Memory](MEMORY.md) for the storage and HTTP API.

## Upgrading an existing installation

The package, CLI, Python imports, and MCP command are now `omote`, `omote`,
`omote`, and `omote-mcp`. Environment variables use the `OMOTE_` prefix. Update
launch scripts, service definitions, and `.env` keys when upgrading. The desktop
app and editor extension are named **Omote**; editor settings use `omote.*`.

Before changing data paths, stop the daemon and back up its data folder. Set
`OMOTE_DATA_DIR` to that existing folder to retain sessions, media, and memories.
Existing environment values and personal data are not reset by the source rename.
Browser preferences use the new `omote.*` namespace, so choose your preferred
theme and other browser-local settings again after upgrading.
