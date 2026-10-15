# Omote editor bridge

Copy `omote_bridge` into your ComfyUI `custom_nodes` directory and restart ComfyUI.
In ComfyUI Settings, search for **Omote: trusted workspace origins**. Add the exact
origin shown in Omote's workbench, e.g. `http://127.0.0.1:8477`. Each origin includes
scheme, hostname, and port; there are no wildcard origins. Only add workspaces you
trust to read and replace the graph in this editor.

In Omote, open the editor and use **Connect bridge**. **Capture editor** saves both
editor JSON and its executable API prompt. **Load in editor** loads a selected saved
version after confirmation. Nodes 2.0, custom widgets, and subgraphs remain handled
by your own frontend; the extension calls its public `graphToPrompt`/`loadGraphData`
APIs and does not patch a renderer. Without the extension, JSON import/export and
the full native editor remain available.

API-only workflows use the frontend's `loadApiJson` importer, then capture its
generated native graph and canonical prompt. Save the result as a new version to
retain both. If a frontend lacks that importer, the bridge reports it explicitly;
update that installation or import/capture the workflow in its own interface.

The bridge does not execute workflows or expose filesystem/network endpoints.
Execution uses the configured server connection and the explicit workbench Run
button, with its own validation and run history. Stored workflows may contain
private prompts or node values; export only what you intend to share.

API reference: https://docs.comfy.org/custom-nodes/js/javascript_objects_and_hijacking
