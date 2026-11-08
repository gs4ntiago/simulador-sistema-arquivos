"""Etapa 2: fila física, HDD/SSD e RAID 5 para o sistema de arquivos."""
from dataclasses import dataclass

from .core import FileSystem, FileSystemError


@dataclass(frozen=True)
class Request:
    disk: int
    block: int
    operation: str
    role: str
    data: bytes = b""


def schedule(tracks, head=53, algorithm="sstf"):
    """Ordena índices de requisições, preservando a ordem em caso de empate."""
    pending = list(range(len(tracks)))
    order = []
    while pending:
        index = pending[0]
        if algorithm == "sstf":
            index = min(pending, key=lambda i: abs(tracks[i] - head))
        pending.remove(index)
        order.append(index)
        head = tracks[index]
    return order


def movement(head, tracks, algorithm):
    route = [head] + [tracks[i] for i in schedule(tracks, head, algorithm)]
    distances = [abs(b - a) for a, b in zip(route, route[1:])]
    formula = " + ".join(f"|{a}-{b}|" for a, b in zip(route, route[1:]))
    terms = " + ".join(map(str, distances))
    return (
        f"Ordem de atendimento: {' -> '.join(map(str, route))}\n"
        f"Deslocamento: {formula or '0'}\n"
        f"Total de movimento: {terms + ' = ' if terms else ''}{sum(distances)} trilhas"
    )


class Drive:
    def __init__(self, capacity, block_size, medium, head, tracks, algorithm):
        self.medium = medium
        self.head = head
        self.tracks = tracks
        self.algorithm = algorithm
        self.zero = bytes(block_size)
        self.data = {}
        self.pages = {}
        # Uma página extra permite escrever fora do lugar antes de liberar a antiga.
        self.wear = [0] * (capacity + 1)
        self.free_pages = set(range(capacity + 1))

    def service(self, requests):
        if self.medium == "hdd":
            # Um setor simulado pertence à trilha dada por seu resto.
            tracks = [req.block % self.tracks for req in requests]
            order = schedule(tracks, self.head, self.algorithm)
            report = [f"Fila de trilhas: {tracks} | Cabeça: {self.head}",
                      movement(self.head, tracks, self.algorithm)]
            self.head = tracks[order[-1]]
        else:
            order = range(len(requests))
            report = ["SSD: fila FIFO de páginas, sem movimento de cabeça."]
        results = {}
        for i in order:
            req = requests[i]
            label = f"{req.operation} bloco {req.block:02d} [{req.role}]"
            if self.medium == "ssd":
                page = self.pages.get(req.block)
                if req.operation == "ESCRITA":
                    new_page = min(self.free_pages, key=lambda p: (self.wear[p], p))
                    self.free_pages.remove(new_page)
                    self.wear[new_page] += 1
                    self.pages[req.block] = new_page
                    if page is not None:
                        self.free_pages.add(page)
                        del self.data[page]
                    page = new_page
                    label += f" -> página {page}, ciclos de gravação: {self.wear[page]}"
                elif page is not None:
                    label += f" -> página {page}"
                else:
                    label += " -> bloco ainda não gravado (zeros)"
                address = page
            else:
                address = req.block
                label += f" -> trilha {tracks[i]}"
            if req.operation == "ESCRITA":
                self.data[address] = req.data
                label += f" ({len(req.data)} bytes)"
            else:
                results[i] = self.data.get(address, self.zero)
            report.append(label)
        return results, report


def xor_blocks(blocks, size):
    value = 0
    for block in blocks:
        value ^= int.from_bytes(block, "big")
    return value.to_bytes(size, "big")


