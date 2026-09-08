# OWS Lab — provisionamento Azure no HCP Terraform

Este projeto demonstra como executar um workflow OWS (Open Workflow Specification) que cria e acompanha uma workspace Azure baseada em um módulo **no-code** do HCP Terraform.

O fluxo usa um catálogo local de funções reutilizáveis para consultar/criar o projeto, localizar e associar variable sets, criar a workspace, acompanhar o run e, em caso de falha com recursos criados, enfileirar um destroy.

> **Atenção:** o teste de integração real altera o HCP Terraform. Ele pode criar projeto, configuração de notificação, associações de variable sets, workspace e recursos. Não há rollback das etapas já concluídas. Se o run falhar e houver recursos no inventário, o workflow apenas **enfileira** um destroy; ele não espera o destroy terminar e não exclui a workspace.

## Estrutura do projeto

```text
ows-lab/
├── workflows/
│   ├── hello.yaml                  # workflow mínimo, sem integração externa
│   └── provision-azure.yaml        # workflow principal
├── catalog/                        # funções e composições reutilizáveis
├── examples/
│   └── provision-azure.local.input.json
├── runner/                         # runner Java do workflow
├── tests/                          # testes automatizados com HTTP mock
└── run-provision-azure.sh          # atalho para a execução real
```

## Pré-requisitos

- Bash;
- Java 17 ou superior;
- Maven;
- Python 3, somente para os testes automatizados;
- acesso a uma organização do HCP Terraform;
- token de **usuário ou equipe** do HCP Terraform. Token de organização não é aceito pela criação de workspace no-code;
- módulo privado habilitado para no-code e os variable sets necessários já existentes.

Confirme as ferramentas:

```bash
java -version
mvn -version
python3 --version
```

Todos os comandos abaixo devem ser executados na raiz deste projeto:

```bash
cd ows-lab
```

## Teste principal: script Bash

O caminho principal e mais rápido para testar o projeto é:

```bash
bash run-provision-azure.sh
```

Antes de executar, preencha o token no `.env.local` e ajuste o contrato em `examples/provision-azure.local.input.json`, conforme os passos abaixo.

### 1. Compile o runner somente se necessário

O repositório já possui o JAR usado pelo script. Se ele não existir ou se o código Java do runner tiver sido alterado, compile-o:

```bash
mvn -f runner/pom.xml --batch-mode --no-transfer-progress clean package
```

O script espera encontrar o arquivo `runner/target/ows-runner-0.1.0.jar`. Alterações somente nos YAMLs não exigem uma nova compilação, portanto normalmente basta executar o Bash.

### 2. Preencha o `.env.local`

Crie ou edite `.env.local` na raiz de `ows-lab`:

```bash
HCP_TERRAFORM_TOKEN='seu-token-de-usuario-ou-equipe'
```

Essa é a única variável obrigatória. O script exporta automaticamente as variáveis desse arquivo antes de iniciar o runner. Não coloque o token no JSON, nos YAMLs ou no controle de versão.

Opcionalmente, é possível definir outro arquivo de entrada padrão:

```bash
HCP_TERRAFORM_TOKEN='seu-token-de-usuario-ou-equipe'
PROVISION_AZURE_INPUT='examples/meu-teste.json'
```

A prioridade do arquivo de entrada é: argumento informado ao script, `PROVISION_AZURE_INPUT` e, por último, `examples/provision-azure.local.input.json`.

### 3. Preencha o contrato de teste

Edite `examples/provision-azure.local.input.json`:

```json
{
  "organization": "minha-organizacao",
  "project_name": "meu-projeto",
  "notification_url": "https://meu-endpoint.exemplo/webhook",
  "notification_triggers": ["run:completed", "run:errored"],
  "module_name": "storage-service-blueprint",
  "provider_name": "azurerm",
  "variable_set_name": "azure-landing-zone",
  "workspace_name": "azure-dev-01",
  "attributes": {
    "description": "Workspace Azure criada pelo catalogo",
    "auto_apply": true
  },
  "vars": [
    {
      "key": "resource_group_name",
      "value": "rg-dev-01",
      "category": "terraform",
      "hcl": false,
      "sensitive": false
    },
    {
      "key": "storage_account_name",
      "value": "stdevexemplo01",
      "category": "terraform",
      "hcl": false,
      "sensitive": false
    }
  ]
}
```

Contrato dos campos:

| Campo | Obrigatório | O que preencher |
|---|---:|---|
| `organization` | sim | Nome da organização no HCP Terraform. |
| `project_name` | sim | Nome exato do projeto. Se não existir, o workflow o cria. |
| `module_name` | sim | Nome do módulo privado habilitado para no-code. |
| `provider_name` | sim | Provider usado para localizar o módulo e também nome exato do variable set do provider, por exemplo `azurerm`. |
| `workspace_name` | sim | Nome da nova workspace. Aceita letras, números, `_` e `-`. |
| `notification_url` | não | URL do webhook criado somente quando o projeto é criado nesta execução. |
| `notification_triggers` | não | Lista de eventos enviados ao webhook, por exemplo `run:completed` e `run:errored`. |
| `variable_set_name` | não | Nome exato do variable set adicional, como o da landing zone. Se não for encontrado, o fluxo continua sem associá-lo. |
| `attributes` | não | Atributos da workspace. No exemplo, descrição e aplicação automática. |
| `vars` | não | Variáveis entregues ao módulo no-code. Se omitido, usa uma lista vazia. |

Cada item de `vars` aceita:

