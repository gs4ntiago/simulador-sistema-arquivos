import unittest
from contextlib import redirect_stdout
from io import StringIO

from filesystem.core import FileSystemError
from filesystem.disk_io import IOFileSystem
from filesystem.memory import Memory, PAGE_SIZE
from filesystem.memory_cli import MemoryCommands
from filesystem.security import SecureFileSystem


def simulator(frames=2, slots=8, policy="lru", block_size=4096, medium="hdd", raid=0):
    blocks = slots * ((PAGE_SIZE + block_size - 1) // block_size)
    fs = IOFileSystem(7, block_size, medium=medium, raid=raid, swap_blocks=blocks)
    return Memory(fs, (4 + frames) * PAGE_SIZE, 4 * PAGE_SIZE, PAGE_SIZE, slots, policy)


class MemoryTests(unittest.TestCase):
    def test_external_fragmentation_and_compaction(self):
        m = simulator()
        for name, size in [("a", 2048), ("b", 4096), ("c", 2048)]:
            m.create(name, size, "critical", "root")
        m.terminate(m.lookup("b"))
        with self.assertRaisesRegex(FileSystemError, "Fragmentação externa"):
            m.create("grande", 6144, "critical", "root")
        self.assertIn("Maior bloco: 4096 bytes", m.reserved_status())
        m.compact()
        self.assertEqual(m.lookup("c").base, 6144)
        m.create("grande", 6144, "critical", "root")
        self.assertEqual(m.lookup("grande").base, 8192)

    def test_global_lru_and_swap_round_trip(self):
        for medium, raid, block_size in [("hdd", 0, 4096), ("ssd", 5, 3000), ("ssd", 0, 8192)]:
            with self.subTest(medium=medium, raid=raid, block_size=block_size):
                m = simulator(medium=medium, raid=raid, block_size=block_size)
                fs = m.fs
                fs.create_text("arquivo", "teste")
                m.create("a", 2 * PAGE_SIZE, "normal", "ana")
                m.create("b", 10, "normal", "bia")
                a, b = m.lookup("a"), m.lookup("b")
                self.assertFalse(a.table)
                m.access(a, 0)
                m.access(b, 0, 9, 173)
                self.assertIn("[HIT]", m.access(a, 0))
                m.access(a, 1)
                self.assertEqual(b.on_disk, {0})
                if raid:
                    fs.storage.fail(0)
                output = m.access(b, 0, 9, show_byte=True)
                self.assertIn("swap (disco)", output)
                self.assertIn("Byte lido: 173", output)
                if raid:
                    fs.storage.rebuild(0)
                self.assertEqual(fs.read("arquivo"), (b"teste", 0))

    def test_second_chance_skips_page_referenced_since_previous_sweep(self):
        m = simulator(frames=3, policy="second-chance")
        m.create("app", 5 * PAGE_SIZE, "normal", "ana")
        p = m.lookup("app")
        for page in [0, 1, 2, 3, 1, 4]:
            m.access(p, page)
        self.assertEqual(p.on_disk, {0, 2})
        self.assertEqual(set(p.table), {1, 3, 4})

    def test_limits_accounting_and_slot_reuse(self):
        m = simulator(frames=1, slots=3)
        m.create("app", 10240, "normal", "root")
        p = m.lookup("app")
        with self.assertRaises(FileSystemError):
            m.create("outro", 1, "normal", "root")
        for page, offset in [(-1, 0), (3, 0), (0, -1), (0, 4096), (2, 2048)]:
            with self.assertRaises(FileSystemError):
                m.access(p, page, offset)
        self.assertEqual(m.clock, 0)
        self.assertIn("2048 bytes virtuais; 0 bytes na RAM", m.virtual_status())
        self.assertIn("Endereço físico: 18431", m.access(p, 2, 2047, 97))
        self.assertIn("2048 bytes virtuais; 2048 bytes na RAM", m.virtual_status())
        m.access(p, 0)
        m.terminate(p)
        self.assertEqual(m.frames, [None])
        self.assertEqual(len(set(m.free_slots)), 3)
        m.create("app", 3 * PAGE_SIZE, "normal", "root")
        for page in range(3):
            self.assertIn("Byte lido: 0", m.access(m.lookup("app"), page, 2047, show_byte=True))

    def test_failed_eviction_preserves_mappings_and_clock_hand(self):
        m = simulator(policy="second-chance")
        m.create("app", 3 * PAGE_SIZE, "normal", "root")
        p = m.lookup("app")
        m.access(p, 0)
        m.access(p, 1)
        m.fs.storage.fail(0)
        with self.assertRaises(FileSystemError):
            m.access(p, 2)
        self.assertEqual(p.table, {0: 0, 1: 1})
        self.assertEqual((m.hand, m.clock, m.faults, m.evictions), (0, 2, 2, 0))
        self.assertTrue(all(frame.referenced for frame in m.frames))

    def test_shell_uses_session_and_reports_only_completed_commands(self):
        fs = SecureFileSystem(password="teste", swap_blocks=4)
        m = Memory(fs, 3 * PAGE_SIZE, PAGE_SIZE, PAGE_SIZE, 4, "lru")
        shell = MemoryCommands(m, 2, security=True)
        fs.login("root", "teste")
        fs.useradd("ana", "admin", (), "ana")
        fs.useradd("bia", "admin", (), "bia")
        fs.login("ana", "ana")
        output = StringIO()
        with redirect_stdout(output):
            shell.execute("create_process --name app --type normal --size 10KB".split())
            with self.assertRaises(FileSystemError):
                shell.execute("memory_compact --reserved".split())
            fs.login("bia", "bia")
            with self.assertRaises(FileSystemError):
                shell.execute("terminate_process --name app".split())
            fs.login("ana", "ana")
            shell.execute(["execute_step", "--process", "app", "--instruction", "LOAD_DATA Página 2"])
        self.assertIn("operação 2", output.getvalue())
        self.assertIn("2048 bytes na RAM", output.getvalue())
