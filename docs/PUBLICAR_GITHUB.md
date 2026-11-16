# Publicar na conta cleitoncampano

Execute os comandos na pasta `emissor-nfse`, que tem seu próprio Git. O nome previsto é `cleitoncampano/emissor-nfse` e a branch é `main`.

## Autenticar

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth switch --hostname github.com --user cleitoncampano
gh api user --jq .login
gh auth setup-git --hostname github.com
```

Entre com a conta `cleitoncampano` no navegador. Confira se o terceiro comando mostra exatamente esse nome antes de continuar. Não coloque tokens em arquivos do projeto.

## Conferir a versão local

```bash
git status --short
git log -1 --oneline
git remote -v
uv run --locked --group dev pytest -q
uv build --no-sources
```

O primeiro commit foi preparado sem os dados operacionais, certificados, convites ou ambiente virtual. Se fizer alterações depois, revise e registre um novo commit antes de enviar. O teste HTTPS precisa de permissão para abrir uma porta local.

## Criar e enviar

O `origin` local já aponta para `https://github.com/cleitoncampano/emissor-nfse.git`. Crie o repositório vazio e depois envie o commit:

```bash
gh repo create cleitoncampano/emissor-nfse --public --description "Conector MCP de NFS-e Nacional para equipes contábeis no Windows"
git push -u origin main
```

Se o repositório já existir, confira o endereço e envie com `git push -u origin main`. Não crie README ou licença pela interface do GitHub, pois esses arquivos já estão no commit local.

Depois do envio, confira o resultado em **Actions**. Ative também o [relato privado de vulnerabilidades](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository) nas configurações do GitHub, para oferecer o canal citado em `SECURITY.md`.

Antes de entregar ao cliente, valide a instalação em um Windows real e a emissão em homologação. Os testes locais não acessam o governo.
