// Pure checks: usable before game/renderer integration and by repair diagnostics.
const CONTRACT = Object.freeze({
  'levels.shape': 'Exactly four levels with finite, nonempty geometry.',
  'ladders.endpoints': 'Every ladder center meets platforms at both endpoints.',
  'route.start-to-exit': 'A ladder route connects the actual start support to the exit support.',
  'bananas.support': 'Every banana has a platform no more than 60 pixels below it.'
});
const EPS = 0.01; // Preserve the existing endpoint precision; do not relax it to 2px.
const overlapX = (a, b) => Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
const point = p => p && Number.isFinite(p.x) && Number.isFinite(p.y);
const rect = p => point(p) && Number.isFinite(p.w) && Number.isFinite(p.h) && p.w > 0 && p.h > 0;
const supports = (obj, p) => Math.abs(obj.y + obj.h - p.y) < EPS && overlapX(obj, p) > 0;

// Generic adjacency traversal, deliberately independent of generated source.
function reachableNodes(adjacency, seeds) {
  const seen = new Set(seeds), queue = [...seeds];
  for (let i = 0; i < queue.length; i++) {
    for (const next of adjacency[queue[i]] || []) {
      if (!seen.has(next)) { seen.add(next); queue.push(next); }
    }
  }
  return seen;
}

function levelIssues(level, levelIndex = 0) {
  const issues = [];
  const add = (requirement, message, measured = {}) => issues.push({requirement, levelIndex, message, measured});
  if (!level || typeof level.name !== 'string' || !level.name.trim() ||
      !point(level.start) || !rect(level.exit) ||
      !['platforms', 'ladders', 'bananas'].every(k => Array.isArray(level[k]) && level[k].length > 0) ||
      !Array.from(level.platforms).every(rect) || !Array.from(level.ladders).every(rect) || !Array.from(level.bananas).every(point)) {
    add('levels.shape', 'Expected a named level, finite start/exit, and nonempty valid platforms, ladders and bananas.');
    return issues;
  }
  const ps = level.platforms;
  const adj = ps.map(() => new Set());
  const atEndpoint = (ld, y) => ps.flatMap((p, i) =>
    Math.abs(p.y - y) < EPS && ld.x + ld.w / 2 >= p.x && ld.x + ld.w / 2 <= p.x + p.w ? [i] : []);
  for (const [ladderIndex, ld] of level.ladders.entries()) {
    const cx = ld.x + ld.w / 2;
    const upper = atEndpoint(ld, ld.y), lower = atEndpoint(ld, ld.y + ld.h);
    for (const [endpoint, y, linked] of [['upper', ld.y, upper], ['lower', ld.y + ld.h, lower]]) {
      if (!linked.length) {
        const surfacesAtY = ps.filter(p => Math.abs(p.y - y) < EPS).map(p => [p.x, p.x + p.w]);
        add('ladders.endpoints', `Ladder ${ladderIndex} ${endpoint}: center x=${cx}, y=${y} has no supporting platform; horizontal spans at y: ${JSON.stringify(surfacesAtY)}.`,
          {ladderIndex, endpoint, centerX: cx, y, surfacesAtY});
      }
    }
    for (const u of upper) for (const d of lower) { adj[u].add(d); adj[d].add(u); }
  }
  const player = {...level.start, w: 24, h: 32};
  const startPlatforms = ps.flatMap((p, i) => supports(player, p) ? [i] : []);
  const exitPlatforms = ps.flatMap((p, i) => supports(level.exit, p) ? [i] : []);
  const reached = [...reachableNodes(adj, startPlatforms)];
  if (!startPlatforms.length || !exitPlatforms.some(i => reached.includes(i))) {
    add('route.start-to-exit', `No ladder route: start supports=${JSON.stringify(startPlatforms)}, reached=${JSON.stringify(reached)}, exit supports=${JSON.stringify(exitPlatforms)}.`,
      {startPlatforms, reached, exitPlatforms});
  }
  for (const [bananaIndex, b] of level.bananas.entries()) {
    const below = ps.filter(p => b.x >= p.x && b.x <= p.x + p.w && b.y < p.y);
    const gaps = below.map(p => p.y - b.y);
    if (!gaps.some(d => d <= 60)) add('bananas.support',
      `Banana ${bananaIndex} (${b.x},${b.y}) has no platform within 60px below; gaps=${JSON.stringify(gaps)}.`,
      {bananaIndex, x: b.x, y: b.y, gaps});
  }
  return issues;
}

function validateLevels(levels) {
  const issues = [];
  if (!Array.isArray(levels) || levels.length !== 4) issues.push({requirement: 'levels.shape',
    message: 'Expected exactly four levels.', measured: {actual: Array.isArray(levels) ? levels.length : null}});
  if (Array.isArray(levels)) Array.from(levels).forEach((l, i) => issues.push(...levelIssues(l, i)));
  return issues;
}
module.exports = {CONTRACT, reachableNodes, levelIssues, validateLevels};
