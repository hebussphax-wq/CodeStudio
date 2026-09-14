// game.js - Game logic module for Donkey Monkey
// Exports: { levels, createGame, update }

(function(root) {
  // --- Module Loading ---
  let levelsModule, physicsModule;

  if (typeof module !== 'undefined' && module.exports) {
    // Node.js environment
    levelsModule = require('./levels.js');
    physicsModule = require('./physics.js');
  } else if (typeof window !== 'undefined') {
    // Browser environment
    levelsModule = window.DMLevels;
    physicsModule = window.DMPhysics;
  }

  const levels = levelsModule ? levelsModule.levels : [];
  const movePlayer = physicsModule ? physicsModule.movePlayer : null;

  // --- Constants ---
  const PLAYER_W = 24;
  const PLAYER_H = 32;
  const BOUNDARY_Y = 600; // Fall threshold for losing a life
  const INVULNERABILITY_TIME = 2.0; // Seconds of safety after respawn
  const SAFE_START_TIME = 6.0;      // Seconds before barrels can kill player
  const BARREL_SPAWN_INTERVAL = 2.0; // Seconds between barrel spawns
  const BARROLL_SPEED = 90;         // px/s horizontal speed for barrels
  const GRAVITY = 900;              // px/s^2 for barrels
  const MAX_LEVELS = levels.length;

  // --- Helper: Deep Copy Bananas ---
  function copyBananas(source) {
    return source.map(b => ({ ...b }));
  }

  // --- Helper: AABB Collision Check ---
  function rectsOverlap(r1, r2) {
    return (
      r1.x < r2.x + r2.w &&
      r1.x + r1.w > r2.x &&
      r1.y < r2.y + r2.h &&
      r1.y + r1.h > r2.y
    );
  }

  // --- Create Game State ---
  function createGame(levelIndex = 0) {
    const lvlIdx = Math.max(0, Math.min(levelIndex, MAX_LEVELS - 1));
    const levelData = levels[lvlIdx];

    return {
      levelIndex: lvlIdx,
      x: levelData.start.x,
      y: levelData.start.y,
      vx: 0,
      vy: 0,
      onGround: true,
      lives: 3,
      score: 0,
      status: 'playing', // 'playing', 'gameover', 'won'
      bananas: copyBananas(levelData.bananas),
      barrels: [],
      invulnerabilityTimer: 0,
      safeStartTimer: SAFE_START_TIME, // Time until barrels become dangerous
      barrelSpawnTimer: 0
    };
  }

  // --- Update Game State ---
  function update(game, input, dt) {
    // Clamp dt to prevent physics explosions
    if (dt > 0.1) dt = 0.1;

    // If game is over or won, ignore updates
    if (game.status === 'gameover' || game.status === 'won') {
      return;
    }

    const currentLevel = levels[game.levelIndex];
    if (!currentLevel) return; // Safety check

    // 1. Handle Fall Death (Out of Bounds Y)
    if (game.y > BOUNDARY_Y) {
      game.lives -= 1;
      if (game.lives <= 0) {
        game.status = 'gameover';
        return;
      }
      // Respawn at start of current level
      respawnPlayer(game, currentLevel);
      return;
    }

    // 2. Update Timers
    game.safeStartTimer = Math.max(0, game.safeStartTimer - dt);
    game.invulnerabilityTimer = Math.max(0, game.invulnerabilityTimer - dt);
    game.barrelSpawnTimer += dt; // Accumulate time for spawning

    // 3. Move Player (Physics)
    if (movePlayer) {
      movePlayer(game, currentLevel, input, dt);
    }

    // 4. Check Exit Overlap (Progression)
    const playerRect = { x: game.x, y: game.y, w: PLAYER_W, h: PLAYER_H };
    if (rectsOverlap(playerRect, currentLevel.exit)) {
      if (game.levelIndex < MAX_LEVELS - 1) {
        // Progress to next level
        const nextIdx = game.levelIndex + 1;
        const nextLevel = levels[nextIdx];
        game.levelIndex = nextIdx;
        game.x = nextLevel.start.x;
        game.y = nextLevel.start.y;
        game.vx = 0;
        game.vy = 0;
        game.onGround = true;
        game.bananas = copyBananas(nextLevel.bananas);
        game.barrels = []; // Clear barrels on level change
        game.invulnerabilityTimer = INVULNERABILITY_TIME; // Safety on transition
        game.safeStartTimer = SAFE_START_TIME; // Reset safe start timer for new level
      } else {
        // Won the game
        game.status = 'won';
      }
      return; // Stop processing this frame after progression/win
    }

    // 5. Check Banana Collection
    for (let i = game.bananas.length - 1; i >= 0; i--) {
      const banana = game.bananas[i];
      if (rectsOverlap(playerRect, { x: banana.x, y: banana.y, w: 24, h: 32 })) {
        game.score += 100;
        game.bananas.splice(i, 1);
      }
    }

    // 6. Barrel Spawning Logic
    // Spawn every 2 seconds starting immediately (timer accumulates from 0)
    if (game.barrelSpawnTimer >= BARREL_SPAWN_INTERVAL) {
      game.barrelSpawnTimer = 0;
      spawnBarrel(game, currentLevel);
    }

    // 7. Update Barrels Physics & Collision
    updateBarrels(game, currentLevel, dt);
  }

  // --- Helper: Respawn Player ---
  function respawnPlayer(game, level) {
    game.x = level.start.x;
    game.y = level.start.y;
    game.vx = 0;
    game.vy = 0;
    game.onGround = true;
    game.invulnerabilityTimer = INVULNERABILITY_TIME;
    game.barrels = []; // Clear hazards on death/respawn
  }

  // --- Helper: Spawn Barrel ---
  function spawnBarrel(game, level) {
    // Find the highest platform (smallest Y value)
    let topPlatform = level.platforms[0];
    for (const p of level.platforms) {
      if (p.y < topPlatform.y) {
        topPlatform = p;
      }
    }

    // Spawn near one edge. Randomly pick left or right.
    const isLeft = Math.random() < 0.5;
    const spawnX = isLeft ? topPlatform.x : topPlatform.x + topPlatform.w - 24;
    // Derive vx from spawn side: Left -> roll right (+), Right -> roll left (-)
    const barrel = {
      x: spawnX,
      y: topPlatform.y - 24, // Barrel height approx 24
      w: 24,
      h: 24,
      vx: isLeft ? BARROLL_SPEED : -BARROLL_SPEED,
      vy: 0,
      onGround: false
    };
    game.barrels.push(barrel);
  }

  // --- Helper: Update Barrels ---
  function updateBarrels(game, level, dt) {
    const playerRect = { x: game.x, y: game.y, w: PLAYER_W, h: PLAYER_H };
    // Player is invulnerable if timer > 0 OR if safe start time hasn't passed yet
    const invulnerable = game.invulnerabilityTimer > 0 || game.safeStartTimer > 0;

    for (let i = game.barrels.length - 1; i >= 0; i--) {
      const barrel = game.barrels[i];

      // Apply Gravity to Barrel
      if (!barrel.onGround) {
        barrel.vy += GRAVITY * dt;
      }

      // Move Barrel
      barrel.x += barrel.vx * dt;
      barrel.y += barrel.vy * dt;

      // Barrel Platform Collision (Simple Landing)
      barrel.onGround = false;
      for (const plat of level.platforms) {
        // Check if barrel is falling and overlaps platform horizontally
        if (barrel.vy >= 0 &&
            barrel.x < plat.x + plat.w &&
            barrel.x + barrel.w > plat.x &&
            barrel.y + barrel.h >= plat.y &&
            barrel.y + barrel.h <= plat.y + 10) { // Tolerance for landing

          barrel.y = plat.y - barrel.h;
          barrel.vy = 0;
          barrel.onGround = true;
        }
      }

      // Barrel Edge Drop Logic
      // If on ground, check if it's near the edge of a platform. If so, drop.
      if (barrel.onGround) {
        let onPlatform = false;
        for (const plat of level.platforms) {
          if (barrel.x + barrel.w > plat.x && barrel.x < plat.x + plat.w) {
            onPlatform = true;
            // Check if near edge to drop
            const distToLeft = barrel.x - plat.x;
            const distToRight = (plat.x + plat.w) - (barrel.x + barrel.w);
            if (distToLeft < 2 || distToRight < 2) {
              barrel.onGround = false; // Allow gravity to take over next frame
            }
          }
        }
      }

      // Remove Offscreen Barrels
      if (barrel.y > BOUNDARY_Y + 50 || barrel.x < -50 || barrel.x > 960 + 50) {
        game.barrels.splice(i, 1);
        continue;
      }

      // Collision with Player
      if (!invulnerable && rectsOverlap(playerRect, barrel)) {
        game.lives -= 1;
        if (game.lives <= 0) {
          game.status = 'gameover';
          return; // Stop update immediately on game over
        }
        respawnPlayer(game, level);
        return; // Stop update to prevent multiple hits in one frame
      }
    }
  }

  // --- Exports ---
  const api = {
    levels: levels,
    createGame: createGame,
    update: update
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (typeof window !== 'undefined') {
    window.DonkeyMonkey = api;
  }

})(this);
