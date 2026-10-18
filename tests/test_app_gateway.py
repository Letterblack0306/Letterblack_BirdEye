import unittest

import app_gateway


class AppGatewayTests(unittest.TestCase):
    def test_dashboard_has_effects_editor(self):
        html = app_gateway.dashboard_html()
        self.assertIn("LB Effects Editor", html)
        self.assertIn("/apps/effects/", html)

    def test_effects_app_is_registered(self):
        app = app_gateway.APPS["effects"]
        self.assertEqual(app.path, "/apps/effects/")
        self.assertTrue(str(app.workspace).endswith("_Web Animator"))

    def test_internal_ports_are_dynamic(self):
        port = app_gateway._free_port()
        self.assertGreater(port, 0)
        self.assertNotEqual(port, 8766)


if __name__ == "__main__":
    unittest.main()
