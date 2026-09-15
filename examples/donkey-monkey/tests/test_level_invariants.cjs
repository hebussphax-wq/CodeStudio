// tests/test_level_invariants.cjs
// Ausführbare Fassung der Prosa-Invariante aus dem Modulvertrag:
// "erreichbar verbundener Weg vom Start zum Ausgang".
// Form-Tests (Anzahl, endliche Zahlen) reichen dafür nicht – hier wird gerechnet.
const test = require('node:test');
const assert = require('node:assert/strict');
const { levels } = require('../levels.js');

const TOL = 2;                                          // Pixel-Toleranz für Auflagekanten
const overlapX = (a, b) => Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
const restsOn = (obj, p) => Math.abs(obj.y + obj.h - p.y) <= TOL && overlapX(obj, p) > 0;
const platformsAtY = (l, y) => l.platforms.filter(p => Math.abs(p.y - y) <= TOL);

function ladderLinks(l, ld) {
  const upper = platformsAtY(l, ld.y).filter(p => overlapX(p, ld) > 0);
  const lower = platformsAtY(l, ld.y + ld.h).filter(p => overlapX(p, ld) > 0);
  return { upper, lower };
}

// Plattform-Graph: Kante, wenn eine Leiter beide verbindet. Erreichbarkeit per BFS.
function reachable(l) {
  const idx = p => l.platforms.indexOf(p);
  const adj = l.platforms.map(() => new Set());
  for (const ld of l.ladders) {
    const { upper, lower } = ladderLinks(l, ld);
    for (const u of upper) for (const d of lower) { adj[idx(u)].add(idx(d)); adj[idx(d)].add(idx(u)); }
  }
  const startP = l.platforms.find(p => restsOn({ x: l.start.x, y: l.start.y, w: 24, h: 32 }, p));
  const exitP = l.platforms.find(p => restsOn(l.exit, p) || (Math.abs(p.y - (l.exit.y + l.exit.h)) <= TOL && overlapX(p, l.exit) > 0));
  if (!startP || !exitP) return { ok: false, why: `start auf Plattform: ${!!startP}, exit auf Plattform: ${!!exitP}` };
  const seen = new Set([idx(startP)]); const q = [idx(startP)];
  while (q.length) for (const n of adj[q.shift()]) if (!seen.has(n)) { seen.add(n); q.push(n); }
  return { ok: seen.has(idx(exitP)), why: `erreichte Plattformen ${[...seen].join(',')}, exit=${idx(exitP)}` };
}

for (const [li, l] of levels.entries()) {
  test(`Level ${li} "${l.name}": jede Leiter verbindet genau eine obere und eine untere Plattform`, () => {
    for (const [i, ld] of l.ladders.entries()) {
      const { upper, lower } = ladderLinks(l, ld);
      assert.ok(upper.length >= 1 && lower.length >= 1,
        `Leiter ${i} x=${ld.x} y=${ld.y}..${ld.y + ld.h}: oben=${upper.length} unten=${lower.length}. ` +
        `Leiter muss in x mit einer Plattform bei y=${ld.y} UND einer bei y=${ld.y + ld.h} überlappen.`);
    }
  });
  test(`Level ${li} "${l.name}": Ausgang vom Start über Leitern erreichbar`, () => {
    const r = reachable(l);
    assert.ok(r.ok, r.why);
  });
  test(`Level ${li} "${l.name}": Bananen liegen über einer Plattform`, () => {
    for (const [i, b] of l.bananas.entries()) {
      assert.ok(l.platforms.some(p => b.x >= p.x && b.x <= p.x + p.w && b.y < p.y && p.y - b.y <= 60),
        `Banane ${i} (${b.x},${b.y}) hat keine Plattform bis 60px darunter.`);
    }
  });
}
