import unittest

from core.search import safe_fts_query


class ResearchMemoryMCPTests(unittest.TestCase):
    def test_fts_query_is_tokenized(self) -> None:
        self.assertEqual(safe_fts_query("approval 연구"), '"approval" OR "연구"')

    def test_fts_query_rejects_non_searchable_text(self) -> None:
        with self.assertRaises(ValueError):
            safe_fts_query("!!!")


if __name__ == "__main__":
    unittest.main()
