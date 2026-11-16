# Segurança

Esta versão é experimental. O servidor central é destinado à rede privada do escritório, com HTTPS e chave por pessoa. Cada pessoa tem acesso às empresas cadastradas; os papéis atuais distinguem equipe e administrador, sem restrição por carteira de clientes.

No Windows, execute o servidor na conta que cadastrou as senhas. O Agendador usa uma sessão interativa dessa conta. Os instaladores restringem a pasta de dados e a pasta de convite ao usuário local e ao sistema. Essa proteção não impede um administrador do computador de obter acesso.

Não publique certificados A1, senhas, chaves TLS privadas, convites, arquivos de `dados/`, XML/PDF de notas ou logs com informações fiscais. O `.gitignore` reduz inclusões acidentais, mas não protege um arquivo já rastreado pelo Git ou adicionado com `--force`.

Para uma suspeita de vulnerabilidade, use **Security → Report a vulnerability** no repositório, se a opção estiver habilitada. Não envie detalhes de exploração ou dados sensíveis em uma issue pública. Se a opção privada não estiver disponível, abra uma issue pedindo um canal privado de contato, sem incluir os detalhes.

Inclua versão, sistema operacional e passos de reprodução com dados fictícios. Não há prazo de resposta garantido para este projeto.
