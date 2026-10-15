"""agent/ulid.py: the ULID encoding."""
import unittest

import _support as S  # noqa: F401  (sets the import paths)
from agent import ulid

CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


class Ulid(unittest.TestCase):
    def test_zero_time_and_zero_bytes_is_26_zeros(self):
        self.assertEqual(ulid.new_ulid(0, bytes(10)), "0" * 26)

    def test_length_is_26(self):
        self.assertEqual(len(ulid.new_ulid(1760000000000, bytes(range(10)))), 26)

    def test_only_crockford_characters(self):
        self.assertLessEqual(set(ulid.new_ulid(1760000000000, bytes(range(10)))), CROCKFORD)

    def test_the_last_character_encodes_the_low_random_bits(self):
        self.assertEqual(ulid.new_ulid(0, bytes(9) + b"\x01"), "0" * 25 + "1")

    def test_the_first_character_encodes_the_top_time_bits(self):
        self.assertEqual(ulid.new_ulid((1 << 48) - 1, bytes(10))[0], "7")

    def test_later_time_sorts_after_earlier_time(self):
        early = ulid.new_ulid(1760000000000, b"\xff" * 10)
        late = ulid.new_ulid(1760000000001, bytes(10))
        self.assertLess(early, late)

    def test_wrong_random_length_is_refused(self):
        with self.assertRaises(ValueError):
            ulid.new_ulid(0, bytes(9))

    def test_a_time_beyond_48_bits_is_refused(self):
        with self.assertRaises(ValueError):
            ulid.new_ulid(1 << 48, bytes(10))

    def test_ulid_now_is_a_valid_ulid(self):
        value = ulid.ulid_now()
        self.assertEqual(len(value), 26)
        self.assertLessEqual(set(value), CROCKFORD)


if __name__ == "__main__":
    unittest.main()
