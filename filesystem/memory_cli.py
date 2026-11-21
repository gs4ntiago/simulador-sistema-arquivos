"""Comandos da Etapa 4 no terminal compartilhado com arquivos e E/S."""
import re

from .cli import Parser, parse_size
from .core import FileSystemError


HELP = """
Comandos de memória
  create_process --name NOME --size TAMANHO --type critical|normal
  terminate_process --name NOME
  translate_address --process NOME --page N [--offset BYTES]
  execute_step --process NOME --instruction "LOAD_DATA Página N"
  execute_step --process NOME --instruction "STORE_DATA Página N VALOR"
  memory_status [--reserved | --virtual]
  memory_compact --reserved"""


class MemoryCommands:
    def __init__(self, memory, report_every, security):
        self.memory = memory
        self.report_every = report_every
        self.security = security
        self.steps = 0
        self.parser = Parser(prog="memória", add_help=False)
        commands = self.parser.add_subparsers(dest="command", required=True)
        create = commands.add_parser("create_process", add_help=False)
        create.add_argument("--name", required=True)
        create.add_argument("--size", required=True, type=parse_size)
        create.add_argument("--type", choices=("critical", "normal"), required=True)
        terminate = commands.add_parser("terminate_process", add_help=False)
        terminate.add_argument("--name", required=True)
        translate = commands.add_parser("translate_address", add_help=False)
        translate.add_argument("--process", required=True)
        translate.add_argument("--page", required=True, type=int)
        translate.add_argument("--offset", type=int, default=0)
        step = commands.add_parser("execute_step", add_help=False)
        step.add_argument("--process", required=True)
        step.add_argument("--instruction", required=True)
        status = commands.add_parser("memory_status", add_help=False)
        area = status.add_mutually_exclusive_group()
        area.add_argument("--reserved", action="store_true")
        area.add_argument("--virtual", action="store_true")
        compact = commands.add_parser("memory_compact", add_help=False)
        compact.add_argument("--reserved", required=True, action="store_true")
        self.names = set(commands.choices)

    def execute(self, words):
        args = self.parser.parse_args(words)
        cmd = args.command
        memory = self.memory
        fs = memory.fs
        owner = fs.user if self.security else "root"
        if self.security and not owner:
            raise FileSystemError("Faça login para usar os comandos de memória.")
        if cmd == "memory_status":
            if self.security and not args.virtual:
                fs.require_root(cmd)
            if not args.virtual:
                print(memory.reserved_status())
            if not args.reserved:
                print(memory.virtual_status())
            return
        if cmd == "memory_compact":
            if self.security:
                fs.require_root(cmd)
            print(memory.compact())
        elif cmd == "create_process":
            if self.security and args.type == "critical":
                fs.require_root(cmd)
            print(memory.create(args.name, args.size, args.type, owner))
        else:
            process = memory.lookup(args.name if cmd == "terminate_process" else args.process)
            if self.security and process.owner != owner:
                fs.require_root(cmd)
            if cmd == "terminate_process":
                print(memory.terminate(process))
            elif cmd == "translate_address":
                print(memory.access(process, args.page, args.offset))
            else:
                match = re.fullmatch(r"(LOAD_DATA|STORE_DATA)\s+p[áa]gina\s+(\d+)(?:\s+(\d+))?",
                                     args.instruction.strip(), re.IGNORECASE)
                if not match or (match[1].upper() == "STORE_DATA") != (match[3] is not None):
                    raise FileSystemError("Use LOAD_DATA Página N ou STORE_DATA Página N VALOR.")
                value = int(match[3]) if match[3] is not None else None
                print(memory.access(process, int(match[2]), value=value, show_byte=True))
        self.steps += 1
        if self.report_every and self.steps % self.report_every == 0:
            print(f"[Relatório periódico de memória — operação {self.steps}]")
            print(memory.reserved_status(show_map=owner == "root"))
            print(memory.virtual_status())
