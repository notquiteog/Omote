# Creative workspace

Omote combines model-driven generation, your installed ComfyUI frontend, a local
workflow workbench, and a reusable character and asset library. It does not bundle
model weights or claim to replace every ComfyUI extension.

**Studio → Models & workflows** downloads verified ComfyUI safetensors and JSON
workflows with cancel/resume, disk checks and live node/model compatibility checks.
An SD Turbo starter recipe opens directly in Generate after its model folder is
connected to ComfyUI. Storage setup and custom URLs stay under expandable details.
See [managed ComfyUI assets](WORKSPACE.md#managed-comfyui-assets) for setup and live
GPU verification. API recipes keep their original defaults; native editor JSON
can be saved to the workbench or exported into the full editor.

## Creative apps and export checks

**Studio → Creative apps** shows detected engines and configured creative MCP
connectors. Choose an app and **Use in chat** to prepare a request without sending
it. Setup instructions and connector configuration stay under expandable details.
Existing chat drafts are preserved. Use a configured chat model with tool calling;
image input is also needed for visual review. Model names are supplied by the
configured provider, rather than a separate creative-model allowlist.

The lazy `creative_apps` tool supports all 13 native ArtCraft engines: PhotoCraft,
VectorCraft, FilmCraft, EffectCraft, LightCraft, PdfCraft, DesignCraft, FigCraft,
WordCraft, SoundCraft, DeckCraft, CADCraft and GridCraft. `status` checks availability
without starting an engine. `tools` connects only the selected app and returns its
actual schemas with search and pagination. `call` invokes a discovered tool with
its original arguments, including the app's editing, rendering and export tools.
Keep the returned session ID across edits so documents, layers and timelines stay
open. Save before closing the session or chat; up to four editor sessions can be
open, and opening another never silently discards an unsaved document.

Install each engine's official CLI on PATH, set `OMOTE_PHOTOCRAFT_BIN` (or the
equivalent uppercase app ID), or put the executable at
`<Omote data directory>/creative-apps/<app-id>/bin/<app-id>-cli` (`.exe` on Windows).
CLI programs and weights are not bundled with Omote. Native engine discovery also
checks standard application locations on macOS/Windows. Engines launch in the
chat's selected project folder. PhotoCraft, VectorCraft and EffectCraft receive
their automation read/write roots; other released engines have different scoping
interfaces and are reported as running with account permissions. Ordinary tool
approvals still apply. A working directory alone is not an OS sandbox.

Adobe applications and other creative applications can use an operator-installed
MCP bridge: add `"creative": true` to that server's `.mcp.json` configuration and
reconnect it. It then appears in the same picker and discovery interface. Install
the application and a bridge it actually supports; Omote does not provide an
Adobe license or claim that every Adobe app has an official MCP server. The main
ArtCraft web/3D studio is separate from the 13 native engines and requires its
own supported connection or browser workflow.

Ask the agent to save both an editable native project and a delivery export,
inspect the app's preview, then use `inspect_export`. This tool confines files to
the chat's allowed roots, checks nonempty output and SHA-256, decodes raster images,
and reads SVG canvas dimensions without resolving external entities. With FFmpeg
installed it checks video/audio codecs, dimensions, duration, frame rate and audio
presence; `decode: true` decodes the complete media file before delivery. Expected
properties produce explicit failures instead of a successful filename alone.
Native project hashes do not prove layer/timeline fidelity: reopen them in the app.

Live Linux verification connected all 13 released engines and discovered their
actual MCP catalogs. PhotoCraft saved/reopened a five-layer PSD and exported PNG;
VectorCraft saved/reopened editable artwork and exported SVG/PNG; FilmCraft saved
and reopened a timeline, exported FCPXML and a 48-frame H.264 video; EffectCraft
saved/reopened motion graphics, edited keyframes and exported PNG/Lottie. The video
passed a complete FFmpeg decode. This does not establish every engine command,
format, platform, Adobe bridge, or individual model's task success.

## From a character to a scene

1. Open **Studio → Characters & assets → New character**. Give the character a
   name, story, appearance, continuity notes and optional project/tags.
2. Add image references. Select the primary reference first; selection order is
   retained. The original files remain visible in the asset library.
3. **Use** prepares the character prompt and supported image inputs in Generate.
   Review the chosen model and inputs before generating. **Save & make reference
   sheet** prepares a multi-view reference-sheet prompt; it does not immediately
   start a paid generation.
4. Open a generated result and choose **Keep in library**. Its prompt, provider,
   model, seed and saved parameters travel with the original result when available.
   In asset details, associate characters, add tags and inspect the recipe.
   Associating an asset with a character does not select it as an identity reference;
   choose identity reference images explicitly in the character editor.

Reference images guide supported models; they do not guarantee identical faces,
outfits or anatomy. Text-only models receive the character description. Model
input schemas determine which reference controls are available. The primary
reference is used for single-image inputs; review additional controls explicitly.
Prepared references are snapshots in the generation media store. Deleting an
original library asset does not remove an independently prepared snapshot or
export. The generation gallery and library each expose deletion.

## ComfyUI workbench

Configure a ComfyUI connection and open **Studio → ComfyUI editor**. The full connected
frontend remains the graph editor, including its custom nodes, groups, subgraphs,
previews, and Nodes 2.0 when that installation supports it. Omote does not maintain
a competing implementation of the renderer.

