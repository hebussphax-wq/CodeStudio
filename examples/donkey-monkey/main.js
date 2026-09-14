// main.js - Browser wiring for Donkey Monkey
// Exports: window.donkeyMonkeyState (debug)

(function(root) {
  // --- Module Loading ---
  let gameModule, rendererModule;

  if (typeof module !== 'undefined' && module.exports) {
    // Node.js environment (for testing logic only, no canvas)
    gameModule = require('./game.js');
    rendererModule = require('./renderer.js');
  } else if (typeof window !== 'undefined') {
    // Browser environment
    gameModule = window.DonkeyMonkey;
    rendererModule = window.DMRenderer;
  }

  if (!gameModule || !rendererModule) return; // Safety check for environments without modules

  const { createGame, update } = gameModule;
  const { draw } = rendererModule;

  // --- DOM Elements ---
  const canvas = document.getElementById('game');
  const ctx = canvas.getContext('2d');
  const startBtn = document.getElementById('start');
  const hudEl = document.getElementById('hud');
  const scoreEl = document.getElementById('score');
  const levelEl = document.getElementById('level');

  // Create stable DOM nodes for lives and status to avoid innerHTML +=
  const livesEl = document.createElement('span');
  livesEl.id = 'lives';
  hudEl.appendChild(livesEl);

  const statusEl = document.createElement('span');
  statusEl.id = 'status';
  hudEl.appendChild(statusEl);

  // --- State ---
  let game = null;
  let lastTime = 0;
  let bgImage = new Image();
  let bgReady = false;

  // Load background image
  bgImage.src = 'assets/jungle.png';
  bgImage.onload = () => { bgReady = true; };
  bgImage.onerror = () => { bgReady = false; }; // Fallback to gradient in renderer

  // --- Input Handling ---
  const keys = {
    left: false,
    right: false,
    up: false,
    down: false,
    jump: false
  };

  let jumpPressed = false; // To handle single jump press per frame

  function handleKey(e, isDown) {
    const code = e.code;
    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Space'].includes(code)) {
      e.preventDefault();
    }

    switch (code) {
      case 'ArrowLeft': keys.left = isDown; break;
      case 'ArrowRight': keys.right = isDown; break;
      case 'ArrowUp': keys.up = isDown; break;
      case 'ArrowDown': keys.down = isDown; break;
      case 'Space':
        if (isDown && !jumpPressed) {
          keys.jump = true;
          jumpPressed = true;
        } else if (!isDown) {
          // Only clear the debounce flag on keyup, do NOT clear keys.jump
          // so that a pending jump request survives until the loop consumes it.
          jumpPressed = false;
        }
        break;
    }
  }

  window.addEventListener('keydown', (e) => handleKey(e, true));
  window.addEventListener('keyup', (e) => handleKey(e, false));

  // Clear keys on blur to prevent stuck movement
  window.addEventListener('blur', () => {
    keys.left = false;
    keys.right = false;
    keys.up = false;
    keys.down = false;
    keys.jump = false;
    jumpPressed = false;
  });

  // --- Game Control ---
  function startGame() {
    game = createGame(0);
    window.donkeyMonkeyState = game; // Expose for debugging

    // Clear inputs on restart
    keys.left = false;
    keys.right = false;
    keys.up = false;
    keys.down = false;
    keys.jump = false;
    jumpPressed = false;

    updateHUD();
  }

  function updateHUD() {
    if (!game) return;
    scoreEl.textContent = `Punkte: ${game.score}`;
    const lvlName = gameModule.levels[game.levelIndex] ? gameModule.levels[game.levelIndex].name : '';
    levelEl.textContent = `Level: ${game.levelIndex + 1} (${lvlName})`;
    livesEl.textContent = `Leben: ${game.lives}`;

    if (game.status === 'won') {
      statusEl.textContent = 'Gewonnen! Enter fuer Neustart';
    } else if (game.status === 'gameover') {
      statusEl.textContent = 'Game Over. Enter fuer Neustart';
    } else {
      // Clear terminal text when playing
      statusEl.textContent = '';
    }
  }

  startBtn.addEventListener('click', startGame);

  // Handle Enter key to start/restart
  window.addEventListener('keydown', (e) => {
    if (e.code === 'Enter') {
      e.preventDefault();
      startGame();
    }
  });

  // --- Game Loop ---
  function loop(timestamp) {
    const dt = Math.min((timestamp - lastTime) / 1000, 0.05); // Cap dt at 50ms
    lastTime = timestamp;

    if (game && game.status === 'playing') {
      update(game, keys, dt);
      keys.jump = false; // Consume the one-frame jump request immediately after update
      window.donkeyMonkeyState = game; // Update debug state
      updateHUD();
    }

    // Draw every frame
    draw(ctx, game, gameModule, bgReady ? bgImage : null, timestamp);

    requestAnimationFrame(loop);
  }

  // Initial title screen draw
  draw(ctx, null, gameModule, bgReady ? bgImage : null, 0);

  // Start loop
  requestAnimationFrame(loop);

})(this);