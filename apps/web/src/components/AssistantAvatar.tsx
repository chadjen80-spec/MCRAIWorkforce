"use client";

import { useEffect, useRef, useState } from "react";
import { speechLevel } from "@/lib/voice";
import { createFacePainter, eyeState, makeBlinker, mouthOpenness, mouthStep } from "./avatarFace";

// Mira's 3D character, loaded from /models/nova.glb (a Blender export with unlit materials). The model
// has no skeleton or face morphs, but its parts are separate nodes: `Head`, `ArmL`, `ArmR` and `Upper`
// (everything above the legs). Mira breathes, follows the pointer, tilts her head while listening,
// looks away while thinking, nods while speaking, bounces and waves when happy and droops when sad.
// Her face (the `Face` mesh's painted picture) is repainted live: the mouth moves with her voice and the
// eyes blink, or close happily when she celebrates (see ./avatarFace).
// three.js and the model load on demand so other pages do not pay for them.

export type AvatarMode = "idle" | "listening" | "thinking" | "speaking";

export const AVATAR_MODEL_URL = "/models/nova.glb";

type Three = typeof import("three");
type Object3D = import("three").Object3D;

/**
 * The model's `Head` node has its origin in the middle of the face, far above the short neck (`Mesh_4`). Turning the
 * head around that point swings the chin off the neck and leaves a gap. So the head is turned around the top of the
 * neck instead: a pivot is placed there and the head hangs from it, keeping its exact position at rest.
 */
export function neckPivot(THREE: Three, head: Object3D | null, neck: Object3D | null): Object3D | null {
  if (!head?.parent || !neck) return head;
  let root: Object3D = head;
  while (root.parent) root = root.parent;
  root.updateMatrixWorld(true); // positions in the world are needed below
  const box = new THREE.Box3().setFromObject(neck);
  if (box.isEmpty()) return head;
  const joint = new THREE.Vector3((box.min.x + box.max.x) / 2, box.max.y - (box.max.y - box.min.y) * 0.2, (box.min.z + box.max.z) / 2);
  const pivot = new THREE.Group();
  pivot.name = "HeadPivot";
  head.parent.add(pivot);
  pivot.position.copy(head.parent.worldToLocal(joint));
  pivot.attach(head); // keeps the head where it is in the world
  return pivot;
}

