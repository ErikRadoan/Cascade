// csgUniverses.ts: universe / fill-cell resolution for the CSG viewport.
//
// WHY THIS EXISTS. A rotated placement is not drawn from root-frame geometry. The
// expander builds the object LOCALLY at the origin inside its own universe, then
// stamps it into the root universe through a "fill cell" carrying a translation and
// a rotation (OpenMC's native instancing; see backend dsl/expander._place_rotated).
// A cell whose `universe_id` is not the root is therefore expressed in that
// universe's LOCAL frame and means nothing in the world until a fill cell places
// it. The viewport used to ignore universe_id and draw every material cell in the
// root frame, so a rotated object showed up unrotated at the origin together with
// its local "background" cell (a moderator-filled bounding sphere), which looked
// like a stray sphere.
//
// resolveWorldCells() walks root -> fill cells -> child universes and returns each
// drawable cell with the rigid transform that carries its local frame into the world.
//
// CONVENTION (verified against OpenMC src/cell.cpp + src/geometry.cpp): for a fill
// cell with translation t and rotation (rx, ry, rz) degrees, OpenMC maps a parent
// point to the child's local frame as  p_local = M (p_parent - t)  with
// M = Rz(-rz) Ry(-ry) Rx(-rx). The inverse, local -> parent, is therefore
//     p_parent = t + Rx(rx) Ry(ry) Rz(rz) p_local
// (intrinsic X-Y-Z). That is what eulerXyzToQuat() builds. Do not "simplify" it to
// Rz Ry Rx: that is the transpose-order and renders rotated objects differently from
// what the simulation sees. tests/csgUniverses.test.ts pins this against OpenMC's
// matrix.
//
// No imports from the app (only structural types): this file is unit-tested with
// plain `node --test` (see frontend/tests).

export type Vec3 = [number, number, number];
/** Unit quaternion (x, y, z, w), local -> parent. */
export type Quat = [number, number, number, number];

export const QUAT_IDENTITY: Quat = [0, 0, 0, 1];
export const ROOT_UNIVERSE = '0';

/** The part of a CSG cell this module reads (structurally compatible with CsgCell). */
export interface UniverseCell {
  id: string;
  material_id: string | null;
  name: string | null;
  universe_id?: string | null;
  fill?: string | null;
  translation?: number[] | null;
  rotation?: number[] | null;
}

export interface WorldCell<C extends UniverseCell = UniverseCell> {
  cell: C;
  /** World position of the cell's local origin. */
  offset: Vec3;
  /** Local -> world rotation. */
  quat: Quat;
  /** True when the cell lives in a universe instanced through a fill cell. */
  instanced: boolean;
}

export interface Bounds { xMin: number; xMax: number; yMin: number; yMax: number; zMin: number; zMax: number; }

// ----------------------------------------------------------------------------
// Quaternion math
// ----------------------------------------------------------------------------

function axisQuat(axis: 0 | 1 | 2, deg: number): Quat {
  const h = (deg * Math.PI) / 360;
  const s = Math.sin(h), c = Math.cos(h);
  return axis === 0 ? [s, 0, 0, c] : axis === 1 ? [0, s, 0, c] : [0, 0, s, c];
}

