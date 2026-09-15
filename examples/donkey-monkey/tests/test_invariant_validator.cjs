const test = require('node:test');
const assert = require('node:assert/strict');
const {CONTRACT, levelIssues, validateLevels, reachableNodes} = require('./level_invariants.cjs');
// Hand-authored fixtures, independent of the model's generated levels.js.
const good = () => ({name: 'Fixture', start: {x: 10, y: 168}, exit: {x: 60, y: 68, w: 24, h: 32},
  platforms: [{x: 0, y: 200, w: 100, h: 10}, {x: 0, y: 100, w: 100, h: 10}],
  ladders: [{x: 40, y: 100, w: 20, h: 100}], bananas: [{x: 20, y: 160}]});
const has = (l, id) => levelIssues(l).some(i => i.requirement === id);

test('valid supported route passes; graph handles cycles and disconnected nodes', () => {
  assert.deepEqual(levelIssues(good()), []);
  assert.deepEqual([...reachableNodes([new Set([1]), new Set([0]), new Set()], [0])], [0, 1]);
});
test('same-height disconnected island cannot seed the route', () => {
  const l = good();
  l.platforms[0].w = 30;
  l.platforms.push({x: 40, y: 200, w: 60, h: 10});
  assert.equal(has(l, 'ladders.endpoints'), false);
  const failure = levelIssues(l).find(i => i.requirement === 'route.start-to-exit');
  assert.deepEqual(failure.measured.startPlatforms, [0]);
  assert.deepEqual(failure.measured.reached, [0]);
});
test('endpoint X violations include supporting spans and actual center', () => {
  const l = good(); l.ladders[0].x = 110;
  const failures = levelIssues(l).filter(i => i.requirement === 'ladders.endpoints');
  assert.equal(failures.length, 2);
  assert.equal(failures[0].measured.centerX, 120);
  assert.deepEqual(failures[0].measured.surfacesAtY, [[0, 100]]);
});
test('shifted endpoint Y and overlap without center support fail', () => {
  const shifted = good(); shifted.ladders[0].y -= 10;
  assert.equal(has(shifted, 'ladders.endpoints'), true);
  const edge = good(); edge.ladders[0].x = 95;
  assert.equal(has(edge, 'ladders.endpoints'), true);
});
test('unsupported start and exit fail even with otherwise valid ladders', () => {
  for (const key of ['start', 'exit']) {
    const l = good(); l[key].x = 500;
    assert.equal(has(l, 'route.start-to-exit'), true);
  }
});
test('banana support rejects distant or horizontally absent platforms', () => {
  for (const banana of [{x: 20, y: 0}, {x: 500, y: 160}]) {
    const l = good(); l.bananas = [banana];
    assert.equal(has(l, 'bananas.support'), true);
  }
});
test('empty, malformed and nonfinite inputs cannot pass vacuously', () => {
  for (const levels of [[], null, [good()], new Array(4)]) assert.equal(validateLevels(levels)[0].requirement, 'levels.shape');
  for (const change of [l => l.platforms = [], l => l.ladders = [], l => l.start.x = NaN,
    l => l.exit.w = 0, l => l.bananas[0].y = Infinity, l => l.platforms = new Array(4)]) {
    const l = good(); change(l); assert.equal(has(l, 'levels.shape'), true);
  }
});
test('every registered requirement is exercised by a failing counterexample', () => {
  const bad = good(); bad.ladders[0].x = 500; bad.bananas[0].x = 500;
  assert.deepEqual(new Set(validateLevels([bad]).map(i => i.requirement)), new Set(Object.keys(CONTRACT)));
});
