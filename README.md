# Email Validator Pro

> Sistema completo de verificação e validação de e-mails com DNS, SMTP e cache global inteligente — inspirado em ferramentas como NeverBounce.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green)
![SQLite](https://img.shields.io/badge/SQLite-Local-orange)
![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-CDN-38bdf8)

---

## ✨ Funcionalidades

- **Verificação em 4 níveis**: sintaxe → DNS/MX → SMTP → detecção de Accept-All
- **Cache global inteligente**: e-mails já verificados não são reprocessados
- **Processamento em background**: lista enviada, resultado assíncrono
- **Progresso em tempo real**: barra de progresso atualizada por polling
- **Upload flexível**: cole e-mails, envie CSV ou XLSX
- **Exportação CSV**: baixe os resultados completos com um clique
- **Painel moderno**: dashboard com gráficos donut, métricas e tabela paginada

---

## 🏗️ Estrutura do Projeto

```
email-validator-pro/
├── app/
│   ├── main.py          ← Rotas FastAPI
│   ├── database.py      ← Engine SQLAlchemy
│   ├── models.py        ← Modelos (GlobalCache, EmailList, ListItem)
│   ├── schemas.py       ← Schemas Pydantic
│   ├── verifier.py      ← Motor de verificação (4 níveis)
│   ├── tasks.py         ← Processamento em background
│   ├── utils.py         ← Utilitários (parse CSV/XLSX, normalização)
│   ├── services/
│   │   ├── cache_service.py
│   │   ├── list_service.py
│   │   └── export_service.py
│   ├── templates/       ← Jinja2 HTML
│   └── static/          ← CSS/JS
├── uploads/             ← Arquivos enviados (não versionado)
├── exports/             ← CSVs exportados (não versionado)
├── database.db          ← Criado automaticamente
├── requirements.txt
└── run.py
```

---

## 🚀 Instalação e Execução

### macOS e Linux

```bash
# 1. Clone ou navegue até o projeto
cd /caminho/para/email-validator-pro

# 2. Crie e ative o ambiente virtual
python3 -m venv venv
source venv/bin/activate

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Inicie o servidor
uvicorn app.main:app --reload
```

Acesse em: **http://localhost:8000**

### Windows

```powershell
# 1. Crie e ative o ambiente virtual
python -m venv venv
venv\Scripts\activate

# 2. Instale as dependências
pip install -r requirements.txt

# 3. Inicie o servidor
uvicorn app.main:app --reload
```

### Usando o script de conveniência

```bash
# Equivalente ao uvicorn acima
python run.py
```

---

## 🛣️ Endpoints

| Método | URL | Descrição |
|--------|-----|-----------|
| `GET` | `/` | Dashboard com métricas e listas |
| `GET` | `/upload` | Formulário de envio |
| `POST` | `/upload` | Recebe texto, CSV ou XLSX |
| `GET` | `/lists/{id}` | Detalhes da lista com gráfico |
| `GET` | `/lists/{id}/export` | Download do CSV de resultados |
| `GET` | `/api/lists/{id}/progress` | Progresso em JSON (para polling) |

---

## 🔍 Status de Verificação

| Status | Significado |
|--------|-------------|
| `VALID` | E-mail existe e foi aceito pelo servidor SMTP |
| `INVALID` | Sintaxe inválida, domínio inexistente ou caixa postal rejeitada |
| `UNKNOWN` | Servidor ambíguo, timeout, greylisting ou bloqueio |
| `ACCEPT_ALL` | Servidor aceita qualquer destinatário (catchall) |

---

## ⚙️ Configurações

Por padrão o sistema usa:
- **Banco**: `database.db` na raiz do projeto
- **SMTP timeout**: 10 segundos por conexão
- **Pausa entre SMTPs**: 200ms (reduz risco de bloqueio)
- **Cache**: global e reutilizável entre todas as listas

Para alterar, edite as constantes em `app/verifier.py` e `app/tasks.py`.

---

## 📋 Requisitos

- Python 3.10 ou superior
- pip
- Acesso à internet (para DNS e SMTP)
- Porta 25 liberada para saída (necessária para verificação SMTP)

> **Nota**: Alguns provedores de internet bloqueiam a porta 25. Se as verificações SMTP retornarem sempre `UNKNOWN`, verifique se a porta 25 está acessível.

---

## 📁 Para mais detalhes

Consulte [`docs/SETUP_MACOS.md`](docs/SETUP_MACOS.md) para um guia detalhado de configuração no macOS.
