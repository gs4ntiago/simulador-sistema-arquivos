import unittest
from filesystem import FileSystem, FileSystemError
from filesystem.cli import Shell, parse_size
from contextlib import redirect_stdout
from io import StringIO


class FileSystemTests(unittest.TestCase):
    def test_rename_preserves_descendants_current_directory_and_disk(self):
        fs = FileSystem()
        fs.mkdir('/sistema')
        fs.mkdir('/sistema/logs')
        fs.create('/sistema/logs/app.log', 0)
        fs.write('/sistema/logs/app.log', 'conteúdo')
        fs.chdir('/sistema/logs')
        before = list(fs.disk)
        fs.rename('..', 'novo sistema')
        self.assertEqual(fs.cwd.path, '/novo sistema/logs')
        self.assertEqual(fs.read('app.log'), ('conteúdo'.encode(), 0))
        self.assertEqual(fs.disk, before)
        self.assertIn('/novo sistema/logs/app.log', fs.status(True))
        with self.assertRaises(FileSystemError):
            fs.resolve('/sistema')
        with redirect_stdout(StringIO()):
            Shell(fs).execute('rename . registros')
        self.assertEqual(fs.cwd.path, '/novo sistema/registros')
        fs.rename('.', 'registros')
        self.assertEqual(fs.disk, before)

    def test_rename_rejects_invalid_destinations_without_changes(self):
        fs = FileSystem()
        fs.mkdir('/a')
        fs.mkdir('/b')
        fs.create('/arquivo', 0)
        before = fs.tree()
        for path, name in (('/', 'raiz'), ('/arquivo', 'novo'),
                           ('/ausente', 'novo'), ('/a', 'b'), ('/a', 'arquivo'),
                           ('/a', ''), ('/a', '.'), ('/a', '..'),
                           ('/a', '/novo'), ('/a', 'b/novo'), ('/a', '\x00')):
            with self.subTest(path=path, name=name):
                with self.assertRaises(FileSystemError):
                    fs.rename(path, name)
                self.assertEqual(fs.tree(), before)

    def test_required_example_and_accounting(self):
        fs = FileSystem()
        fs.mkdir('/sistema')
        fs.mkdir('/sistema/logs')
        fs.create('/sistema/logs/app.log', 10240)
        node = fs.file('/sistema/logs/app.log')
        self.assertEqual(node.blocks, [1, 2, 3])
        self.assertEqual(node.inode, 0)
        status = fs.status(True)
        for expected in ('app.log (10240 bytes)', 'Dados alocados: 12288 bytes',
                         'Fragmentação interna dos dados: 2048 bytes',
                         'Total alocado (dados + i-nodes): 16384 bytes'):
            self.assertIn(expected, status)

    def test_scattered_blocks_are_usable(self):
        fs = FileSystem(8)
        for name in ('a', 'b', 'c', 'd'):
            fs.create(name, 4096)
        fs.remove('a')
        fs.remove('c')
        fs.create('large', 3 * 4096)
        self.assertEqual(fs.file('large').blocks, [1, 4, 5])

    def test_out_of_space_is_atomic(self):
        fs = FileSystem(4)
        fs.create('a', 4096)
        fs.write('a', 'abc')
        before = list(fs.disk)
        with self.assertRaises(FileSystemError):
            fs.resize('a', 4 * 4096)
        self.assertEqual(fs.disk, before)
        self.assertEqual(fs.read('a'), (b'abc', 0))
        with self.assertRaises(FileSystemError):
            fs.create('b', 4 * 4096)
        self.assertNotIn('b', fs.root.children)
        self.assertEqual(fs.disk, before)

    def test_paths_update_and_recursive_delete(self):
        fs = FileSystem()
        fs.mkdir('/a')
        fs.mkdir('/a/b')
        fs.chdir('/a/b')
        fs.create('../texto', 0)
        fs.write('../texto', 'olá')
        self.assertEqual(fs.read('/a/./texto'), ('olá'.encode(), 0))
        fs.resize('../texto', 2)
        self.assertEqual(fs.read('../texto'), (b'ol', 0))
        fs.resize('../texto', 8)
        self.assertEqual(fs.read('../texto'), (b'ol', 6))
        with self.assertRaises(FileSystemError):
            fs.remove('/a', True)
        fs.chdir('/')
        with self.assertRaises(FileSystemError):
            fs.remove('/a')
        fs.remove('/a', True)
        self.assertTrue(all(block is None for block in fs.disk))

    def test_invalid_paths_and_index_capacity(self):
        fs = FileSystem(16, 8)
        fs.create('a', 16)
        for path in ('a/..', 'a/', 'missing'):
            with self.assertRaises(FileSystemError):
                fs.resolve(path)
        with self.assertRaises(FileSystemError):
            fs.resize('a', 17)
        with self.assertRaises(FileSystemError):
            fs.remove('/')

    def test_shell_commands_and_invalid_input(self):
        shell = Shell(FileSystem())
        with redirect_stdout(StringIO()):
            for command in ('mkdir /logs', 'cd /logs', 'create "app log" --size 10KB',
                            'write "app log" "Olá mundo"', 'read "app log"',
                            'update "app log" --size 0', 'fs_status --details', 'rm "app log"'):
                self.assertTrue(shell.execute(command))
        self.assertFalse(shell.execute('exit'))
        for command in ('create a --size -1', 'fs_status --wrong', 'mkdir'):
            with self.assertRaises(FileSystemError):
                shell.execute(command)
        self.assertEqual(parse_size('10KB'), 10240)


if __name__ == '__main__':
    unittest.main()
