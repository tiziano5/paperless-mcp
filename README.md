# paperless-mcp 📄🔌

Server **MCP (Model Context Protocol)** che collega Claude al tuo archivio
documentale **Paperless-ngx** self-hosted. Versione 1: **sola lettura** by design.

Una volta collegato, puoi chiedere a Claude cose come:

> "Trova l'ultima bolletta Enel e dimmi quanto ho pagato"
> "Quali documenti di Centuripe ho archiviato nel 2025?"
> "Quanti documenti ho in inbox? Com'è organizzato l'archivio?"

## Architettura

```
Claude (Desktop / claude.ai)
        │  protocollo MCP
        ▼
  paperless-mcp  (questo server, Python + FastMCP)
        │  REST API + token
        ▼
  Paperless-ngx  (NAS, porta 8010)
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

1. Paperless-ngx raggiungibile (es. `http://192.168.1.44:8010`)
2. Un **token API**. In Paperless: menu profilo (in alto a destra) →
   *API Auth Token* → copia il token.
3. Python 3.11+ (per uso locale) **oppure** Docker (per deploy sul NAS).

### 🔒 Sicurezza (consigliato)

Il token eredita i permessi dell'utente. Per un'integrazione in sola lettura:

1. In Paperless crea un utente dedicato, es. `claude-readonly`
2. Assegnagli solo i permessi **view** su Document, Tag, Correspondent,
   DocumentType, StoragePath
3. Genera il token per *quell'utente* e usa quello qui

Così, anche in caso di problemi, nessuno può modificare o cancellare nulla.

## Opzione A — Uso locale con Claude Desktop (stdio)

Ideale per iniziare: il server gira sulla tua macchina (es. la VM Windows)
e raggiunge Paperless sulla LAN.

```bash
git clone <questa-cartella> paperless-mcp
cd paperless-mcp
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt    # Windows
cp .env.example .env                              # poi inserisci il token
```

Configura Claude Desktop (`claude_desktop_config.json`, da
*Impostazioni → Sviluppatore*):

```json
{
  "mcpServers": {
    "paperless": {
      "command": "C:\\percorso\\paperless-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\percorso\\paperless-mcp\\server.py"],
      "env": {
        "PAPERLESS_URL": "http://192.168.1.44:8010",
        "PAPERLESS_TOKEN": "il-tuo-token"
      }
    }
  }
}
```

Riavvia Claude Desktop: nell'icona degli strumenti compariranno i 4 tool.

## Opzione B — Docker sul NAS (trasporto HTTP)

Il server gira accanto a Paperless come container e resta sempre attivo.

```bash
# sul NAS, nella cartella del progetto
cp .env.example .env    # inserisci PAPERLESS_URL e PAPERLESS_TOKEN
docker compose up -d --build
```

Il server ascolta su `http://IP-NAS:8802/mcp` (endpoint streamable HTTP).

Per collegarlo a **claude.ai** come connettore personalizzato serve un URL
**pubblico HTTPS**: puoi riusare la tua infrastruttura Cloudflare Tunnel
(come per `spese.mt4-expert-advisor.com`) puntando un hostname alla porta
8802. ⚠️ Nota: se metti davanti Cloudflare Access/Zero Trust con OTP, la
connessione da claude.ai verrà bloccata dalla pagina di login — serve una
policy di bypass o un service token per quel hostname. In alternativa,
proteggi l'endpoint tenendolo solo su LAN/VPN e usa l'Opzione A.

## Test rapido da terminale

```bash
# con il venv attivo e .env configurato
python server.py --help          # verifica CLI
python server.py                 # avvio stdio (Ctrl+C per uscire)
python server.py --transport http --port 8802   # avvio HTTP
```

Per un test interattivo degli strumenti: `npx @modelcontextprotocol/inspector python server.py`

## Esempi d'uso in chat

- *"Cerca le bollette Enel del 2025"* → `paperless_search_documents(query="bolletta", correspondent="Enel", created_after="2025-01-01")`
- *"Leggimi il documento 431"* → `paperless_get_document(document_id=431)`
- *"Che tag uso di più?"* → `paperless_list_taxonomy(kind="tags")`

## Troubleshooting

| Sintomo | Causa probabile | Rimedio |
|---|---|---|
| `authentication failed (401)` | Token errato/revocato | Rigenera il token, aggiorna `.env` |
| `permission denied (403)` | Utente senza permessi view | Assegna i permessi in Paperless |
| `cannot reach Paperless-ngx` | URL errato, container fermo, firewall | Verifica `PAPERLESS_URL` e che Paperless risponda |
| Nomi tag non trovati | Nome diverso da quello reale | Usa `paperless_list_taxonomy` per i nomi esatti |

## Roadmap (v2)

- Upload documenti (`post_document`) con conferma esplicita
- Gestione note e custom fields
- Strumento "scadenze" basato sulle date dei documenti

---

Progetto portfolio di **Tiziano Coco** — costruito seguendo le best practice
MCP di Anthropic (FastMCP, Pydantic, annotazioni read-only, errori actionable).