class Storage:
    """Mapeia blocos lógicos em discos; executa lotes físicos por fase."""
    def __init__(self, blocks, block_size, medium, raid, disk_count, head, tracks, algorithm):
        self.raid = raid
        self.medium = medium
        self.block_size = block_size
        self.zero = bytes(block_size)
        self.failed = set()
        self.report = []
        self.used_stripes = set()
        count = disk_count if raid == 5 else 1
        width = count - 1 if raid == 5 else 1
        self.capacity = (blocks + width - 1) // width
        self.drives = [
            Drive(self.capacity, block_size, medium, head, tracks, algorithm)
            for _ in range(count)
        ]

    def layout(self, block):
        if self.raid != 5:
            return 0, block, None
        count = len(self.drives)
        stripe, offset = divmod(block, count - 1)
        parity = count - 1 - stripe % count
        data_disks = [i for i in range(count) if i != parity]
        return data_disks[offset], stripe, parity

    def check(self):
        if len(self.failed) > (1 if self.raid == 5 else 0):
            raise FileSystemError("Armazenamento indisponível: falhas acima da tolerância.")

    def dispatch(self, requests):
        results = {}
        for disk, drive in enumerate(self.drives):
            group = [req for req in requests if req.disk == disk]
            if not group:
                continue
            values, lines = drive.service(group)
            results.update({group[i]: data for i, data in values.items()})
            mode = drive.algorithm.upper() if self.medium == "hdd" else "SSD"
            self.report.extend([f"[E/S] Disco {disk + 1} — {mode}", *lines])
        return results

    def read(self, blocks):
        self.check()
        direct = {}
        recovery = {}
        for block in blocks:
            disk, stripe, _ = self.layout(block)
            if disk not in self.failed:
                direct[block] = Request(disk, stripe, "LEITURA", f"lógico {block}")
            else:
                recovery[block] = [
                    Request(other, stripe, "LEITURA", "reconstrução XOR")
                    for other in range(len(self.drives)) if other != disk
                ]
        values = self.dispatch(list(direct.values()) + list(dict.fromkeys(
            req for group in recovery.values() for req in group
        )))
        result = {block: values[req] for block, req in direct.items()}
        for block, group in recovery.items():
            result[block] = xor_blocks((values[req] for req in group), self.block_size)
            self.report.append(f"Reconstrução XOR do bloco lógico {block}: OK")
        return result

    def write(self, blocks):
        self.check()
        requests = []
        stripes = {}
        for block, data in blocks.items():
            disk, stripe, parity = self.layout(block)
            if disk not in self.failed:
                requests.append(Request(disk, stripe, "ESCRITA", f"lógico {block}", data))
            if parity is not None:
                stripes.setdefault(stripe, {})[disk] = data
        # Ler os membros que não mudam antes de gravar a nova paridade.
        count = len(self.drives)
        width = count - 1
        unchanged = [
            stripe * width + offset
            for stripe, updates in stripes.items()
            if count - 1 - stripe % count not in self.failed
            for offset in range(width)
            if self.layout(stripe * width + offset)[0] not in updates
        ]
        old = self.read(unchanged) if unchanged else {}
        for stripe, updates in stripes.items():
            parity = count - 1 - stripe % count
            if parity not in self.failed:
                members = list(updates.values()) + [
                    old[block] for block in range(stripe * width, (stripe + 1) * width)
                    if block in old
                ]
                requests.append(Request(parity, stripe, "ESCRITA", "PARIDADE XOR",
                                        xor_blocks(members, self.block_size)))
            self.used_stripes.add(stripe)
        self.dispatch(requests)

    def fail(self, disk):
        self._validate_disk(disk)
        if disk in self.failed:
            raise FileSystemError("O disco já está em falha.")
        self.failed.add(disk)
        self.report.append(f"Falha forçada no disco {disk + 1}.")

    def rebuild(self, disk):
        self._validate_disk(disk)
        if self.raid != 5 or self.failed != {disk}:
            raise FileSystemError("A reconstrução RAID 5 exige exatamente um disco em falha.")
        requests = [
            Request(other, stripe, "LEITURA", "reconstrução XOR")
            for stripe in sorted(self.used_stripes)
            for other in range(len(self.drives)) if other != disk
        ]
        values = self.dispatch(requests)
        writes = [
            Request(disk, stripe, "ESCRITA", "RECONSTRUÇÃO",
                    xor_blocks((values[req] for req in requests if req.block == stripe),
                               self.block_size))
            for stripe in sorted(self.used_stripes)
        ]
        self.dispatch(writes)
        self.failed.remove(disk)
        self.report.append(f"Reconstrução do disco {disk + 1}: OK ({len(writes)} faixas).")

    def _validate_disk(self, disk):
        if not 0 <= disk < len(self.drives):
            raise FileSystemError("Número de disco inválido.")

    def status(self):
        arrangement = "RAID 5" if self.raid == 5 else "disco único"
        lines = [f"Arranjo ativo: {arrangement} | {len(self.drives)} disco(s) "
                 f"{self.medium.upper()} | Blocos de {self.block_size} bytes"]
        for i, drive in enumerate(self.drives):
            state = "em falha" if i in self.failed else "operacional"
            detail = (f"cabeça na trilha {drive.head}" if self.medium == "hdd" else
                      f"{len(drive.pages)} páginas mapeadas; desgaste por página: {drive.wear}")
            lines.append(f"Disco {i + 1}: {state}; {detail}")
        return "\n".join(lines)


