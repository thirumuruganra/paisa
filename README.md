# Paisa

```text
 888888ba   .d888888  dP .d88888b   .d888888
 88    `8b d8'    88  88 88.    "' d8'    88
a88aaaa8P' 88aaaaa88a 88 `Y88888b. 88aaaaa88a
 88        88     88  88       `8b 88     88
 88        88     88  88 d8'   .8P 88     88
 dP        88     88  dP  Y88888P  88     88
```

Paisa is a local-first personal finance app for importing HDFC and ICICI PDF bank statements.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Optional semantic categorization boost will also need:

```bash
python -m pip install -e ".[ai]"
```

Create a `.env` file when Ollama is not running on the default local address:

```bash
cat <<'EOF' > .env
OLLAMA_BASE_URL=http://172.26.48.1:11434
EOF
```

Paisa loads `.env` from the current working directory at startup.

## How To Use

### 1. Prepare input files

Place statement PDFs in:

```text
statements/
```

Files in `statements/` are gitignored by default.

### 2. Initialize local database

```bash
paisa init-db
```

This creates the local SQLite database and seeds starter categories like `Food`, `Travel`, `Income`, and `Misc`.

### 3. Import bank statements

```bash
paisa import
```

Import behavior:

- Reads every `*.pdf` from `statements/`
- Detects HDFC or ICICI format
- Prompts for PDF password when needed
- Stores passwords in secure keyring when available
- Skips already imported files by hashing file contents
- Normalizes merchant names
- Categorizes transactions before saving
- Falls back to `Misc` when no confident category exists

Useful flags:

```bash
paisa import --no-prompt
paisa import --debug-pdf
```

### 4. Launch app

```bash
paisa
```

Running `paisa` without subcommands also initializes the database before opening the TUI.

## App Navigation

Main shortcuts:

- `1` Overview
- `2` Expenses
- `3` Ledger
- `4` Categories
- `5` Ask AI
- `Ctrl+K` command palette
- `t` toggle theme
- `q` quit

What each screen does:

- `Overview`: current balance, income this month, expenses this month, rolling 30-day balance chart, recent transactions
- `Expenses`: expense breakdown by category or bank, timeframe filters, investment totals by month
- `Ledger`: search transactions, edit merchant/category, run AI recategorization on visible debit rows with `r`
- `Categories`: add, rename, delete unused categories
- `Ask AI`: ask local finance questions; app generates read-only SQL and answers from your local database

## AI Features

Two features use Ollama:

- `Ledger` AI recategorization for uncertain debit transactions
- `Ask AI` natural-language finance queries

Ollama must be reachable at `OLLAMA_BASE_URL` or default `http://127.0.0.1:11434`.

## Data Location

- Statement source folder: `./statements`
- Default database: platform data directory under `paisa/paisa.db`
- PDF passwords: secure keyring plus local encryption key when secure backend exists

## Full Architecture Notes

See [ARCHITECTURE.md](/home/thiru/Desktop/paisa/ARCHITECTURE.md) for full app workflow, architecture notes, and methodology diagram.
