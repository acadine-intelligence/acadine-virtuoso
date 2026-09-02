---
schema: virtuoso/curriculum@0.1
title: Systems foundations
---

# Systems foundations

This public fixture describes two practice items for a systems curriculum.

```virtuoso-practice
{"schema":"virtuoso/practice-item@0.1","id":"systems-idempotence","title":"Explain idempotence","focus":"distributed-systems","prompt":"What makes an operation idempotent?","answer":"Repeating the operation has the same intended effect as applying it once.","hint":"Compare one application with repeated applications.","follow_up":"Give one idempotent API example.","state":"active","historical_due_at":null}
```

```virtuoso-practice
{"schema":"virtuoso/practice-item@0.1","id":"systems-source-truth","title":"Choose one source of truth","focus":"distributed-systems","prompt":"Why should one system own each mutable state?","answer":"One owner prevents conflicting writes and makes reconciliation explicit.","hint":null,"follow_up":null,"state":"active","historical_due_at":"2025-01-10"}
```
