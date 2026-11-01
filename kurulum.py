"""Kısayollar, ikon ve güvenlik duvarı. `python kurulum.py` hepsini kurar."""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import subprocess
import sys
import winreg
from pathlib import Path

DONMUS = getattr(sys, "frozen", False)
KOK = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UYGULAMA = KOK / "demirdrop.pyw"
AYAR = Path(os.environ["APPDATA"]) / "DemirDrop"
GIZLI = subprocess.CREATE_NO_WINDOW
KURAL = "Demir Drop"
ACIK = '{374DE290-123F-4565-9164-39C4925E467B}'  # Windows'un "İndirilenler" kimliği


def ayarlar() -> dict:
    """Token bir kez üretilir, ana ekran kısayolu bozulmasın diye sabit kalır."""
    AYAR.mkdir(parents=True, exist_ok=True)
    dosya = AYAR / "ayar.json"
    if dosya.exists():
        return json.loads(dosya.read_text("utf-8"))
    veri = {"token": secrets.token_urlsafe(16), "pc_adi": "Bilgisayarım"}
    _yaz(veri)
    return veri


def _yaz(veri: dict) -> None:
    (AYAR / "ayar.json").write_text(json.dumps(veri, ensure_ascii=False, indent=1), "utf-8")


def gelen_klasoru() -> Path:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as a:
        ham, _ = winreg.QueryValueEx(a, ACIK)
    return Path(os.path.expandvars(ham)) / "iPhone Drop"


def hedef_ve_arg() -> tuple[str, str]:
    """Kısayolun çalıştıracağı şey: derlenmiş exe ya da pythonw + betik."""
    if DONMUS:
        return sys.executable, ""
    aday = Path(sys.executable).with_name("pythonw.exe")
    return str(aday if aday.exists() else sys.executable), f'"{UYGULAMA}"'


def _bilinen(klasor: str) -> Path:
    cikti = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"[Console]::OutputEncoding=[Text.Encoding]::UTF8;[Environment]::GetFolderPath('{klasor}')"],
        capture_output=True, encoding="utf-8", creationflags=GIZLI, check=True)
    return Path(cikti.stdout.strip())


def kisayol(yer: Path, ad: str, ek_arg: str = "") -> None:
    """Yollar ortam değişkeniyle geçer; Türkçe karakter ve tırnak derdi olmasın."""
    hedef, arg = hedef_ve_arg()
    ortam = dict(os.environ, DD_YOL=str(yer / f"{ad}.lnk"), DD_HEDEF=hedef, DD_ARG=f"{arg} {ek_arg}".strip(),
                 DD_KOK=str(Path(hedef).parent), DD_IKON=str(KOK / "ikon.ico"))
    betik = ("$k=(New-Object -ComObject WScript.Shell).CreateShortcut($env:DD_YOL);"
             "$k.TargetPath=$env:DD_HEDEF;$k.Arguments=$env:DD_ARG;$k.WorkingDirectory=$env:DD_KOK;"
             "$k.IconLocation=$env:DD_IKON;$k.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", betik], env=ortam, creationflags=GIZLI, check=True)


def _baslangic_yolu() -> Path:
    return _bilinen("Startup") / "Demir Drop.lnk"


def baslangicta_mi() -> bool:
    return _baslangic_yolu().exists()


def baslangic_ayarla(acik: bool) -> None:
    yol = _baslangic_yolu()
    if acik:
        kisayol(yol.parent, "Demir Drop", "--tepsi")
    else:
        yol.unlink(missing_ok=True)


def kural_var_mi() -> bool:
    return subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={KURAL}"],
                          capture_output=True, creationflags=GIZLI).returncode == 0


def kural_ekle(port: int) -> bool:
    """UAC penceresi açar. Port yalnızca yerel ağa açılır, internete değil."""
    arg = (f'advfirewall firewall add rule name="{KURAL}" dir=in action=allow protocol=TCP '
           f'localport={port} remoteip=localsubnet profile=any')
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh", arg, None, 0) > 32


def kur() -> None:
    """Gönder menüsü ve masaüstü kısayolları. Program taşındıysa yeniden yazar."""
    veri = ayarlar()
    hedef = " ".join(hedef_ve_arg())
    if veri.get("kurulu_hedef") == hedef:
        return
    kisayol(_bilinen("SendTo"), "iPhone'a At")
    kisayol(_bilinen("Desktop"), "Demir Drop")
    if baslangicta_mi():
        baslangic_ayarla(True)
    veri["kurulu_hedef"] = hedef
    _yaz(veri)


if __name__ == "__main__":
    kur()
