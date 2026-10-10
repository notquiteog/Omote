/* Self-contained, local 3D preview. No CDN, decoder downloads or model URLs. */
import * as THREE from './vendor/three/three.module.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import { GLTFLoader } from './vendor/three/GLTFLoader.js';
import { OBJLoader } from './vendor/three/OBJLoader.js';
import { STLLoader } from './vendor/three/STLLoader.js';
import { api, el } from './dom.js';

const active = new WeakMap();
const MAX_BYTES = 128 * 1024 * 1024;
const DATA_RESOURCE = /^data:(?:image\/(?:png|jpeg|webp)|application\/(?:octet-stream|gltf-buffer));base64,[A-Za-z0-9+/=\r\n]*$/;

/* Check again in the client: an imported catalogue or a changed server must
 * not turn a model into a request for a website, local service or filesystem. */
function modelJSON(data, extension) {
  if (extension === 'gltf') return JSON.parse(new TextDecoder().decode(data));
  if (data.byteLength < 20) throw new Error('The GLB header is incomplete.');
  const header = new DataView(data);
  if (header.getUint32(0, true) !== 0x46546c67 || header.getUint32(4, true) !== 2 ||
      header.getUint32(8, true) !== data.byteLength || header.getUint32(16, true) !== 0x4e4f534a) {
    throw new Error('The GLB header is invalid.');
  }
  const size = header.getUint32(12, true);
  if (size > data.byteLength - 20 || size > 16 * 1024 * 1024) throw new Error('The GLB JSON is too large or incomplete.');
  return JSON.parse(new TextDecoder().decode(new Uint8Array(data, 20, size)).replace(/[\0\s]+$/, ''));
}

function checkResources(document) {
  if (!document || document.asset?.version !== '2.0') throw new Error('Only glTF 2.0 is supported.');
  const pending = [document];
  let visited = 0;
  while (pending.length) {
    if (++visited > 250000) throw new Error('The model has too many JSON objects.');
    const value = pending.pop();
    if (value && typeof value === 'object') {
      for (const [key, child] of Object.entries(value)) {
        if (/^(?:uri|url)$/i.test(key) && (typeof child !== 'string' || !DATA_RESOURCE.test(child))) {
          throw new Error('External model resources are disabled. Export a self-contained GLB with embedded textures.');
        }
        pending.push(child);
      }
    }
  }
}

function disposeTree(root) {
  const geometries = new Set(), materials = new Set(), textures = new Set();
  root?.traverse(object => {
    if (object.geometry) geometries.add(object.geometry);
    for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
      if (!material) continue;
      materials.add(material);
      for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
    }
    object.skeleton?.dispose?.();
  });
  for (const texture of textures) {
    texture.source?.data?.close?.();
    texture.dispose();
  }
  for (const material of materials) material.dispose();
  for (const geometry of geometries) geometry.dispose();
}

function modelStats(root, clipCount) {
  let meshes = 0, vertices = 0, triangles = 0;
  const materials = new Set();
  root.traverse(object => {
    if (!object.isMesh) return;
    meshes += object.isInstancedMesh ? object.count : 1;
    const copies = object.isInstancedMesh ? object.count : 1;
    vertices += (object.geometry?.attributes.position?.count || 0) * copies;
    triangles += ((object.geometry?.index?.count || object.geometry?.attributes.position?.count || 0) / 3) * copies;
    for (const material of (Array.isArray(object.material) ? object.material : [object.material])) materials.add(material);
  });
  const fmt = n => Math.round(n).toLocaleString();
  return `${fmt(meshes)} meshes · ${fmt(vertices)} vertices · ${fmt(triangles)} triangles · ` +
    `${materials.size} materials${clipCount ? ` · ${clipCount} animations` : ''}`;
}

/** Cancel even a preview whose initial fetch or texture decoding is still pending. */
export function unmountModel(container) { active.get(container)?.(); }

