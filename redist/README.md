# VC++ Redistributable bundle folder

The Inno Setup installer expects `vc_redist.x64.exe` in this folder.
Without it, end users on a clean Windows install get the error:

    Failed to load Python DLL '_internal\python312.dll'

## How to populate

Download once from Microsoft, save as `vc_redist.x64.exe` here:

    https://aka.ms/vs/17/release/vc_redist.x64.exe

It's ~14 MB. After that, every `build.bat` run produces an installer
that auto-installs the runtime as a prerequisite.

## Why .gitignored

It's a Microsoft binary; not ours to redistribute under the project license.
Each developer downloads their own copy.
