"""Open the dedicated CodeStudio workspace in the installed Visual Studio Code."""
import argparse
import os
import pathlib
import subprocess
import sys

def main():
    parser=argparse.ArgumentParser(description='CodeStudio in Visual Studio Code starten')
    parser.add_argument('project',nargs='?')
    args=parser.parse_args()
    frozen=bool(getattr(sys,'frozen',False))
    root=pathlib.Path(sys.executable).parent if frozen else pathlib.Path(__file__).resolve().parent
    local_appdata=pathlib.Path(os.environ.get('LOCALAPPDATA') or pathlib.Path.home()/'AppData'/'Local')
    candidates=[os.environ.get('CODESTUDIO_VSCODE'),str(local_appdata/'Programs/Microsoft VS Code/Code.exe')]
    for key in ('LOCALAPPDATA','ProgramFiles','ProgramFiles(x86)'):
        if os.environ.get(key):
            candidates.append(str(pathlib.Path(os.environ[key])/('Programs/Microsoft VS Code/Code.exe' if key=='LOCALAPPDATA' else 'Microsoft VS Code/Code.exe')))
    exe=next((pathlib.Path(p) for p in candidates if p and pathlib.Path(p).is_absolute() and pathlib.Path(p).is_file()),None)
    if not exe: raise RuntimeError('Visual Studio Code ist nicht installiert oder CODESTUDIO_VSCODE ist nicht gesetzt.')
    state=local_appdata/'CodeStudio'/'VSCode'
    workspace=pathlib.Path(args.project).resolve() if args.project else root/'workspace'
    if not workspace.is_dir(): raise ValueError('Projektordner existiert nicht: '+str(workspace))
    argv=[str(exe),'--new-window','--skip-welcome','--skip-release-notes','--user-data-dir',str(state)]
    env=dict(os.environ);env.pop('ELECTRON_RUN_AS_NODE',None)
    if frozen:
        extensions=root/'vscode-extensions'
        if not extensions.is_dir(): raise RuntimeError('Vollständiges CodeStudio-Paket einschließlich vscode-extensions erforderlich.')
        argv+=['--extensions-dir',str(extensions)]
    else:
        argv+=['--extensions-dir',str(state/'source-extensions'),'--extensionDevelopmentPath',str(root/'vscode')]
        env['CODESTUDIO_PYTHON']=sys.executable
    argv.append(str(workspace))
    subprocess.Popen(argv,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
    return 0

if __name__=='__main__': raise SystemExit(main())
