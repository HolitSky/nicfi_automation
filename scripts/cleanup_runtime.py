from pathlib import Path
import shutil

BASE_DIR = Path(__file__).resolve().parent.parent

runtime_files = [
    BASE_DIR / "config" / "selected_quads.json",
    BASE_DIR / "config" / "last_run.json",
]
runtime_dirs = [
    BASE_DIR / "config_backups",
    BASE_DIR / "__pycache__",
]

print("File runtime yang akan dibersihkan:")
for path in runtime_files + runtime_dirs:
    print(f"- {path}")

confirmation = input("Ketik BERSIHKAN untuk melanjutkan: ").strip()
if confirmation != "BERSIHKAN":
    print("Dibatalkan.")
    raise SystemExit(0)

for path in runtime_files:
    if path.exists():
        path.unlink()
        print(f"Dihapus: {path}")

for path in runtime_dirs:
    if path.exists():
        shutil.rmtree(path)
        print(f"Dihapus: {path}")

remove_outputs = input(
    "Hapus juga seluruh folder downloads? Ketik HAPUS OUTPUT, "
    "atau tekan Enter untuk melewati: "
).strip()

if remove_outputs == "HAPUS OUTPUT":
    output_dir = BASE_DIR / "downloads"
    if output_dir.exists():
        shutil.rmtree(output_dir)
        print(f"Dihapus: {output_dir}")
else:
    print("Folder downloads dipertahankan.")

print("Pembersihan selesai.")
