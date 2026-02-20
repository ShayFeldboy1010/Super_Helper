# Contributing

Thanks for your interest in contributing to AI Super Man!

## Development Setup

```bash
# Clone and set up virtual environment
git clone https://github.com/your-username/AI_Super_man.git
cd AI_Super_man
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio ruff

# Configure environment
cp .env.example .env
# Fill in your API keys
```

## Code Style

- **Async-first**: All I/O operations use `async/await`. Use `asyncio.gather` for parallel fetches.
- **Type hints**: All function signatures should include parameter types and return types.
- **Pydantic models**: Use Pydantic for data validation and serialization.
- **Error handling**: Services should catch exceptions and return fallback values — never crash the bot.
- **Linting**: We use [Ruff](https://docs.astral.sh/ruff/) with a 120-character line length.

```bash
ruff check app/ tests/
```

## Running Tests

```bash
pytest tests/ -v
```

Tests mock external services (LLM, Supabase, Google APIs) so they run without real credentials.

## Pull Request Process

1. Fork the repo and create a feature branch from `main`
2. Write tests for new functionality
3. Ensure `ruff check` and `pytest` pass
4. Open a PR with a clear description of what changed and why
