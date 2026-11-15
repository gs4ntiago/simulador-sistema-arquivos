# Projeto de Sistemas Operacionais

Projeto didático que simula componentes de um sistema operacional em três
etapas cumulativas: organização do armazenamento, entrada e saída (E/S) de disco
e segurança. Desenvolvido em Python 3.10 ou superior, sem dependências externas.
O armazenamento e o estado do simulador ficam em memória durante a execução.

**Etapa atual: 3 — Segurança e proteção do sistema.**

| Etapa | Tema | Como executar |
| --- | --- | --- |
| 1 | Sistema de arquivos, i-nodes e alocação de blocos | `python3 main.py` |
| 2 | E/S de disco, escalonamento, SSD e RAID 5 | `python3 -m filesystem.disk_io` |
| 3 (atual) | Modos de execução, autenticação, permissões, criptografia e integridade | `python3 main.py --security` |

Execute os comandos a partir da raiz do projeto.

## Etapa 1 — Sistema de arquivos e alocação de blocos

Esta etapa modela a organização de arquivos e diretórios em um sistema
operacional. A árvore representa a estrutura lógica, e a alocação indexada
por i-nodes permite observar a ocupação do disco e a fragmentação.

### Execução

```bash
python3 main.py
```

Configuração opcional do disco:

```bash
python3 main.py --blocks 128 --block-size 4KB
```

Reproduzir os exemplos automaticamente:

```bash
python3 main.py --script exemplo.txt
```

O modo script para no primeiro erro e retorna código 1. O terminal interativo informa erros e permite continuar. `help` mostra os comandos, `exit` encerra e Ctrl+D também sai.

### Exemplo de alocação

```text
/> mkdir /sistema
/> mkdir /sistema/logs
/> create /sistema/logs/app.log --size 10KB
/
└── sistema/
    └── logs/
        └── app.log (10240 bytes)
/> fs_status --details
```

Com blocos de 4096 bytes, o relatório mostra:

- Tamanho real: 10240 bytes.
- Dados alocados: 12288 bytes (3 blocos).
- Fragmentação interna dos dados: 2048 bytes.
- I-node: 4096 bytes (1 bloco reservado).
- Total físico alocado: 16384 bytes.
- Mapa: bloco 0 como `I`, blocos 1–3 como `D`, demais como `LIVRE`.

### Operações no simulador

```text
cd /sistema/logs
pwd
ls
rename /sistema/logs registros
cd /sistema/registros
read app.log
write app.log "Aplicação iniciada com sucesso"
read app.log
update app.log --size 20KB
truncate app.log --size 1KB
create vazio.txt --size 0
rm vazio.txt
cd /
rm -r /sistema
tree /
fs_status --details
```

`write` substitui o conteúdo e usa seu tamanho em bytes UTF-8. `update` e `truncate` redimensionam: reduzir descarta bytes finais; aumentar adiciona zeros virtuais. `read` exibe texto e informa a quantidade de bytes NUL sem despejar milhares de zeros no terminal. Um corte no meio de um caractere UTF-8 aparece como caractere de substituição na leitura; o armazenamento mantém os bytes exatos.

Caminhos aceitam `/`, `.`, `..` e nomes com espaços entre aspas. Pais devem existir. `rm` remove arquivos ou diretórios vazios; `rm -r` remove árvores. A raiz, o diretório atual e seus ancestrais são protegidos contra remoção.

`rename <caminho> <novo_nome>` renomeia um diretório dentro do mesmo pai. Por exemplo, `rename /sistema/logs registros` altera seu caminho para `/sistema/registros`. O novo nome não pode conter `/`, ser vazio, `.` ou `..`, nem colidir com outro arquivo ou diretório. A raiz não pode ser renomeada. Renomear o diretório atual ou um ancestral atualiza automaticamente os caminhos dos descendentes, preservando conteúdo e blocos alocados. Repetir o nome atual não altera nada.

### Modelo físico e fragmentação

Cada arquivo, inclusive vazio, reserva um bloco inteiro de i-node para uma tabela de ponteiros de 4 bytes. Os blocos de dados podem estar dispersos e são escolhidos entre os livres por ordem de índice. Um i-node comporta `tamanho_do_bloco // 4` ponteiros: com 4KB, o limite por arquivo é 1024 blocos de dados (4MiB), sujeito ao espaço disponível. Não há índices indiretos.

