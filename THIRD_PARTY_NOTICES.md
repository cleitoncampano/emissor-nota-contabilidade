# Dependências e recursos de terceiros

- O transporte e a assinatura usam [pynfse-nacional](https://github.com/roberto-mello/pynfse-nacional), versão fixada em `pyproject.toml` e `uv.lock`. A distribuição instalada identifica sua licença como AGPL-3.0. O texto integral dessa licença foi incluído em `LICENSE`.
- Os XSD em `src/emissor_nfse/recursos/xsd` correspondem ao leiaute nacional NFS-e 1.01 e arquivos auxiliares. A cópia local removeu `^` e `$` do padrão da série no XSD, conforme a adaptação já presente no projeto. Preserve os comentários e avisos desses arquivos.
- `xmldsig-core-schema.xsd` contém o aviso de copyright e licença de software W3C em seu próprio cabeçalho; esse aviso deve ser preservado.
- As tabelas JSON de municípios, NBS e serviços nacionais são recursos de referência já presentes no projeto. A origem exata e a versão de cada extração ainda precisa ser documentada; não representam cadastros dos clientes do escritório.
- As demais bibliotecas mantêm suas próprias licenças. Os nomes e versões resolvidas estão em `uv.lock`; consulte os avisos das respectivas distribuições.
