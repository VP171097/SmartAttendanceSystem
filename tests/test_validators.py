import unittest
from core.validators import (
    validate_email,
    validate_mobile,
    validate_username,
    validate_password,
    passwords_match,
    required
)

class TestValidators(unittest.TestCase):
    def test_required(self):
        self.assertTrue(required("sample")[0])
        self.assertFalse(required("")[0])
        self.assertFalse(required(None)[0])

    def test_validate_email(self):
        self.assertTrue(validate_email("student@college.ac.in")[0])
        self.assertFalse(validate_email("invalid-email")[0])
        self.assertTrue(validate_email("")[0])  # allow blank by default

    def test_validate_mobile(self):
        self.assertTrue(validate_mobile("9876543210")[0])
        self.assertTrue(validate_mobile("+919876543210")[0])
        self.assertFalse(validate_mobile("12345")[0])

    def test_validate_username(self):
        self.assertTrue(validate_username("admin_user")[0])
        self.assertFalse(validate_username("ab")[0])  # too short
        self.assertFalse(validate_username("user@123")[0])

    def test_validate_password(self):
        self.assertTrue(validate_password("Secure123", strict=True)[0])
        self.assertFalse(validate_password("short", strict=True)[0])
        self.assertFalse(validate_password("nodigits", strict=True)[0])

    def test_passwords_match(self):
        self.assertTrue(passwords_match("pass123", "pass123")[0])
        self.assertFalse(passwords_match("pass123", "different")[0])

if __name__ == "__main__":
    unittest.main()
