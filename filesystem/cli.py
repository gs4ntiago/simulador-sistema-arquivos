"""Interface interativa e execução de scripts de comandos."""
import argparse
import re
import shlex
import sys
from .core import FileSystem, FileSystemError


HELP = """Comandos (caminhos absolutos ou relativos; aspas permitem espaços):
  mkdir <caminho>                  Cria um diretório; pai deve existir
  rename <caminho> <novo_nome>     Renomeia um diretório no mesmo pai
  create <caminho> --size <valor>   Cria arquivo (ex.: 10KB, 512B, 1MiB)
  read <caminho>                   Lê texto e informa zeros virtuais
  write <caminho> <texto>          Substitui conteúdo e ajusta tamanho UTF-8
  update <caminho> --size <valor>  Redimensiona (alias: truncate)
  rm [-r] <caminho>                Remove arquivo ou diretório (alias: delete)
  cd <caminho> | pwd               Navega ou mostra diretório atual
  ls [caminho] | tree [caminho]     Lista ou mostra árvore
  fs_status [--details]            Diagnóstico lógico e físico completo
  help | exit | quit
Unidades KB/MB/GB e KiB/MiB/GiB usam base 1024. Sem unidade: bytes.
O disco é simulado em memória; os dados não persistem após sair."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise FileSystemError(message)


def parse_size(value):
    match = re.fullmatch(r"(\d+)\s*(B|KB|MB|GB|KIB|MIB|GIB)?", value.upper())
    if not match:
        raise argparse.ArgumentTypeError("Use um inteiro não negativo com B, KB, MB, GB, KiB, MiB ou GiB.")
    unit = match[2] or "B"
    exponent = {"B": 0, "KB": 1, "KIB": 1, "MB": 2, "MIB": 2, "GB": 3, "GIB": 3}[unit]
    return int(match[1]) * 1024 ** exponent


def command_parser():
    parser = Parser(prog="comando", add_help=False)
    subs = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in ("mkdir", "read", "cd"):
        subs.add_parser(name, add_help=False).add_argument("path")
    child = subs.add_parser("rename", add_help=False)
    child.add_argument("path")
    child.add_argument("new_name")
    for name in ("create", "update", "truncate"):
        child = subs.add_parser(name, add_help=False)
        child.add_argument("path")
        child.add_argument("--size", required=True, type=parse_size)
    child = subs.add_parser("write", add_help=False)
    child.add_argument("path")
    child.add_argument("text", nargs="+")
    for name in ("rm", "delete"):
        child = subs.add_parser(name, add_help=False)
        child.add_argument("-r", "--recursive", action="store_true")
        child.add_argument("path")
    for name in ("ls", "tree"):
        subs.add_parser(name, add_help=False).add_argument("path", nargs="?", default=".")
    subs.add_parser("fs_status", add_help=False).add_argument("--details", action="store_true")
    for name in ("help", "pwd", "exit", "quit"):
        subs.add_parser(name, add_help=False)
    return parser


class Shell:
    def __init__(self, fs):
        self.fs = fs
        self.parser = command_parser()

    def execute(self, line):
        """Retorna False para sair; erros são tratados pelo chamador."""
        words = shlex.split(line, comments=True)
        if not words:
            return True
        args = self.parser.parse_args(words)
        cmd, fs = args.command, self.fs
        if cmd in ("exit", "quit"):
            return False
        if cmd == "help":
            print(HELP)
        elif cmd == "mkdir":
            fs.mkdir(args.path)
            print(fs.tree())
        elif cmd == "rename":
            fs.rename(args.path, args.new_name)
            print(fs.tree())
        elif cmd == "create":
            fs.create(args.path, args.size)
            print(fs.tree())
        elif cmd in ("update", "truncate"):
            fs.resize(args.path, args.size)
            print(f"Arquivo atualizado: {fs.file(args.path).size} bytes.")
        elif cmd == "write":
            fs.write(args.path, " ".join(args.text))
            print(f"Conteúdo salvo: {fs.file(args.path).size} bytes.")
        elif cmd == "read":
            content, zeros = fs.read(args.path)
            print(content.decode("utf-8", errors="replace"))
            if zeros:
                print(f"[{zeros} bytes NUL (zeros virtuais)]")
        elif cmd in ("rm", "delete"):
            fs.remove(args.path, args.recursive)
            print("Removido.")
        elif cmd == "cd":
            fs.chdir(args.path)
        elif cmd == "pwd":
            print(fs.cwd.path)
        elif cmd == "tree":
            print(fs.tree(args.path))
        elif cmd == "ls":
            node = fs.resolve(args.path)
            if node.directory:
                for child in sorted(node.children.values(), key=lambda n: n.name):
                    print(child.name + ("/" if child.directory else f" ({child.size} bytes)"))
            else:
                print(f"{node.name} ({node.size} bytes)")
        elif cmd == "fs_status":
            print(fs.status(args.details))
        return True


def main():
    parser = argparse.ArgumentParser(description="Simulador de sistema de arquivos com i-nodes")
    parser.add_argument("--blocks", type=int, default=64, help="Quantidade de blocos (padrão: 64)")
    parser.add_argument("--block-size", type=parse_size, default=4096, help="Tamanho do bloco (padrão: 4KB)")
    parser.add_argument("--script", help="Arquivo UTF-8 de comandos; para no primeiro erro")
    args = parser.parse_args()
    try:
        fs = FileSystem(args.blocks, args.block_size)
    except ValueError as error:
        parser.error(str(error))
    shell = Shell(fs)
    if args.script:
        try:
            with open(args.script, encoding="utf-8") as source:
                for number, line in enumerate(source, 1):
                    try:
                        if not shell.execute(line):
                            break
                    except (FileSystemError, ValueError) as error:
                        print(f"Erro na linha {number}: {error}", file=sys.stderr)
                        return 1
        except OSError as error:
            print(f"Erro ao abrir script: {error}", file=sys.stderr)
            return 1
        return 0
    print("Simulador de Sistema de Arquivos — digite help para ajuda.")
    while True:
        try:
            if not shell.execute(input(f"{fs.cwd.path}> ")):
                break
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrado.")
            break
        except (FileSystemError, ValueError) as error:
            print(f"Erro: {error}")
    return 0