/** Hamilton product a*b: the rotation that applies b first, then a. */
export function quatMul(a: Quat, b: Quat): Quat {
  const [ax, ay, az, aw] = a, [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

/** Rotate v by q:  v + 2 u x (u x v + w v). */
export function quatRotate(q: Quat, v: Vec3): Vec3 {
  const [x, y, z, w] = q;
  const tx = y * v[2] - z * v[1] + w * v[0];
  const ty = z * v[0] - x * v[2] + w * v[1];
  const tz = x * v[1] - y * v[0] + w * v[2];
  return [v[0] + 2 * (y * tz - z * ty), v[1] + 2 * (z * tx - x * tz), v[2] + 2 * (x * ty - y * tx)];
}

/** Local -> parent rotation of an OpenMC fill cell given (rx, ry, rz) in degrees: Rx Ry Rz. */
export function eulerXyzToQuat(rxDeg: number, ryDeg: number, rzDeg: number): Quat {
  return quatMul(quatMul(axisQuat(0, rxDeg), axisQuat(1, ryDeg)), axisQuat(2, rzDeg));
}

/** World-space AABB of a local-frame AABB under (offset, quat): the 8 corners, rotated. */
export function transformAABB(b: Bounds, offset: Vec3, quat: Quat): Bounds {
  const isIdentity = quat[0] === 0 && quat[1] === 0 && quat[2] === 0;
  if (isIdentity) {
    return {
      xMin: b.xMin + offset[0], xMax: b.xMax + offset[0],
      yMin: b.yMin + offset[1], yMax: b.yMax + offset[1],
      zMin: b.zMin + offset[2], zMax: b.zMax + offset[2],
    };
  }
  const out: Bounds = { xMin: Infinity, xMax: -Infinity, yMin: Infinity, yMax: -Infinity, zMin: Infinity, zMax: -Infinity };
  for (const x of [b.xMin, b.xMax]) for (const y of [b.yMin, b.yMax]) for (const z of [b.zMin, b.zMax]) {
    const p = quatRotate(quat, [x, y, z]);
    const wx = p[0] + offset[0], wy = p[1] + offset[1], wz = p[2] + offset[2];
    if (wx < out.xMin) out.xMin = wx; if (wx > out.xMax) out.xMax = wx;
    if (wy < out.yMin) out.yMin = wy; if (wy > out.yMax) out.yMax = wy;
    if (wz < out.zMin) out.zMin = wz; if (wz > out.zMax) out.zMax = wz;
  }
  return out;
}

// ----------------------------------------------------------------------------
// Universe resolution
// ----------------------------------------------------------------------------

/**
 * A rotated instance's "background" cell fills the part of its confining sphere
 * that the object does not occupy, with the surrounding moderator (so the
 * simulation has no gap). It is bookkeeping, not an object: drawn, it is an opaque
 * sphere that hides the object inside it. Identified by the expander's naming
 * convention (`<placement>_background`, expander._background) inside a non-root universe.
 */
export function isBackgroundCell(cell: UniverseCell): boolean {
  return !!cell.name && cell.name.endsWith('_background') && !!cell.universe_id && cell.universe_id !== ROOT_UNIVERSE;
}

const MAX_UNIVERSE_DEPTH = 8;

/**
 * Every drawable (material-filled) cell with the transform that places it in the
 * world. Root-universe cells come back with an identity transform. A fill cell
 * whose `fill` is not a universe present in `cells` (an OpenMC lattice id, say) is
 * skipped, exactly as fill cells always were. Cells in universes that no fill cell
 * reaches are not drawn.
 */
export function resolveWorldCells<C extends UniverseCell>(cells: C[]): WorldCell<C>[] {
  const byUniverse = new Map<string, C[]>();
  for (const c of cells) {
    const u = c.universe_id ?? ROOT_UNIVERSE;
    const list = byUniverse.get(u);
    if (list) list.push(c); else byUniverse.set(u, [c]);
  }

  const out: WorldCell<C>[] = [];
  const walk = (universe: string, offset: Vec3, quat: Quat, depth: number, path: Set<string>) => {
    for (const cell of byUniverse.get(universe) ?? []) {
      if (cell.fill != null) {
        const child = cell.fill;
        if (!byUniverse.has(child) || path.has(child) || depth >= MAX_UNIVERSE_DEPTH) continue;
        const t: Vec3 = cell.translation && cell.translation.length >= 3
          ? [cell.translation[0], cell.translation[1], cell.translation[2]] : [0, 0, 0];
        const r: Quat = cell.rotation && cell.rotation.length >= 3
          ? eulerXyzToQuat(cell.rotation[0], cell.rotation[1], cell.rotation[2]) : QUAT_IDENTITY;
        // p_parent = t + R p_child, and the parent is itself placed by (offset, quat):
        //   p_world = offset + quat (t + R p_child) = (offset + quat t) + (quat R) p_child
        const qt = quatRotate(quat, t);
        walk(child, [offset[0] + qt[0], offset[1] + qt[1], offset[2] + qt[2]], quatMul(quat, r), depth + 1, new Set(path).add(child));
        continue;
      }
      if (cell.material_id == null) continue;
      if (universe !== ROOT_UNIVERSE && isBackgroundCell(cell)) continue;
      out.push({ cell, offset, quat, instanced: universe !== ROOT_UNIVERSE });
    }
  };
  walk(ROOT_UNIVERSE, [0, 0, 0], QUAT_IDENTITY, 0, new Set([ROOT_UNIVERSE]));
  return out;
}
