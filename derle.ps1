# Temiz bir ortamda exe paketi üretir: .\derle.ps1 -Surum 1.0.0
param([string]$Surum = "1.0.0")
$ErrorActionPreference = "Stop"
$kok = $PSScriptRoot
$d = "$kok\.derleme"
$py = "$d\venv\Scripts\python.exe"
if (-not (Test-Path $py)) { python -m venv "$d\venv" }
& $py -m pip install --quiet --disable-pip-version-check -r "$kok\requirements.txt" pyinstaller
& $py -m PyInstaller --noconfirm --clean --windowed --name DemirDrop --icon "$kok\ikon.ico" `
  --distpath "$d\dist" --workpath "$d\work" --specpath $d `
  --add-data "$kok\telefon.html;." --add-data "$kok\pc.html;." --add-data "$kok\ikon.png;." --add-data "$kok\ikon.ico;." `
  --hidden-import pystray._win32 --hidden-import clr --collect-all pillow_heif --collect-all webview "$kok\demirdrop.pyw"
if ($LASTEXITCODE) { throw "PyInstaller başarısız" }
Compress-Archive -Force -Path "$d\dist\DemirDrop" -DestinationPath "$d\DemirDrop-$Surum-windows-x64.zip"
Get-Item "$d\DemirDrop-$Surum-windows-x64.zip" | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
