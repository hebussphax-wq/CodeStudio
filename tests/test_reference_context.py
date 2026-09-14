import json
import pathlib
import tempfile
import unittest
from core import CodeStudioCore


class ReferenceContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.ws = self.root / 'project'
        self.ws.mkdir()
        (self.ws / 'README.md').write_text('Contract: export update and four levels.', encoding='utf-8')
        (self.ws / 'tests').mkdir()
        (self.ws / 'tests/test_game.js').write_text('assert.equal(levels.length, 4)', encoding='utf-8')
        self.messages = []
        self.reject = False
        def transport(command, body):
            self.messages.append(body)
            if body['role'] == 'planner':
                return {'plan': ['Implement game'], 'files': ['game.js'], 'questions': [], 'acceptance': ['four levels']}
            if body['role'] == 'coder':
                return {'edits': [{'path': 'game.js', 'op': 'create', 'content': 'const levels = [1,2,3,4];'}]}
            return {'verdict': 'reject' if self.reject else 'ok', 'issues': ['Missing update'] if self.reject else [], 'summary': ''}
        self.core = CodeStudioCore(self.root, {'workspace': str(self.ws), 'tests': []}, transport=transport)

    def test_new_file_plan_retains_contract_for_coder_and_reviewer(self):
        result = self.core.analyze('Build game', 'fixture')
        for body in self.messages:
            if body['role'] in ('coder', 'reviewer'):
                self.assertIn('export update and four levels', body['messages'][0]['content'])
                self.assertIn('assert.equal(levels.length, 4)', body['messages'][0]['content'])
        self.assertEqual(set(result.final_state), {'game.js'})
        self.assertIn('README.md', result.binding['before'])
        self.assertNotIn('README.md', self.core.fully_read)
        _, issues = self.core.validate_edits([{'path': 'README.md', 'op': 'delete'}], {'game.js'})
        self.assertTrue(any('Plan-Scope' in x for x in issues))

    def test_reference_change_invalidates_proposal(self):
        result = self.core.analyze('Build game', 'fixture')
        (self.ws / 'README.md').write_text('Changed contract', encoding='utf-8')
        with self.assertRaises((ValueError, RuntimeError)):
            self.core.claim_proposal(result)

    def test_budget_secret_and_binary_exclusion(self):
        (self.ws / 'private.txt').write_text('password=DoNotIncludeThis', encoding='utf-8')
        (self.ws / 'binary.txt').write_bytes(b'abc\0def')
        self.core.config['reference_max_bytes'] = 12
        refs, evidence = self.core.reference_context(self.core.file_tree(), [{'path': 'private.txt'}, {'path': 'binary.txt'}], [])
        self.assertLessEqual(sum(x['bytes'] for x in evidence.values()), 12)
        self.assertTrue(evidence['README.md']['truncated'])
        self.core.config['reference_max_bytes'] = 16000
        refs, _ = self.core.reference_context(self.core.file_tree(), [{'path': 'private.txt'}, {'path': 'binary.txt'}], [])
        self.assertNotIn('private.txt', refs)
        self.assertNotIn('binary.txt', refs)

    def test_duplicate_rejected_candidate_stops_before_repeat_review(self):
        self.reject = True
        with self.assertRaisesRegex(RuntimeError, 'kein Fortschritt'):
            self.core.analyze('Build game', 'fixture')
        self.assertEqual(sum(x['role'] == 'reviewer' for x in self.messages), 1)
        receipt = json.loads(next(self.core.runs.glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(receipt['status'], 'rejected')
        self.assertEqual(receipt['rounds'][-1]['status'], 'no_progress')
        self.assertFalse((self.ws / 'game.js').exists())

    def test_failure_learning_reused_only_for_identical_inputs(self):
        self.reject = True
        with self.assertRaises(RuntimeError):
            self.core.analyze('Build game', 'fixture')
        self.messages.clear()
        with self.assertRaisesRegex(RuntimeError, 'kein Fortschritt'):
            self.core.analyze('Build game', 'fixture')
        coder = next(x for x in self.messages if x['role'] == 'coder')
        self.assertIn('Missing update', coder['messages'][0]['content'])
        self.assertFalse(any(x['role'] == 'reviewer' for x in self.messages))
        (self.ws / 'README.md').write_text('Different API contract', encoding='utf-8')
        self.messages.clear()
        self.reject = False
        result = self.core.analyze('Build game', 'fixture')
        self.assertEqual(result.receipt['learning']['reused_failures'], 0)
        self.assertTrue(any(x['role'] == 'reviewer' for x in self.messages))


if __name__ == '__main__':
    unittest.main()