The canvas gets the workspace by default. **Workflow library** opens saved
versions and API controls alongside it (as an overlay drawer on narrow screens).
**Focus mode** fills the window; **Exit focus** or Escape in the Omote toolbar
returns to the usual layout. Switching Generate/ComfyUI/library or opening the
workflow drawer preserves the mounted editor and unsaved graph. Changing servers
or reloading asks first because it replaces the editor document. **Connection
options** contains refresh, reload and connection settings; **Open in tab** is
available when a server blocks embedding. Loading status describes the frame,
not a verified cross-origin application handshake.

Generate has prompt starters that fill an editable draft without running it.
Native workflow JSON imported from Generate opens in the workflow library with
the complete graph retained; API prompts continue through the generation-template
import path. On a phone the generation form and results use one vertical flow.

The optional [bridge](../integrations/comfyui/README.md) is downloadable inside the
workbench. Extract it into ComfyUI's `custom_nodes`, restart ComfyUI, and add the
exact Omote origin in **Omote: trusted workspace origins**. **Connect bridge**, then
**Capture editor** to retrieve both native JSON and the executable API prompt.
The bridge can read and replace the current graph; it cannot queue execution.
The allowlist is empty until configured and accepts no wildcard origins.

**Load in editor** also accepts an API-only draft. The bridge uses the connected
frontend's native API importer, then captures both the generated editor layout
and canonical executable prompt. Save that paired result as a new version.
**Edit with chat** prepares a request referencing the exact saved workflow and
revision; save pending edits first. It does not submit the request or run a graph.

The lazy `comfy_workflow` tool uses this same version library and run history.
Models can discover the live server's node/model schemas, inspect subsets of large
graphs, create workflows, atomically patch inputs/nodes, compare versions, validate,
export API/editor JSON, run an exact saved revision and keep outputs in Studio.
Updates require `base_revision`; stale edits fail without partial changes. API
patches to a paired editor graph require explicitly dropping its current layout
or saving both updated representations, while earlier versions retain their
layout. Load and capture the patched API graph to rebuild a native editor layout.
Linux verification authored a seven-node SD Turbo graph through the agent tool,
edited it, imported it into the official frontend, saved its layout, and generated
and retained a real image on an RTX 5060 Ti.

**Save version** keeps immutable graph/API snapshots, notes and project labels in
Omote. **Save copy** starts a separate history. Select an earlier version to export,
load or run it. Concurrent edits produce a conflict instead of silently overwriting
the latest version. Each revision retains its own name and project. Databases
created before versioned metadata was added inherit their then-current name/project
for older versions; their graph and prompt snapshots are preserved. Standard
workflow JSON and API JSON can also be imported.
Native JSON alone must be loaded and captured through ComfyUI to obtain its API
prompt before it can run from Omote.

**Compare saved versions** shows additions, removals and changed values between
any two revisions, including API inputs, graph links and custom Nodes 2.0 metadata.
Choose a baseline and compare it to the currently loaded saved revision. Unsaved
edits are excluded. The comparison is local and does not contact ComfyUI or execute
a graph. Large comparisons show the first 250 changes with shortened value previews;
export the versions to inspect everything. Arrays use their stored index order.

**Load node schemas** reads the actual installed node catalog. **Validate** checks
required inputs, available choices, numeric bounds, missing nodes, output slots
and dependency cycles. Custom-node runtime checks still belong to ComfyUI.
The inspector edits executable API inputs. Such edits discard the draft's native
snapshot because API parameters cannot reliably be mapped back to arbitrary
custom widgets. Capture the editor again to save a paired native/API snapshot.

**Run saved version** queues exactly the saved API prompt. Unsaved edits cannot
silently change that run. **Run history & outputs → Check status** reads the owned
prompt's status; **Keep outputs** copies recognized result files into the library.
History stores the provider, prompt ID, version and immutable snapshot. Queued
runs can be canceled individually; a running job is not stopped with ComfyUI's
server-wide interrupt because that could interrupt another client's job.

Files queued directly inside ComfyUI remain in ComfyUI. Workbench history only
tracks jobs queued through the workbench. Updating a connection does not move
old jobs to a different server; retain the original connection while collecting
its results. Backend calls honor connection transport and local-only settings.
Browser embedding still requires a browser-reachable editor URL.

## Asset library and 3D preview

Import PNG, JPEG, WebP, GIF, MP4, WebM, GLB, GLTF, OBJ or STL files up to 128 MiB each.
Browse by project, character, kind, name or tags. Rename and describe assets,
download their originals, inspect source metadata, delete them, or **Export library**
as a ZIP containing original files and a JSON manifest of characters and assets.
Character deletion leaves original assets available. Removing a character association
also removes that asset from the character’s selected references; deselecting a
reference leaves its ordinary character association available. Asset deletion removes its
character references. Storage is per configured user under `creative/` in the
Omote data directory; workflow versions and run records are in `workbench.db`.
These are local readable files, not encrypted storage. Back them up with the rest
of the Omote data directory when the daemon is stopped.

The bundled 3D viewer supports orbit/pan/zoom, fitting the camera, grid, wireframe,
background choices and embedded animation clips. It renders locally using WebGL
and bundled Three.js modules without a CDN. Closing or replacing the preview
releases its rendering resources. Self-contained models are required: remote
textures, filesystem paths and sidecar resource URLs are not fetched. OBJ files
show geometry without a separate material file; package textured scenes as GLB.
Draco/Meshopt/KTX2 extensions that require separately supplied decoders are not
bundled. The original remains downloadable when a preview is unsupported.

Omote can retain 3D output from a configured ComfyUI workflow or import a model
created elsewhere; the viewer is not itself a mesh-generation model. Generated
images and videos continue to use the configured image/video providers. Native
editing uses the Creative apps integration above or installed desktop applications.
