import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import vscode_launcher


class LauncherTests(unittest.TestCase):
    def test_frozen_start_uses_normal_profile_and_does_not_disable_trust(self):
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp)
            home=root/'user'
            local=home/'AppData'/'Local'
            code=local/'Programs'/'Microsoft VS Code'/'Code.exe'
            code.parent.mkdir(parents=True);code.write_bytes(b'fixture')
            package=root/'CodeStudio';package.mkdir()
            (package/'workspace').mkdir();(package/'vscode-extensions').mkdir()
            exe=package/'CodeStudio.exe';exe.write_bytes(b'fixture')
            with patch.dict(os.environ,{'ELECTRON_RUN_AS_NODE':'1'},clear=True), \
                 patch.object(pathlib.Path,'home',return_value=home), \
                 patch.object(vscode_launcher.sys,'frozen',True,create=True), \
                 patch.object(vscode_launcher.sys,'executable',str(exe)), \
                 patch.object(vscode_launcher.sys,'argv',[str(exe)]), \
                 patch.object(vscode_launcher.subprocess,'Popen') as launch:
                self.assertEqual(vscode_launcher.main(),0)
            args=launch.call_args.args[0]
            self.assertEqual(args[0],str(code))
            self.assertEqual(args[args.index('--user-data-dir')+1],str(local/'CodeStudio'/'VSCode'))
            self.assertEqual(args[-1],str(package/'workspace'))
            self.assertIn('--skip-welcome',args)
            self.assertIn('--skip-release-notes',args)
            self.assertNotIn('--disable-workspace-trust',args)
            self.assertNotIn('ELECTRON_RUN_AS_NODE',launch.call_args.kwargs['env'])


if __name__=='__main__':unittest.main()