export function AssistantAvatar({
  mode,
  happy = false,
  sad = false,
  className,
}: {
  mode: AvatarMode;
  happy?: boolean;
  sad?: boolean;
  className?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const modeRef = useRef<AvatarMode>(mode);
  const happyRef = useRef(happy);
  const sadRef = useRef(sad);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);
  useEffect(() => {
    happyRef.current = happy;
    sadRef.current = sad;
  }, [happy, sad]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let disposed = false;
    let cleanup = () => {};

    void (async () => {
      let THREE: Three;
      let GLTFLoader: typeof import("three/examples/jsm/loaders/GLTFLoader.js").GLTFLoader;
      try {
        THREE = await import("three");
        ({ GLTFLoader } = await import("three/examples/jsm/loaders/GLTFLoader.js"));
      } catch {
        if (!disposed) setFailed(true);
        return;
      }
      if (disposed) return;

      let model: Object3D;
      try {
        model = (await new GLTFLoader().loadAsync(AVATAR_MODEL_URL)).scene;
      } catch {
        if (!disposed) setFailed(true);
        return;
      }
      if (disposed) return;

      let renderer: import("three").WebGLRenderer;
      try {
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      } catch {
        setFailed(true); // WebGL unavailable
        return;
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      el.appendChild(renderer.domElement);
      renderer.domElement.style.display = "block";
      renderer.domElement.setAttribute("aria-hidden", "true");

      const scene = new THREE.Scene();
      // The materials are unlit; this light only matters if a lit material is added to the model later.
      scene.add(new THREE.HemisphereLight(0xffffff, 0xb8c6e6, 2));
      scene.add(model);

      const node = (name: string) => model.getObjectByName(name) ?? null;
      const head = neckPivot(THREE, node("Head"), node("Mesh_4"));
      const upper = node("Upper");
      const arms = [node("ArmL"), node("ArmR")];
      const rest = (o: Object3D | null) => (o ? { o, rot: o.rotation.clone(), pos: o.position.clone() } : null);
      const headRest = rest(head);
      const upperRest = rest(upper);
      const armRest = arms.map(rest);

      // Live face: replace the face picture with a canvas copy that is repainted when the mouth or eyes change.
      let face: { tex: import("three").CanvasTexture; draw: ReturnType<typeof createFacePainter>["draw"] } | null = null;
      const faceMesh = node("Face") as import("three").Mesh | null;
      const faceMat = faceMesh?.material as import("three").MeshBasicMaterial | undefined;
      const faceImage = faceMat?.map?.image as CanvasImageSource | undefined;
      if (faceMat?.map && faceImage) {
        const canvas = document.createElement("canvas");
        const painter = createFacePainter(faceImage, canvas);
        const tex = new THREE.CanvasTexture(canvas);
        tex.flipY = faceMat.map.flipY;
        tex.colorSpace = faceMat.map.colorSpace;
        tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
        faceMat.map.dispose();
        faceMat.map = tex;
        faceMat.needsUpdate = true;
        face = { tex, draw: painter.draw };
      }
      const blink = makeBlinker(Math.floor(Math.random() * 1e9));
      let mouth = 0; // eased mouth opening

      // Frame the head and upper body; the caption bubble covers the bottom of the stage anyway.
      const box = new THREE.Box3().setFromObject(model);
      const top = box.max.y;
      const bottom = top - (top - box.min.y) * 0.82;
      const centre = new THREE.Vector3((box.min.x + box.max.x) / 2, (top + bottom) / 2, (box.min.z + box.max.z) / 2);
      const height = top - bottom;
      const camera = new THREE.PerspectiveCamera(26, 1, 0.01, 100);
      const fit = () => {
        const vfov = (camera.fov * Math.PI) / 180;
        // fit the height, and the width on narrow stages
        const byHeight = (height * 1.12) / 2 / Math.tan(vfov / 2);
        const width = (box.max.x - box.min.x) * 1.1;
        const byWidth = width / 2 / (Math.tan(vfov / 2) * camera.aspect);
        const dist = Math.max(byHeight, byWidth);
        // aim a little low so she sits in the upper part of the stage, clear of the caption
        const aim = centre.clone().setY(centre.y - height * 0.07);
        camera.position.set(aim.x, aim.y, box.max.z + dist);
        camera.lookAt(aim);
      };

      const resize = () => {
        const w = el.clientWidth || 1;
        const h = el.clientHeight || 1;
        renderer.setSize(w, h, false);
        renderer.domElement.style.width = "100%";
        renderer.domElement.style.height = "100%";
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        fit();
      };
      const ro = new ResizeObserver(resize);
      ro.observe(el);
      resize();

      const pointer = { x: 0, y: 0 };
      const onPointer = (e: PointerEvent) => {
        const rect = el.getBoundingClientRect();
        pointer.x = Math.max(-1, Math.min(1, ((e.clientX - rect.left) / rect.width) * 2 - 1));
        pointer.y = Math.max(-1, Math.min(1, ((e.clientY - rect.top) / rect.height) * 2 - 1));
      };
      window.addEventListener("pointermove", onPointer);

      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const clock = new THREE.Clock();
      let joy = 0; // 0..1, eased toward the `happy` prop
      let gloom = 0; // 0..1, eased toward the `sad` prop
      const rot = { x: 0, y: 0, z: 0 };
      let raf = 0;

      const frame = () => {
        raf = requestAnimationFrame(frame);
        const t = clock.getElapsedTime();
        const m = modeRef.current;
        const k = reduced ? 0.25 : 1; // damp idle motion when reduced motion is requested

        // head pose target per mode
        let tx = pointer.y * 0.12;
        let ty = pointer.x * 0.35 + Math.sin(t * 0.5) * 0.06 * k;
        let tz = Math.sin(t * 0.37) * 0.03 * k;
        if (m === "listening") {
          tx += 0.08;
          tz += 0.14;
        } else if (m === "thinking") {
          tx = -0.15;
          ty = 0.32 + Math.sin(t * 0.8) * 0.05;
          tz = -0.06;
        } else if (m === "speaking") {
          tx += (0.04 + Math.sin(t * 6.2) * 0.05) * k; // nodding along with the words
          ty += Math.sin(t * 1.3) * 0.04 * k;
          tz += Math.sin(t * 2.2) * 0.03 * k;
        }
        joy += ((happyRef.current ? 1 : 0) - joy) * 0.08;
        gloom += ((sadRef.current && !happyRef.current ? 1 : 0) - gloom) * 0.05;
        tx += 0.25 * gloom;
        tz += Math.sin(t * 0.9) * 0.05 * gloom * k;
        const beat = t * 5.5;
        tz += Math.sin(beat) * 0.14 * joy * k;
        tx -= 0.06 * joy;
        rot.x += (tx - rot.x) * 0.08;
        rot.y += (ty - rot.y) * 0.08;
        rot.z += (tz - rot.z) * 0.08;
        if (headRest) headRest.o.rotation.set(headRest.rot.x + rot.x, headRest.rot.y + rot.y, headRest.rot.z + rot.z);

        // breathing, a happy bounce, and slumped shoulders when sad
        if (upperRest) {
          const breathe = Math.sin(t * 1.4) * 0.012 * k;
          upperRest.o.position.y = upperRest.pos.y + breathe * height + Math.abs(Math.sin(beat)) * 0.04 * height * joy * k - 0.02 * height * gloom;
          upperRest.o.rotation.x = upperRest.rot.x + 0.06 * gloom;
        }

        // arms: hang a little lower when sad, wave when happy, small sway otherwise
        armRest.forEach((a, i) => {
          if (!a) return;
          const side = i === 0 ? 1 : -1;
          const sway = Math.sin(t * 1.4 + i) * 0.03 * k;
          const wave = side === 1 ? (0.9 + Math.sin(t * 9) * 0.35) * joy : 0.15 * joy;
          a.o.rotation.z = a.rot.z + side * (sway + wave - 0.25 * gloom);
        });

        // face: mouth follows the voice (or a talking rhythm), eyes blink; still when reduced motion is requested
        if (face) {
          const speaking = m === "speaking";
          const target = reduced ? 0 : mouthOpenness(t, speaking, speaking ? speechLevel() : null);
          mouth += (target - mouth) * (target > mouth ? 0.55 : 0.35);
          const eyes = eyeState(!reduced && blink(t), joy);
          if (face.draw(eyes, mouthStep(mouth))) face.tex.needsUpdate = true;
        }

        renderer.render(scene, camera);
      };
      frame();

      cleanup = () => {
        cancelAnimationFrame(raf);
        ro.disconnect();
        window.removeEventListener("pointermove", onPointer);
        scene.traverse((obj) => {
          const mesh = obj as import("three").Mesh;
          if (mesh.geometry) mesh.geometry.dispose();
          const material = mesh.material as import("three").Material | import("three").Material[] | undefined;
          const mats = Array.isArray(material) ? material : material ? [material] : [];
          for (const mat of mats) {
            for (const value of Object.values(mat)) if (value instanceof THREE.Texture) value.dispose();
            mat.dispose();
          }
        });
        renderer.dispose();
        renderer.domElement.remove();
      };
      if (disposed) cleanup();
    })();

    return () => {
      disposed = true;
      cleanup();
    };
  }, []);

  return (
    <div ref={host} className={className}>
      {failed && (
        <div className="flex h-full items-center justify-center">
          <div aria-hidden="true" className="size-40 rounded-full bg-gradient-to-br from-brand to-cyan opacity-80" />
        </div>
      )}
    </div>
  );
}
