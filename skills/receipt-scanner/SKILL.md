---
name: receipt-scanner
description: Record receipt photos and supplier invoices in the connected Receipt Scanner ledger, inspect product costs and margins, correct item aliases, and export purchases. Use for this plugin's ledger, not general bookkeeping advice.
---

Use the Receipt Scanner MCP tools for persistence and all cost calculations.
The server resolves the signed-in account; never choose an account ID yourself.

Read `get_business` before starting. If not configured, gather the user's aliases,
recipe quantities and units, batch yield, overhead, production and pricing; call
`configure_business`. Use actual business data. Demo data requires an explicit
request. For changes, use the current revision and preserve unrelated settings.

When asked to record a photo, extract merchant, YYYY-MM-DD date, printed receipt
ID (null if absent), and every item's exact raw description, positive quantity,
unit, unit price if readable, and line total. Include subtotal, tax and total.
Ask about unreadable required values. Reconcile line totals to subtotal and
subtotal plus tax to total before submitting. Resolve discounts and other
adjustments with the user; the current schema does not model separate fees,
negative return lines, or tax allocation. Never silently discard them.

Preview with `preview_receipt`. Explain unmapped items or unit errors. Cases and
bags require verified package contents before conversion to recipe units; never
infer the contents. If the user requested recording and the extraction is
resolved, call `record_receipt` with the same data. Preview does not save rows.
Do not change IDs or receipt content to evade a duplicate error.

Report merchant, date, total, mapped/unmapped counts and material cost changes.
If a product has `status: incomplete`, explain its missing prices or unit errors;
null cost, profit and margin values are unavailable and must not appear as zero.
Display money to two decimals and per-unit costs to three decimals.

Use `get_costs` for current costs. Use `list_purchases` with `unmapped_only` to
inspect unknown items; page through results when needed. Before `update_alias`,
confirm the mapping reflects the user's intent, then read the current revision.
Alias updates remap historical purchases and can change costs immediately.

Use `export_ledger` when a workbook is requested. Return its `download_url` as a
Markdown download link and mention its five-minute expiry. The signed URL grants
access to that snapshot; never include it in public documents or unrelated tools.
Photo extraction happens in ChatGPT; no image-reading model runs on this server.
