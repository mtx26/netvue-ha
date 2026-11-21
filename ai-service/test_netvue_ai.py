import unittest

from netvue_ai import IoUTracker, Detection, iou, point_in_zone


class AiUnitTest(unittest.TestCase):
    def test_iou_tracks_same_object_across_frames(self):
        tracker = IoUTracker(confirmation_frames=3)
        first = Detection(0, "person", 0.9, (100, 100, 200, 300))
        second = Detection(0, "person", 0.9, (108, 100, 208, 300))
        tracker.update([first])
        tracker.update([second])
        confirmed = tracker.update([second])
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].label, "person")

    def test_iou_rejects_different_object_position(self):
        self.assertLess(iou((0, 0, 10, 10), (100, 100, 110, 110)), 0.01)

    def test_zone_uses_normalized_coordinates(self):
        rect = [0.0, 0.5, 0.5, 1.0]
        self.assertTrue(point_in_zone((100, 300, 200, 500), rect, (500, 1000)))
        self.assertFalse(point_in_zone((600, 300, 700, 500), rect, (500, 1000)))


if __name__ == "__main__":
    unittest.main()
