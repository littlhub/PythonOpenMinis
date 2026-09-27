"""Dev helper: list top-level symbols of key zcode contract modules."""
import os
import re

root = "G:/PythonOpenMinis-main/PythonOpenMinis-main/src/openminis/agent/zcode/contracts"
for name in ["tools.py", "model.py", "ids.py", "errors.py"]:
    p = os.path.join(root, name)
    if not os.path.exists(p):
        print("MISSING", name)
        continue
    s = open(p, encoding="utf-8").read()
    symbols = set()
    symbols.update(re.findall(r"^class (\w+)", s, re.M))
    symbols.update(re.findall(r"^def (\w+)", s, re.M))
    symbols.update(re.findall(r"^(\w+) = ", s, re.M))
    m = re.search(r"__all__\s*=\s*\[(.*?)\]", s, re.S)
    print("==", name)
    print(sorted(symbols))