/** Mount one model and return an idempotent cleanup function, including errors. */
export async function mountModel(container, toolbar, asset) {
  active.get(container)?.();
  const controller = new AbortController();
  let stopped = false, renderer, controls, scene, root, grid, observer, detachObserver, mixer;
  let frame = 0, playing = false, previousTime = 0, action = null;
  const generatedURLs = new Set();
  const cleanup = () => {
    if (stopped) return;
    stopped = true;
    controller.abort();
    cancelAnimationFrame(frame);
    observer?.disconnect();
    detachObserver?.disconnect();
    document.removeEventListener('visibilitychange', visibility);
    controls?.dispose();
    mixer?.stopAllAction();
    if (root) mixer?.uncacheRoot(root);
    disposeTree(scene);
    renderer?.dispose();
    renderer?.forceContextLoss();
    for (const url of generatedURLs) URL.revokeObjectURL(url);
    generatedURLs.clear();
    if (active.get(container) === cleanup) {
      active.delete(container);
      container.replaceChildren();
      toolbar.replaceChildren();
      container.dataset.modelState = 'disposed';
    }
  };
  active.set(container, cleanup);
  container.dataset.modelState = 'loading';
  container.replaceChildren(el('p', 'model-status', 'Loading 3D model…'));
  toolbar.replaceChildren();
  const status = container.firstChild;
  status.setAttribute('role', 'status');
  let camera;
  function schedule() {
    if (!stopped && !frame && !document.hidden) frame = requestAnimationFrame(render);
  }
  function render(time) {
    frame = 0;
    if (stopped) return;
    const delta = previousTime ? Math.min((time - previousTime) / 1000, .1) : 0;
    previousTime = time;
    if (mixer && playing) mixer.update(delta);
    try {
      controls.update();
      renderer.render(scene, camera);
      if (playing) schedule();
    } catch (error) { fail(error); }
  }
  function visibility() {
    previousTime = 0;
    if (document.hidden) { cancelAnimationFrame(frame); frame = 0; }
    else schedule();
  }
  function fail(error) {
    if (stopped) return;
    cleanup();
    container.dataset.modelState = 'error';
    const message = el('p', 'model-error', `Preview unavailable: ${error.message || 'this model could not be decoded.'}`);
    message.setAttribute('role', 'alert');
    container.append(message);
  }
  try {
    const url = new URL(asset.url, window.location.href);
    if (url.origin !== window.location.origin || url.search || url.hash ||
        !/^\/api\/creative\/assets\/[a-f0-9]{32}\/file$/.test(url.pathname)) {
      throw new Error('Only files owned by this creative library can be previewed.');
    }
    const extension = String(asset.filename || '').split('.').pop().toLowerCase();
    if (!['glb', 'gltf', 'obj', 'stl'].includes(extension)) throw new Error('Choose a GLB, GLTF, OBJ or STL model.');
    const response = await api(url.pathname, { signal: controller.signal, credentials: 'same-origin' });
    if (stopped) return cleanup;
    if (!response?.ok) throw new Error(`The model file could not be loaded${response ? ` (${response.status})` : ''}.`);
    const length = Number(response.headers.get('content-length') || 0);
    if (length > MAX_BYTES) throw new Error('This model exceeds the 128 MB preview limit.');
    const chunks = [];
    let size = 0;
    const reader = response.body.getReader();
    try {
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        size += next.value.byteLength;
        if (size > MAX_BYTES) { await reader.cancel(); throw new Error('This model exceeds the 128 MB preview limit.'); }
        chunks.push(next.value);
      }
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    if (stopped) return cleanup;
    const manager = new THREE.LoadingManager();
    manager.setURLModifier(resource => {
      if (DATA_RESOURCE.test(resource)) return resource;
      // The glTF document is checked before parsing, so blob URLs can only
      // originate in the loader's embedded buffer-view texture conversion.
      if (resource.startsWith('blob:')) { generatedURLs.add(resource); return resource; }
      throw new Error('External model resource requests are disabled.');
    });
    let clips = [];
    if (extension === 'glb' || extension === 'gltf') {
      const document = modelJSON(bytes.buffer, extension);
      checkResources(document);
      const result = await new GLTFLoader(manager).parseAsync(bytes.buffer, '');
      root = result.scene;
      clips = result.animations || [];
      if (stopped) { disposeTree(root); return cleanup; }
    } else if (extension === 'obj') {
      const source = new TextDecoder().decode(bytes);
      if (/^\s*(?:mtllib|call|csh|map_\w*)\b/im.test(source)) {
        throw new Error('External OBJ materials are disabled. Export a GLB to preserve textures.');
      }
      root = new OBJLoader(manager).parse(source);
    } else {
      const geometry = new STLLoader(manager).parse(bytes.buffer);
      geometry.computeVertexNormals();
      root = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
        color: 0x93b8cc, roughness: .7, metalness: .08, side: THREE.DoubleSide,
        vertexColors: geometry.hasAttribute('color'),
      }));
    }
    if (!container.isConnected) { disposeTree(root); cleanup(); return cleanup; }
    scene = new THREE.Scene();
    scene.background = new THREE.Color('#17191d');
    scene.add(root);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x536179, 2.7));
    const key = new THREE.DirectionalLight(0xffffff, 3);
    key.position.set(3, 5, 4);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x9fbfff, 1.3);
    rim.position.set(-3, 2, -3);
    scene.add(rim);
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    const canvas = renderer.domElement;
    canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:none;';
    canvas.tabIndex = 0;
    canvas.setAttribute('aria-label', `3D preview of ${asset.name || asset.filename}. Drag to orbit, right-drag to pan, scroll to zoom.`);
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      if (stopped) return;
      cleanup();
      container.dataset.modelState = 'error';
      container.append(el('p', 'model-error', 'The graphics context was lost. Reopen the model to retry.'));
    });
    camera = new THREE.PerspectiveCamera(45, 1, .01, 1000);
    controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = .08;
    controls.addEventListener('change', schedule);
    controls.listenToKeyEvents(canvas);
    grid = new THREE.GridHelper(10, 20, 0x77818d, 0x424954);
    scene.add(grid);
    const bounds = new THREE.Box3().setFromObject(root);
    const center = bounds.getCenter(new THREE.Vector3());
    const dimensions = bounds.getSize(new THREE.Vector3());
    const extent = Math.max(dimensions.x, dimensions.y, dimensions.z);
    if (bounds.isEmpty() || !Number.isFinite(extent) || extent <= 0 || extent > 1e12) {
      throw new Error('This model has no finite, visible geometry.');
    }
    grid.position.set(center.x, bounds.min.y, center.z);
    grid.scale.setScalar(extent / 4);
    function fit() {
      const radius = dimensions.length() / 2;
      const distance = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) /
        Math.min(camera.aspect, 1) * 1.25;
      camera.near = Math.max(distance / 1000, .000001);
      camera.far = distance * 1000;
      camera.position.copy(center).add(new THREE.Vector3(1, .7, 1).normalize().multiplyScalar(distance));
      camera.updateProjectionMatrix();
      controls.target.copy(center);
      controls.minDistance = radius / 100;
      controls.maxDistance = distance * 100;
      controls.update();
      controls.saveState();
      schedule();
    }
    function button(text, label, onClick) {
      const node = el('button', 'model-button', text);
      node.type = 'button'; node.title = label; node.setAttribute('aria-label', label);
      node.onclick = onClick;
      toolbar.append(node);
      return node;
    }
    button('Fit', 'Fit model in view', fit);
    const gridButton = button('Grid', 'Toggle ground grid', () => {
      grid.visible = !grid.visible;
      gridButton.setAttribute('aria-pressed', String(grid.visible)); schedule();
    });
    gridButton.setAttribute('aria-pressed', 'true');
    let wireframe = false;
    const wireButton = button('Wireframe', 'Toggle wireframe', () => {
      wireframe = !wireframe;
      root.traverse(object => {
        for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
          if (material && 'wireframe' in material) material.wireframe = wireframe;
        }
      });
      wireButton.setAttribute('aria-pressed', String(wireframe)); schedule();
    });
    wireButton.setAttribute('aria-pressed', 'false');
    const background = el('select', 'model-background');
    background.setAttribute('aria-label', 'Model background');
    for (const [name, value] of [['Dark', '#17191d'], ['Light', '#edf0f3'], ['Neutral', '#777777'], ['Transparent', '']]) {
      const option = el('option', '', name); option.value = value; background.append(option);
    }
    background.onchange = () => { scene.background = background.value ? new THREE.Color(background.value) : null; schedule(); };
    toolbar.append(background);
    if (clips.length) {
      mixer = new THREE.AnimationMixer(root);
      const animation = el('select', 'model-animation');
      animation.setAttribute('aria-label', 'Animation clip');
      clips.forEach((clip, index) => {
        const option = el('option', '', clip.name || `Animation ${index + 1}`); option.value = String(index); animation.append(option);
      });
      const playButton = button('Play', 'Play animation', () => {
        playing = !playing;
        if (playing) {
          action ||= mixer.clipAction(clips[Number(animation.value)]);
          action.play();
        }
        playButton.textContent = playing ? 'Pause' : 'Play';
        playButton.setAttribute('aria-label', playing ? 'Pause animation' : 'Play animation');
        previousTime = 0; schedule();
      });
      animation.onchange = () => {
        mixer.stopAllAction(); action = mixer.clipAction(clips[Number(animation.value)]);
        action.reset(); if (playing) action.play();
        mixer.update(0); schedule();
      };
      toolbar.append(animation);
    }
    const stats = el('span', 'model-stats', modelStats(root, clips.length));
    stats.setAttribute('role', 'status');
    toolbar.append(stats);
    const hints = el('span', 'model-controls-hint', 'Drag to orbit · right-drag to pan · scroll to zoom');
    hints.style.cssText = 'position:absolute;bottom:10px;left:10px;pointer-events:none;font-size:11px;color:#bac3ce;background:#17191dbb;padding:4px 7px;border-radius:5px;';
    if (getComputedStyle(container).position === 'static') container.style.position = 'relative';
    container.replaceChildren(canvas, hints);
    const resize = () => {
      if (stopped) return;
      const { width, height } = container.getBoundingClientRect();
      if (width < 1 || height < 1) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height; camera.updateProjectionMatrix(); schedule();
    };
    observer = new ResizeObserver(resize);
    observer.observe(container);
    resize(); fit();
    detachObserver = new MutationObserver(() => { if (!container.isConnected) cleanup(); });
    detachObserver.observe(document.body, { childList: true, subtree: true });
    document.addEventListener('visibilitychange', visibility);
    container.dataset.modelState = 'ready';
    schedule();
  } catch (error) {
    fail(error);
  }
  return cleanup;
}
