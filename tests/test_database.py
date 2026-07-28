import unittest
from pathlib import Path
import tempfile
from core.database import Database

class TestDatabase(unittest.TestCase):
    def test_database_initialisation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_db_path = Path(tmpdir) / "test_attendance.db"
            test_db = Database.__new__(Database)
            test_db._initialised = False
            test_db.__init__(db_path=temp_db_path)
            
            test_db.initialise_schema()
            self.assertTrue(temp_db_path.exists())
            self.assertTrue(test_db.is_empty())
            
            stats = test_db.table_stats()
            self.assertIn("users", stats)
            self.assertIn("students", stats)
            test_db.close()

if __name__ == "__main__":
    unittest.main()
