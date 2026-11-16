# Architecture diagrams (Mermaid source)

These diagrams render automatically on GitHub. To edit them, paste the code into https://mermaid.live,
or redraw them in draw.io or Excalidraw. The same diagrams appear as editable shapes in the PowerPoint.

## 1. Complete CQRS + Event Sourcing architecture

```mermaid
flowchart LR
  subgraph WRITE["WRITE MODEL (commands)"]
    UI[Browser form<br/>expected_version] --> V[Django view<br/>Pydantic v2 validation]
    V --> CH[Command handler<br/>BankingApplication._execute]
    CH -->|load: snapshot + delta| AGG[Aggregate root<br/>BankAccount]
    AGG -->|new immutable events| CH
    CH -->|atomic append| ES[(Event store<br/>PostgreSQL ledger_events<br/>UNIQUE stream+version)]
    ES --- SS[(Snapshot store<br/>every N events)]
  end
  subgraph READ["READ MODEL (queries)"]
    ES -->|notification log<br/>ordered positions| P[Projector worker<br/>checkpoint in same tx]
    P --> RDB[(Read DB ledger_read<br/>AccountSummary, LedgerEntry,<br/>EscrowHold, DailyBankStats)]
    RDB --> Q[Dashboard / statements<br/>simple SELECT]
    P --> OB[(Webhook outbox)]
    OB -->|HMAC-signed POST| WH[Receivers: /webhooks/receive demo,<br/>or any WEBHOOK_URLS target e.g. n8n]
  end
  subgraph AUDIT["AI AUDIT (verified)"]
    ES --> EV[Evidence pack<br/>left-fold]
    EV --> LLM[LLM: Ollama / Gemini]
    LLM --> VER{Deterministic verifier<br/>7 checks}
    VER -->|rejected: feedback| LLM
    VER -->|passed| REP[Verified report]
    VER -->|2 rejections| DET[Deterministic template]
  end
```

## 2. Aggregate reconstruction: snapshot + delta replay

```mermaid
flowchart LR
  v1[v1 Opened] --> v2[v2] --> v3[v3] --> v4[v4] --> v5[(v5 SNAPSHOT)]
  v5 --> v6[v6] --> v7[v7] --> v8[v8] --> v9[v9] --> v10[(v10 SNAPSHOT)]
  v10 --> v11[v11 delta] --> v12[v12 delta] --> v13[v13 delta]
  classDef snap fill:#0f9d8a,color:#fff
  classDef delta fill:#fff1e8,stroke:#c2410c
  class v5,v10 snap
  class v11,v12,v13 delta
```
To rebuild v13, the repository loads snapshot v10 (1 read) and replays only v11 to v13, instead of all 13 events.

## 3. Sequence: one deposit command, end to end

```mermaid
sequenceDiagram
  actor U as Clerk (browser)
  participant W as Django view
  participant A as BankingApplication
  participant G as BankAccount
  participant E as Event store
  participant P as Projector
  participant R as Read DB
  U->>W: POST deposit 50 TND, expected_version=12
  W->>W: Pydantic v2 validation
  W->>A: deposit(id, 50000 millimes, expected_version=12)
  A->>E: load snapshot v10 + events v11..v12
  A->>A: version == 12 ? else ConcurrencyConflict
  A->>G: deposit() checks rules
  G-->>A: pending event FundsDeposited v13
  A->>E: INSERT (stream, 13) - unique key, atomic
  E-->>A: position #99
  A-->>W: EVT-xxxxxxxx-0013
  loop every 0.2 s
    P->>E: select_notifications(start = checkpoint + 1)
  end
  P->>R: update rows + checkpoint=99 (one transaction)
  W->>R: wait until last_version >= 13 (read-your-writes)
  W-->>U: redirect + "read model updated ~200 ms after the write"
```

## References used for these diagrams
* eventsourcing library (v9.5.6): https://github.com/pyeventsourcing/eventsourcing
* documentation: https://eventsourcing.readthedocs.io/ (topics: domain, application, persistence, projection)
* Django persistence package evaluated as an alternative: https://github.com/pyeventsourcing/eventsourcing-django
