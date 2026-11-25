// @vitest-environment jsdom
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { neckPivot } from "./AssistantAvatar";

/** The Nova layout: head origin mid-face at y 2.23, a short neck from y 1.40 to 1.68. */
function rig() {
  const body = new THREE.Group();
  const head = new THREE.Group();
  head.name = "Head";
  head.position.set(0, 2.23, 0);
  const chin = new THREE.Object3D();
  chin.position.set(0, -0.62, 0.2); // bottom-front of the face, in head space
  head.add(chin);
  const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.28));
  neck.position.set(0, 1.54, 0);
  body.add(head, neck);
  return { body, head, chin, neck };
}

const world = (o: THREE.Object3D) => o.getWorldPosition(new THREE.Vector3());

describe("neckPivot", () => {
  it("turns the head around the top of the neck and leaves it exactly where it was at rest", () => {
    const { head, chin, neck } = rig();
    const before = world(chin);
    const pivot = neckPivot(THREE, head, neck)!;
    expect(pivot).not.toBe(head);
    expect(world(pivot).y).toBeCloseTo(1.624, 3);
    expect(world(chin).distanceTo(before)).toBeLessThan(1e-9);
  });

  it("keeps the chin over the neck when she tilts, where turning the old way swung it off", () => {
    const tilt = (o: THREE.Object3D) => {
      o.rotation.set(-0.15, 0.32, 0.14); // chin up, turned and tilted: the largest pose the avatar uses
      o.updateMatrixWorld(true);
    };
    const old = rig();
    const restOld = world(old.chin);
    tilt(old.head);
    const movedOld = world(old.chin).distanceTo(restOld);

    const fixed = rig();
    const restFixed = world(fixed.chin);
    tilt(neckPivot(THREE, fixed.head, fixed.neck)!);
    const movedFixed = world(fixed.chin).distanceTo(restFixed);

    expect(movedOld).toBeGreaterThan(0.08); // more than the neck's radius: the gap
    expect(movedFixed).toBeLessThan(movedOld / 2);
  });

  it("does nothing when the model has no neck", () => {
    const { head } = rig();
    expect(neckPivot(THREE, head, null)).toBe(head);
  });
});
