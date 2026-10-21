from __future__ import annotations

import json
import math
import uuid
from datetime import datetime
from decimal import Decimal

from django.test import SimpleTestCase

from resource_registry.utils.json_checksum import (
    TransformationSpecificationChecksumError,
    calculate_transformation_specification_checksum,
    canonicalize_transformation_specification,
)


class TransformationSpecificationChecksumTests(SimpleTestCase):
    def test_equivalent_dictionaries_with_different_order_produce_same_checksum(self) -> None:
        first = {"spec_version": "1.0", "steps": [{"step_key": "inspect"}]}
        second = {"steps": [{"step_key": "inspect"}], "spec_version": "1.0"}

        self.assertEqual(
            calculate_transformation_specification_checksum(first),
            calculate_transformation_specification_checksum(second),
        )

    def test_nested_object_order_does_not_affect_checksum(self) -> None:
        first = {"parameters": {"alpha": 1, "beta": {"x": True, "y": None}}}
        second = {"parameters": {"beta": {"y": None, "x": True}, "alpha": 1}}

        self.assertEqual(
            calculate_transformation_specification_checksum(first),
            calculate_transformation_specification_checksum(second),
        )

    def test_array_order_changes_checksum(self) -> None:
        first = {"steps": [{"step_key": "inspect"}, {"step_key": "render"}]}
        second = {"steps": [{"step_key": "render"}, {"step_key": "inspect"}]}

        self.assertNotEqual(
            calculate_transformation_specification_checksum(first),
            calculate_transformation_specification_checksum(second),
        )

    def test_different_values_change_checksum(self) -> None:
        first = {"spec_version": "1.0", "enabled": True}
        second = {"spec_version": "1.0", "enabled": False}

        self.assertNotEqual(
            calculate_transformation_specification_checksum(first),
            calculate_transformation_specification_checksum(second),
        )

    def test_numeric_values_follow_python_json_encoder_behavior(self) -> None:
        self.assertNotEqual(
            canonicalize_transformation_specification({"value": 1}),
            canonicalize_transformation_specification({"value": 1.0}),
        )
        self.assertNotEqual(
            canonicalize_transformation_specification({"value": -0.0}),
            canonicalize_transformation_specification({"value": 0.0}),
        )

    def test_empty_dictionary_produces_deterministic_checksum(self) -> None:
        self.assertEqual(
            calculate_transformation_specification_checksum({}),
            calculate_transformation_specification_checksum({}),
        )

    def test_empty_array_produces_deterministic_checksum(self) -> None:
        self.assertEqual(
            calculate_transformation_specification_checksum([]),
            calculate_transformation_specification_checksum([]),
        )

    def test_unicode_content_produces_deterministic_checksum(self) -> None:
        specification = {"label": "Campaña Niño 🚀"}

        self.assertEqual(
            calculate_transformation_specification_checksum(specification),
            calculate_transformation_specification_checksum(
                json.loads('{"label":"Campaña Niño 🚀"}')
            ),
        )

    def test_checksum_is_lowercase_sha256_hex(self) -> None:
        checksum = calculate_transformation_specification_checksum({"spec_version": "1.0"})

        self.assertEqual(len(checksum), 64)
        self.assertRegex(checksum, r"^[0-9a-f]{64}$")

    def test_raw_json_strings_are_rejected_as_specification_documents(self) -> None:
        with self.assertRaises(TransformationSpecificationChecksumError):
            calculate_transformation_specification_checksum('{"spec_version":"1.0"}')  # type: ignore[arg-type]

    def test_nan_and_infinity_are_rejected(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(TransformationSpecificationChecksumError):
                    calculate_transformation_specification_checksum({"value": value})

    def test_non_string_dictionary_keys_are_rejected(self) -> None:
        with self.assertRaises(TransformationSpecificationChecksumError):
            calculate_transformation_specification_checksum({1: "one"})  # type: ignore[dict-item]

    def test_decimal_is_rejected(self) -> None:
        with self.assertRaises(TransformationSpecificationChecksumError):
            calculate_transformation_specification_checksum({"value": Decimal("1.23")})

    def test_uuid_is_rejected(self) -> None:
        with self.assertRaises(TransformationSpecificationChecksumError):
            calculate_transformation_specification_checksum({"id": uuid.uuid4()})

    def test_datetime_is_rejected(self) -> None:
        with self.assertRaises(TransformationSpecificationChecksumError):
            calculate_transformation_specification_checksum({"created_at": datetime.now()})

    def test_sets_are_rejected(self) -> None:
        with self.assertRaises(TransformationSpecificationChecksumError):
            calculate_transformation_specification_checksum({"values": {"a", "b"}})

    def test_tuples_are_rejected(self) -> None:
        with self.assertRaises(TransformationSpecificationChecksumError):
            calculate_transformation_specification_checksum({"values": ("a", "b")})

    def test_arbitrary_objects_are_rejected(self) -> None:
        class UnsupportedValue:
            pass

        with self.assertRaises(TransformationSpecificationChecksumError):
            calculate_transformation_specification_checksum({"value": UnsupportedValue()})

    def test_canonical_json_uses_compact_separators(self) -> None:
        canonical_json = canonicalize_transformation_specification(
            {"b": [True, None], "a": "ñ"},
        )

        self.assertEqual(canonical_json, '{"a":"ñ","b":[true,null]}')

    def test_canonical_json_preserves_array_order(self) -> None:
        canonical_json = canonicalize_transformation_specification(
            {"steps": [{"step_key": "a"}, {"step_key": "b"}]},
        )

        self.assertEqual(canonical_json, '{"steps":[{"step_key":"a"},{"step_key":"b"}]}')
