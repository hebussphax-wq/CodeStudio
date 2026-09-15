import unittest
from director import required_artifacts, validate_director, PlanValidationError
from tests.test_director import workflow

class PlanCoverageTests(unittest.TestCase):
    def test_explicit_lists_are_bounded_and_fenced_examples_ignored(self):
        text='# Project\nordinary style.css prose\n## Required files\n- index.html, style.css: page and styling\n- src/app.js: app\n```md\n- fake.js: example\n```\n## Optional\n- optional.js: optional\n'
        self.assertEqual(set(required_artifacts({'README.md':text})),{'index.html','style.css','src/app.js'})
        self.assertEqual(required_artifacts({'test.py':text}),{})
        with self.assertRaises(ValueError):required_artifacts({'README.md':'## Required files\n- ../escape.js: wrong'})
    def test_plan_cannot_omit_missing_required_deliverables(self):
        with self.assertRaisesRegex(PlanValidationError,'index.html, style.css'):
            validate_director(workflow(),1,9,{'test_app.py'},['test_app.py'],required_files=['index.html','style.css'])
    def test_existing_artifact_does_not_force_artificial_write(self):
        plan=validate_director(workflow(),1,9,{'test_app.py'},['test_app.py','index.html'],required_files=['index.html'])
        self.assertEqual(len(plan['modules']),2)
    def test_actual_producers_cover_contract(self):
        value=workflow();value['modules'][-1]['files']+=['index.html','style.css']
        plan=validate_director(value,1,9,{'test_app.py'},['test_app.py'],required_files=['index.html','style.css'])
        self.assertIn('index.html',plan['modules'][-1]['files'])

if __name__=='__main__':unittest.main()
