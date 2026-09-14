// renderer.js - Rendering module for Donkey Monkey
// Exports: { draw }

(function(root) {
  const CANVAS_W = 960;
  const CANVAS_H = 540;

  // --- Drawing Helpers ---

  function drawJungleGradient(ctx) {
    const grad = ctx.createLinearGradient(0, 0, 0, CANVAS_H);
    grad.addColorStop(0, '#1a2e1a'); // Dark jungle top
    grad.addColorStop(1, '#354a35'); // Lighter jungle bottom
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
  }

  function drawPlatform(ctx, p, tint) {
    ctx.save();
    // Base color varies slightly by level index (tint)
    const r = 139 - tint * 5;
    const g = 69 + tint * 2;
    const b = 19 + tint * 2;
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(p.x, p.y, p.w, p.h);

    // Edge highlight (top)
    ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
    ctx.fillRect(p.x, p.y, p.w, 4);

    // Shadow (bottom)
    ctx.fillStyle = 'rgba(0, 0, 0, 0.3)';
    ctx.fillRect(p.x, p.y + p.h - 2, p.w, 2);
    ctx.restore();
  }

  function drawLadder(ctx, l) {
    ctx.save();
    ctx.strokeStyle = '#8B4513'; // SaddleBrown
    ctx.lineWidth = 4;

    // Rails
    const leftX = l.x + 4;
    const rightX = l.x + l.w - 4;

    ctx.beginPath();
    ctx.moveTo(leftX, l.y);
    ctx.lineTo(leftX, l.y + l.h);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(rightX, l.y);
    ctx.lineTo(rightX, l.y + l.h);
    ctx.stroke();

    // Rungs
    const rungSpacing = 16;
    for (let y = l.y + 8; y < l.y + l.h; y += rungSpacing) {
      ctx.beginPath();
      ctx.moveTo(leftX, y);
      ctx.lineTo(rightX, y);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawBanana(ctx, b) {
    ctx.save();
    ctx.translate(b.x + 12, b.y + 16); // Center of 24x32 hitbox approx
    ctx.rotate(-Math.PI / 4);
    ctx.beginPath();
    ctx.arc(0, 0, 8, 0.5 * Math.PI, 1.5 * Math.PI, false);
    ctx.lineWidth = 6;
    ctx.strokeStyle = '#FFD700'; // Gold
    ctx.lineCap = 'round';
    ctx.stroke();
    ctx.restore();
  }

  function drawBarrel(ctx, barrel) {
    ctx.save();
    const cx = barrel.x + barrel.w / 2;
    const cy = barrel.y + barrel.h / 2;
    const r = barrel.w / 2;

    // Body
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = '#8B4513'; // SaddleBrown
    ctx.fill();
    ctx.strokeStyle = '#3e1f09';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Metal Bands
    ctx.beginPath();
    ctx.arc(cx, cy, r - 2, 0, Math.PI * 2);
    ctx.strokeStyle = '#A9A9A9'; // DarkGray
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.restore();
  }

  function drawMonkey(ctx, x, y, scale) {
    ctx.save();
    ctx.translate(x + 12, y + 16); // Center pivot for 24x32 box
    ctx.scale(scale || 1, scale || 1);

    const bodyColor = '#8B4513'; // SaddleBrown
    const faceColor = '#DEB887'; // BurlyWood
    const earColor = '#CD853F';  // Peru

    // Tail (behind body)
    ctx.beginPath();
    ctx.moveTo(0, 10);
    ctx.quadraticCurveTo(-15, 15, -20, 5);
    ctx.strokeStyle = bodyColor;
    ctx.lineWidth = 4;
    ctx.lineCap = 'round';
    ctx.stroke();

    // Feet
    ctx.fillStyle = '#3e1f09';
    ctx.fillRect(-8, 12, 6, 4);
    ctx.fillRect(2, 12, 6, 4);

    // Body
    ctx.beginPath();
    ctx.arc(0, 5, 8, 0, Math.PI * 2);
    ctx.fillStyle = bodyColor;
    ctx.fill();

    // Head
    ctx.beginPath();
    ctx.arc(0, -6, 7, 0, Math.PI * 2);
    ctx.fillStyle = bodyColor;
    ctx.fill();

    // Ears
    ctx.beginPath();
    ctx.arc(-7, -6, 3, 0, Math.PI * 2);
    ctx.arc(7, -6, 3, 0, Math.PI * 2);
    ctx.fillStyle = earColor;
    ctx.fill();

    // Face
    ctx.beginPath();
    ctx.ellipse(0, -4, 5, 4, 0, 0, Math.PI * 2);
    ctx.fillStyle = faceColor;
    ctx.fill();

    // Eyes
    ctx.fillStyle = 'black';
    ctx.fillRect(-3, -6, 1.5, 1.5);
    ctx.fillRect(1.5, -6, 1.5, 1.5);

    ctx.restore();
  }

  function drawExit(ctx, exit) {
    ctx.save();
    // Pole
    ctx.fillStyle = '#C0C0C0'; // Silver
    ctx.fillRect(exit.x + 4, exit.y, 4, exit.h);

    // Flag (Glowing Gold)
    ctx.shadowColor = '#FFD700';
    ctx.shadowBlur = 15;
    ctx.fillStyle = '#FFD700';
    ctx.beginPath();
    ctx.moveTo(exit.x + 8, exit.y);
    ctx.lineTo(exit.x + 24, exit.y + 10);
    ctx.lineTo(exit.x + 8, exit.y + 20);
    ctx.fill();
    ctx.shadowBlur = 0;

    // Base
    ctx.fillStyle = '#555';
    ctx.fillRect(exit.x, exit.y + exit.h - 4, exit.w, 4);
    ctx.restore();
  }

  function drawTextOverlay(ctx, title, subtitle) {
    ctx.save();
    // Semi-transparent background for readability
    ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
    ctx.fillRect(CANVAS_W / 2 - 150, CANVAS_H / 2 - 40, 300, 80);

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // Title
    ctx.font = 'bold 36px sans-serif';
    ctx.fillStyle = '#FFD700'; // Gold
    ctx.shadowColor = 'black';
    ctx.shadowBlur = 4;
    ctx.fillText(title, CANVAS_W / 2, CANVAS_H / 2 - 15);

    // Subtitle
    ctx.font = '20px sans-serif';
    ctx.fillStyle = '#FFFFFF';
    ctx.fillText(subtitle, CANVAS_W / 2, CANVAS_H / 2 + 25);

    ctx.restore();
  }

  function drawHUD(ctx, game) {
    ctx.save();
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.font = '16px monospace';
    ctx.fillStyle = '#FFFFFF';
    ctx.shadowColor = 'black';
    ctx.shadowBlur = 2;

    ctx.fillText(`Lives: ${game.lives}`, 10, 10);
    ctx.fillText(`Score: ${game.score}`, 10, 30);
    ctx.fillText(`Level: ${game.levelIndex + 1}`, 10, 50);

    ctx.restore();
  }

  // --- Main Draw Function ---

  function draw(ctx, game, api, background, now) {
    // Clear canvas
    ctx.clearRect(0, 0, CANVAS_W, CANVAS_H);

    // 1. Background
    if (background && background.naturalWidth > 0) {
      ctx.drawImage(background, 0, 0, CANVAS_W, CANVAS_H);
      // Translucent navy overlay
      ctx.fillStyle = 'rgba(0, 0, 128, 0.4)';
      ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
    } else {
      drawJungleGradient(ctx);
    }

    // 2. Title Screen (No Game State)
    if (!game) {
      drawTextOverlay(ctx, 'Donkey Monkey', 'Press Start to Play');
      return;
    }

    const levelIndex = game.levelIndex || 0;
    const currentLevelData = api.levels[levelIndex];
    if (!currentLevelData) return; // Safety fallback

    // 3. Level Geometry
    // Platforms
    for (const p of currentLevelData.platforms) {
      drawPlatform(ctx, p, levelIndex);
    }

    // Ladders
    for (const l of currentLevelData.ladders) {
      drawLadder(ctx, l);
    }

    // Bananas
    for (const b of game.bananas) {
      drawBanana(ctx, b);
    }

    // Exit
    drawExit(ctx, currentLevelData.exit);

    // 4. Entities
    // Barrels
    for (const barrel of game.barrels) {
      drawBarrel(ctx, barrel);
    }

    // Player Monkey
    // Blink if invulnerable
    const isInvulnerable = game.invulnerabilityTimer > 0;
    const shouldDrawPlayer = !isInvulnerable || Math.floor(now / 100) % 2 === 0;

    if (shouldDrawPlayer) {
      drawMonkey(ctx, game.x, game.y, 1);
    }

    // Larger friendly monkey near top (decorative)
    // Positioned on highest platform roughly center-ish or fixed spot
    const topPlat = currentLevelData.platforms.reduce((prev, curr) => prev.y < curr.y ? prev : curr);
    if (topPlat) {
       drawMonkey(ctx, topPlat.x + 50, topPlat.y - 32, 1.5);
    }

    // 5. HUD
    drawHUD(ctx, game);

    // 6. Status Overlays
    if (game.status === 'won') {
      drawTextOverlay(ctx, 'You Won!', 'Congratulations!');
    } else if (game.status === 'gameover') {
      drawTextOverlay(ctx, 'Game Over', 'Try Again?');
    }
  }

  // --- Exports ---
  const moduleExports = { draw: draw };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = moduleExports;
  }
  if (typeof window !== 'undefined') {
    window.DMRenderer = moduleExports;
  }

})(this);
