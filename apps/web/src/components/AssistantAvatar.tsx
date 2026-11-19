"use client";

import { useEffect, useRef, useState } from "react";

// Stylised 3D assistant built with three.js from primitives and painted textures (no external
// model file). Cute proportions: large head, big glossy eyes, soft blush, a bob with a fringe.
// She breathes, blinks, follows the pointer, tilts her head while listening, looks away while
// thinking and moves her mouth while speaking. three.js is loaded on demand so other pages do
// not pay for it. A photorealistic human would need a rigged model file (GLB/VRM) instead.

export type AvatarMode = "idle" | "listening" | "thinking" | "speaking";

type Three = typeof import("three");

/** Glossy iris painted on a canvas: dark rim, warm gradient, pupil and two catch-lights. */
function irisTexture(THREE: Three): import("three").CanvasTexture {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const g = canvas.getContext("2d")!;
  const c = size / 2;
  g.beginPath();
  g.arc(c, c, c - 2, 0, Math.PI * 2);
  g.clip();
  const body = g.createLinearGradient(0, 0, 0, size);
  body.addColorStop(0, "#2a160e");
  body.addColorStop(0.45, "#6b3a22");
  body.addColorStop(1, "#d98a52");
  g.fillStyle = body;
  g.fillRect(0, 0, size, size);
  // fine radial streaks
  g.globalAlpha = 0.18;
  g.strokeStyle = "#ffd9b0";
  for (let i = 0; i < 48; i++) {
    const a = (i / 48) * Math.PI * 2;
    g.beginPath();
    g.moveTo(c + Math.cos(a) * 52, c + Math.sin(a) * 52);
    g.lineTo(c + Math.cos(a) * 118, c + Math.sin(a) * 118);
    g.stroke();
  }
  g.globalAlpha = 1;
  // lower glow
  const glow = g.createRadialGradient(c, size * 0.86, 4, c, size * 0.86, 90);
  glow.addColorStop(0, "rgba(255,214,170,0.9)");
  glow.addColorStop(1, "rgba(255,214,170,0)");
  g.fillStyle = glow;
  g.fillRect(0, 0, size, size);
  // pupil
  g.fillStyle = "#120805";
  g.beginPath();
  g.arc(c, c, 50, 0, Math.PI * 2);
  g.fill();
  // rim
  g.lineWidth = 16;
  g.strokeStyle = "#1c0e08";
  g.beginPath();
  g.arc(c, c, c - 9, 0, Math.PI * 2);
  g.stroke();
  // catch-lights
  g.fillStyle = "#ffffff";
  g.beginPath();
  g.ellipse(c - 40, c - 46, 30, 24, -0.5, 0, Math.PI * 2);
  g.fill();
  g.globalAlpha = 0.85;
  g.beginPath();
  g.arc(c + 46, c + 50, 12, 0, Math.PI * 2);
  g.fill();
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** Soft round blush with a feathered edge. */
function blushTexture(THREE: Three): import("three").CanvasTexture {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const g = canvas.getContext("2d")!;
  const grad = g.createRadialGradient(size / 2, size / 2, 2, size / 2, size / 2, size / 2);
  grad.addColorStop(0, "rgba(255,120,130,0.75)");
  grad.addColorStop(0.6, "rgba(255,130,140,0.3)");
  grad.addColorStop(1, "rgba(255,130,140,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
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
      try {
        THREE = await import("three");
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
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.12;
      el.appendChild(renderer.domElement);
      renderer.domElement.style.display = "block";
      renderer.domElement.setAttribute("aria-hidden", "true");

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(26, 1, 0.1, 50);
      const BASE_Z = 9.8;
      camera.position.set(0, -0.55, BASE_Z);
      camera.lookAt(0, -0.62, 0);

      // soft studio lighting: warm key, cool fill, coloured rims
      scene.add(new THREE.HemisphereLight(0xffffff, 0xb8c6e6, 1.25));
      const key = new THREE.DirectionalLight(0xfff1e2, 1.7);
      key.position.set(2.2, 2.8, 5);
      scene.add(key);
      const fill = new THREE.DirectionalLight(0xdfe9ff, 0.55);
      fill.position.set(-3, 0.5, 3);
      scene.add(fill);
      const rim = new THREE.PointLight(0x22d3ee, 16, 12);
      rim.position.set(-3, 1.6, -1.2);
      scene.add(rim);
      const rim2 = new THREE.PointLight(0xff8fb6, 9, 12);
      rim2.position.set(3, 0.4, -1.2);
      scene.add(rim2);

      const textures: import("three").Texture[] = [];
      const physical = (color: number, opts: Partial<import("three").MeshPhysicalMaterialParameters> = {}) =>
        new THREE.MeshPhysicalMaterial({ color, roughness: 0.55, ...opts });
      // sheen gives skin and hair a soft, velvety falloff at grazing angles
      const skin = physical(0xffdccd, { roughness: 0.48, sheen: 1, sheenColor: new THREE.Color(0xff9f8f), sheenRoughness: 0.45 });
      const hairMat = physical(0x4a3129, { roughness: 0.38, sheen: 1, sheenColor: new THREE.Color(0xc79a7e), sheenRoughness: 0.3, clearcoat: 0.25 });
      const hairDouble = hairMat.clone();
      hairDouble.side = THREE.DoubleSide;
      const blazer = physical(0xcdb9a2, { roughness: 0.8, sheen: 0.6, sheenColor: new THREE.Color(0xffffff) });
      const shirtMat = physical(0xffffff, { roughness: 0.7 });
      const ribbonMat = physical(0x3b9cf6, { roughness: 0.35, clearcoat: 0.6 });
      const lipMat = physical(0xe86f84, { roughness: 0.25, clearcoat: 0.8 });
      const sphere = (r: number, m: import("three").Material, seg = 40) => new THREE.Mesh(new THREE.SphereGeometry(r, seg, seg), m);

      const bust = new THREE.Group();
      scene.add(bust);

      // --- body: small shoulders under a big head (cute proportions) ----------------------
      const torso = sphere(1.35, blazer, 48);
      torso.scale.set(0.98, 0.9, 0.56);
      torso.position.set(0, -2.62, 0);
      bust.add(torso);
      const shirt = sphere(0.5, shirtMat);
      shirt.scale.set(0.55, 1.05, 0.4);
      shirt.position.set(0, -1.86, 0.52);
      bust.add(shirt);
      for (const side of [-1, 1]) {
        const lapel = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.95, 0.07), blazer);
        lapel.position.set(side * 0.33, -1.98, 0.7);
        lapel.rotation.set(-0.22, side * -0.3, side * 0.38);
        bust.add(lapel);
        const collar = new THREE.Mesh(new THREE.ConeGeometry(0.2, 0.36, 3), shirtMat);
        collar.position.set(side * 0.2, -1.44, 0.34);
        collar.rotation.set(0.5, 0, side * 2.3);
        collar.scale.z = 0.45;
        bust.add(collar);
        // ribbon bow at the collar
        const wing = new THREE.Mesh(new THREE.ConeGeometry(0.2, 0.4, 20), ribbonMat);
        wing.position.set(side * 0.23, -1.7, 0.72);
        wing.rotation.z = side * (Math.PI / 2);
        wing.scale.z = 0.55;
        bust.add(wing);
      }
      const knot = sphere(0.09, ribbonMat, 20);
      knot.position.set(0, -1.7, 0.76);
      bust.add(knot);
      const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.21, 0.27, 0.7, 32), skin);
      neck.position.set(0, -1.25, 0);
      bust.add(neck);

      // --- head ---------------------------------------------------------------------------
      const head = new THREE.Group();
      head.position.set(0, -0.05, 0);
      bust.add(head);
      const skull = sphere(1, skin, 64);
      skull.scale.set(0.95, 1.0, 0.93); // one smooth surface: no seams across the cheeks
      head.add(skull);

      // hair: voluminous bob open at the front, crown cap, fringe strands, side locks
      const opening = 1.95; // radians left open around +z for the face
      const bob = new THREE.Mesh(
        new THREE.SphereGeometry(1.17, 64, 40, Math.PI / 2 + opening / 2, Math.PI * 2 - opening, 0, Math.PI * 0.8),
        hairDouble,
      );
      bob.scale.set(1.02, 1.08, 1.04);
      bob.position.set(0, 0.1, -0.06);
      head.add(bob);
      const crown = new THREE.Mesh(new THREE.SphereGeometry(1.17, 64, 24, 0, Math.PI * 2, 0, Math.PI * 0.4), hairMat);
      crown.scale.set(1.02, 1.08, 1.04);
      crown.position.set(0, 0.1, -0.06);
      crown.rotation.x = -0.12;
      head.add(crown);
      for (let i = -3; i <= 3; i++) {
        const strand = sphere(0.3, hairMat, 24);
        const a = Math.abs(i);
        strand.scale.set(0.78, 1.05 - a * 0.04, 0.36);
        strand.position.set(i * 0.2 - 0.03, 0.56 - a * 0.035, 0.84 - a * a * 0.028);
        strand.rotation.set(0.12, i * 0.16, -i * 0.11 - 0.06);
        head.add(strand);
      }
      for (const side of [-1, 1]) {
        const lock = sphere(0.5, hairMat, 28);
        lock.scale.set(0.3, 1.25, 0.46);
        lock.position.set(side * 0.86, -0.32, 0.34);
        lock.rotation.z = side * -0.12; // curves in toward the chin
        head.add(lock);
      }
      const shine = new THREE.Mesh(
        new THREE.TorusGeometry(0.8, 0.045, 8, 64),
        new THREE.MeshBasicMaterial({ color: 0xffe9d6, transparent: true, opacity: 0.22 }),
      );
      shine.rotation.x = Math.PI / 2 - 0.12;
      shine.scale.set(1, 1, 0.5);
      shine.position.set(0, 0.86, 0.02);
      head.add(shine);
      // hair clip in the brand colour
      const clip = new THREE.Mesh(new THREE.CapsuleGeometry(0.045, 0.26, 6, 12), ribbonMat);
      clip.position.set(0.63, 0.42, 0.74);
      clip.rotation.set(0.1, -0.5, -0.75);
      head.add(clip);

      // eyes: large and glossy
      const irisTex = irisTexture(THREE);
      const blushTex = blushTexture(THREE);
      textures.push(irisTex, blushTex);
      const eyeWhite = physical(0xffffff, { roughness: 0.15, clearcoat: 1 });
      const lashMat = new THREE.MeshBasicMaterial({ color: 0x2a1a15 });
      const eyes: import("three").Group[] = [];
      const irises: import("three").Object3D[] = [];
      const happyEyes: import("three").Mesh[] = []; // closed, upward-curved "smiling" eyes
      const blushes: import("three").Mesh[] = [];
      const brows: { mesh: import("three").Mesh; side: number; rest: number }[] = [];
      for (const side of [-1, 1]) {
        const eye = new THREE.Group();
        eye.position.set(side * 0.37, -0.1, 0.85);
        eye.rotation.y = side * 0.22;
        const white = sphere(0.235, eyeWhite, 32);
        white.scale.set(0.92, 1.08, 0.36);
        eye.add(white);
        const iris = new THREE.Mesh(new THREE.CircleGeometry(0.19, 48), new THREE.MeshBasicMaterial({ map: irisTex, toneMapped: false }));
        iris.position.z = 0.095;
        eye.add(iris);
        const lash = new THREE.Mesh(new THREE.TorusGeometry(0.232, 0.034, 10, 32, Math.PI * 0.92), lashMat);
        lash.rotation.z = Math.PI * 0.04;
        lash.scale.set(0.95, 1.06, 1);
        lash.position.z = 0.07;
        eye.add(lash);
        const flick = new THREE.Mesh(new THREE.ConeGeometry(0.035, 0.16, 8), lashMat);
        flick.position.set(side * 0.25, 0.1, 0.05);
        flick.rotation.z = side * -1.0;
        eye.add(flick);
        head.add(eye);
        eyes.push(eye);
        irises.push(iris);
        const arc = new THREE.Mesh(new THREE.TorusGeometry(0.2, 0.038, 10, 28, Math.PI * 0.8), lashMat);
        arc.position.set(side * 0.37, -0.24, 0.92);
        arc.rotation.set(0, side * 0.22, Math.PI * 0.1);
        arc.scale.set(1.15, 1.7, 1); // tall arch so it clearly reads as a smiling eye, not a sleepy one
        arc.visible = false;
        head.add(arc);
        happyEyes.push(arc);

        const brow = new THREE.Mesh(new THREE.TorusGeometry(0.2, 0.014, 6, 20, Math.PI * 0.42), new THREE.MeshBasicMaterial({ color: 0x5a3f35 }));
        brow.position.set(side * 0.37, 0.07, 0.93);
        brow.rotation.z = Math.PI * 0.29;
        head.add(brow);
        brows.push({ mesh: brow, side, rest: brow.rotation.z });

        const blush = new THREE.Mesh(
          new THREE.PlaneGeometry(0.46, 0.34),
          new THREE.MeshBasicMaterial({ map: blushTex, transparent: true, depthWrite: false, opacity: 0.85 }),
        );
        blush.position.set(side * 0.55, -0.4, 0.78);
        blush.rotation.y = side * 0.55;
        head.add(blush);
        blushes.push(blush);
      }
      const nose = sphere(0.045, physical(0xf7bfae, { roughness: 0.5 }), 16);
      nose.scale.set(0.9, 0.8, 0.8);
      nose.position.set(0, -0.34, 0.96);
      head.add(nose);
      const smile = new THREE.Mesh(new THREE.TorusGeometry(0.11, 0.024, 12, 28, Math.PI * 0.7), lipMat);
      smile.rotation.z = Math.PI + Math.PI * 0.15; // lower arc, centred
      smile.position.set(0, -0.5, 0.9);
      head.add(smile);
      // sad face parts: a downturned mouth and a tear that rolls down the cheek
      const frown = new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.024, 12, 28, Math.PI * 0.7), lipMat);
      frown.rotation.z = Math.PI * 0.15; // upper arc = corners of the mouth turned down
      frown.position.set(0, -0.63, 0.89);
      frown.visible = false;
      head.add(frown);
      const tear = sphere(0.055, physical(0x9fdcff, { roughness: 0.05, clearcoat: 1, transparent: true, opacity: 0.9 }), 20);
      tear.scale.set(0.8, 1.35, 0.6);
      tear.visible = false;
      head.add(tear);
      const mouth = sphere(0.1, physical(0xb23a55, { roughness: 0.4 }), 24);
      mouth.position.set(0, -0.58, 0.88);
      mouth.scale.set(0.9, 0.3, 0.3);
      mouth.visible = false;
      head.add(mouth);

      // --- sizing / pointer ------------------------------------------------------------------
      const resize = () => {
        const w = el.clientWidth || 1;
        const h = el.clientHeight || 1;
        renderer.setSize(w, h, false);
        renderer.domElement.style.width = "100%";
        renderer.domElement.style.height = "100%";
        camera.aspect = w / h;
        // keep the whole bust in view on narrow stages
        camera.position.z = camera.aspect < 0.9 ? (BASE_Z / Math.max(camera.aspect, 0.55)) * 0.9 : BASE_Z;
        camera.updateProjectionMatrix();
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
      let nextBlink = 2;
      let blinkUntil = 0;
      let mouthOpen = 0.3;
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
        let ty = pointer.x * 0.28 + Math.sin(t * 0.5) * 0.05 * k;
        let tz = Math.sin(t * 0.37) * 0.03 * k;
        if (m === "listening") {
          tx += 0.07;
          tz += 0.13;
        } else if (m === "thinking") {
          tx = -0.14;
          ty = 0.28 + Math.sin(t * 0.8) * 0.04;
          tz = -0.06;
        } else if (m === "speaking") {
          tx += Math.sin(t * 3.1) * 0.035 * k;
          ty += Math.sin(t * 1.3) * 0.03 * k;
          tz += Math.sin(t * 2.2) * 0.03 * k;
        }
        // happy: sway the head side to side and bounce (see below)
        joy += ((happyRef.current ? 1 : 0) - joy) * 0.08;
        // sad: head drops and sways slowly, shoulders sink
        gloom += ((sadRef.current && !happyRef.current ? 1 : 0) - gloom) * 0.05;
        tx += 0.2 * gloom;
        tz += Math.sin(t * 0.9) * 0.05 * gloom * k;
        const beat = t * 5.5;
        tz += Math.sin(beat) * 0.12 * joy * k;
        tx -= 0.05 * joy;
        rot.x += (tx - rot.x) * 0.08;
        rot.y += (ty - rot.y) * 0.08;
        rot.z += (tz - rot.z) * 0.08;
        head.rotation.set(rot.x, rot.y, rot.z);
        bust.position.y = Math.sin(t * 1.4) * 0.025 * k + Math.abs(Math.sin(beat)) * 0.16 * joy * k - 0.1 * gloom;
        torso.scale.y = 0.9 + Math.sin(t * 1.4) * 0.006 * k;

        // eyes: follow the pointer a little, look away while thinking
        const ix = m === "thinking" ? 0.04 : pointer.x * 0.035;
        const iy = (m === "thinking" ? 0.035 : -pointer.y * 0.025) - 0.045 * gloom; // downcast when sad
        for (const iris of irises) iris.position.set(ix, iy, 0.095);
        if (t > nextBlink) {
          blinkUntil = t + 0.13;
          nextBlink = t + 2.2 + Math.random() * 3;
        }
        const lid = t < blinkUntil ? 0.1 : 1 - 0.28 * gloom; // heavy eyelids when sad
        for (const eye of eyes) eye.scale.y += (lid - eye.scale.y) * 0.5;
        const beaming = joy > 0.5;
        for (const eye of eyes) eye.visible = !beaming;
        for (const arc of happyEyes) arc.visible = beaming;
        for (const blush of blushes) blush.scale.setScalar(1 + 0.3 * joy - 0.5 * gloom);
        // brows: inner ends lift when sad
        for (const b of brows) b.mesh.rotation.z = b.rest + b.side * 0.42 * gloom;
        for (const b of brows) b.mesh.position.y = 0.07 + 0.05 * gloom;
        const weeping = gloom > 0.6;
        tear.visible = weeping;
        if (weeping) {
          const drip = (t * 0.45) % 1; // one tear every ~2.2s
          tear.position.set(0.44, -0.36 - drip * 0.42, 0.9 - drip * 0.06);
          (tear.material as import("three").MeshPhysicalMaterial).opacity = 0.9 * (1 - drip * drip);
        }

        // mouth: syllable-like movement while speaking, a small smile otherwise
        const target = m === "speaking" ? 0.3 + Math.abs(Math.sin(t * 11.5)) * (0.38 + 0.2 * Math.sin(t * 3.7)) : 0.3;
        mouthOpen += (target - mouthOpen) * 0.35;
        const talking = m === "speaking" || mouthOpen > 0.33;
        mouth.visible = talking;
        smile.visible = !talking && gloom <= 0.5;
        frown.visible = !talking && gloom > 0.5;
        smile.scale.setScalar(1 + 0.55 * joy);
        mouth.scale.set(0.9, mouthOpen, 0.3);

        rim.intensity = m === "listening" ? 24 + Math.sin(t * 5) * 6 : 16;
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
          if (Array.isArray(material)) material.forEach((x) => x.dispose());
          else material?.dispose();
        });
        textures.forEach((x) => x.dispose());
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