Diretórios e seus metadados são mantidos na árvore em memória e não consomem blocos neste modelo didático. O disco simula a propriedade dos blocos; o conteúdo escrito é mantido no objeto do arquivo. Arquivos criados apenas com `--size` usam zeros virtuais. Nesta etapa, o conteúdo e a ocupação dos blocos são representados em memória. A Etapa 2 acrescenta as operações de E/S física simulada.

A fragmentação interna soma `blocos_de_dados × tamanho_do_bloco − tamanho_real`, separadamente do espaço reservado aos i-nodes. Ela representa o desperdício no último bloco de cada arquivo.

Na alocação indexada, espaços livres dispersos continuam utilizáveis, portanto não existe fragmentação externa que impeça uma alocação por falta de contiguidade. O relatório também informa número de extensões livres, maior extensão e bytes livres fora dela para mostrar a dispersão física. Esse último indicador não é desperdício e não implica necessidade de compactação.

Antes de alocar ou ampliar, o simulador verifica capacidade do índice e espaço livre. Uma falha mantém o arquivo e o disco inalterados. Remover ou reduzir libera os blocos correspondentes.

Unidades `KB`, `MB`, `GB`, `KiB`, `MiB` e `GiB` usam múltiplos de 1024; sem unidade, o tamanho é em bytes. Tamanhos devem ser inteiros não negativos.

## Etapa 2 — E/S, escalonamento e RAID 5

O módulo `filesystem/disk_io.py` reutiliza os comandos e a árvore da Etapa 1.
Execute-o a partir da raiz do projeto. A versão original continua disponível em
`python3 main.py`.

### HDD e escalonamento

```bash
python3 -m filesystem.disk_io --blocks 16 --scheduler sstf
```

No simulador:

```text
io_demo
mkdir /sistema
mkdir /sistema/logs
write_file /sistema/logs/app.log "Novo Registro"
read_file /sistema/logs/app.log
io_status
```

`io_demo` usa a fila [98, 183, 37, 122, 14] e cabeça inicial na trilha 53
com as opções padrão. O SSTF atende 37 → 14 → 98 → 122 → 183, com 208
trilhas de deslocamento. Use `--scheduler fcfs` ao iniciar para atender na
ordem de chegada. Cada disco tem sua própria cabeça e a posição persiste
entre operações. Nos lotes de E/S do sistema de arquivos, a trilha é dada
pelo resto do índice do bloco físico pelo número de trilhas.

`create`, `write`, `update` e `truncate` gravam o i-node e os dados.
`read` lê o i-node antes de buscar os blocos de dados. `write_file` cria ou
atualiza um arquivo com texto ou com `--size`; `read_file` é um alias de
`read`. Os lotes de uma fase são escalonados juntos. A leitura do i-node e
a leitura dos dados são fases distintas por causa da dependência entre elas.

### RAID 5

```bash
python3 -m filesystem.disk_io --raid 5 --disks 3 --blocks 16
```

```text
mkdir /dados
write_file /dados/planilha.csv --size 8KB
fail_disk 2
read_file /dados/planilha.csv
rebuild_disk 2
io_status
```

Com três discos, cada faixa comporta dois blocos lógicos e um bloco de
paridade XOR. A posição da paridade gira entre os discos. Os i-nodes
também fazem parte das faixas; por isso, os dois blocos de dados de um
arquivo podem cair em faixas diferentes. O log mostra o disco, o bloco
físico e o bloco lógico ou a indicação de paridade.

Com um disco em falha, o sistema continua aceitando leituras e gravações.
A leitura de um bloco indisponível usa o XOR dos demais blocos da faixa.
`rebuild_disk` regrava as faixas usadas no disco em falha. Duas falhas
simultâneas tornam o arranjo indisponível. A opção `--raid 0` significa
modo sem RAID (disco único).

### SSD e nivelamento de desgaste

```bash
python3 -m filesystem.disk_io --medium ssd --blocks 4
```

```text
write_file /registro "Primeira versão"
write_file /registro "Segunda versão"
read_file /registro
io_status
```

