/* Local blocking, camera and lighting. Three.js is loaded only for the stage. */
import * as THREE from './vendor/three/three.module.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import { el } from './dom.js';

export function mountStage(container, data, ratio, changed, onCapture) {
  const viewport = el('div', 'stage-viewport');
  const [w, h] = ratio.split(':').map(Number); viewport.style.aspectRatio = String(w / h);
  const sidebar = el('div', 'stage-inspector'); container.append(viewport, sidebar);
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true }); }
  catch { viewport.append(el('p', 'meta', 'WebGL is unavailable. Your scene is saved; use a browser with hardware acceleration to preview it.')); return () => {}; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  viewport.append(renderer.domElement); renderer.domElement.setAttribute('aria-label', 'Editable 3D scene');
  const scene = new THREE.Scene(); scene.background = new THREE.Color(data.background);
  const camera = new THREE.PerspectiveCamera(data.fov, w / h, .1, 5000); camera.position.fromArray(data.camera);
  const controls = new OrbitControls(camera, renderer.domElement); controls.target.fromArray(data.target); controls.minDistance = .5; controls.maxDistance = 200; controls.update();
  const light = new THREE.DirectionalLight(0xffffff, data.light); light.position.set(4, 8, 5); light.castShadow = true; light.shadow.mapSize.set(1024, 1024);
  Object.assign(light.shadow.camera, { left: -12, right: 12, top: 12, bottom: -12, near: .1, far: 60 });
  light.shadow.bias = -.0003; scene.add(light, new THREE.AmbientLight(0xffffff, 1));
  const grid = new THREE.GridHelper(40, 40, 0x747d8f, 0x3c4350); scene.add(grid);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), new THREE.MeshStandardMaterial({ color: '#343a44', roughness: 1 }));
  floor.receiveShadow = true; floor.rotation.x = -Math.PI / 2; floor.position.y = -.015; scene.add(floor);
  let group = new THREE.Group(), selected = data.objects[0]?.id, helper, stopped = false, drag = null;
  scene.add(group);
  const dispose = root => root.traverse(o => { o.geometry?.dispose(); for (const m of (Array.isArray(o.material) ? o.material : [o.material])) m?.dispose(); });
  const render = () => { if (!stopped) { helper?.update(); renderer.render(scene, camera); } };
  function highlight() {
    if (helper) { scene.remove(helper); dispose(helper); helper = null; }
    const object = group.children.find(o => o.userData.id === selected);
    if (object) { helper = new THREE.BoxHelper(object, '#e4d4ac'); scene.add(helper); }
  }
  function objects() {
    scene.remove(group); dispose(group); group = new THREE.Group(); scene.add(group);
    for (const item of data.objects) {
      const material = new THREE.MeshStandardMaterial({ color: item.color, roughness: .65 });
      let mesh;
      if (item.shape === 'person') {
        mesh = new THREE.Group();
        const torso = new THREE.Mesh(new THREE.CylinderGeometry(.25, .3, 1.2, 16), material); torso.position.y = .6;
        const head = new THREE.Mesh(new THREE.SphereGeometry(.24, 16, 12), material); head.position.y = 1.45; mesh.add(torso, head);
      } else {
        const geometry = item.shape === 'sphere' ? new THREE.SphereGeometry(.5, 24, 16)
          : item.shape === 'cylinder' ? new THREE.CylinderGeometry(.5, .5, 1, 24) : new THREE.BoxGeometry(1, 1, 1);
        mesh = new THREE.Mesh(geometry, material);
      }
      mesh.position.fromArray(item.position); mesh.scale.fromArray(item.scale); mesh.rotation.y = THREE.MathUtils.degToRad(item.rotation);
      mesh.traverse(o => { o.userData.id = item.id; if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } }); group.add(mesh);
    }
    highlight(); render();
  }
  function button(label, run) {
    const b = el('button', 'ghost', label); b.type = 'button';
    b.onclick = async () => { b.disabled = true; try { await run(); } catch (e) { status.textContent = e.message; } finally { b.disabled = false; } }; return b;
  }
  function input(label, value, update, { type = 'number', min = -1000, max = 1000, step = .1 } = {}) {
    const wrap = el('label', 'project-field'); wrap.append(el('span', '', label));
    const node = el('input'); node.setAttribute('aria-label', label); node.type = type; node.value = value;
    if (type === 'number') { node.min = min; node.max = max; node.step = step; }
    node.onchange = () => {
      if (!node.reportValidity()) return;
      update(type === 'number' ? Number(node.value) : node.value); changed(); objects();
    };
    wrap.append(node); return wrap;
  }
  const status = el('p', 'meta');
  function inspector() {
    sidebar.replaceChildren();
    const capture = button('Capture reference frame', async () => {
        grid.visible = false; if (helper) helper.visible = false;
        let blob;
        try {
          // A consistent export size is independent of the size of the open dialog.
          const width = w >= h ? 1280 : Math.round(1280 * w / h), height = Math.round(width * h / w);
          renderer.setPixelRatio(1); renderer.setSize(width, height, false); render();
          blob = await new Promise(resolve => renderer.domElement.toBlob(resolve, 'image/png'));
        } finally {
          grid.visible = true; if (helper) helper.visible = true;
          renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); resize();
        }
        if (stopped) return;
        if (!blob) throw new Error('The browser could not capture this frame.');
        await onCapture(blob); status.textContent = 'Reference frame saved to your first shot.';
      });
    capture.className = 'primary'; sidebar.append(capture);
    const add = el('div', 'project-actions');
    for (const shape of ['box', 'sphere', 'cylinder', 'person']) add.append(button('Add ' + shape, () => {
      if (data.objects.length >= 60) throw new Error('This stage supports up to 60 objects.');
      const item = { id: crypto.randomUUID(), name: shape[0].toUpperCase() + shape.slice(1), shape,
        position: [0, shape === 'person' ? 0 : .5, 0], scale: [1, 1, 1], rotation: 0, color: '#a5b6cc' };
      data.objects.push(item); selected = item.id; changed(); objects(); inspector();
    })); sidebar.append(add);
    const chooser = el('select'); chooser.setAttribute('aria-label', 'Scene object');
    for (const item of data.objects) { const option = el('option', '', item.name); option.value = item.id; chooser.append(option); }
    chooser.value = selected || ''; chooser.onchange = () => { selected = chooser.value; highlight(); render(); inspector(); }; sidebar.append(chooser);
    const item = data.objects.find(o => o.id === selected);
    if (item) {
      sidebar.append(input('Object name', item.name, v => { item.name = v.slice(0, 100) || 'Object'; chooser.selectedOptions[0].textContent = item.name; }, { type: 'text' }),
        input('Color', item.color, v => { item.color = v; }, { type: 'color' }));
      for (const [key, title] of [['position', 'Position'], ['scale', 'Size']]) {
        const row = el('div', 'stage-vector');
        ['X', 'Y', 'Z'].forEach((axis, i) => row.append(input(`${title} ${axis}`, Math.round(item[key][i] * 100) / 100,
          v => { item[key][i] = v; }, { min: key === 'scale' ? .05 : -1000, max: key === 'scale' ? 100 : 1000 })));
        sidebar.append(row);
      }
      sidebar.append(input('Rotation', item.rotation, v => { item.rotation = v; }, { min: -360, max: 360, step: 5 }),
        button('Remove object', () => { data.objects = data.objects.filter(o => o.id !== selected); selected = data.objects[0]?.id; changed(); objects(); inspector(); }));
    }
    sidebar.append(el('h3', '', 'Camera & light'), input('Field of view', data.fov, v => { data.fov = v; camera.fov = v; camera.updateProjectionMatrix(); }, { min: 15, max: 100, step: 1 }),
      input('Light intensity', data.light, v => { data.light = v; light.intensity = v; }, { min: 0, max: 10 }),
      input('Background', data.background, v => { data.background = v; scene.background.set(v); }, { type: 'color' }),
      status);
  }
  function resize() { if (stopped) return; renderer.setSize(viewport.clientWidth || 640, (viewport.clientWidth || 640) * h / w, false); render(); }
  controls.addEventListener('change', () => {
    data.camera = camera.position.toArray(); data.target = controls.target.toArray(); changed(); render();
  });
  const raycaster = new THREE.Raycaster(), mouse = new THREE.Vector2(), point = new THREE.Vector3();
  function ray(e) {
    const rect = renderer.domElement.getBoundingClientRect(); mouse.set((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.setFromCamera(mouse, camera);
  }
  function down(e) {
    if (e.button !== 0) return;
    ray(e); const hit = raycaster.intersectObjects(group.children, true)[0]; if (!hit) return;
    selected = hit.object.userData.id; const item = data.objects.find(o => o.id === selected);
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -hit.point.y);
    if (!raycaster.ray.intersectPlane(plane, point)) return;
    drag = { item, plane, offset: point.clone().sub(new THREE.Vector3().fromArray(item.position)) };
    controls.enabled = false; renderer.domElement.setPointerCapture(e.pointerId); e.stopImmediatePropagation(); highlight(); inspector(); render();
  }
  function move(e) {
    if (!drag) return;
    ray(e); if (!raycaster.ray.intersectPlane(drag.plane, point)) return;
    drag.item.position[0] = Math.max(-1000, Math.min(1000, point.x - drag.offset.x));
    drag.item.position[2] = Math.max(-1000, Math.min(1000, point.z - drag.offset.z)); objects(); changed();
  }
  function up() { if (drag) { drag = null; controls.enabled = true; inspector(); } }
  renderer.domElement.addEventListener('pointerdown', down, true);
  renderer.domElement.addEventListener('pointermove', move);
  renderer.domElement.addEventListener('pointerup', up);
  renderer.domElement.addEventListener('pointercancel', up);
  const observer = new ResizeObserver(resize); observer.observe(viewport);
  objects(); inspector(); resize();
  return () => { stopped = true; observer.disconnect(); controls.dispose(); dispose(scene); renderer.dispose(); renderer.forceContextLoss(); container.replaceChildren(); };
}
