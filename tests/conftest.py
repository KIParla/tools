import importlib.util
import pathlib
import sys

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]

# Ensure the tools directory is on sys.path so that modules can import
# each other with plain `import <name>` statements.
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def load_tool_module(module_name):
    module_path = TOOLS_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"tools_{module_name}", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"tools_{module_name}"] = module  # must register before exec for dataclasses
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
