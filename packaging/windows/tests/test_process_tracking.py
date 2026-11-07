"""Process-tree tracking must ignore stale parent PIDs that Windows has reused."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smoke_frozen import update_children


class UpdateChildrenTests(unittest.TestCase):
    def test_tracks_descendants_started_after_their_parent(self):
        processes = {10: (1, "desktop.exe"), 11: (10, "amadeus-backend.exe"),
                     12: (10, "msedgewebview2.exe"), 13: (12, "msedgewebview2.exe")}
        created = {10: 100, 11: 110, 12: 120, 13: 130}.get
        tracked: set[int] = set()
        update_children(10, tracked, processes, created)
        self.assertEqual(tracked, {11, 12, 13})

    def test_ignores_older_process_naming_a_reused_pid_as_parent(self):
        # wininit.exe (500) was started at boot by a process whose PID 10 now belongs to desktop.exe;
        # following it would pull in services.exe and every svchost.exe.
        processes = {10: (1, "desktop.exe"), 11: (10, "amadeus-backend.exe"),
                     500: (10, "wininit.exe"), 600: (500, "services.exe"), 700: (600, "svchost.exe")}
        created = {10: 100, 11: 110, 500: 5, 600: 6, 700: 7}.get
        tracked: set[int] = set()
        update_children(10, tracked, processes, created)
        self.assertEqual(tracked, {11})

    def test_ignores_children_whose_start_time_cannot_be_read(self):
        processes = {10: (1, "desktop.exe"), 11: (10, "amadeus-backend.exe"), 12: (10, "protected.exe")}
        created = {10: 100, 11: 110}.get
        tracked: set[int] = set()
        update_children(10, tracked, processes, created)
        self.assertEqual(tracked, {11})

    def test_keeps_tracked_processes_after_they_exit(self):
        tracked = {11}
        update_children(10, tracked, {10: (1, "desktop.exe")}, {10: 100}.get)
        self.assertEqual(tracked, {11})


if __name__ == "__main__":
    unittest.main()
