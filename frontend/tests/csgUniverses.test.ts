// Unit tests for universe/fill resolution and the OpenMC rotation convention.
//   npm run test:unit
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  eulerXyzToQuat, quatRotate, resolveWorldCells, transformAABB, isBackgroundCell, QUAT_IDENTITY,
  type UniverseCell, type Vec3,
} from '../src/lib/components/geometry/csgUniverses.ts';

const close = (a: number[], b: number[], eps = 1e-9) =>
  a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < eps, `component ${i}: ${v} vs ${b[i]} (${a} vs ${b})`));

// OpenMC's matrix, copied from src/cell.cpp (Cell::set_rotation): it maps a PARENT
// point (after subtracting the translation) to the child's LOCAL frame.
function openmcWorldToLocal(rx: number, ry: number, rz: number): number[][] {
  const phi = (-rx * Math.PI) / 180, theta = (-ry * Math.PI) / 180, psi = (-rz * Math.PI) / 180;
  const c = Math.cos, s = Math.sin;
  return [
    [c(theta) * c(psi), -c(phi) * s(psi) + s(phi) * s(theta) * c(psi), s(phi) * s(psi) + c(phi) * s(theta) * c(psi)],
    [c(theta) * s(psi), c(phi) * c(psi) + s(phi) * s(theta) * s(psi), -s(phi) * c(psi) + c(phi) * s(theta) * s(psi)],
    [-s(theta), s(phi) * c(theta), c(phi) * c(theta)],
  ];
}
const mul = (m: number[][], v: number[]) => m.map((row) => row[0] * v[0] + row[1] * v[1] + row[2] * v[2]);

test('local->world rotation is the exact inverse of OpenMC\'s stored matrix', () => {
  const samples: Vec3[] = [[1, 0, 0], [0, 1, 0], [0, 0, 1], [0.3, -2.0, 5.5], [-4, 7, 1.25]];
  for (const [rx, ry, rz] of [[30, 0, 0], [0, 45, 0], [0, 0, 60], [30, 15, 0], [20, -35, 70], [90, 90, 90], [-12, 33, 101]]) {
    const q = eulerXyzToQuat(rx, ry, rz);
    for (const p of samples) {
      const world = quatRotate(q, p);
      // applying OpenMC's world->local matrix to our world point must give the local point back
      close(mul(openmcWorldToLocal(rx, ry, rz), world), p, 1e-9);
    }
  }
});

test('order matters: Rx Ry Rz is NOT Rz Ry Rx', () => {
  const p: Vec3 = [1, 2, 3];
  const ours = quatRotate(eulerXyzToQuat(30, 40, 50), p);
  const wrong = quatRotate(
    // Rz Ry Rx built the other way around
    (() => { const a = eulerXyzToQuat(0, 0, 50), b = eulerXyzToQuat(0, 40, 0), c = eulerXyzToQuat(30, 0, 0);
      const mulq = (x: number[], y: number[]) => [x[3]*y[0]+x[0]*y[3]+x[1]*y[2]-x[2]*y[1], x[3]*y[1]-x[0]*y[2]+x[1]*y[3]+x[2]*y[0], x[3]*y[2]+x[0]*y[1]-x[1]*y[0]+x[2]*y[3], x[3]*y[3]-x[0]*y[0]-x[1]*y[1]-x[2]*y[2]];
      return mulq(mulq(a, b), c) as [number, number, number, number]; })(), p);
  assert.ok(Math.hypot(ours[0] - wrong[0], ours[1] - wrong[1], ours[2] - wrong[2]) > 0.1);
});

const cell = (over: Partial<UniverseCell> & { id: string }): UniverseCell => ({ material_id: null, name: null, ...over });

test('root cells come back untouched; fill cells are not drawn themselves', () => {
  const out = resolveWorldCells([
    cell({ id: 'a', material_id: 'UO2', name: 'a' }),
    cell({ id: 'f', name: 'void-fill-with-nothing', fill: 'u_missing' }),
    cell({ id: 'v', name: 'void' }),
  ]);
  assert.equal(out.length, 1);
  assert.deepEqual(out[0].offset, [0, 0, 0]);
  assert.deepEqual(out[0].quat, QUAT_IDENTITY);
  assert.equal(out[0].instanced, false);
});

test('a rotated placement: local cell is placed by the fill cell, background is dropped', () => {
  const cells = [
    cell({ id: 'local', material_id: 'UO2', name: 'p', universe_id: 'u8' }),
    cell({ id: 'bg', material_id: 'H2O', name: 'p_background', universe_id: 'u8' }),
    cell({ id: 'fill', name: 'p', fill: 'u8', translation: [5, 0, 0], rotation: [0, 0, 90] }),
  ];
  const out = resolveWorldCells(cells);
  assert.equal(out.length, 1, 'only the object, not the moderator bubble');
  assert.equal(out[0].cell.id, 'local');
  assert.equal(out[0].instanced, true);
  assert.deepEqual(out[0].offset, [5, 0, 0]);
  // +90 deg about z turns the local +x axis into world +y
  close(quatRotate(out[0].quat, [1, 0, 0]), [0, 1, 0]);
  assert.ok(isBackgroundCell(cells[1]) && !isBackgroundCell(cells[0]));
});

test('nested fills compose: offset + quat t, quat R', () => {
  const out = resolveWorldCells([
    cell({ id: 'outerFill', name: 'o', fill: 'uA', translation: [10, 0, 0], rotation: [0, 0, 90] }),
    cell({ id: 'innerFill', name: 'i', universe_id: 'uA', fill: 'uB', translation: [2, 0, 0], rotation: [0, 0, 0] }),
    cell({ id: 'leaf', material_id: 'X', name: 'leaf', universe_id: 'uB' }),
  ]);
  assert.equal(out.length, 1);
  // inner translation (2,0,0) is expressed in uA's frame, which is rotated 90deg about z: it lands on +y
  close(out[0].offset, [10, 2, 0]);
});

test('cycles and over-deep chains terminate', () => {
  const out = resolveWorldCells([
    cell({ id: 'f1', name: 'a', fill: 'uA' }),
    cell({ id: 'f2', name: 'b', universe_id: 'uA', fill: 'uA' }),
    cell({ id: 'm', material_id: 'X', name: 'm', universe_id: 'uA' }),
  ]);
  assert.equal(out.length, 1);
});

test('transformAABB: identity translates, rotation takes the corner hull', () => {
  const b = { xMin: -1, xMax: 1, yMin: -1, yMax: 1, zMin: 0, zMax: 4 };
  assert.deepEqual(transformAABB(b, [10, 0, 0], QUAT_IDENTITY), { xMin: 9, xMax: 11, yMin: -1, yMax: 1, zMin: 0, zMax: 4 });
  // +90 deg about x maps (x, y, z) -> (x, -z, y): the 0..4 height becomes a -4..0 extent in y.
  const r = transformAABB(b, [0, 0, 0], eulerXyzToQuat(90, 0, 0));
  close([r.xMin, r.xMax, r.yMin, r.yMax, r.zMin, r.zMax], [-1, 1, -4, 0, -1, 1], 1e-9);
});
