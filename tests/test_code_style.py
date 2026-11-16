"""Check new module help without changing imported or direct-call behavior.

Usage: python3 -m unittest discover -s tests -v.
"""

import ast
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ModuleHelpTests(unittest.TestCase):
    def test_help_only_modules_keep_other_calls_silent(self):
        for path in (ROOT / "scripts").glob("*.py"):
            tree = ast.parse(path.read_text())
            if not any(
                isinstance(node, ast.FunctionDef) and node.name == "usage" for node in tree.body
            ):
                continue
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            for flag in ["--help", "-h"]:
                result = subprocess.run(
                    [sys.executable, str(path), flag],
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, path.name + result.stderr)
                self.assertIn("Usage:", result.stdout)
            for arguments in [[], ["--unknown"]]:
                result = subprocess.run(
                    [sys.executable, str(path), *arguments],
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    (result.returncode, result.stdout, result.stderr), (0, "", ""), path.name
                )


if __name__ == "__main__":
    unittest.main()
