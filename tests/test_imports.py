import unittest
import importlib

class TestModuleImports(unittest.TestCase):
    def test_module_imports(self):
        modules = [
            "config.settings",
            "config.theme",
            "core.database",
            "core.validators",
            "core.auth",
            "core.audit",
            "core.logger",
            "models.academic",
            "models.student",
            "models.subject",
            "models.faculty",
            "models.attendance",
            "models.timetable",
            "models.leave",
            "services.report_service",
            "services.import_export",
            "services.analytics_service",
        ]
        for mod_name in modules:
            imported = importlib.import_module(mod_name)
            self.assertIsNotNone(imported)

if __name__ == "__main__":
    unittest.main()
