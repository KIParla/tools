import importlib.util
import pathlib


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]


def load_tool_module(module_name):
    module_path = TOOLS_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"tools_{module_name}", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
