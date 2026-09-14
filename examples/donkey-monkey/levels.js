// levels.js - Data-only module for jungle-themed level layouts
// Exports: { levels: [...] }

(function(root) {
  const LEVEL_COUNT = 4;
  const PLAYER_H = 32;
  const EXIT_W = 32;
  const EXIT_H = 48;

  // Factory for generating distinct level configurations
  function createLevel(i) {
    // Tier Y positions (bottom to top)
    const tierYs = [500, 390, 280, 170];

    // Platforms: Bottom platform + 3 upper tiers with varying widths
    const platforms = [
      { x: 0, y: tierYs[0], w: 960, h: 18 }, // Bottom full width
      { x: 20 + 10 * i, y: tierYs[1], w: 920 - 20 * i, h: 18 }, // Tier 1
      { x: 20 + 10 * i, y: tierYs[2], w: 920 - 20 * i, h: 18 }, // Tier 2
      { x: 20 + 10 * i, y: tierYs[3], w: 920 - 20 * i, h: 18 }  // Tier 3 (highest)
    ];

    // Ladders connecting tiers
    // Ladder 1: Bottom (500) to Tier 1 (390), height=110
    const ladder1X = 120 + 20 * i;
    // Ladder 2: Tier 1 (390) to Tier 2 (280), height=110
    const ladder2X = 740 - 20 * i;
    // Ladder 3: Tier 2 (280) to Tier 3 (170), height=110
    const ladder3X = 150 + 20 * i;

    const ladders = [
      { x: ladder1X, y: tierYs[1], w: 36, h: 110 }, // Connects 500->390
      { x: ladder2X, y: tierYs[2], w: 36, h: 110 }, // Connects 390->280
      { x: ladder3X, y: tierYs[3], w: 36, h: 110 }  // Connects 280->170
    ];

    // Start position: on bottom platform, safe distance from edges
    const start = {
      x: 50,
      y: tierYs[0] - PLAYER_H // Feet at y=500
    };

    // Exit position: on highest platform (tierYs[3]=170)
    // Exit feet must be at top surface: exit.y + EXIT_H = 170 => exit.y = 122
    const exit = {
      x: 780,
      y: tierYs[3] - EXIT_H, // Feet at y=170
      w: EXIT_W,
      h: EXIT_H
    };

    // Bananas placed on reachable platforms (above platform surfaces)
    const bananas = [
      { x: 200, y: tierYs[0] - 40 },   // On bottom
      { x: 400, y: tierYs[1] - 40 },   // On tier 1
      { x: 600, y: tierYs[2] - 40 },   // On tier 2
      { x: 300, y: tierYs[3] - 40 }    // On tier 3 (highest)
    ];

    return {
      name: 'Jungle Level ' + (i + 1),
      start,
      exit,
      platforms,
      ladders,
      bananas
    };
  }

  const levels = [];
  for (let i = 0; i < LEVEL_COUNT; i++) {
    levels.push(createLevel(i));
  }

  // Guarded exports
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { levels };
  }
  if (typeof window !== 'undefined') {
    window.DMLevels = { levels };
  }

})(this);