class IOFileSystem(FileSystem):
    """Sistema de arquivos da Etapa 1 com leitura e gravação físicas."""
    def __init__(self, total_blocks=64, block_size=4096, *, medium="hdd", raid=0,
                 disk_count=3, head=53, tracks=200, algorithm="sstf"):
        super().__init__(total_blocks, block_size)
        if not 0 <= head < tracks or tracks < 1:
            raise ValueError("A cabeça inicial deve estar dentro do intervalo de trilhas.")
        if raid == 5 and disk_count < 3:
            raise ValueError("RAID 5 precisa de ao menos três discos.")
        self.storage = Storage(total_blocks, block_size, medium, raid, disk_count,
                               head, tracks, algorithm)

    def _store(self, node):
        size = self.block_size
        # A tabela de índices ocupa o bloco reservado ao i-node.
        inode = b"".join(block.to_bytes(4, "little") for block in node.blocks)
        blocks = {node.inode: inode.ljust(size, b"\0")}
        for i, block in enumerate(node.blocks):
            blocks[block] = node.content[i * size:(i + 1) * size].ljust(size, b"\0")
        self.storage.write(blocks)

    def _create_file(self, path, size, content=b""):
        if path.endswith("/"):
            raise FileSystemError("Informe um caminho de arquivo sem barra final.")
        self.storage.check()
        super().create(path, size)
        node = self.file(path)
        node.content = content
        self._store(node)

    def create(self, path, size=0):
        self._create_file(path, size)

    def create_text(self, path, text):
        content = text.encode("utf-8")
        self._create_file(path, len(content), content)

    def resize(self, path, size):
        self.storage.check()
        super().resize(path, size)
        self._store(self.file(path))

    def write(self, path, text):
        self.storage.check()
        super().write(path, text)
        self._store(self.file(path))

    def read(self, path):
        node = self.file(path)
        # O i-node precisa ser lido primeiro para localizar os blocos de dados.
        inode = self.storage.read([node.inode])[node.inode]
        blocks = [int.from_bytes(inode[i:i + 4], "little")
                  for i in range(0, 4 * len(node.blocks), 4)]
        data = self.storage.read(blocks)
        content_length = len(node.content)
        return b"".join(data[block] for block in blocks)[:content_length], node.size - content_length


