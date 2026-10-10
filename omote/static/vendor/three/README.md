# Three.js runtime

Pinned to [Three.js r180](https://github.com/mrdoob/three.js/tree/r180), MIT licensed.
The original license is retained in `LICENSE`.

Vendored upstream files:

- `build/three.module.min.js` → `three.module.js`
- `build/three.core.min.js`
- `examples/jsm/controls/OrbitControls.js`
- `examples/jsm/loaders/GLTFLoader.js`, `OBJLoader.js`, `STLLoader.js`
- `examples/jsm/utils/BufferGeometryUtils.js`

Example module imports are rewritten to relative local imports; no runtime CDN
or package resolver is needed. The renderer core is otherwise unchanged. Omote's
viewer integration lives in `../../model-viewer.js`.
