import json,os,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
import replau_sla_monitor as monitor

class SlaMonitorTests(unittest.TestCase):
 def test_closed_restaurant_is_quiet(self):
  with TemporaryDirectory() as d:
   p=Path(d)/"status.json"; p.write_text(json.dumps({"accepting_orders":False}))
   with patch.object(monitor,"STATUS_PATH",p): self.assertTrue(monitor.restaurant_quiet())
 def test_posts_thresholds_and_notifies(self):
  response=Mock(); response.json.return_value={"ok":True,"notifications":[{"level":"URGENT","wait_minutes":16}]}
  with patch.object(monitor.requests,"post",return_value=response) as post,patch.object(monitor,"restaurant_quiet",return_value=False),patch.object(monitor,"desktop_notice") as notice:
   result=monitor.run()
  self.assertTrue(result["ok"]); notice.assert_called_once(); self.assertEqual(post.call_args.kwargs["json"],{"p_warning_minutes":10,"p_urgent_minutes":15,"p_cooldown_minutes":30,"p_quiet":False})
 def test_invalid_response_fails(self):
  response=Mock(); response.json.return_value={"ok":False}
  with patch.object(monitor.requests,"post",return_value=response),patch.object(monitor,"restaurant_quiet",return_value=False):
   with self.assertRaises(RuntimeError): monitor.run()

if __name__=="__main__": unittest.main()
