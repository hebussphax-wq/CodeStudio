const test = require('node:test');
const assert = require('node:assert/strict');
const {levels} = require('../levels.js');
const {CONTRACT, validateLevels} = require('./level_invariants.cjs');
const issues = validateLevels(levels);

// Fixed requirement IDs: even an empty levels export must execute and fail checks.
for (const [id, description] of Object.entries(CONTRACT)) {
  test(`${id}: ${description}`, () => {
    const failures = issues.filter(issue => issue.requirement === id);
    assert.deepEqual(failures, [], JSON.stringify(failures, null, 2));
  });
}
