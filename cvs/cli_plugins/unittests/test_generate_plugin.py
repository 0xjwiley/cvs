import argparse
import unittest
from unittest.mock import MagicMock, patch

from cvs.cli_plugins.generate_plugin import _run_generator


class TestRunGenerator(unittest.TestCase):
    @staticmethod
    def _generator(result):
        generator = MagicMock()
        generator.get_parser.return_value = argparse.ArgumentParser(add_help=False)
        generator.supports_raw_argv.return_value = False
        generator.generate.return_value = result
        return generator

    @patch("cvs.cli_plugins.generate_plugin._discover_generators")
    def test_nonzero_generator_status_exits_with_same_status(self, discover_generators):
        generator = self._generator(7)
        discover_generators.return_value = {"fixture": generator}

        with self.assertRaises(SystemExit) as raised:
            _run_generator("fixture", [])

        self.assertEqual(raised.exception.code, 7)
        generator.generate.assert_called_once()

    @patch("cvs.cli_plugins.generate_plugin._discover_generators")
    def test_zero_generator_status_returns_normally(self, discover_generators):
        generator = self._generator(0)
        discover_generators.return_value = {"fixture": generator}

        _run_generator("fixture", [])

        generator.generate.assert_called_once()

    @patch("cvs.cli_plugins.generate_plugin._discover_generators")
    def test_legacy_none_status_returns_normally(self, discover_generators):
        generator = self._generator(None)
        discover_generators.return_value = {"fixture": generator}

        _run_generator("fixture", [])

        generator.generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