| Campo | Uso |
|---|---|
| `key` | Nome da variável. |
| `value` | Valor em formato string. |
| `category` | `terraform` ou `env`. |
| `hcl` | `true` se o valor deve ser interpretado como HCL; caso contrário, `false`. |
| `sensitive` | `true` para valor sensível; caso contrário, `false`. |
| `description` | Descrição opcional. |

O `provider_name` precisa coincidir com o provider do módulo privado. A busca de variable sets também exige igualdade exata. Variable set ausente é ignorado; nomes duplicados interrompem o fluxo.

### 4. Execute

Usando o contrato padrão em `examples`:

```bash
bash run-provision-azure.sh
```

Usando outro contrato:

```bash
bash run-provision-azure.sh examples/meu-teste.json
```

Um caminho relativo é resolvido a partir da raiz de `ows-lab`, pois o script muda automaticamente para essa pasta.

Durante a execução, o progresso aparece em `stderr` com o prefixo `workflow-progress:`. Ao terminar, o JSON final é impresso em `stdout`, por exemplo:

```json
{"success":true,"workspace_id":"ws-example","run_id":"run-example","status":"applied"}
```

Em uma falha terminal, confira `success: false` e `cleanup.destroy_queued`. Um resultado de negócio com `success: false` pode ainda terminar com exit code zero; portanto, automações devem validar o campo `success` do JSON.

## Como o workflow funciona

O arquivo `workflows/provision-azure.yaml` declara o contrato de entrada, o secret `HCP_TERRAFORM_TOKEN` e cinco aliases de catálogo. Ele executa três etapas principais:

1. `provision` chama a composição `azurerm:1.0.0@provisioning`;
2. `monitor` acompanha o run atual da workspace até um estado terminal;
3. `result` devolve o resultado e, se o run falhar com recursos registrados, enfileira um destroy.

Dentro de `provision`, a ordem é:

1. localizar o módulo privado e obter seu ID no-code;
2. buscar o projeto e criá-lo caso não exista;
3. criar a notificação somente se o projeto tiver acabado de ser criado;
4. localizar e associar o variable set adicional ao projeto;
5. localizar e associar o variable set cujo nome é `provider_name`;
6. criar a workspace no-code dentro do projeto.

O monitor fixa o primeiro `current-run` encontrado e consulta o run e seus eventos a cada 10 segundos. `applied` é sucesso. Estados terminais como `errored`, `canceled`, `force_canceled`, `discarded` e `policy_soft_failed` são falhas. O limite total do monitoramento no workflow é de uma hora.

## Como o catálogo funciona

O catálogo separa operações HTTP simples de composições com regra de negócio:

```text
workflow
  └── composição de provisionamento
      ├── catálogo de módulos
      ├── catálogo de projetos
      ├── catálogo de variable sets
      └── catálogo base HCP Terraform
```

Os aliases ficam em `use.catalogs` no workflow:

- `hcp`: funções HTTP básicas, monitoramento, inventário e destroy;
- `modules`: consulta ao registry privado e resolução do módulo no-code;
- `projects`: validação/criação de projeto e notificação;
- `variable-sets`: busca e associação de variable sets;
- `provisioning`: composição Azure e montagem do resultado final.

Uma chamada como `hcp-terraform-project-get:1.0.0@hcp` significa:

- função `hcp-terraform-project-get`;
- versão `1.0.0`;
- catálogo com alias `hcp`.

Para catálogos locais, o runner resolve o arquivo no formato:

```text
<endpoint>/main/functions/<nome>/<versao>/function.yaml
```

Por isso as funções ficam dentro de pastas `main/functions`. O carregamento é relativo ao arquivo do workflow, não ao diretório em que o comando foi chamado. Não existe um servidor de catálogo separado: os YAMLs são carregados diretamente do disco.

As funções recebem seus parâmetros pelo bloco `with` e retornam dados para a próxima tarefa. O token é declarado em `use.secrets`, lido do ambiente pelo runner e nunca deve ser passado em `with` ou incluído na saída.

Para detalhes de cada função e de seus contratos, consulte [`catalog/README.md`](catalog/README.md).

## Validação automatizada opcional

Depois do teste principal via Bash, a suíte automatizada pode ser usada para validar os cenários internos sem acessar o HCP Terraform:

```bash
python3 -m unittest discover -s tests -v
```

Os testes usam um servidor HTTP mock local e validam os workflows, o catálogo, retries, erros, monitoramento e cleanup.

Também é possível testar somente o runner com o workflow mínimo:

```bash
java -jar runner/target/ows-runner-0.1.0.jar \
  run workflows/hello.yaml \
  --input tests/fixtures/empty.input.json
```

Resultado esperado:

```json
{"message":"Hello World"}
```

## Problemas comuns

- `Preencha HCP_TERRAFORM_TOKEN`: falta a variável no `.env.local` ou ela está vazia.
- `Arquivo de entrada não encontrado`: confira o caminho do JSON; caminhos relativos partem de `ows-lab`.
- `Compile o runner`: execute o comando Maven da etapa 1.
- módulo no-code não encontrado: confira `organization`, `module_name`, `provider_name` e se o módulo está habilitado para no-code.
- variable set ambíguo: há mais de um resultado com o mesmo nome exato; corrija a duplicidade antes de executar novamente.
- falha depois de criar parte dos objetos: POSTs não têm retry automático e não há rollback geral; confira o HCP Terraform antes de repetir para evitar duplicidade ou conflito.
