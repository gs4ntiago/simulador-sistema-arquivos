"""Modelo lógico e físico, independente da interface de terminal."""
from dataclasses import dataclass, field
from math import ceil


class FileSystemError(Exception):
    """Erro esperado de uma operação do sistema simulado."""


@dataclass(eq=False)
class Node:
    name: str
    parent: "Node | None"
    directory: bool = True
    children: dict = field(default_factory=dict)
    size: int = 0
    content: bytes = b""
    inode: int | None = None
    blocks: list[int] = field(default_factory=list)

    @property
    def path(self):
        return "/" if self.parent is None else self.parent.path.rstrip("/") + "/" + self.name


class FileSystem:
    def __init__(self, total_blocks=64, block_size=4096):
        if total_blocks < 1 or block_size < 1:
            raise ValueError("O disco deve ter ao menos um bloco e o tamanho deve ser positivo.")
        self.block_size = block_size
        self.disk = [None] * total_blocks
        self.root = Node("", None)
        self.cwd = self.root

    def resolve(self, path):
        if not path:
            raise FileSystemError("Caminho vazio.")
        node = self.root if path.startswith("/") else self.cwd
        for part in path.split("/"):
            if part in ("", "."):
                continue
            if not node.directory:
                raise FileSystemError(f"Não é diretório: {node.path}")
            if part == "..":
                node = node.parent or node
            elif part in node.children:
                node = node.children[part]
            else:
                raise FileSystemError(f"Caminho não encontrado: {path}")
        if path.endswith("/") and not node.directory:
            raise FileSystemError("O caminho exige um diretório.")
        return node

    def _destination(self, path):
        clean = path.rstrip("/")
        prefix, _, name = clean.rpartition("/")
        if not name or name in (".", ".."):
            raise FileSystemError("Nome inválido.")
        parent = self.resolve(prefix or ("/" if path.startswith("/") else "."))
        if not parent.directory:
            raise FileSystemError("O destino não é um diretório.")
        if name in parent.children:
            raise FileSystemError("O destino já existe.")
        return parent, name

    def mkdir(self, path):
        parent, name = self._destination(path)
        parent.children[name] = Node(name, parent)

    def rename(self, path, new_name):
        """Renomeia um diretório no mesmo pai, preservando seus descendentes."""
        node = self.resolve(path)
        if node is self.root:
            raise FileSystemError("Não é permitido renomear a raiz.")
        if not node.directory:
            raise FileSystemError("A operação exige um diretório.")
        if (not new_name or new_name in (".", "..")
                or "/" in new_name or "\x00" in new_name):
            raise FileSystemError("Informe apenas um nome válido, sem barras.")
        if new_name == node.name:
            return
        if new_name in node.parent.children:
            raise FileSystemError("O destino já existe.")
        del node.parent.children[node.name]
        node.name = new_name
        node.parent.children[new_name] = node

    def create(self, path, size=0):
        if size < 0:
            raise FileSystemError("Tamanho negativo.")
        parent, name = self._destination(path)
        node = Node(name, parent, directory=False)
        self._allocate(node, size)
        parent.children[name] = node

    def _allocate(self, node, size):
        # Um bloco de i-node guarda a tabela de índices (ponteiros de 4 bytes).
        needed = ceil(size / self.block_size)
        if needed > self.block_size // 4:
            raise FileSystemError("Arquivo excede a capacidade do i-node (ponteiros de 4 bytes).")
        available = [i for i, owner in enumerate(self.disk) if owner is None]
        extra = max(0, needed - len(node.blocks)) + (node.inode is None)
        if len(available) < extra:
            raise FileSystemError("Espaço insuficiente; operação cancelada sem alterações.")
        if node.inode is None:
            node.inode = available.pop(0)
            self.disk[node.inode] = (node, "I")
        while len(node.blocks) > needed:
            self.disk[node.blocks.pop()] = None
        while len(node.blocks) < needed:
            index = available.pop(0)
            node.blocks.append(index)
            self.disk[index] = (node, "D")
        node.size = size
        node.content = node.content[:size]

    def file(self, path):
        node = self.resolve(path)
        if node.directory:
            raise FileSystemError("A operação exige um arquivo.")
        return node

    def resize(self, path, size):
        if size < 0:
            raise FileSystemError("Tamanho negativo.")
        self._allocate(self.file(path), size)

    def write(self, path, text):
        data = text.encode("utf-8")
        node = self.file(path)
        self._allocate(node, len(data))
        node.content = data

    def read(self, path):
        node = self.file(path)
        # Bytes criados apenas com --size são zeros virtuais; não exigem RAM.
        return node.content, node.size - len(node.content)

    def remove(self, path, recursive=False):
        node = self.resolve(path)
        if node is self.root:
            raise FileSystemError("Não é permitido remover a raiz.")
        current = self.cwd
        while current:
            if current is node:
                raise FileSystemError("Não é permitido remover o diretório atual ou seus ancestrais.")
            current = current.parent
        if node.children and not recursive:
            raise FileSystemError("Diretório não vazio; use rm -r.")
        def release(item):
            for child in item.children.values():
                release(child)
            for block in ([item.inode] if item.inode is not None else []) + item.blocks:
                self.disk[block] = None
        release(node)
        del node.parent.children[node.name]

    def chdir(self, path):
        node = self.resolve(path)
        if not node.directory:
            raise FileSystemError("Não é um diretório.")
        self.cwd = node

    def tree(self, path="/"):
        root = self.resolve(path)
        lines = [root.path]
        def visit(node, prefix):
            children = sorted(node.children.values(), key=lambda n: (not n.directory, n.name))
            for index, child in enumerate(children):
                last = index == len(children) - 1
                label = child.name + ("/" if child.directory else f" ({child.size} bytes)")
                lines.append(prefix + ("└── " if last else "├── ") + label)
                visit(child, prefix + ("    " if last else "│   "))
        visit(root, "")
        return "\n".join(lines)

    def status(self, details=False):
        files = []
        def collect(node):
            if not node.directory:
                files.append(node)
            for child in node.children.values():
                collect(child)
        collect(self.root)
        real = sum(n.size for n in files)
        data = sum(len(n.blocks) * self.block_size for n in files)
        metadata = len(files) * self.block_size
        runs, run = [], 0
        for owner in self.disk + [True]:
            if owner is None:
                run += 1
            elif run:
                runs.append(run)
                run = 0
        free = sum(runs)
        largest = max(runs, default=0)
        lines = ["Árvore do sistema:", self.tree(), "", f"Tamanho do Bloco: {self.block_size} bytes",
                 f"Total de Blocos: {len(self.disk)}", f"Blocos livres: {free}",
                 "Mapa de Alocação de Disco (I = i-node reservado; D = dados; LIVRE = disponível):"]
        for i, owner in enumerate(self.disk):
            lines.append(f"[{i:03d}] " + (f"{owner[1]} {owner[0].path}" if owner else "LIVRE"))
        lines += ["", "Relatório de Fragmentação:", f"Tamanho real: {real} bytes",
                  f"Dados alocados: {data} bytes", f"I-nodes reservados: {metadata} bytes",
                  f"Total alocado (dados + i-nodes): {data + metadata} bytes",
                  f"Fragmentação interna dos dados: {data - real} bytes",
                  "Fragmentação externa impeditiva: 0 bytes (alocação indexada aceita blocos dispersos)",
                  f"Extensões livres: {len(runs)}; maior extensão: {largest} blocos",
                  f"Espaço livre fora da maior extensão: {(free - largest) * self.block_size} bytes",
                  "Necessidade de compactação: não se aplica à alocação indexada."]
        if details:
            lines.append("\nDetalhes por arquivo (desperdício no último bloco):")
            for n in files:
                lines.append(f"{n.path}: real={n.size} bytes; alocado={len(n.blocks)*self.block_size} bytes; "
                             f"interna={len(n.blocks)*self.block_size-n.size} bytes; "
                             f"i-node={n.inode}; dados={n.blocks}")
        return "\n".join(lines)
