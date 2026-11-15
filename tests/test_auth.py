import unittest

from app.backend.auth import verify_credentials


class AuthenticationTests(unittest.TestCase):
    def test_unknown_account_is_rejected(self):
        self.assertFalse(verify_credentials("unknown", "not-a-password"))

    def test_wrong_password_is_rejected(self):
        self.assertFalse(verify_credentials("phapnv", "wrong-password"))


if __name__ == "__main__":
    unittest.main()
