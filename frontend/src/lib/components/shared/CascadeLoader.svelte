<script lang="ts">
  // CascadeLoader — the Cascade logo (a 19-hexagon lattice) as an animated spinner.
  //
  // The geometry and colours are taken from Cascade_Logo.svg: flat-top hexagons in
  // concentric rings — yellow core, orange ring, red outer ring. The animation is a
  // brightness "cascade" that ripples outward from the core ring by ring, which is
  // both the app's name and how a chain reaction spreads through a reactor lattice.
  //
  // It is generated (not an <img>) so each hexagon can be animated independently,
  // and so it stays ~1 KB instead of the 37 KB logo file. Cascade_Logo.svg remains
  // the source of truth for the static brand mark: if the logo's proportions or
  // palette change, update the constants below to match.

  let { size = 120, label = 'Loading' }: { size?: number; label?: string } = $props();

  // --- geometry, in logo units (measured from Cascade_Logo.svg, 500px reference) ---
  const PITCH   = 85;                // centre-to-centre distance of neighbouring hexes
  const COL_DX  = PITCH * Math.sqrt(3) / 2;   // horizontal step between columns (flat-top)
  const RADIUS  = 43;                // hexagon circumradius (corner to centre)
  const ROUND   = 4;                 // corner rounding: inset the shape, stroke it back out
  const RINGS   = 2;                 // 2 rings around the core = 19 hexagons
  const VIEW    = 215;               // half-extent of the viewBox

  // Palette from the logo, by ring (0 = core).
  const RING_FILL = ['#F8C51D', '#F29B16', '#B22A35'];
  // The two vertical neighbours of the core are the deeper orange in the logo.
  const RING1_VERTICAL = '#EF6B2F';
  // Seconds between one ring lighting up and the next (the ripple speed).
  const RING_DELAY = 0.2;

  function hexPoints(cx: number, cy: number, r: number): string {
    // flat-top: corners at 0°, 60°, … 300°
    return Array.from({ length: 6 }, (_, i) => {
      const a = (Math.PI / 3) * i;
      return `${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`;
    }).join(' ');
  }

  // Axial coordinates (q, r); ring = hex distance from the core.
  const hexes: { points: string; fill: string; ring: number }[] = [];
  for (let q = -RINGS; q <= RINGS; q++) {
    for (let r = -RINGS; r <= RINGS; r++) {
      const ring = Math.max(Math.abs(q), Math.abs(r), Math.abs(q + r));
      if (ring > RINGS) continue;
      const cx = COL_DX * q;
      const cy = PITCH * (r + q / 2);
      const fill = ring === 1 && q === 0 ? RING1_VERTICAL : RING_FILL[ring];
      hexes.push({ points: hexPoints(cx, cy, RADIUS - ROUND), fill, ring });
    }
  }
</script>

<svg
  class="cascade-loader"
  width={size}
  height={size}
  viewBox="{-VIEW} {-VIEW} {VIEW * 2} {VIEW * 2}"
  role="img"
  aria-label={label}
>
  <g class="lattice">
    {#each hexes as h}
      <polygon
        class="hex"
        points={h.points}
        fill={h.fill}
        stroke={h.fill}
        stroke-width={ROUND * 2}
        stroke-linejoin="round"
        style="animation-delay: {h.ring * RING_DELAY}s"
      />
    {/each}
  </g>
</svg>

<style>
  .cascade-loader {
    display: block;
    overflow: visible;
  }

  /* Soft warm halo that breathes with the ripple. */
  .lattice {
    filter: drop-shadow(0 0 14px rgba(239, 107, 47, 0.28));
    animation: halo 2.4s ease-in-out infinite;
  }

  .hex {
    transform-box: fill-box;
    transform-origin: center;
    opacity: 0.2;
    transform: scale(0.88);
    animation: cascade 2.4s cubic-bezier(0.4, 0, 0.2, 1) infinite;
  }

  /* Each ring runs this same cycle, offset by RING_DELAY, so the bright band
     travels outward from the core and the lattice settles dim before the
     next pulse starts. */
  @keyframes cascade {
    0%   { opacity: 0.2; transform: scale(0.88); }
    14%  { opacity: 1;   transform: scale(1); }
    34%  { opacity: 1;   transform: scale(1); }
    58%  { opacity: 0.2; transform: scale(0.88); }
    100% { opacity: 0.2; transform: scale(0.88); }
  }

  @keyframes halo {
    0%, 100% { filter: drop-shadow(0 0 10px rgba(239, 107, 47, 0.18)); }
    30%      { filter: drop-shadow(0 0 22px rgba(248, 197, 29, 0.38)); }
  }

  /* Respect the OS setting: no travelling ripple, just a static full-colour logo. */
  @media (prefers-reduced-motion: reduce) {
    .hex, .lattice { animation: none; }
    .hex { opacity: 1; transform: none; }
  }
</style>
