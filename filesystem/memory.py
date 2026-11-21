"""Etapa 4: RAM contígua, paginação por demanda e swap no disco da Etapa 2."""
from dataclasses import dataclass, field

from .core import FileSystemError


PAGE_SIZE = 4096
KERNEL = "Núcleo Base do SO"


@dataclass(eq=False)
class Process:
    pid: int
    name: str
    size: int
    owner: str
    base: int | None = None
    # Cada página lógica tem um slot garantido no disco desde a criação.
    slots: list[int] = field(default_factory=list)
    table: dict[int, int] = field(default_factory=dict)
    on_disk: set[int] = field(default_factory=set)


@dataclass
class Frame:
    process: Process
    page: int
    data: bytearray
    last_used: int
    referenced: bool = True


class Memory:
    def __init__(self, fs, ram_size, reserved_size, kernel_size, swap_pages, policy):
        if not 0 < kernel_size <= reserved_size < ram_size:
            raise ValueError("Verifique os tamanhos: 0 < núcleo <= reserva < RAM.")
        if reserved_size % PAGE_SIZE or ram_size % PAGE_SIZE:
            raise ValueError("A reserva e a RAM devem ser múltiplos de 4 KB.")
        if swap_pages < 1 or policy not in ("lru", "second-chance"):
            raise ValueError("Configuração de swap ou de substituição inválida.")
        self.fs = fs
        self.reserved_size = reserved_size
        self.kernel_size = kernel_size
        self.frames = [None] * ((ram_size - reserved_size) // PAGE_SIZE)
        self.swap_pages = swap_pages
        self.free_slots = list(reversed(range(swap_pages)))
        self.policy = policy
        self.processes = {}
        self.next_pid = 1
        self.clock = self.hand = self.faults = self.evictions = 0

    def reserved_map(self):
        """Intervalos [início, fim), em bytes; None representa uma lacuna."""
        yield 0, self.kernel_size, KERNEL
        end = self.kernel_size
        critical = (p for p in self.processes.values() if p.base is not None)
        for process in sorted(critical, key=lambda p: p.base):
            if end < process.base:
                yield end, process.base, None
            end = process.base + process.size
            yield process.base, end, process.name
        if end < self.reserved_size:
            yield end, self.reserved_size, None

    def create(self, name, size, kind, owner):
        if not name.strip() or name == KERNEL or name in self.processes:
            raise FileSystemError("Nome de processo vazio, reservado ou já utilizado.")
        if size <= 0:
            raise FileSystemError("O processo deve ocupar pelo menos um byte.")
        p = Process(self.next_pid, name, size, owner)
        if kind == "critical":
            holes = [(a, b) for a, b, label in self.reserved_map() if label is None]
            p.base = next((a for a, b in holes if b - a >= size), None)
            if p.base is None:
                free = sum(b - a for a, b in holes)
                reason = ("Fragmentação externa; use memory_compact --reserved."
                          if free >= size else "Espaço livre insuficiente na reserva.")
                raise FileSystemError(reason)
            info = f"Área reservada: [{p.base}, {p.base + size}) bytes contíguos."
        elif kind == "normal":
            pages = (size + PAGE_SIZE - 1) // PAGE_SIZE
            if pages > len(self.free_slots):
                raise FileSystemError("Sem espaço de swap para garantir as páginas do processo.")
            p.slots = [self.free_slots.pop() for _ in range(pages)]
            waste = pages * PAGE_SIZE - size
            used = PAGE_SIZE - waste
            full = (f"Páginas anteriores: {PAGE_SIZE} bytes utilizados por página (100%).\n"
                    if pages > 1 else "")
            info = (f"{size} bytes em {pages} página(s) de {PAGE_SIZE} bytes.\n"
                    f"{full}Última página ({pages - 1}): {used} bytes utilizados ({used / PAGE_SIZE:.1%}).\n"
                    f"Fragmentação interna: {waste} bytes. Molduras residentes: 0 (sob demanda).")
        else:
            raise FileSystemError("Tipo de processo inválido.")
        self.processes[name] = p
        self.next_pid += 1
        return f"Processo {name!r} criado (PID {p.pid}). {info}"

    def lookup(self, name):
        if name not in self.processes:
            raise FileSystemError(f"Processo não encontrado: {name!r}.")
        return self.processes[name]

    def terminate(self, p):
        if p.base is not None:
            info = f"Área reservada liberada: [{p.base}, {p.base + p.size}) bytes."
        else:
            for index in p.table.values():
                self.frames[index] = None
            self.free_slots.extend(p.slots)
            info = f"Liberadas {len(p.table)} moldura(s) e {len(p.slots)} slot(s) de swap."
        del self.processes[p.name]
        return f"Processo {p.name!r} encerrado. {info}"

    def compact(self):
        lines = ["Compactação da área reservada"]
        base = self.kernel_size
        critical = (p for p in self.processes.values() if p.base is not None)
        for p in sorted(critical, key=lambda p: p.base):
            if p.base != base:
                lines.append(f"{p.name}: [{p.base}, {p.base + p.size}) -> [{base}, {base + p.size})")
                p.base = base
            base += p.size
        return "\n".join([*lines, self.reserved_status()])

    def reserved_status(self, show_map=True):
        layout = list(self.reserved_map())
        holes = [b - a for a, b, label in layout if label is None]
        free = sum(holes)
        largest = max(holes, default=0)
        lines = [f"RAM reservada: {self.reserved_size - free}/{self.reserved_size} bytes ocupados"]
        if show_map:
            lines.extend(f"[{a}, {b}) {label or 'LIVRE'} ({b - a} bytes)" for a, b, label in layout)
        lines.extend([f"Livre: {free} bytes em {len(holes)} lacuna(s). Maior bloco: {largest} bytes.",
                      f"Fragmentação externa: {'SIM' if len(holes) > 1 else 'NÃO'} "
                      f"({free - largest} bytes livres fora do maior bloco)."])
        return "\n".join(lines)

    def virtual_status(self):
        normal = [p for p in self.processes.values() if p.base is None]
        used = sum(len(p.table) for p in normal)
        pages = sum(len(p.slots) for p in normal)
        real = sum(p.size for p in normal)
        swapped = sum(len(p.on_disk) for p in normal)
        resident_waste = sum(len(p.slots) * PAGE_SIZE - p.size for p in normal
                             if len(p.slots) - 1 in p.table)
        return (f"RAM dinâmica: {used}/{len(self.frames)} molduras "
                f"({used * PAGE_SIZE}/{len(self.frames) * PAGE_SIZE} bytes).\n"
                f"Memória virtual: {real} bytes solicitados; {pages * PAGE_SIZE} bytes em páginas.\n"
                f"Fragmentação interna: {pages * PAGE_SIZE - real} bytes virtuais; "
                f"{resident_waste} bytes na RAM.\n"
                f"Swap: {swapped} página(s) em disco; {pages}/{self.swap_pages} slots comprometidos.\n"
                f"Política: {self.policy}. Acessos: {self.clock}. "
                f"Page faults: {self.faults}. Substituições: {self.evictions}.")

    def _swap_blocks(self, slot):
        block_size = self.fs.block_size
        count = (PAGE_SIZE + block_size - 1) // block_size
        start = len(self.fs.disk) + slot * count
        return range(start, start + count)

    def _victim(self):
        if self.policy == "lru":
            return min(range(len(self.frames)), key=lambda i: self.frames[i].last_used)
        while self.frames[self.hand].referenced:
            self.frames[self.hand].referenced = False
            self.hand = (self.hand + 1) % len(self.frames)
        index = self.hand
        self.hand = (index + 1) % len(self.frames)
        return index

    def _load(self, p, page, lines):
        storage = self.fs.storage
        if page in p.on_disk:
            blocks = self._swap_blocks(p.slots[page])
            data = storage.read(blocks)
            content = bytearray(b"".join(data[b] for b in blocks)[:PAGE_SIZE])
            source = "swap (disco)"
        else:
            content = bytearray(PAGE_SIZE)
            source = "página inicial zerada"
        index = next((i for i, frame in enumerate(self.frames) if frame is None), None)
        if index is None:
            storage.check()  # Valida a E/S antes de avançar o ponteiro da segunda chance.
            index = self._victim()
            old = self.frames[index]
            blocks = self._swap_blocks(old.process.slots[old.page])
            size = self.fs.block_size
            storage.write({b: bytes(old.data[i*size:(i+1)*size]).ljust(size, b"\0")
                           for i, b in enumerate(blocks)})
            old.process.on_disk.add(old.page)
            del old.process.table[old.page]
            reason = (f"último acesso no passo {old.last_used}" if self.policy == "lru"
                      else "bit de referência 0")
            lines.append(f"{self.policy.upper()}: PID {old.process.pid}, página {old.page} "
                         f"({reason}) -> swap.")
            self.evictions += 1
        p.on_disk.discard(page)
        p.table[page] = index
        self.frames[index] = Frame(p, page, content, self.clock + 1)
        self.faults += 1
        lines.append(f"Carregada de {source}.")
        return index

    def access(self, p, page, offset=0, value=None, show_byte=False):
        if p.base is not None:
            raise FileSystemError("Processos críticos usam endereçamento contíguo, sem paginação.")
        if not (0 <= page < len(p.slots) and 0 <= offset < PAGE_SIZE
                and page * PAGE_SIZE + offset < p.size):
            raise FileSystemError("Página ou deslocamento fora dos limites do processo.")
        if value is not None and not 0 <= value <= 255:
            raise FileSystemError("O valor de um byte deve estar entre 0 e 255.")
        index = p.table.get(page)
        if index is None:
            lines = [f"[PAGE FAULT] Página {page} ausente na RAM."]
            index = self._load(p, page, lines)
        else:
            lines = [f"[HIT] Página {page} residente."]
        self.clock += 1
        frame = self.frames[index]
        frame.last_used = self.clock
        frame.referenced = True
        number = self.reserved_size // PAGE_SIZE + index
        lines.append(f"PID {p.pid}, página {page} -> moldura física {number}. "
                     f"Endereço físico: {number * PAGE_SIZE + offset} (deslocamento {offset}).")
        if value is not None:
            frame.data[offset] = value
        if show_byte:
            lines.append(f"Byte {'gravado' if value is not None else 'lido'}: {frame.data[offset]}.")
        return "\n".join(lines)
