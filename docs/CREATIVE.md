# Creative workspace

Omote combines model-driven generation, your installed ComfyUI frontend, a local
workflow workbench, and a reusable character and asset library. It does not bundle
model weights or claim to replace every ComfyUI extension.

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

Configure a ComfyUI connection and open **Advanced · ComfyUI**. The full connected
frontend remains the graph editor, including its custom nodes, groups, subgraphs,
previews, and Nodes 2.0 when that installation supports it. Omote does not maintain
a competing implementation of the renderer.

The optional [bridge](../integrations/comfyui/README.md) is downloadable inside the
workbench. Extract it into ComfyUI's `custom_nodes`, restart ComfyUI, and add the
exact Omote origin in **Omote: trusted workspace origins**. **Connect bridge**, then
**Capture editor** to retrieve both native JSON and the executable API prompt.
The bridge can read and replace the current graph; it cannot queue execution.
The allowlist is empty until configured and accepts no wildcard origins.

**Save version** keeps immutable graph/API snapshots, notes and project labels in
Omote. **Save copy** starts a separate history. Select an earlier version to export,
load or run it. Concurrent edits produce a conflict instead of silently overwriting
the latest version. Each revision retains its own name and project. Databases
created before versioned metadata was added inherit their then-current name/project
for older versions; their graph and prompt snapshots are preserved. Standard
workflow JSON and API JSON can also be imported.
Native JSON alone must be loaded and captured through ComfyUI to obtain its API
prompt before it can run from Omote.

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
images and videos continue to use the configured image/video providers. Layered
2D editing is available through the installed [PhotoCraft integration](BUSINESS.md#photocraft)
or desktop applications.
