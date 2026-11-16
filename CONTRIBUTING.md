# Contribuir

Use Python 3.13 e `uv`:

```bash
uv sync --locked --group dev
uv run --locked --group dev pytest -q
uv build --no-sources
```

Explique o problema e o comportamento esperado no pull request. Inclua testes quando houver mudança de comportamento. Para alterações no Windows, confira também os instaladores no Windows PowerShell 5.1 e o início/parada da tarefa agendada com a mesma conta do cofre.

Os testes usam certificados gerados temporariamente, cofre em memória e governo simulado. Não use certificados, senhas, convites, dados de clientes ou notas reais em código, testes, issues e pull requests. Exemplos e capturas devem usar dados fictícios.

Ao mudar dependências, atualize `pyproject.toml` e `uv.lock` juntos. Preserve os avisos e licenças dos recursos de terceiros. Contribuições seguem a licença do projeto.
