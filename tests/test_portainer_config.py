"""Vérifie que la stack conserve la configuration MediaMTX de référence."""

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class PortainerConfigTest(unittest.TestCase):
    def test_mediamtx_environment_matches_reference(self):
        stack = yaml.safe_load((ROOT / "deployment/go-stack.yaml").read_text())
        reference = yaml.safe_load((ROOT / "deployment/mediamtx.yml").read_text())
        service = stack["services"]["mediamtx"]
        environment = service["environment"]

        def compare(values, prefix="MTX_"):
            for key, value in values.items():
                if key == "paths":
                    continue
                name = prefix + key.upper()
                if isinstance(value, dict):
                    compare(value, name + "_")
                else:
                    if isinstance(value, list):
                        expected = ",".join(value)
                    elif isinstance(value, bool):
                        expected = str(value).lower()
                    else:
                        expected = str(value)
                    self.assertEqual(environment[name], expected, name)

        compare(reference)
        self.assertEqual(reference["paths"], {"all_others": None})
        self.assertNotIn("volumes", service)


if __name__ == "__main__":
    unittest.main()
