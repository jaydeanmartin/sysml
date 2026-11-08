import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { api } from "../api";
import type { GeometryInfo, GeometryPart } from "../types";
import { cssVar, useTheme } from "../theme";
import { errorText, short, type CatalogPanelProps } from "./catalogUtil";

/** Everything three.js, kept out of React state. */
interface Viewer {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  model: THREE.Group | null;
  meshes: THREE.Mesh[];
  /** centre of the model, for the exploded view */
  center: THREE.Vector3;
  /** themed: background, grid and the light from the ground follow the colour theme */
  hemi: THREE.HemisphereLight;
  grid: THREE.GridHelper;
}

/** The floor grid in the theme's colours: 10 cm squares. */
const themedGrid = () => new THREE.GridHelper(10, 100, cssVar("--viewer-grid"), cssVar("--viewer-grid-minor"));

/** 3-D viewer of system instances. The geometry service generates a glTF scene
 *  from the component instances in the system's slots; the scene is re-read
 *  whenever the catalog changes. */
export function GeometryPanel({ projectId, catalog, loading, error, onOpen }: CatalogPanelProps) {
  const [systemQn, setSystemQn] = useState<string | null>(null);
  const [info, setInfo] = useState<GeometryInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [exploded, setExploded] = useState(0);
  const [discs, setDiscs] = useState(true);
  const [spin, setSpin] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<Viewer | null>(null);
  const { theme } = useTheme();

  const configs = catalog?.configurations || [];
  const system = configs.find((c) => c.qualifiedName === systemQn) || configs[0];
  const archs = catalog?.architectures || [];

  // ---------------------------------------------------------- three.js setup (once)
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(cssVar("--viewer-bg"));
    const hemi = new THREE.HemisphereLight("#ffffff", cssVar("--viewer-ground"), 1.6);
    scene.add(hemi);
    const sun = new THREE.DirectionalLight("#ffffff", 2.2);
    sun.position.set(3, 6, 4);
    scene.add(sun);
    const grid = themedGrid();
    scene.add(grid);
    const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 200);
    camera.position.set(1.5, 1.2, 1.5);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const v: Viewer = { renderer, scene, camera, controls, model: null, meshes: [], center: new THREE.Vector3(), hemi, grid };
    viewer.current = v;

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / Math.max(h, 1);
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    let frame = 0;
    const tick = () => {
      frame = requestAnimationFrame(tick);
      controls.update();
      renderer.render(scene, camera);
    };
    tick();
    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
      viewer.current = null;
    };
  }, []);

  // ---------------------------------------------------------- load the system's scene (and again after every catalog change)
  const fitKey = useRef<string | null>(null);
  useEffect(() => {
    const v = viewer.current;
    if (!v || !system) return;
    let stale = false;
    setBusy(true);
    setLoadError(null);
    api
      .geometry(projectId, system.qualifiedName)
      .then((buf) => new GLTFLoader().parseAsync(buf, ""))
      .then((gltf) => {
        if (stale) return;
        if (v.model) {
          v.scene.remove(v.model);
          v.model.traverse((o) => {
            if (o instanceof THREE.Mesh) {
              o.geometry.dispose();
              (o.material as THREE.Material).dispose();
            }
          });
        }
        const model = gltf.scene;
        const meshes: THREE.Mesh[] = [];
        model.traverse((o) => {
          if (o instanceof THREE.Mesh) {
            o.material = (o.material as THREE.MeshStandardMaterial).clone(); // highlighted one by one
            o.geometry.computeBoundingBox();
            meshes.push(o);
          }
        });
        v.scene.add(model);
        v.model = model;
        v.meshes = meshes;
        const box = new THREE.Box3().setFromObject(model);
        v.center = box.getCenter(new THREE.Vector3());
        // keep the camera when the same system is re-read after an edit
        if (fitKey.current !== system.qualifiedName) {
          fitKey.current = system.qualifiedName;
          const size = box.getSize(new THREE.Vector3()).length();
          v.controls.target.copy(v.center);
          v.camera.position.copy(v.center).add(new THREE.Vector3(0.75, 0.55, 0.9).multiplyScalar(size));
          v.camera.near = size / 100;
          v.camera.far = size * 100;
          v.camera.updateProjectionMatrix();
        }
        setInfo(model.userData as GeometryInfo);
      })
      .catch((e) => !stale && setLoadError(errorText(e)))
      .finally(() => !stale && setBusy(false));
    return () => {
      stale = true;
    };
  }, [projectId, system?.qualifiedName, catalog]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------- display options
  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    v.controls.autoRotate = spin;
    const select = new THREE.Color(cssVar("--accent"));
    const hover = new THREE.Color(cssVar("--active"));
    for (const m of v.meshes) {
      // exploded view: every part moves away from the centre, a little more upwards
      m.geometry.boundingBox!.getCenter(m.position).sub(v.center).multiply(new THREE.Vector3(1, 1.6, 1)).multiplyScalar(exploded);
      m.visible = discs || !m.name.endsWith("/disc");
      const mat = m.material as THREE.MeshStandardMaterial;
      const node = m.userData as GeometryPart;
      const sel = selected && partKey(node) === selected;
      const hov = hovered && partKey(node) === hovered;
      mat.emissive.copy(sel ? select : hov ? hover : new THREE.Color(0));
      mat.emissiveIntensity = sel ? 0.55 : hov ? 0.45 : 0;
    }
  }, [info, exploded, discs, spin, selected, hovered, theme]);

  // ---------------------------------------------------------- colour theme
  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    v.scene.background = new THREE.Color(cssVar("--viewer-bg"));
    v.hemi.groundColor.set(cssVar("--viewer-ground"));
    v.scene.remove(v.grid);
    v.grid.dispose();
    v.grid = themedGrid();
    v.scene.add(v.grid);
  }, [theme]);

  // ---------------------------------------------------------- picking
  const pick = (e: React.PointerEvent): string | null => {
    const v = viewer.current;
    if (!v) return null;
    const r = v.renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), v.camera);
    const hit = ray.intersectObjects(v.meshes.filter((m) => m.visible), false).find((h) => !(h.object as THREE.Mesh).name.endsWith("/disc")) || ray.intersectObjects(v.meshes.filter((m) => m.visible), false)[0];
    return hit ? partKey(hit.object.userData as GeometryPart) : null;
  };
  const down = useRef<{ x: number; y: number } | null>(null);

  // parts grouped by slot element, as listed beside the viewer
  const groups = useMemo(() => {
    const out = new Map<string, { key: string; label: string; part: GeometryPart; names: string[] }>();
    for (const p of info?.parts || []) {
      const key = partKey(p);
      const g = out.get(key) || { key, label: p.index !== null && p.index !== undefined ? `${p.slot}[${p.index + 1}]` : p.slot, part: p, names: [] };
      g.names.push(p.name);
      out.set(key, g);
    }
    return [...out.values()];
  }, [info]);
  const sel = groups.find((g) => g.key === selected);
  const selInstance = sel?.part.instance ? catalog?.instances.find((i) => i.qualifiedName === sel.part.instance) : undefined;

  if (!catalog) return <div className="panel">{error ? <div className="error-box">{error}</div> : <div className="muted">{loading ? "Reading the project's models…" : ""}</div>}</div>;

  const m = info?.metrics || {};
  const metric = (k: string, unit: string, f = 1, digits = 0) => (typeof m[k] === "number" ? `${((m[k] as number) * f).toFixed(digits)} ${unit}` : "—");

  return (
    <div className="panel lib">
      <div className="panel-bar">
        <label className="inline small">
          <input type="checkbox" checked={discs} onChange={(e) => setDiscs(e.target.checked)} /> propeller discs
        </label>
        <label className="inline small">
          <input type="checkbox" checked={spin} onChange={(e) => setSpin(e.target.checked)} /> rotate
        </label>
        <label className="inline small">
          exploded
          <input type="range" min={0} max={1.5} step={0.05} value={exploded} onChange={(e) => setExploded(Number(e.target.value))} />
        </label>
        {(busy || loading) && <span className="hint">Generating geometry…</span>}
      </div>
      {error && <div className="error-box">{error}</div>}
      {!configs.length && <div className="empty-card">No system instances in this project yet: configure one in the Configurator tab, then view it here.</div>}
      <div className="lib-split" style={{ display: configs.length ? undefined : "none" }}>
        <div className="lib-list">
          {archs.map((a) => {
            const cs = configs.filter((c) => c.architectureId === a.id);
            if (!cs.length) return null;
            return (
              <div key={a.id}>
                <div className="lib-group">{a.name}</div>
                {cs.map((c) => (
                  <div key={c.id} className={`lib-item sub${system?.id === c.id ? " on" : ""}`} onClick={() => { setSystemQn(c.qualifiedName); setSelected(null); }}>
                    <span className="mono">{c.name}</span>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
        <div className="lib-detail">
          <div className="viewer-wrap">
            <div
              ref={host}
              className="viewer"
              onPointerDown={(e) => (down.current = { x: e.clientX, y: e.clientY })}
              onPointerMove={(e) => setHovered(pick(e))}
              onPointerLeave={() => setHovered(null)}
              onPointerUp={(e) => {
                const d = down.current;
                if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) < 4) setSelected(pick(e)); // a click, not a drag
              }}
            />
            {system && (
              <div className="viewer-caption">
                <span className="strong mono">{system.name}</span> <span className="muted">: {short(system.architecture)}</span>
                {info && <span className="muted small"> · {info.generator} geometry</span>}
              </div>
            )}
            {hovered && <div className="viewer-hover mono small">{groups.find((g) => g.key === hovered)?.label}</div>}
            {loadError && <div className="error-box viewer-error">{loadError}</div>}
          </div>
          {info && (
            <div className="lib-cols">
              <div>
                <div className="section-title">Dimensions</div>
                <table className="table compact">
                  <tbody>
                    {typeof m.rotors === "number" && (
                      <>
                        <tr><td>rotors</td><td className="mono num">{m.rotors as number}</td></tr>
                        <tr><td>wheelbase</td><td className="mono num">{metric("wheelbase", "mm", 1000)}</td></tr>
                        <tr><td>propeller diameter</td><td className="mono num">{metric("propellerDiameter", "in", 1 / 0.0254, 1)}</td></tr>
                        <tr>
                          <td>propeller tip clearance</td>
                          <td className={`mono num${typeof m.tipClearance === "number" && (m.tipClearance as number) < 0 ? " error-text" : ""}`}>{metric("tipClearance", "mm", 1000)}</td>
                        </tr>
                      </>
                    )}
                    <tr><td>length × width × height</td><td className="mono num">{metric("length", "", 1000)}× {metric("width", "", 1000)}× {metric("height", "mm", 1000)}</td></tr>
                  </tbody>
                </table>
                {info.warnings.length > 0 && <div className="error-box">{info.warnings.join("\n")}</div>}
              </div>
              <div>
                <div className="section-title">Parts</div>
                <div className="part-list">
                  {groups.map((g) => (
                    <div
                      key={g.key}
                      className={`lib-item${selected === g.key ? " on" : ""}`}
                      onMouseEnter={() => setHovered(g.key)}
                      onMouseLeave={() => setHovered(null)}
                      onClick={() => setSelected(selected === g.key ? null : g.key)}
                    >
                      <span className="mono">{g.label}</span>
                      <span className="count">{g.part.instance ? short(g.part.instance) : <span className="error-text">open</span>}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
          {sel && (
            <div className="form-card">
              <div className="row-between">
                <span>
                  <span className="strong mono">{sel.label}</span> <span className="muted mono">: {short(sel.part.type)}</span>
                  {sel.part.instance && <span className="mono"> ⟵ {short(sel.part.instance)}</span>}
                </span>
                {selInstance && (
                  <button className="link" onClick={() => onOpen(selInstance)}>
                    Show in editor
                  </button>
                )}
              </div>
              {selInstance?.doc && <div className="small">{selInstance.doc}</div>}
              <div className="mono small muted">
                {Object.entries(sel.part.values || {})
                  .map(([k, v]) => `${k} ${v}`)
                  .join(" · ") || "no values"}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** a part's slot element: what is picked and highlighted together */
const partKey = (p: GeometryPart) => `${p.slot}#${p.index ?? ""}`;
