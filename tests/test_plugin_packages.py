"""Include component-owned animation tests in the repository's root suite."""

import importlib.util
import sys
import unittest
from pathlib import Path


def load_tests(loader: unittest.TestLoader, _tests, _pattern):
    suite = unittest.TestSuite()
    animation_dir = Path(__file__).resolve().parents[1] / "animation"
    for test_path in sorted(animation_dir.glob("**/tests/test_*.py")):
        relative = test_path.relative_to(animation_dir).with_suffix("")
        component_id = "_".join(relative.parts)
        module_name = f"_ledgrid_test_{component_id}"
        spec = importlib.util.spec_from_file_location(module_name, test_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load plugin test module: {test_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite
