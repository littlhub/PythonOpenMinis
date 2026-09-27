"""Quick import-chain diagnostic for the zcode kernel (dev helper)."""
import importlib
import sys

sys.path.insert(0, "G:/PythonOpenMinis-main/PythonOpenMinis-main/src")
mods = [
    "openminis.agent.zcode.contracts.ids",
    "openminis.agent.zcode.contracts.model",
    "openminis.agent.zcode.contracts.errors",
    "openminis.agent.zcode.agent.turn_state",
    "openminis.agent.zcode.agent.turn_machine",
    "openminis.agent.zcode.agent.message_history",
    "openminis.agent.zcode.tool.types",
    "openminis.agent.zcode.tool.registry",
    "openminis.agent.zcode.tool.scheduler",
    "openminis.agent.zcode.tool.executor.types",
    "openminis.agent.zcode.tool.executor.impl",
]
for m in mods:
    try:
        importlib.import_module(m)
        print("OK  ", m)
    except Exception as e:
        print("FAIL", m, "->", type(e).__name__, str(e)[:150])
