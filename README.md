# Receipt Scanner for ChatGPT

**ChatGPT mobile plugin:** the remote MCP implementation is in `receipt_plugin/`.
It adds OAuth sign-in, a persistent account-scoped ledger, receipt preview and
recording tools, alias updates, and expiring Excel download links. See
[PLUGIN_SETUP.md](PLUGIN_SETUP.md) for local tests, deployment, authentication,
packaging, and connection instructions. Hosting and identity-provider setup are
required before it can be used from a phone.

The file-based workflow below remains available independently.

Turn receipt photos into a purchases ledger and product costs. ChatGPT extracts
receipt lines; `cost_engine.py` maps items, performs deterministic calculations,
and exports an Excel workbook. No hosted backend or API key is required for
this file-based workflow.

## Included files

- `instructions.md`: instructions for your ChatGPT Project or custom GPT.
- `cost_engine.py`: standalone Python engine.
- `example_business_config.json`: editable aliases, recipes, overhead and pricing.
- `example_receipt.json`: complete sample purchase, including ounces-to-pounds conversion.
- `tests/test_cost_engine.py`: regression tests for costing, duplicates and workbook output.
- `requirements.txt`: workbook export dependency.

## Run a complete example locally

Requires **Python 3.9 or newer**. From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python cost_engine.py --config example_business_config.json \
  --receipt example_receipt.json --out purchases.xlsx
```

The sample produces five purchase rows and a complete Pork Dumplings cost of
**$4.1125 per serving**, including **$2.75 overhead**. The workbook includes
Purchases, Latest Prices, Menu Costs, Margins and Unmapped Items. Both ends of
ranged pricing appear in Margins.

Recompute without adding another receipt:

```bash
python cost_engine.py --config example_business_config.json \
  --ledger purchases.xlsx --out purchases.xlsx
```

Running the first command again **with `--ledger purchases.xlsx`** rejects the
duplicate and preserves the existing rows. Omitting `--ledger` always starts a
new ledger, so provide it when adding subsequent purchases.

Test an alias or run the regression suite:

```bash
python cost_engine.py --config example_business_config.json --map "GRD PORK 10LB"
python -m unittest discover -s tests -v
```

## Use in ChatGPT

1. Copy `example_business_config.json` to `business_config.json` and edit it for
   your business. No setup wizard is needed or included.
2. Create a ChatGPT Project, paste `instructions.md` into its instructions, and
   add `cost_engine.py` and `business_config.json` as project files. Use a chat
   with Python/file execution available.
3. Attach a receipt photo and say "record this receipt". On the first run, start
   a new ledger; on later runs, attach the latest `purchases.xlsx`.
4. Download and retain the updated workbook after each run. The example JSON
   can also be used to try the workflow before uploading real receipts.

For a custom GPT, use the same instructions and files and enable Python/data
analysis. File and tool availability depends on your ChatGPT environment.

If a connected Drive tool is available, you can use it to retrieve the ledger.
Writing the workbook back depends on that tool's capabilities. The downloadable
workbook is the fallback; this project itself does not implement Drive access,
authentication, automatic synchronization or background ingestion.

## Costing and data rules

- Aliases use word boundaries; the longest matching pattern wins. Unmapped
  purchases remain visible but are excluded from prices.
- Standard mass conversions: g, kg, oz, lb. Volume conversions: ml, l, US fl oz,
  US gal. Count conversions: ea and dozen. Common unit spellings are normalized.
- Matching units can be used directly. Cases, bags and packages are **not**
  automatically converted to weight or individual units. Expand them using a
  verified package size before recording, or use matching recipe units.
- Missing ingredient prices, incompatible units, or nonpositive batch size or
  production mark costs `incomplete`. Total cost, profit and margin are withheld
  in JSON and the workbook; Status and Issues explain what needs fixing.
- Latest prices use the greatest purchase date. On the same date the last ledger
  row wins. This is ledger order, not a reconstructed transaction timestamp.
- Transaction IDs are checked within a supplier. Content fingerprints also check
  merchant, date and receipt line descriptions, quantities, units and totals,
  including older ledger rows with generated IDs. Item order and alias changes
  do not affect fingerprints. Without a transaction ID, identical same-day
  purchases cannot reliably be distinguished; matching content is conservatively
  rejected. OCR changes can evade the check, so inspect extraction carefully.
- Receipt dates must be readable YYYY-MM-DD dates. Quantities must be positive,
  and numeric fields must be finite. Review receipt totals before recording;
  the engine does not allocate tax, discounts or shipping to ingredient costs.
- Updating aliases does not remap historical rows automatically. Correct the
  Purchases rows or reprocess the affected receipts into a fresh ledger.

This standalone folder has no upstream parity test or external pipeline
integration. Its regression tests cover the implementation included here.
