import unittest

from resolve_facebook_share_url import canonical_facebook_target


class ResolveFacebookShareUrlTests(unittest.TestCase):
    def test_canonicalizes_reel_redirect(self):
        value = "https://www.facebook.com/reel/1054985397317048/?rdid=abc&share_url=https%3A%2F%2Fwww.facebook.com%2Fshare%2Fr%2Fx%2F"
        self.assertEqual(canonical_facebook_target(value), "https://www.facebook.com/reel/1054985397317048/")

    def test_unwraps_login_next_to_story_target(self):
        value = "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fstory.php%3Fstory_fbid%3D1691321539667513%26id%3D100063690504939%26post_id%3D100063690504939_1691321539667513%26rdid%3Dx"
        self.assertEqual(
            canonical_facebook_target(value),
            "https://www.facebook.com/story.php?story_fbid=1691321539667513&id=100063690504939&post_id=100063690504939_1691321539667513",
        )

    def test_keeps_group_permalink_and_rejects_non_target(self):
        value = "https://www.facebook.com/groups/132423725423951/permalink/1415834113749566/?rdid=x"
        self.assertEqual(
            canonical_facebook_target(value),
            "https://www.facebook.com/groups/132423725423951/permalink/1415834113749566/",
        )
        self.assertIsNone(canonical_facebook_target("https://www.facebook.com/login/"))
        self.assertIsNone(canonical_facebook_target("https://example.com/reel/123/"))


if __name__ == "__main__":
    unittest.main()
