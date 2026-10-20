"""Native Windows support installation, with the system's own approval dialog."""
import os
import shutil
from pathlib import Path

from omote.setup.catalog import vc_artifact
from omote.setup.process import SetupError, SetupHelp

RUNTIME_CHECK = '''import ctypes
try:
    ctypes.WinDLL("msvcp140.dll")
    ctypes.WinDLL("vcruntime140.dll")
    print("ready")
except OSError:
    print("missing")
'''

INSTALL_RUNTIME = r'''
$ErrorActionPreference = 'Stop'
$installer = $env:OMOTE_VC_INSTALLER
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw 'The Windows support installer could not be verified.'
}
try {
    $process = Start-Process -FilePath $installer -ArgumentList '/install','/quiet','/norestart' -Verb RunAs -Wait -PassThru
} catch {
    Write-Output 'OMOTE_PERMISSION_NEEDED'
    exit 0
}
if ($process.ExitCode -eq 3010) {
    Write-Output 'OMOTE_RESTART_NEEDED'
} elseif ($process.ExitCode -notin @(0,1638)) {
    throw "Windows support installation failed ($($process.ExitCode))."
}
'''


async def ensure_runtime(manager, python):
    if os.name != 'nt':
        return
    if 'ready' in await manager.command([python, '-c', RUNTIME_CHECK], timeout=30):
        return
    manager.update('comfyui', note='Adding Windows support. Allow the Windows permission dialog to continue.')
    installer = await manager.fetch(vc_artifact())
    # Give the verified artifact its executable extension; the download store
    # uses a content-oriented filename. Copy only after hash verification.
    from omote.setup.service import in_thread
    executable = manager.root / 'vc-runtime-setup.exe'
    await in_thread(shutil.copyfile, installer, executable)
    script = manager.root / 'install-windows-support.ps1'
    script.write_text(INSTALL_RUNTIME, encoding='utf-8-sig')
    env = manager.env()
    env['OMOTE_VC_INSTALLER'] = str(executable)
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        output = await manager.command(
            [powershell, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script],
            env=env, timeout=900)
    except SetupError as error:
        raise SetupHelp('Windows support could not be added. Retry setup to try again.') from error
    if 'OMOTE_RESTART_NEEDED' in output:
        raise SetupHelp('Windows support is installed. Restart this computer, then open Omote and choose Retry setup.')
    if 'OMOTE_PERMISSION_NEEDED' in output:
        raise SetupHelp('Windows needs permission to add generation support. Choose Retry setup and allow the Windows permission dialog.')
    if 'ready' not in await manager.command([python, '-c', RUNTIME_CHECK], timeout=30):
        raise SetupHelp('Restart this computer to finish adding Windows support, then choose Retry setup in Omote.')
