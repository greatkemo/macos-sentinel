import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from desktop import launch


class LauncherTests(unittest.TestCase):
    def test_existing_server_is_reused(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(launch,'ROOT',Path(directory)),patch.object(launch,'is_dashboard',return_value=True),patch.object(launch.subprocess,'Popen') as spawn,patch.object(launch.subprocess,'run') as open_browser:
            self.assertEqual(launch.launch(False),'http://127.0.0.1:8000')
            spawn.assert_not_called();open_browser.assert_not_called()

    def test_wrong_service_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(launch,'ROOT',Path(directory)),patch.object(launch,'is_dashboard',return_value=False),patch.object(launch.socket,'socket') as socket,patch.object(launch.subprocess,'Popen') as spawn:
            socket.return_value.__enter__.return_value.connect_ex.return_value=0
            with self.assertRaisesRegex(RuntimeError,'occupied'):
                launch.launch(False)
            spawn.assert_not_called()
