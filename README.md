# paperless-mcp 📄🔌

**Ask your archive.**

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![MCP](https://img.shields.io/badge/MCP-server-orange) ![Status](https://img.shields.io/badge/v1-read--only-success)

*🇮🇹 Versione italiana più in basso.*

An **MCP (Model Context Protocol)** server connecting Claude to your
self-hosted **Paperless-ngx** document archive. Version 1 is **read-only by design**.

Once connected, you can ask Claude things like:

> "Find my latest electricity bill and tell me how much I paid"
> "Which documents did I archive in 2025?"
> "How many documents are in my inbox? How is my archive organized?"

## Architecture

```
Claude (Desktop / claude.ai)
        │  MCP protocol
        ▼
  paperless-mcp  (this server, Python + FastMCP)
        │  REST API + token
        ▼
  Paperless-ngx  (e.g. on your NAS)
```

Claude never executes anything by itself: it requests a tool, **this server**
queries the Paperless API and returns the result. The model never sees the token.

## Exposed tools

| Tool | What it does |
|---|---|
| `paperless_search_documents` | Full-text search + filters (tags, correspondent, type, date range), paginated |
| `paperless_get_document` | Full metadata + OCR content (truncatable) of a document |
| `paperless_list_taxonomy` | Lists tags / correspondents / document types / storage paths with counts |
| `paperless_get_statistics` | Archive-wide statistics |

Filters accept **human names** (e.g. `correspondent="Enel"`), automatically
resolved to IDs with a 5-minute cache. All tools are marked `readOnlyHint: true`.

## Prerequisites

1. A reachable Paperless-ngx instance (e.g. `http://<NAS-IP>:8010`)
2. An **API token**. In Paperless: profile menu (top right) → *API Auth Token*.
3. Python 3.11+ (local use) **or** Docker (deployment).

### 🔒 Security (recommended)

The token inherits the permissions of its user. For a truly read-only integration:

1. Create a dedicated user in Paperless, e.g. `claude-readonly`
2. Grant it **view-only** permissions on Document, Tag, Correspondent, DocumentType, StoragePath
3. Note: Paperless permissions are **object-level** — share existing documents
   *and* taxonomy objects with the new user (bulk "Permissions" action)
4. Generate the token for *that* user and use it here

Even in the worst case, nothing can be modified or deleted.

## Option A — Local use with Claude Desktop (stdio)

The server runs on your machine and reaches Paperless over the LAN.

```bash
git clone https://github.com/tiziano5/paperless-mcp.git
cd paperless-mcp
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # macOS/Linux
# .venv\Scripts\pip install -r requirements.txt  (Windows)
cp .env.example .env                            # then insert your token
```

Configure Claude Desktop (`claude_desktop_config.json`, from *Settings → Developer*):

```json
{
  "mcpServers": {
    "paperless": {
      "command": "C:\\path\\to\\paperless-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\paperless-mcp\\server.py"],
      "env": {
        "PAPERLESS_URL": "http://<NAS-IP>:8010",
        "PAPERLESS_TOKEN": "your-token"
      }
    }
  }
}
```

Restart Claude Desktop: the 4 tools will appear in the tools icon.

## Option B — Docker (HTTP transport)

The server runs as a container next to Paperless and stays always on.

```bash
cp .env.example .env    # insert PAPERLESS_URL and PAPERLESS_TOKEN
docker compose up -d --build
```

The server listens on `http://<host>:8802/mcp` (streamable HTTP endpoint).

To attach it to **claude.ai** as a custom connector you need a **public HTTPS
URL** — e.g. a Cloudflare Tunnel hostname pointing to port 8802. ⚠️ If the
hostname sits behind an identity layer with interactive login (OTP page),
the connection from claude.ai will be blocked: use a bypass policy or a
service token for that hostname, or keep the endpoint LAN/VPN-only and use
Option A instead.

## Quick test from the terminal

```bash
python server.py --help                          # CLI check
python server.py                                 # stdio (Ctrl+C to exit)
python server.py --transport http --port 8802    # HTTP
```

Interactive tool testing: `npx @modelcontextprotocol/inspector python server.py`

## Chat usage examples

- *"Search 2025 electricity bills"* → `paperless_search_documents(query="bill", correspondent="Enel", created_after="2025-01-01")`
- *"Read document 431"* → `paperless_get_document(document_id=431)`
- *"Which tags do I use the most?"* → `paperless_list_taxonomy(kind="tags")`

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `authentication failed (401)` | Wrong/revoked token | Regenerate the token, update `.env` |
| `permission denied (403)` | User lacks view permissions | Grant permissions in Paperless |
| `cannot reach Paperless-ngx` | Wrong URL, container down, firewall | Check `PAPERLESS_URL` and that Paperless responds |
| Tag/correspondent names not found | Name differs, or object not shared with the user | Use `paperless_list_taxonomy`; check object-level permissions |

## Roadmap (v2)

- Document upload (`post_document`) with explicit confirmation
- Notes and custom fields
- A "deadlines" tool based on document dates

---
---

# 🇮🇹 paperless-mcp — Documentazione italiana

Server **MCP (Model Context Protocol)** che collega Claude al tuo archivio
documentale **Paperless-ngx** self-hosted. Versione 1: **sola lettura** by design.

Una volta collegato, puoi chiedere a Claude cose come:

> "Trova l'ultima bolletta e dimmi quanto ho pagato"
> "Quali documenti ho archiviato nel 2025?"
> "Quanti documenti ho in inbox? Com'è organizzato l'archivio?"

## Architettura

```
Claude (Desktop / claude.ai)
        │  protocollo MCP
        ▼
  paperless-mcp  (questo server, Python + FastMCP)
        │  REST API + token
        ▼
  Paperless-ngx  (es. sul tuo NAS)
```

Claude non esegue mai nulla da solo: richiede uno strumento, **questo server**
interroga l'API di Paperless e restituisce il risultato. Il modello non vede
mai il token.

## Strumenti esposti

| Strumento | Cosa fa |
|---|---|
| `paperless_search_documents` | Ricerca full-text + filtri (tag, corrispondente, tipo, intervallo date), paginata |
| `paperless_get_document` | Metadati completi + contenuto OCR (troncabile) di un documento |
| `paperless_list_taxonomy` | Elenca tag / corrispondenti / tipi documento / percorsi con conteggi |
| `paperless_get_statistics` | Statistiche generali dell'archivio |

I filtri accettano **nomi umani** (es. `correspondent="Enel"`), risolti
automaticamente in ID con cache di 5 minuti. Tutti gli strumenti sono
marcati `readOnlyHint: true`.

## Prerequisiti

1. Paperless-ngx raggiungibile (es. `http://<IP-DEL-NAS>:8010`)
2. Un **token API**. In Paperless: menu profilo (in alto a destra) → *API Auth Token*.
3. Python 3.11+ (uso locale) **oppure** Docker (deploy).

### 🔒 Sicurezza (consigliato)

Il token eredita i permessi dell'utente. Per un'integrazione davvero in sola lettura:

1. In Paperless crea un utente dedicato, es. `claude-readonly`
2. Assegnagli solo i permessi **view** su Document, Tag, Correspondent, DocumentType, StoragePath
3. Nota: i permessi di Paperless sono **a livello di oggetto** — condividi con
   il nuovo utente sia i documenti esistenti *sia* gli oggetti della tassonomia
   (azione bulk "Permessi")
4. Genera il token per *quell'utente* e usa quello

Anche nello scenario peggiore, nulla può essere modificato o cancellato.

## Opzione A — Uso locale con Claude Desktop (stdio)

Il server gira sulla tua macchina e raggiunge Paperless sulla LAN.

```bash
git clone https://github.com/tiziano5/paperless-mcp.git
cd paperless-mcp
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # macOS/Linux
# .venv\Scripts\pip install -r requirements.txt  (Windows)
cp .env.example .env                            # poi inserisci il token
```

Configura Claude Desktop (`claude_desktop_config.json`, da *Impostazioni → Sviluppatore*):

```json
{
  "mcpServers": {
    "paperless": {
      "command": "C:\\percorso\\paperless-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\percorso\\paperless-mcp\\server.py"],
      "env": {
        "PAPERLESS_URL": "http://<IP-DEL-NAS>:8010",
        "PAPERLESS_TOKEN": "il-tuo-token"
      }
    }
  }
}
```

Riavvia Claude Desktop: nell'icona degli strumenti compariranno i 4 tool.

## Opzione B — Docker (trasporto HTTP)

Il server gira come container accanto a Paperless e resta sempre attivo.

```bash
cp .env.example .env    # inserisci PAPERLESS_URL e PAPERLESS_TOKEN
docker compose up -d --build
```

Il server ascolta su `http://<host>:8802/mcp` (endpoint streamable HTTP).

Per collegarlo a **claude.ai** come connettore personalizzato serve un URL
**pubblico HTTPS** — ad esempio un hostname Cloudflare Tunnel che punta alla
porta 8802. ⚠️ Se l'hostname sta dietro un layer di identità con login
interattivo (pagina OTP), la connessione da claude.ai verrà bloccata: usa una
policy di bypass o un service token per quell'hostname, oppure tieni
l'endpoint solo su LAN/VPN e usa l'Opzione A.

## Test rapido da terminale

```bash
python server.py --help                          # verifica CLI
python server.py                                 # stdio (Ctrl+C per uscire)
python server.py --transport http --port 8802    # HTTP
```

Test interattivo degli strumenti: `npx @modelcontextprotocol/inspector python server.py`

## Esempi d'uso in chat

- *"Cerca le bollette del 2025"* → `paperless_search_documents(query="bolletta", correspondent="Enel", created_after="2025-01-01")`
- *"Leggimi il documento 431"* → `paperless_get_document(document_id=431)`
- *"Che tag uso di più?"* → `paperless_list_taxonomy(kind="tags")`

## Troubleshooting

| Sintomo | Causa probabile | Rimedio |
|---|---|---|
| `authentication failed (401)` | Token errato/revocato | Rigenera il token, aggiorna `.env` |
| `permission denied (403)` | Utente senza permessi view | Assegna i permessi in Paperless |
| `cannot reach Paperless-ngx` | URL errato, container fermo, firewall | Verifica `PAPERLESS_URL` e che Paperless risponda |
| Nomi tag/corrispondenti non trovati | Nome diverso, o oggetto non condiviso con l'utente | Usa `paperless_list_taxonomy`; controlla i permessi a livello di oggetto |

## Roadmap (v2)

- Upload documenti (`post_document`) con conferma esplicita
- Gestione note e custom fields
- Strumento "scadenze" basato sulle date dei documenti

---

**Portfolio project by [Tiziano Coco](https://github.com/tiziano5)** — built following
Anthropic's MCP best practices (FastMCP, Pydantic, read-only annotations, actionable errors). · MIT License