O SSD atende em ordem de chegada, sem deslocamento mecânico. Cada
gravação escolhe a página física livre com menor contagem de gravações,
atualiza o mapeamento e devolve a página antiga ao conjunto de páginas
livres. O modelo usa uma página por bloco, uma página reserva por disco e
contagem de gravações como aproximação do desgaste. A memória é volátil
em todos os modos.

## Etapa 3 — Segurança e proteção do sistema (atual)

Esta etapa usa a árvore e a alocação da Etapa 1 sobre a camada de E/S da
Etapa 2. A identidade da sessão determina quais operações ela pode executar,
e a integridade é verificada sobre os blocos gravados no disco simulado.

### Execução e autenticação

Execute `python3 main.py --security`. As opções de HDD, SSD, escalonamento e
RAID 5 da Etapa 2 também estão disponíveis. Na primeira interação, defina a senha
do root simulado. As senhas são derivadas com PBKDF2-HMAC-SHA256 e salt
individual. Após o login, root opera em `MODO_KERNEL` e as demais contas em
`MODO_USUARIO`. A sessão começa como visitante e `login` solicita a senha
sem exibi-la. Use `logout` para encerrar a sessão e `whoami` para consultar a
identidade. Em scripts, forneça a senha inicial e de login do root por
`SIM_ROOT_PASSWORD` e as demais por `SIM_PASSWORD_<usuário>`.

### Permissões e controle de acesso

Como root, configure usuários, grupos e permissões:

```text
login --user root
groupadd devs
useradd carlos --group devs
mkdir /sistema
write_file /sistema/config.sys "configuração interna"
chmod 750 /sistema/config.sys
mkdir /home
mkdir /home/carlos
chown carlos:devs /home/carlos
chmod 700 /home/carlos
logout
```

Como Carlos:

```text
login --user carlos
read_file /sistema/config.sys
write_file /home/carlos/segredo.txt "mensagem confidencial"
```

A leitura de `config.sys` gera uma negativa registrada na auditoria. Scripts
encerram ao primeiro erro. As permissões usam três dígitos octais para
proprietário, grupo e outros. Diretórios exigem execução para travessia e
escrita mais execução para alterar entradas. O proprietário também pode usar
`chmod`. Cadastro, `chown`, diagnósticos físicos, falha e reconstrução de disco,
`antivirus_scan` e `audit_log` exigem root em MODO_KERNEL.

### Criptografia em repouso

Na mesma sessão de Carlos, criptografe o arquivo e forneça a chave quando
solicitada. Para lê-lo, informe novamente a mesma chave.

```text
encrypt_file /home/carlos/segredo.txt
read_file /home/carlos/segredo.txt
```

A cifra didática usa fluxo de HMAC-SHA256 com nonce aleatório e autenticação
com chave separada. O conteúdo cifrado chega aos blocos antes do cálculo de
paridade. Use `decrypt_file` com a chave para editar ou redimensionar o arquivo.
Os comandos de cifra aceitam `--key`, obrigatório em scripts. Na sessão
interativa, omita a opção para digitar a chave sem eco.

### Auditoria e varredura de integridade

```text
login --user root
antivirus_scan /
audit_log
```

A varredura compara os
hashes SHA-256 dos blocos alocados de cada arquivo e de seu i-node com o registro
das gravações. A regra de assinatura reconhece os marcadores
`SIMULATED_MALWARE` e `EICAR-STANDARD-ANTIVIRUS-TEST-FILE`. Para arquivos cifrados,
o resultado de assinatura é registrado na cifragem e o hash é verificado sobre
os blocos cifrados. Disco, contas e auditoria ficam em memória durante a execução.

## Estrutura do projeto

- `filesystem/core.py` — árvore, i-nodes, alocação e fragmentação da Etapa 1.
- `filesystem/cli.py` — comandos básicos e terminal da Etapa 1.
- `filesystem/disk_io.py` — E/S, escalonamento e RAID 5 da Etapa 2.
- `filesystem/security.py` — identidade, permissões, criptografia e auditoria da Etapa 3.
- `main.py` — ponto de entrada para a Etapa 1 e, com `--security`, para a Etapa 3.
- `exemplo.txt` — roteiro de comandos da Etapa 1.

## Testes

```bash
python3 -m unittest discover -s tests -v
```
