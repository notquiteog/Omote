# Workspace setup

Omote runs on the user's machine. It provides the agent runtime and interface;
model inference, ComfyUI, and desktop applications must be configured separately.
Settings → AI providers selects a provider and model independently for chat,
embeddings, dictation, speech, images, and video. The default system theme uses
opaque light/dark surfaces, a session sidebar, task starters, and a central composer.
Existing themes are still available in Settings.

## First use

1. Open Omote and choose a provider in the welcome screen. OpenRouter is selected
   initially; OpenAI, Anthropic, other hosted services and local model servers are
   available in the same picker.
2. Paste your API key and choose **Connect and continue**. Local servers also show
   an address field. Omote checks the connection before saving it and selects a
   chat model from the provider’s live catalog. It does not send a billed test
   message. The provider may require credit or a paid account for actual use.
3. Choose **Start chatting** and write your first message. Omote creates the chat
   automatically with **Ask first** permissions. No model ID, routing configuration
   or environment file is needed for this path.

OpenRouter uses its [automatic router](https://openrouter.ai/docs/guides/routing/routers/auto-router)
when that model appears in the live catalog. Other choices prefer models that
advertise tool support when the provider exposes that information. The selected
model is shown after connecting, and can be changed under **Settings → AI providers
→ Default providers and models**. Saved choices apply to new chats and survive
restarting Omote. A model’s permissions and availability still depend on its provider.

The one-minute tour is optional. Choose **Take a quick tour**, **Explore without
connecting**, or close setup. **Getting started** in the sidebar reopens setup or
replays the tour. The page keeps showing a connection button until chat is available.
API keys are stored in the daemon’s private connection file, never browser storage.

Use **New chat** to choose a project folder. Model, tool and permission overrides
are under **Model, tools and permissions**. Failed chat creation keeps the message
in the composer so you can retry. Keyboard users can navigate settings tabs with
arrow keys and select chats with Enter or Space.

Memory remains opt-in; connecting a provider does not enable it. Images, video and
voice use the capabilities that provider supplies. ComfyUI, browser control,
PhotoCraft and other local integrations may still need their own installations;
see the relevant sections below. Studio links directly to provider setup when a
feature is not configured and offers **Try again** after connection failures.

## Local tools and system usage

Open **System** in the sidebar for live CPU, RAM, workspace disk, GPU utilization,
VRAM, temperature and power when the driver exposes them. It measures the computer
running Omote, even when viewing from another device. GPU telemetry currently
supports NVIDIA through `nvidia-smi` and Linux AMD through sysfs; other readings
show **Unavailable**. Polling stops when the panel closes or the browser is hidden.
You can pause updates or refresh manually. Active chats and their background task
counts link back to the conversation controlling them; Studio jobs remain in Studio.

**Local tools** detects common commands on PATH and checks the default localhost
ComfyUI/Ollama ports. A reachable port is not a health check. Custom installations,
ports and containers may need connecting manually in Settings.

**Plan local setup** drafts a chat request. Ask what you want to download and run;
the agent can inspect `system_usage`, use package installation tools or the shell,
and track ordinary long jobs through background tasks. Installs and execution use
the chat's approval policy.

Choose **System → Local models** to manage local GGUF chat models:

1. **Install runtime** downloads a checksum-pinned llama.cpp build for this
   computer. Linux/Windows builds use Vulkan; Mac builds use Metal where supported.
   GPU drivers and required OS libraries must be installed separately.
2. Choose **GPU** or **CPU** explicitly, then **Download** a starter model. Omote
   checks available RAM, estimated VRAM, platform support, and free space on the
   actual library disk. The UI shows download size and memory estimates.
3. **Cancel download** stops transfer and keeps the partial file. **Resume** uses
   HTTP byte ranges and validators, including after restarting Omote. If a server
   ignores ranges it restarts safely. Size and SHA-256 must match before the model
   becomes available; corrupt downloads are removed.
4. **Start** verifies the model again, checks the GPU backend, and starts one owned
   server on an authenticated loopback port. **Use in chat** opens a chat with that
   model and Ask first permissions. **Stop** frees the runtime; it never stops
   Ollama, ComfyUI or other servers started outside Omote. Existing chats reconnect
   when the same managed model is started again.

**Remove** deletes model and partial files after confirmation. **Add another GGUF
model** accepts a public HTTPS URL, published SHA-256 and exact size. Architecture
support is checked during startup; not every GGUF architecture is supported by
every runtime build. **Storage & runtime details** shows the library path, bounded
logs and **Repair runtime**. Runtime and weights live under the data directory's
`local-models` folder and survive app restarts. They are downloaded only on request.

Transfers run one at a time to avoid competing disk reservations, retain a 512 MiB
free-space margin, and recheck disk space while streaming. Runtime installation
also budgets 512 MiB for bounded unpacking. RAM/VRAM estimates cover an 8192-token
context and remain estimates. Actual unsupported drivers/models fail visibly;
GPU mode never silently selects a software Vulkan device. Managed chats report
the runtime's real context window and compact before reaching its limit.

Downloads use the network even when inference is local. Inference does not fall
back to a hosted model. On shutdown, transfers become resumable and the owned
runtime stops. On restart, a leftover process is stopped only when executable,
creation time and unique runtime key match the ownership record.

The agent can discover `local_models` through `find_tools` and use the same
library; list is read-only, downloads are network actions, runtime controls require
execution permission, and removing files is destructive. Discovery grants no permissions.

Starter weights come from [official Qwen repositories](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF).
The pinned runtime comes from [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases/tag/b11563),
with tool-aware [Jinja chat templates](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md).
Linux NVIDIA GPU download/resume, UI start, actual Omote chat, and stop have been
tested with Qwen3 0.6B and 1.7B on an RTX 5060 Ti. The 0.6B model is a hardware
test option; its replies were inconsistent under the full agent prompt. The 1.7B
model is listed first for everyday chat. Native Mac/Windows GPU runs remain unverified.
Additional inference runtime adapters and automatic multi-file diffusion packages
remain future work.

## Managed ComfyUI assets

Open **Studio → Models & workflows**. The library downloads ComfyUI safetensors
weights and JSON workflows with the same persistent Range/If-Range transfers,
cancel/resume, exact size, SHA-256 verification and 512 MiB free-space reserve as
managed chat models. Downloads survive restarts. Files with invalid safetensors
headers or invalid/oversized workflow JSON are rejected, even with a matching hash.
Nothing is installed or generated until explicitly requested.

The starter model is [Stability AI SD Turbo](https://huggingface.co/stabilityai/sd-turbo),
with an Omote four-step 512 × 512 image recipe. The library also offers the
[official ByteDance SDXL Lightning editor graph](https://huggingface.co/ByteDance/SDXL-Lightning/tree/main/comfyui),
which needs its own separate model weights. These are pinned to repository
revisions, sizes and checksums. Model licenses remain the publisher's; SD Turbo
is labeled for non-commercial research use.

1. Download a model. **Cancel download** retains its partial file; **Resume
   download** continues it. **Remove** deletes only that managed download.
2. Expand **ComfyUI setup & storage** once to obtain the extra-model-paths YAML
   configuration. Add it to ComfyUI's startup with `--extra-model-paths-config`,
   then restart ComfyUI. The configuration points at Omote's managed model folders;
   existing ComfyUI models and configuration are not overwritten. A remote server
   needs the downloaded files and matching paths on its own machine.
3. Select your ComfyUI connection and **Check connection** to verify model
   visibility. **Check & use in Studio** validates the SD Turbo recipe against
   live node schemas, model choices, links and GPU memory before opening Generate.
   Architecture support is finally validated when ComfyUI loads the weights.
4. Downloaded API workflows can be checked and used in Studio. Their recipe
   defaults are preserved. Native editor JSON is checked for missing node types
   and saved to the workbench; model selections still need review in ComfyUI.
   **Export JSON** also lets you open the verified document in the native editor.

**Add a model or workflow URL** accepts a public HTTPS URL, exact bytes and
published SHA-256. Models use safetensors and a selected ComfyUI model folder:
checkpoints, LoRAs, VAEs, text encoders, diffusion models, ControlNets, CLIP vision
or upscalers. A model filename can be supplied when a workflow expects one.
Workflow JSON is limited to 4 MiB and 5000 nodes, with image/video output selected
for API templates. Custom-node code is never downloaded or installed by this flow.

Managed files live in `local-models/comfy` under the data directory. Imported
Studio templates live in the data directory's `workflows` folder, rather than the
application installation. The lazy `local_models` tool accepts `library: "comfyui"`
for list/download/cancel/remove, using the same action-specific approvals.
ComfyUI's process lifecycle remains managed by your existing ComfyUI installation.

Live verification on Linux used ComfyUI 0.39.2, PyTorch CUDA 13.0 and an RTX 5060 Ti.
The 5,214,561,328-byte SD Turbo transfer was cancelled at 240,774,136 bytes and resumed
from exactly that offset. Studio then checked the connected server, loaded the
recipe and generated a real 512 × 512 image into its gallery. First generation
took about 22 seconds including model initialization; this is a smoke test, not
a performance benchmark. Native Mac/Windows diffusion runs remain unverified.

The shell supports per-command root/admin access on Linux, macOS and Windows.
Authentication stays in the OS dialog; Omote never asks for passwords in chat.
See [platform notes](INSTALL.md#platform-notes) for routes and limitations.

## Work that returns on a schedule

Open **Scheduled → New task** to save a daily briefing, weekly project review,
one-time follow-up or recurring research task. Each run has its own chat, uses the
chosen permissions and exposes questions, approvals and results. Keep Omote running
on an awake computer. **Pause** prevents future runs; **Stop run** ends current work.
See [Scheduled tasks](SCHEDULES.md) for time zones, recovery and stored data.

## Images, video, and the full ComfyUI editor

Use **Studio → Generate** for provider-specific image and video forms, reference
images, jobs, cancellation, and the generated-media gallery. Connect an image/video
provider first. ComfyUI generation in this form requires an API-format workflow
template matching the checkpoints and custom nodes installed on its server.

Use **Studio → ComfyUI editor** to edit the actual ComfyUI graph. Connect ComfyUI
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

The **Workflow workbench** beside the canvas adds local version history, project
labels, an API node inspector, validation against the connected server's installed
node schemas, and run history. **Run saved version** queues the exact saved API
prompt. **Keep outputs** copies a finished run's images, videos, or supported 3D
models into the creative library with provenance. Installing the optional
[editor bridge](../integrations/comfyui/README.md) enables **Capture editor** and
**Load in editor**, preserving native graph JSON, custom widgets and subgraphs.
Without the bridge, import/export JSON and the native editor remain available.

Graphs queued directly inside the native editor use ComfyUI's storage and controls;
they are not automatically copied into Omote. To use an API graph with the simple
form and gallery, choose **Import a workflow** in Generate. ComfyUI editor actions
run under ComfyUI's own controls, outside the chat agent's approval policy.

## Characters, assets, and companions

**Studio → Characters & assets** stores reusable character profiles, ordered image
references, appearance prompts, continuity notes and projects. **Use** prepares the
profile in Generate; **Save & make reference sheet** prepares a reference-sheet
prompt for review. The chosen model still determines consistency and supported
reference inputs. Generated results have **Keep in library** in their preview.

Import images, videos, or self-contained GLB, GLTF, OBJ and STL models. The 3D viewer
has orbit/pan/zoom, fit, grid, wireframe, backgrounds and animation playback for
supported clips. Asset details expose associations, tags, the original file,
provenance and generation recipes. Export the library as a ZIP with a manifest
and original files. See [Creative workspace](CREATIVE.md) for setup and storage.

The companion picker has nine characters, including Maple the fox, Quill the owl,
Inky the octopus and Orbit the drone. Each reacts to the agent's current activity
with distinct behavior. Search and preview them in the picker; motion can be
reduced independently of the system preference.

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
The dedicated **PhotoCraft** tool also inspects and edits layered graphics through
its installed CLI, with verified separate outputs and image previews. General
browser-based EMS, email and travel workflows use real pages and configured
accounts. See [Personal and business work](BUSINESS.md) for setup and examples.
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

## User and project memory (Brain)

Open **Brain** to opt in. Automatic capture is a separate switch. Select an embedding
model in Connections; the chat model is not automatically used for embeddings.
The dialog shows the embedding model, index status, every saved item's text, kind,
project/subject, source, creation time, and recall count. **Load more** reaches entries
beyond the first page; search and personal/project filters cover the entire
collection locally. **Export all** includes the complete collection.

You can add facts or project details, correct existing entries, forget individual
items, or delete everything. Pausing stops capture and recall and keeps saved entries
visible. Corrections work while paused or offline: the old vector is immediately
invalidated and the corrected entry waits for indexing. Manual additions also
survive embedding outages. **Memory settings & search connection → Index pending
memories** retries when memory and the provider are available. Paused users are not
re-embedded.

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
