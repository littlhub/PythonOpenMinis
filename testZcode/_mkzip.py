import zipfile
from pathlib import Path
src, out = Path("dist/OpenMinis"), Path("dist/OpenMinis-1.0.3-win64.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in sorted(src.rglob("*")):
        if p.is_file():
            z.write(p, "OpenMinis/" + p.relative_to(src).as_posix())
print(out, round(out.stat().st_size/1024/1024,1), "MB")
with zipfile.ZipFile(out) as z:
    print("CRC:", z.testzip() or "OK")
print("top-level:", sorted(set(x.split("/")[0] for x in z.namelist())))