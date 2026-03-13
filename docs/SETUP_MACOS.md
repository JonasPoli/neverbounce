# Guia de Configuração no macOS — Email Validator Pro

Este guia descreve o passo a passo completo para configurar e executar o **Email Validator Pro** no macOS.

---

## Pré-requisitos

### 1. Python 3.10+

Verifique a versão instalada:

```bash
python3 --version
```

Se não tiver Python 3.10 ou superior, instale via [Homebrew](https://brew.sh):

```bash
# Instalar Homebrew (se ainda não tiver)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Instalar Python
brew install python@3.12
```

### 2. pip

O pip geralmente acompanha o Python. Verifique:

```bash
pip3 --version
```

---

## Instalação

### Passo 1 — Clone ou navegue até o projeto

```bash
cd /Volumes/Dados/work/neverbounce/email-validator-pro
```

Ou, se for clonar de um repositório:

```bash
git clone <url-do-repositorio>
cd email-validator-pro
```

### Passo 2 — Crie o ambiente virtual

```bash
python3 -m venv venv
```

Isso cria um diretório `venv/` com Python isolado para este projeto.

### Passo 3 — Ative o ambiente virtual

```bash
source venv/bin/activate
```

Seu terminal exibirá `(venv)` no prompt, indicando que o ambiente está ativo.

> Para desativar quando terminar: `deactivate`

### Passo 4 — Instale as dependências

```bash
pip install -r requirements.txt
```

A instalação leva cerca de 1-2 minutos dependendo da conexão.

---

## Executando o Servidor

### Opção A — Comando direto (recomendado para desenvolvimento)

```bash
uvicorn app.main:app --reload
```

O `--reload` faz o servidor reiniciar automaticamente quando você editar arquivos.

### Opção B — Script de conveniência

```bash
python run.py
```

### Opção C — Porta personalizada

```bash
uvicorn app.main:app --reload --port 9000
```

---

## Acessando a Aplicação

Com o servidor rodando, abra o navegador em:

```
http://localhost:8000
```

O banco de dados (`database.db`) e as tabelas são criados automaticamente na primeira execução.

---

## Estrutura de Diretórios Criados Automaticamente

| Diretório | Finalidade |
|-----------|-----------|
| `database.db` | Banco SQLite local |
| `uploads/` | Arquivos enviados temporariamente |
| `exports/` | CSVs exportados gerados pelo sistema |

---

## Verificando a Porta 25 (SMTP)

O sistema usa a **porta 25** para verificar caixas postais via SMTP. Alguns ISPs e provedores de nuvem bloqueiam essa porta por padrão.

Para verificar se a porta 25 está acessível:

```bash
nc -zv gmail-smtp-in.l.google.com 25
```

Se retornar `Connection refused` ou timeout, a porta 25 pode estar bloqueada. Nesse caso, as verificações SMTP retornarão `UNKNOWN` em vez de `VALID/INVALID`, o que é o comportamento esperado pelo sistema (seguro e não-destrutivo).

---

## Solução de Problemas

### `ModuleNotFoundError`
O ambiente virtual não está ativo. Execute:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### `Address already in use`
A porta 8000 já está em uso. Use uma porta diferente:
```bash
uvicorn app.main:app --reload --port 8001
```

### Banco não criado
O banco é criado automaticamente ao iniciar. Se houver problemas, verifique permissões na pasta do projeto:
```bash
ls -la database.db
```

### Templates não encontrados
Certifique-se de executar o servidor a partir da raiz do projeto (`email-validator-pro/`), não de dentro da pasta `app/`.

---

## Encerrando o Servidor

Pressione `Ctrl + C` no terminal onde o servidor está rodando.

Para desativar o ambiente virtual:

```bash
deactivate
```

---

## Atualização das Dependências

Para atualizar todas as dependências para as versões mais recentes compatíveis:

```bash
pip install -r requirements.txt --upgrade
```

---

## Reiniciar do Zero (reset completo)

Para apagar todos os dados e recomeçar:

```bash
rm -f database.db
rm -f exports/*.csv
```

O banco será recriado automaticamente ao reiniciar o servidor.
