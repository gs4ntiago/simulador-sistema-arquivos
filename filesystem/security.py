"""Identidade, permissões e integridade sobre o disco simulado."""
from dataclasses import dataclass
from getpass import getpass
from hashlib import pbkdf2_hmac, sha256
import hmac
import os
import re
import secrets
import shlex

from .cli import Parser, Shell
from .core import FileSystemError
from .disk_io import IOFileSystem


def derive(secret, salt, length=32):
    return pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 600_000, length)


def root_password(script):
    value = os.environ.get("SIM_ROOT_PASSWORD")
    if value is None and not script:
        value = getpass("Defina a senha do root simulado: ")
    if not value:
        raise FileSystemError("Defina uma senha root; em scripts use SIM_ROOT_PASSWORD.")
    return value


@dataclass(frozen=True)
class Account:
    primary: str
    groups: frozenset[str]
    salt: bytes
    password_hash: bytes


@dataclass(frozen=True)
class Encryption:
    salt: bytes
    nonce: bytes
    tag: bytes
    signature_detected: bool
    content_length: int


def transform(data, key, nonce):
    """Cifra de fluxo didática com HMAC-SHA256 e contador."""
    result = bytearray()
    for offset in range(0, len(data), 32):
        counter = (offset // 32).to_bytes(8, "big")
        stream = hmac.digest(key, nonce + counter, "sha256")
        result.extend(byte ^ mask for byte, mask in zip(data[offset:offset + 32], stream))
    return bytes(result)


def malicious(data):
    return b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data or b"SIMULATED_MALWARE" in data


class SecureFileSystem(IOFileSystem):
    def __init__(self, *args, password, **kwargs):
        super().__init__(*args, **kwargs)
        self.audit = []
        self.groups = {"admin"}
        self.accounts = {}
        self.block_hashes = {}
        self.encrypted = {}
        self._register("root", "admin", (), password)
        self.user = None

    @property
    def mode(self):
        return "MODO_KERNEL" if self.user == "root" else "MODO_USUARIO"

    def _record(self, event):
        self.audit.append(f"{self.user or 'visitante'} | {event}")

    def require_root(self, operation):
        if self.user != "root":
            self._record(f"NEGADO: {operation} exige MODO_KERNEL")
            raise FileSystemError(f"{operation} exige MODO_KERNEL.")

    def _register(self, name, group, supplementary, password):
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", name) or name in self.accounts:
            raise FileSystemError("Nome de usuário inválido ou já cadastrado.")
        groups = frozenset([group, *supplementary])
        if not groups <= self.groups or not password:
            raise FileSystemError("Informe grupos cadastrados e uma senha não vazia.")
        salt = secrets.token_bytes(16)
        self.accounts[name] = Account(group, groups, salt, derive(password, salt))

    def groupadd(self, group):
        self.require_root("groupadd")
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", group) or group in self.groups:
            raise FileSystemError("Nome de grupo inválido ou já cadastrado.")
        self.groups.add(group)
        self._record(f"grupo cadastrado: {group}")

    def useradd(self, name, group, supplementary, password):
        self.require_root("useradd")
        self._register(name, group, supplementary, password)
        self._record(f"usuário cadastrado: {name}")

    def login(self, name, password):
        account = self.accounts.get(name)
        candidate = derive(password, account.salt if account else bytes(16))
        if account is None or not hmac.compare_digest(candidate, account.password_hash):
            self._record(f"falha de autenticação: {name!r}")
            raise FileSystemError("Credenciais inválidas.")
        self.user = name
        self.cwd = self.root
        self._record("login")

    def logout(self):
        self._record("logout")
        self.user = None
        self.cwd = self.root

    def _allowed(self, node, required):
        if self.user == "root":
            return True
        account = self.accounts.get(self.user)
        if not account and required & 2:
            return False
        shift = 0
        if self.user == node.owner:
            shift = 6
        elif account and node.group in account.groups:
            shift = 3
        return ((node.mode >> shift) & required) == required

    def check_access(self, node, required):
        if self._allowed(node, required):
            return
        action = "/".join(label for bit, label in ((4, "LEITURA"), (2, "ESCRITA"),
                                                   (1, "EXECUÇÃO")) if required & bit)
        user = self.user or "visitante"
        group = self.accounts[self.user].primary if self.user else "-"
        permissions = "".join(letter if node.mode & (1 << (8 - i)) else "-"
                              for i, letter in enumerate("rwxrwxrwx"))
        self._record(f"NEGADO: {action} em {node.path!r}")
        raise FileSystemError(
            "[VERIFICAÇÃO DE ACL]\n"
            f"Objeto: {node.path}\nProprietário: {node.owner} (Grupo: {node.group})\n"
            f"Permissões: {permissions} ({node.mode:03o})\n"
            f"Ação pedida: {action} por {user!r} (Grupo: {group})\n"
            "[NEGADO] Permissão insuficiente. Tentativa registrada na auditoria."
        )

    def can_list(self, node):
        return self._allowed(node, 5)

    def _new_node(self, name, parent, directory=True):
        node = super()._new_node(name, parent, directory)
        node.owner = self.user
        node.group = self.accounts[self.user].primary
        node.mode = 0o755 if directory else 0o644
        return node

    def chmod(self, path, mode):
        node = self.resolve(path)
        if self.user is None or self.user != node.owner:
            self.require_root("chmod")
        node.mode = mode
        self._record(f"chmod {mode:03o} {node.path!r}")

    def chown(self, path, owner, group):
        self.require_root("chown")
        if owner not in self.accounts or group not in self.groups:
            raise FileSystemError("Usuário ou grupo desconhecido.")
        node = self.resolve(path)
        node.owner, node.group = owner, group
        self._record(f"chown {owner}:{group} {node.path!r}")

    def status(self, details=False):
        self.require_root("fs_status")
        return super().status(details)

    def _write_blocks(self, blocks):
        super()._write_blocks(blocks)
        self.block_hashes.update({block: sha256(data).digest() for block, data in blocks.items()})

    def _verified_content(self, node):
        blocks = [node.inode, *node.blocks]
        data = self.storage.read(blocks)
        corrupt = [block for block in blocks if sha256(data[block]).digest()
                   != self.block_hashes.get(block)]
        if corrupt:
            self._record(f"ALERTA: blocos {corrupt} alterados em {node.path!r}")
            raise FileSystemError(f"Hash difere do registro oficial nos blocos {corrupt}.")
        return b"".join(data[block] for block in node.blocks)[:len(node.content)]

    def read(self, path, key=None):
        node = self.file(path)
        self.check_access(node, 4)
        record = self.encrypted.get(node)
        if record and not key:
            raise FileSystemError("Arquivo criptografado; informe --key.")
        data = self._verified_content(node)
        if record:
            data = self._decrypt(node, data, key)
        return data, node.size - len(data)

    def _check_write(self, path):
        node = self.file(path)
        self.check_access(node, 2)
        if node in self.encrypted:
            raise FileSystemError("Use decrypt_file antes de modificar um arquivo criptografado.")
        return node

    def write(self, path, text):
        node = self._check_write(path)
        previous_blocks = set(node.blocks)
        super().write(path, text)
        self._forget_released_blocks(node, previous_blocks)

    def resize(self, path, size):
        node = self._check_write(path)
        self._verified_content(node)
        previous_blocks = set(node.blocks)
        super().resize(path, size)
        self._forget_released_blocks(node, previous_blocks)

    def _forget_released_blocks(self, node, previous_blocks):
        for block in previous_blocks.difference(node.blocks):
            self.block_hashes.pop(block, None)

    def _replace_content(self, node, content):
        previous = node.content
        node.content = content
        try:
            self._store(node)
        except FileSystemError:
            node.content = previous
            raise

    def encrypt(self, path, key):
        node = self.file(path)
        self.check_access(node, 6)
        if node in self.encrypted or not key:
            raise FileSystemError("Informe uma chave não vazia para um arquivo ainda em claro.")
        plaintext = self._verified_content(node)
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
        keys = derive(key, salt, length=64)
        ciphertext = transform(plaintext.ljust(node.size, b"\0"), keys[:32], nonce)
        tag = hmac.digest(keys[32:], nonce + ciphertext, "sha256")
        self._replace_content(node, ciphertext)
        self.encrypted[node] = Encryption(salt, nonce, tag, malicious(plaintext), len(plaintext))
        self._record(f"criptografia: {node.path!r}")

    def _decrypt(self, node, ciphertext, key):
        record = self.encrypted[node]
        keys = derive(key, record.salt, length=64)
        tag = hmac.digest(keys[32:], record.nonce + ciphertext, "sha256")
        if not hmac.compare_digest(record.tag, tag):
            self._record(f"falha na autenticação da cifra: {node.path!r}")
            raise FileSystemError("Chave incorreta ou conteúdo criptografado alterado.")
        return transform(ciphertext, keys[:32], record.nonce)[:record.content_length]

    def decrypt(self, path, key):
        node = self.file(path)
        self.check_access(node, 6)
        if node not in self.encrypted:
            raise FileSystemError("O arquivo está em claro.")
        plaintext = self._decrypt(node, self._verified_content(node), key)
        self._replace_content(node, plaintext)
        del self.encrypted[node]
        self._record(f"descriptografia: {node.path!r}")

    def remove(self, path, recursive=False):
        node = self.resolve(path)
        super().remove(path, recursive)
        for item in self._files(node):
            self.encrypted.pop(item, None)
            for block in [item.inode, *item.blocks]:
                self.block_hashes.pop(block, None)

    def _files(self, node):
        if node.directory:
            for child in sorted(node.children.values(), key=lambda item: item.name):
                yield from self._files(child)
        else:
            yield node

    def scan(self, path):
        self.require_root("antivirus_scan")
        files = list(self._files(self.resolve(path)))
        self.storage.check()
        results = [f"[VARREDURA DE INTEGRIDADE] Analisando {len(files)} arquivo(s)."]
        for node in files:
            try:
                data = self._verified_content(node)
            except FileSystemError as error:
                result = f"[ALERTA] {error}"
            else:
                record = self.encrypted.get(node)
                signature = record.signature_detected if record else malicious(data)
                if signature:
                    result = "[ALERTA] Assinatura maliciosa simulada."
                    self._record(f"ALERTA: assinatura em {node.path!r}")
                else:
                    result = "[OK - Criptografado e Íntegro]" if record else "[OK - Íntegro]"
            results.append(f"{node.path}: {result}")
        self._record(f"varredura: {path!r}")
        return "\n".join(results)


class SecurityShell(Shell):
    def __init__(self, fs, scripted=False):
        super().__init__(fs)
        self.scripted = scripted
        self.security_parser = Parser(prog="segurança", add_help=False)
        commands = self.security_parser.add_subparsers(dest="command", required=True)
        commands.add_parser("login").add_argument("--user", required=True)
        commands.add_parser("groupadd").add_argument("name")
        user = commands.add_parser("useradd")
        user.add_argument("name")
        user.add_argument("--group", required=True)
        user.add_argument("--supplementary", action="append", default=[])
        chmod = commands.add_parser("chmod")
        chmod.add_argument("mode")
        chmod.add_argument("path")
        chown = commands.add_parser("chown")
        chown.add_argument("owner_group")
        chown.add_argument("path")
        for name in ("read", "read_file", "encrypt_file", "decrypt_file"):
            command = commands.add_parser(name)
            command.add_argument("path")
            command.add_argument("--key")
        commands.add_parser("antivirus_scan").add_argument("path", nargs="?", default="/")
        for name in ("logout", "whoami", "audit_log"):
            commands.add_parser(name)

    def show_change(self, message):
        print(message)

    def _password(self, name):
        variable = "SIM_ROOT_PASSWORD" if name == "root" else f"SIM_PASSWORD_{name}"
        if self.scripted:
            value = os.environ.get(variable)
            if not value:
                raise FileSystemError(f"Defina {variable} para executar o script.")
            return value
        return getpass("Senha: ")

    def execute(self, line):
        words = shlex.split(line, comments=True)
        security_commands = {
            "login", "logout", "whoami", "groupadd", "useradd", "chmod", "chown",
            "read", "read_file", "encrypt_file", "decrypt_file", "antivirus_scan", "audit_log",
        }
        if not words or words[0] not in security_commands:
            result = super().execute(line)
            if words and words[0] == "help":
                print(
                    "\nComandos de segurança\n"
                    "  login --user <nome> | logout | whoami\n"
                    "  groupadd <grupo> | useradd <nome> --group <grupo> [--supplementary <grupo>]\n"
                    "  chmod <octal> <caminho> | chown <usuário>:<grupo> <caminho>\n"
                    "  read_file <caminho> [--key <chave>]\n"
                    "  encrypt_file <caminho> [--key <chave>] | decrypt_file <caminho> [--key <chave>]\n"
                    "  antivirus_scan [caminho] | audit_log"
                )
            return result

        args = self.security_parser.parse_args(words)
        fs, command = self.fs, args.command
        if command == "login":
            fs.login(args.user, self._password(args.user))
            print(f"Autenticado com sucesso. {self._identity()}")
        elif command == "logout":
            fs.logout()
            print(self._identity())
        elif command == "whoami":
            print(self._identity())
        elif command == "groupadd":
            fs.groupadd(args.name)
        elif command == "useradd":
            fs.require_root(command)
            fs.useradd(args.name, args.group, args.supplementary, self._password(args.name))
        elif command == "chmod":
            if not re.fullmatch(r"[0-7]{3}", args.mode):
                raise FileSystemError("Informe três dígitos octais, por exemplo 750.")
            fs.chmod(args.path, int(args.mode, 8))
        elif command == "chown":
            owner, _, group = args.owner_group.partition(":")
            fs.chown(args.path, owner, group)
        elif command in ("read", "read_file"):
            node = fs.file(args.path)
            fs.check_access(node, 4)
            key = self._key(args.key) if node in fs.encrypted else args.key
            content, zeros = fs.read(args.path, key)
            print(content.decode("utf-8", errors="replace"))
            if zeros:
                print(f"[{zeros} bytes NUL (zeros virtuais)]")
        elif command in ("encrypt_file", "decrypt_file"):
            fs.check_access(fs.file(args.path), 6)
            key = self._key(args.key)
            if command == "encrypt_file":
                fs.encrypt(args.path, key)
                print(f"Arquivo {args.path!r} criptografado e gravado no disco simulado.")
            else:
                fs.decrypt(args.path, key)
                print(f"Arquivo {args.path!r} descriptografado.")
        elif command == "antivirus_scan":
            print(fs.scan(args.path))
        elif command == "audit_log":
            fs.require_root(command)
            print("\n".join(fs.audit) or "Auditoria vazia.")
        return True

    def _key(self, supplied):
        if supplied is not None:
            return supplied
        if self.scripted:
            raise FileSystemError("Em scripts, informe --key.")
        return getpass("Chave: ")

    def _identity(self):
        user = self.fs.user
        group = self.fs.accounts[user].primary if user else "-"
        return f"Usuário: {user or 'visitante'!r} | Grupo: {group!r} | Modo: {self.fs.mode}"
