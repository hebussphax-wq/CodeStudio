// physics.js - CommonJS module for Donkey Kong physics
// Exports movePlayer function

function movePlayer(g, level, input, dt) {
  // Clamp dt to max 0.05s to prevent tunneling
  if (dt > 0.05) dt = 0.05;

  const playerW = 24;
  const playerH = 32;
  const moveSpeed = 180; // px/s horizontal
  const climbSpeed = 130; // px/s vertical on ladder
  const gravity = 900;   // px/s^2
  const jumpVel = -360;  // px/s upward

  // Helper: check if player overlaps a rectangle
  function overlaps(rect) {
    return (
      g.x < rect.x + rect.w &&
      g.x + playerW > rect.x &&
      g.y < rect.y + rect.h &&
      g.y + playerH > rect.y
    );
  }

  // Helper: check horizontal overlap with a platform (for landing)
  function horizOverlap(plat) {
    return g.x < plat.x + plat.w && g.x + playerW > plat.x;
  }

  // Determine if currently on a ladder
  let onLadder = false;
  for (const lad of level.ladders) {
    if (overlaps(lad)) {
      onLadder = true;
      break;
    }
  }

  // FIX: Allow entering ladder from top platform when standing on it and pressing down.
  // If grounded, check if feet are touching the top of a ladder (within small tolerance).
  if (!onLadder && g.onGround) {
    for (const lad of level.ladders) {
      const feet = g.y + playerH;
      // Check horizontal overlap with ladder
      if (g.x < lad.x + lad.w && g.x + playerW > lad.x) {
        // Check if feet are at the top edge of the ladder (allowing 2px tolerance for float errors)
        if (feet >= lad.y - 2 && feet <= lad.y + 2) {
          onLadder = true;
          break;
        }
      }
    }
  }

  // --- LADDER MOVEMENT ---
  // Check if we are actively climbing up or down
  const isClimbing = onLadder && (input.up || input.down);

  if (isClimbing) {
    // Disable gravity while climbing
    g.vy = 0;
    if (input.up) {
      g.y -= climbSpeed * dt;
    }
    if (input.down) {
      g.y += climbSpeed * dt;
    }
  } else {
    // --- GRAVITY & JUMPING (Not climbing) ---

    // Apply gravity if not on ground and not on a ladder
    // If onLadder but not climbing, we still apply gravity unless onGround
    if (!g.onGround && !onLadder) {
      g.vy += gravity * dt;
    }

    // Jumping: only if on ground
    if (input.jump && g.onGround) {
      g.vy = jumpVel;
      g.onGround = false;
    }
  }

  // --- HORIZONTAL MOVEMENT ---
  let vx = 0;
  if (input.left) vx = -moveSpeed;
  if (input.right) vx = moveSpeed;

  g.vx = vx;
  g.x += g.vx * dt;

  // Clamp X to screen bounds [0, 936] (960 - 24)
  if (g.x < 0) g.x = 0;
  if (g.x > 936) g.x = 936;

  // --- VERTICAL MOVEMENT & COLLISION ---

  // Save previous Y for landing detection
  const prevY = g.y;
  const prevFeet = prevY + playerH;

  // Apply vertical velocity
  g.y += g.vy * dt;

  const newFeet = g.y + playerH;

  // Reset onGround for this frame, will be set true if we land
  g.onGround = false;

  // Check platform collisions
  for (const plat of level.platforms) {
    // Only check landing if we are descending (vy >= 0)
    // And we crossed the platform top surface
    // Previous feet <= platform.y AND New feet >= platform.y
    // And horizontal overlap exists

    if (g.vy >= 0 && horizOverlap(plat)) {
      if (prevFeet <= plat.y + 1 && newFeet >= plat.y) { // +1 for float tolerance
        // Land on platform
        g.y = plat.y - playerH;
        g.vy = 0;
        g.onGround = true;
      }
    }
  }

  // Note: The prompt says "Standing at surface counts grounded".
  // If we start on ground, onGround is true. If we don't move vertically, we stay on ground.
  // Our logic above sets onGround=false then checks for landing.
  // If we were already on ground and didn't jump/move off, vy=0, prevFeet==plat.y, newFeet==plat.y.
  // The condition prevFeet <= plat.y && newFeet >= plat.y holds. So it re-sets onGround=true. Correct.

  // Important: Do NOT clamp Y to 540. Falling below is handled by game.js.
}

// Export for CommonJS (Node.js)
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { movePlayer };
}

// Export for Browser
if (typeof window !== 'undefined') {
  window.DMPhysics = { movePlayer };
}
