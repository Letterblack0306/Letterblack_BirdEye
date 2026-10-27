import unittest
from unittest.mock import patch
import mcp_server
import linear_gateway

class LinearGatewayTests(unittest.TestCase):
    def test_discovery(self):
        names={x["name"] for x in mcp_server._TOOL_DEFINITIONS}
        self.assertTrue(set(linear_gateway.NAMES).issubset(names))
    def test_registered(self):
        self.assertTrue(set(linear_gateway.NAMES).issubset(mcp_server._TOOL_REGISTRY))
    def test_absent_credential_fails_closed(self):
        with patch.object(linear_gateway,"_key",return_value=None):
            r=mcp_server.invoke("linear_search_issues",{"query":"LET-41"})
            self.assertEqual(r["error"],"LINEAR_NOT_CONNECTED")
    def test_status_no_secrets(self):
        with patch.object(linear_gateway,"_key",return_value="do-not-reveal"), patch.object(linear_gateway,"credential_present",return_value=True):
            r=mcp_server.invoke("linear_status",{})
            self.assertNotIn("do-not-reveal",str(r))
            self.assertTrue(r["credential_usable"])
    def test_query_parameterized(self):
        with patch.object(linear_gateway,"_request",return_value={"ok":True,"data":{}}) as stub:
            r=mcp_server.invoke("linear_search_issues",{"query":"a\"bad", "limit":25})
            self.assertTrue(r["ok"])
            self.assertEqual(stub.call_args.args[1]["limit"],25)
            self.assertEqual(stub.call_args.args[1]["term"],'a"bad')
    def test_bad_input_denied(self):
        self.assertEqual(mcp_server.invoke("linear_get_issue",{"id":""})["error"],"INVALID_ISSUE_ID")
    def test_write_disabled_by_default(self):
        with patch.object(linear_gateway,"_write_policy",return_value={}):
            for name,params in [("linear_create_issue",{"title":"Test"}),("linear_update_issue",{"id":"LET-41","title":"Test"})]:
                self.assertEqual(mcp_server.invoke(name,params)["error"],"LINEAR_WRITE_DISABLED")
    def test_issue_team_is_checked_before_update(self):
        with patch.object(linear_gateway,"_write_policy",return_value={"team_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}), patch.object(linear_gateway,"_team_of_issue",return_value="other"), patch.object(linear_gateway,"_request") as mock_call:
            self.assertEqual(mcp_server.invoke("linear_update_issue",{"id":"LET-41","title":"Test"})["error"],"LINEAR_TEAM_DENIED")
            mock_call.assert_not_called()

if __name__=="__main__":
    unittest.main()