def main():
    """Executa o terminal da Etapa 2 com os comandos da Etapa 1."""
    import argparse
    import shlex
    import sys

    from .cli import Parser, Shell, parse_size

    parser = argparse.ArgumentParser(description="Etapa 2: E/S de disco, SSD e RAID 5")
    parser.add_argument("--blocks", type=int, default=64, help="Blocos lógicos do sistema")
    parser.add_argument("--block-size", type=parse_size, default=4096)
    parser.add_argument("--medium", choices=("hdd", "ssd"), default="hdd")
    parser.add_argument("--scheduler", choices=("sstf", "fcfs"), default="sstf")
    parser.add_argument("--tracks", type=int, default=200)
    parser.add_argument("--head", type=int, default=53)
    parser.add_argument("--raid", type=int, choices=(0, 5), default=0,
                        help="0 = sem RAID (disco único); 5 = RAID 5")
    parser.add_argument("--disks", type=int, default=3, help="Discos físicos no RAID 5")
    parser.add_argument("--script", help="Arquivo UTF-8 com comandos")
    args = parser.parse_args()
    try:
        fs = IOFileSystem(args.blocks, args.block_size, medium=args.medium, raid=args.raid,
                          disk_count=args.disks, head=args.head, tracks=args.tracks,
                          algorithm=args.scheduler)
    except ValueError as error:
        parser.error(str(error))

    shell = Shell(fs)
    extra = Parser(prog="E/S", add_help=False)
    subs = extra.add_subparsers(dest="command", required=True, parser_class=Parser)
    write_file = subs.add_parser("write_file", add_help=False)
    write_file.add_argument("path")
    write_file.add_argument("text", nargs="?")
    write_file.add_argument("--size", type=parse_size)
    for name in ("fail_disk", "rebuild_disk"):
        subs.add_parser(name, add_help=False).add_argument("disk", type=int)
    subs.add_parser("io_status", add_help=False)
    demo = subs.add_parser("io_demo", add_help=False)
    demo.add_argument("tracks", type=int, nargs="*")
    demo.add_argument("--head", type=int, default=args.head)
    demo.add_argument("--scheduler", choices=("sstf", "fcfs"), default=args.scheduler)

    def execute(line):
        words = shlex.split(line, comments=True)
        if not words:
            return True
        command = words[0]
        try:
            if command == "read_file":
                return shell.execute(shlex.join(["read", *words[1:]]))
            if command not in {"write_file", "fail_disk", "rebuild_disk", "io_status", "io_demo"}:
                result = shell.execute(line)
                if command == "help":
                    print(
                        "\nComandos de E/S\n"
                        '  write_file <caminho> "<texto>" | --size <tamanho>\n'
                        "  read_file <caminho>\n"
                        "  io_status\n"
                        "  io_demo [trilhas...] [--head <trilha>] [--scheduler sstf|fcfs]\n"
                        "  fail_disk <número> | rebuild_disk <número>"
                    )
                return result

            options = extra.parse_args(words)
            if command == "write_file":
                if (options.text is None) == (options.size is None):
                    raise FileSystemError("Informe texto ou --size.")
                if options.path.endswith("/"):
                    raise FileSystemError("Informe um caminho de arquivo sem barra final.")
                parent, _, name = options.path.rstrip("/").rpartition("/")
                directory = fs.resolve(parent or ("/" if options.path.startswith("/") else "."))
                if name in directory.children:
                    if options.size is not None:
                        fs.resize(options.path, options.size)
                    else:
                        fs.write(options.path, options.text)
                else:
                    if options.size is not None:
                        fs.create(options.path, options.size)
                    else:
                        # Cria e grava em um único lote físico.
                        fs.create_text(options.path, options.text)
                print(f"Arquivo gravado: {options.path} ({fs.file(options.path).size} bytes).")
            elif command == "fail_disk":
                fs.storage.fail(options.disk - 1)
            elif command == "rebuild_disk":
                fs.storage.rebuild(options.disk - 1)
            elif command == "io_status":
                print(fs.storage.status())
            elif command == "io_demo":
                tracks = options.tracks or [98, 183, 37, 122, 14]
                if any(not 0 <= track < args.tracks for track in [options.head, *tracks]):
                    raise FileSystemError("Trilha fora do intervalo do disco.")
                print(f"[E/S] Fila: {tracks} | Cabeça: {options.head}")
                print(f"Escalonamento: {options.scheduler.upper()}")
                print(movement(options.head, tracks, options.scheduler))
            return True
        finally:
            if fs.storage.report:
                print("\n".join(fs.storage.report))
                fs.storage.report.clear()

    print(fs.storage.status())
    if args.script:
        try:
            with open(args.script, encoding="utf-8") as source:
                for number, line in enumerate(source, 1):
                    try:
                        if not execute(line):
                            break
                    except (FileSystemError, ValueError) as error:
                        print(f"Erro na linha {number}: {error}", file=sys.stderr)
                        return 1
        except OSError as error:
            print(f"Erro ao abrir script: {error}", file=sys.stderr)
            return 1
        return 0

    print("Simulador de E/S — digite help para ajuda.")
    while True:
        try:
            if not execute(input(f"{fs.cwd.path}> ")):
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrado.")
            return 0
        except (FileSystemError, ValueError) as error:
            print(f"Erro: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
