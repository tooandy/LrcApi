import unittest
from unittest.mock import Mock, patch
from mod.searchx import lrclib


class LRCLIBLegacyFallbackTests(unittest.TestCase):
    def setUp(self):
        lrclib._cache.clear()
        lrclib._cooldown_until=0

    def test_strict_metadata_and_timestamp(self):
        payload = [
            {"trackName": "I Want to Live", "artistName": "Borislav Slavov",
             "albumName": "Baldur's Gate 3", "duration": 233,
             "syncedLyrics":"[00:10.00]Sample"},
            {"trackName": "Other Song", "artistName": "Borislav Slavov",
             "albumName": "Baldur's Gate 3", "duration": 233,
             "syncedLyrics":"[00:10.00]Wrong"},
        ]
        response = Mock(status_code=200)
        response.json.return_value = payload
        with patch.object(lrclib.requests, "get", return_value=response) as req:
            result=lrclib.search("I Want to Live","Borislav Slavov","Baldur's Gate 3",233)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["source"], "lrclib")
        self.assertEqual(req.call_args.kwargs["timeout"],(3,9))

    def test_duration_rejection(self):
        response=Mock(status_code=200)
        response.json.return_value=[{"trackName":"A","artistName":"B","albumName":"C",
          "duration":200,"syncedLyrics":"[00:00.12]Text"}]
        with patch.object(lrclib.requests, "get", return_value=response):
            self.assertEqual(lrclib.search("A","B","C",233),[])

    def test_rate_limit_cools_down(self):
        response=Mock(status_code=429,headers={"Retry-After":"120"})
        with patch.object(lrclib.requests, "get",return_value=response) as req:
            self.assertEqual(lrclib.search("A","B"),[])
            self.assertEqual(lrclib.search("Different","B"),[])
        self.assertEqual(req.call_count,1)


if __name__ == "__main__":
    unittest.main()
