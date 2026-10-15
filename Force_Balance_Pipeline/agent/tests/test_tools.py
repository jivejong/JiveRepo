"""agent/tools.py: the five function declarations."""
import unittest

import _support as S  # noqa: F401  (sets the import paths)
from agent import loop, tools

BY_NAME = {d["name"]: d for d in tools.FUNCTION_DECLARATIONS}


class Declarations(unittest.TestCase):
    def test_five_declarations_in_order(self):
        self.assertEqual([d["name"] for d in tools.FUNCTION_DECLARATIONS],
                         ["get_disturbance_context", "list_available_jedi", "list_available_starships", "deploy", "stand_down"])

    def test_names_match_the_loop(self):
        self.assertEqual(set(BY_NAME), set(loop.INFO_TOOLS) | set(loop.TERMINAL_TOOLS))

    def test_each_has_name_description_and_parameters_json_schema_only(self):
        for d in tools.FUNCTION_DECLARATIONS:
            with self.subTest(name=d["name"]):
                self.assertEqual(set(d), {"name", "description", "parametersJsonSchema"})
                self.assertEqual(d["parametersJsonSchema"]["type"], "object")

    def test_descriptions_are_one_sentence(self):
        for d in tools.FUNCTION_DECLARATIONS:
            with self.subTest(name=d["name"]):
                self.assertTrue(d["description"].endswith("."))
                self.assertNotIn(". ", d["description"])

    def test_info_tools_say_the_data_is_already_in_the_user_message(self):
        for name in loop.INFO_TOOLS:
            with self.subTest(name=name):
                self.assertIn("already in the user message", BY_NAME[name]["description"])

    def test_get_disturbance_context(self):
        schema = BY_NAME["get_disturbance_context"]["parametersJsonSchema"]
        self.assertEqual(schema["properties"], {"disturbance_id": {"type": "string"}})
        self.assertEqual(schema["required"], ["disturbance_id"])

    def test_list_available_jedi(self):
        schema = BY_NAME["list_available_jedi"]["parametersJsonSchema"]
        self.assertEqual(schema["properties"], {"sector_id": {"type": "string"}, "signature": {"type": "string"}})
        self.assertEqual(schema["required"], ["sector_id", "signature"])

    def test_list_available_starships_takes_nothing(self):
        schema = BY_NAME["list_available_starships"]["parametersJsonSchema"]
        self.assertEqual(schema["properties"], {})
        self.assertNotIn("required", schema)

    def test_deploy(self):
        schema = BY_NAME["deploy"]["parametersJsonSchema"]
        self.assertEqual(schema["properties"]["jedi_ids"], {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4})
        self.assertEqual(schema["properties"]["starship_id"], {"type": "string"})
        self.assertEqual(schema["properties"]["rationale"], {"type": "string", "maxLength": 1000})
        self.assertEqual(sorted(schema["required"]), ["jedi_ids", "rationale", "starship_id"])

    def test_stand_down(self):
        schema = BY_NAME["stand_down"]["parametersJsonSchema"]
        self.assertEqual(schema["properties"], {"rationale": {"type": "string", "maxLength": 1000}})
        self.assertEqual(schema["required"], ["rationale"])

    def test_limits_match_the_constraint_layer(self):
        from agent import constraints
        self.assertEqual(tools.MAX_PARTY, constraints.MAX_PARTY)


if __name__ == "__main__":
    unittest.main()
