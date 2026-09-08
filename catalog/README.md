# Catálogo local HCP Terraform

As funções contêm a interação HTTP e as composições reutilizáveis concentram
as decisões de negócio usadas pelos workflows de provisionamento.

```text
catalog/
  main/functions/
    hcp-terraform-project-get/1.0.0/function.yaml
    hcp-terraform-project-create/1.0.0/function.yaml
    hcp-terraform-no-code-workspace-create/1.0.0/function.yaml
  hcp-terraform-projects/main/functions/
    notification-create/1.0.0/function.yaml
    validate/1.0.0/function.yaml
  hcp-terraform-variable-sets/main/functions/
    validate/1.0.0/function.yaml
  hcp-terraform-provisioning/main/functions/
    azurerm/1.0.0/function.yaml
    result/1.0.0/function.yaml
```

## Resolução do módulo no-code

O contrato de `provision-azure` recebe `module_name` no lugar de `no_code_module_id`.
`get:1.0.0@modules`, em `hcp-terraform-modules/main/functions/get`, faz o GET
`/api/v2/organizations/{organization}/registry-modules/private/{organization}/{module_name}/{module_provider}`
e retorna a resposta completa. Declare o catálogo `modules` com endpoint
`../catalog/hcp-terraform-modules` no workflow consumidor.

`get-nocode-module-id:1.0.0@hcp` chama essa função, valida `no-code: true`
e uma única relação no-code, e retorna exclusivamente a string
`.data.relationships["no-code-modules"].data[0].id`.
Ambiguidade ou ausência interrompem o fluxo antes de criar o projeto.
O provisioning `azurerm` recebe `provider_name` obrigatório e usa esse valor
como `module_provider` no GET e como nome do variable set de provider.
Se a busca não encontrar esse variable set, o provisionamento continua sem associá-lo.

Referência: [Get a Module](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/private-registry/modules#get-a-module).

## Consumir as funções

No workflow localizado em `workflows/`:

```yaml
use:
  secrets:
    - HCP_TERRAFORM_TOKEN
  catalogs:
    hcp:
      endpoint: ../catalog
do:
  - get-project:
      call: hcp-terraform-project-get:1.0.0@hcp
      with:
        organization: '${ .organization }'
        project_name: '${ .project_name }'
```

Este trecho deve fazer parte de um workflow com `document`. A função recebe
os valores de `with`; não consulta o `$workflow.input` do chamador. O secret é
um contrato explícito: o workflow consumidor declara `HCP_TERRAFORM_TOKEN` e
o processo fornece essa variável de ambiente. O token não é passado em `with`
nem incluído na saída. Não é necessário declarar uma política `hcpTerraform`.

| Função | Entrada | Saída |
|---|---|---|
| `hcp-terraform-project-get:1.0.0@hcp` | `organization`, `project_name` | `exists`, `project_id`, `project_name` |
| `hcp-terraform-project-create:1.0.0@hcp` | `organization`, `project_name` | `project_id`, `project_name` |
| `notification-create:1.0.0@projects` | `project_id`, `url`, `triggers` | `notification_configuration_id`, `project_id`, `enabled`, `url`, `triggers` |
| `hcp-terraform-variable-set-find:1.0.0@hcp` | `organization`, `variable_set_name` | `found`, `ambiguous`, `match_count`, `variable_set_id`, `variable_set_name` |
| `hcp-terraform-variable-set-apply-to-project:1.0.0@hcp` | `variable_set_id`, `project_id` | `applied`, `variable_set_id`, `project_id` |
| `validate:1.0.0@projects` | `organization`, `project_name`, `notification_url`, `notification_triggers` | `project_id`, `project_name`, `project_created` |
| `validate:1.0.0@variable-sets` | `organization`, `variable_set_name`, `project_id` | `applied`, `found`, `variable_set_id`, `variable_set_name`, `project_id`, `reason` |
| `azurerm:1.0.0@provisioning` | Dados do provisionamento Azure | Workspace criada e IDs das associações |
| `result:1.0.0@provisioning` | `workspace_id`, `monitor` | Resultado final e, em falha com recursos, dados do destroy |

As entradas das funções de projeto e variable set são strings. Na consulta sem correspondência, `exists` é `false`
e os demais campos são `null`. Quando há projeto, o nome vem da resposta HCP.
GET mantém até três retries para os status transitórios já definidos. POST
não tem retry automático. As funções retornam ao chamador; não usam `then: end`.

A busca de variable sets usa o parâmetro `q`, mas exige igualdade exata do nome
na resposta. Zero correspondências retornam `applied: false, reason: not_found`;
nomes duplicados retornam `applied: false, reason: ambiguous`. A associação
envia um único projeto por chamada e espera
HTTP 204; seu POST também não é repetido automaticamente.

## Criar workspace de módulo no-code

A função `hcp-terraform-no-code-workspace-create:1.0.0@hcp` recebe
`no_code_module_id`, `workspace_name` e `project_id` (strings), além dos opcionais
`attributes` (objeto) e `vars` (lista de objetos de atributos de variáveis).
Retorna `workspace_id`, `workspace_name`, `project_id` e
`configuration_version_id` (null quando ausente na resposta).

Exemplo de tarefa no workflow consumidor, com o catálogo e secret acima:

```yaml
- create-workspace:
    call: hcp-terraform-no-code-workspace-create:1.0.0@hcp
    with:
      no_code_module_id: '${ .no_code_module_id }'
      workspace_name: '${ .workspace_name }'
      project_id: '${ .project_id }'
      attributes:
        description: Workspace criado pelo catálogo
        auto_apply: false
      vars:
        - key: region
          value: us-east-1
          category: terraform
          hcl: false
          sensitive: false
```

`attributes` aceita os campos do endpoint, como `description`, `auto_apply`,
`agent-pool-id`, `source-name`, `source-url` e
`terraform-version`. O nome é sempre definido por `workspace_name`.
Os campos `execution-mode` e `setting-overwrites` são omitidos mesmo quando informados.
Cada variável aceita `key`, `value` (string), `description`, `category`
(`terraform` ou `env`), `hcl` e `sensitive`. Sem `vars`, é enviada uma lista vazia;
sem `attributes`, somente o nome é enviado, preservando os defaults da API.
O consumidor deve validar as entradas e definir seu timeout total.

O módulo deve estar habilitado para no-code. Use token de usuário ou equipe;
tokens de organização não são aceitos. A função faz um único POST, propaga
falhas HTTP e retorna ao chamador, sem retry automático.

Referência: [Create a no-code module workspace](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/no-code-provisioning#create-a-no-code-module-workspace).

## Composição AzureRM

`azurerm:1.0.0@provisioning` combina functions reutilizáveis dos catálogos de domínio. O consumidor
pronto está em `workflows/provision-azure.yaml`, com entrada de exemplo em
`examples/provision-azure.input.json`.

Entradas obrigatórias: `organization`, `project_name`,
`module_name`, `provider_name` e `workspace_name`. `variable_set_name`, `attributes` e `vars` são opcionais e
seguem o contrato de criação de workspace acima. O consumidor valida as
entradas e limita a etapa de provisionamento a 120 segundos; depois acompanha
o run por até uma hora e decide a limpeza em caso de falha.

A composição resolve primeiro o módulo e depois executa `validate-project`,
`validate-variable-set`, `validate-provider-variable-set` e `create-workspace`.
`validate-project` reúne a busca, a decisão de existência, a criação e a configuração
de notificações. `notification_url` e `notification_triggers` são definidos pelo
consumidor e usados somente quando o projeto é criado nesta execução; projetos
existentes não recebem uma nova configuração. A configuração criada é do tipo
`generic`, fica habilitada e usa o nome `Project notifications`. A mesma
composição `validate:1.0.0@variable-sets` é chamada para
`variable_set_name` e para `provider_name`; ela reúne busca exata, validação de
ambiguidade e associação ao projeto. Por fim, o workspace é criado a partir do
módulo no-code existente. Os dois variable sets são associados ao projeto.
A workspace é associada ao `project_id` retornado pela busca ou criação do
projeto desta execução, independentemente dos nomes ou da existência dos variable sets.
Se `variable_set_name` ou `provider_name` estiver ausente ou vazio, a busca
correspondente é pulada. Ambos representam nomes exatos de variable sets.
Os JSONs de exemplo omitem `execution-mode` e definem `auto_apply: true`.
Nenhuma criação ou associação tem retry automático.

Se um variable set não for encontrado, a composição pula sua associação e
continua até criar o workspace. Nomes duplicados continuam causando falha
antes da criação do workspace; erros HTTP também são propagados. Alterações anteriores
(como criação do projeto ou primeira associação) permanecem; não há rollback.
O SDK atual pode apresentar falhas internas como erro de timeout quando a
tarefa tem timeout configurado; a interrupção é preservada.

A saída da função `azurerm-provision` contém `workspace_id`, `workspace_name`, `project_id`,
`configuration_version_id`, `project_created` e `variable_set_ids` (landing
zone, seguido de AzureRM, apenas os encontrados e associados). Não inclui token nem valores das variáveis.
O workflow final transforma essa saída no resultado de sucesso/falha descrito
em "Provisionamento acompanhado".

Para testar, substitua os identificadores e variáveis do arquivo de exemplo,
forneça `HCP_TERRAFORM_TOKEN` pelo ambiente e execute na raiz do exemplo:

```bash
java -jar runner/target/ows-runner-0.1.0.jar run workflows/provision-azure.yaml --input examples/provision-azure.input.json
```

Outros consumidores podem chamar a composição declarando o catálogo com o
alias `hcp` e o secret `HCP_TERRAFORM_TOKEN`, e passando as entradas em `with`.
A composição usa esses parâmetros, sem depender do `$workflow.input` original.

## Particularidades verificadas do SDK 7.32.0.Final

- O resolvedor de catálogo acrescenta `main/functions/<nome>/<versão>/function.yaml`
  ao endpoint, mesmo quando é local. A pasta `main` acomoda esse comportamento;
  não é uma exigência geral da especificação para catálogos locais.
- O runner configura o carregador de recursos do SDK com a pasta do arquivo
  de workflow. Assim, `../catalog` funciona independentemente do diretório do
  terminal. Não há serviço HTTP adicional nem inclusão textual de YAML.
- Uma função com `try` diretamente na raiz falha ao carregar com
  `catchTransitionBuilder` nulo. GET envolve o `try` em `do` para usar a
  inicialização de tarefas internas do SDK.

Ao atualizar o SDK, revalidar esses comportamentos antes de simplificar a
estrutura. Novas versões das funções podem coexistir em novas pastas, com
atualização explícita da referência `call` nos consumidores.

## Build e testes

Na raiz do exemplo:

```bash
mvn -f runner/pom.xml --batch-mode --no-transfer-progress clean package
python3 -m unittest discover -s tests -v
```

Recompile uma vez para incorporar a resolução dos arquivos relativos. Mudanças posteriores
nos YAMLs não exigem recompilar o JAR; distribua `catalog/` junto a `workflows/`.

Os mocks copiam o catálogo para uma pasta temporária, substituem o host HCP
por localhost e executam o JAR a partir de outro diretório. Também verificam
reutilização por um segundo consumidor e falha quando a versão não existe.

Referência: [catálogos na especificação](https://github.com/open-workflow-specification/specification/blob/main/dsl.md#catalogs).


## Execução local do provisionamento Azure

Preencha `../.env.local` (na raiz do exemplo) com `HCP_TERRAFORM_TOKEN`.
O arquivo existente é preservado. `PROVISION_AZURE_INPUT` aponta por padrão
para `examples/provision-azure.local.input.json`; preencha nesse JSON a
organização, projeto, módulo no-code, workspace e variáveis.
Ambos os arquivos locais são ignorados pelo Git.

Na raiz do exemplo, com Java 17 e Maven:

```bash
mvn -f runner/pom.xml --batch-mode --no-transfer-progress clean package
bash run-provision-azure.sh
```

O script carrega o `.env.local` automaticamente. Para apontar outro JSON:

```bash
bash run-provision-azure.sh /caminho/entrada.json
```

O caminho relativo da entrada é resolvido a partir da raiz do exemplo.
A execução altera o HCP Terraform e cria um workspace; não há rollback.

## Modo de execução herdado do projeto

A criação do workspace no-code omite completamente `execution-mode` e
`setting-overwrites` de `data.attributes`, inclusive quando fornecidos na entrada.
O projeto continua associado em `data.relationships.project`. Não há PATCH posterior.

## Provisionamento acompanhado

O workflow final `workflows/provision-azure.yaml` possui três etapas principais:

1. `provision`: chama `azurerm:1.0.0@provisioning`.
2. `monitor`: chama `hcp-terraform-workspace-run-monitor`.
3. `result`: chama `result:1.0.0@provisioning`. Essa function consulta os
   recursos em caso de falha, chama `queue destroy` quando necessário e monta
   a saída final de sucesso ou falha.

As decisões do resultado, polling, paginação, retries HTTP e validação das
respostas ficam nas functions reutilizáveis. O monitor só consulta; a function
de destroy faz um único POST, sem retry automático.

| Função (`:1.0.0@hcp`) | Entrada | Resultado |
|---|---|---|
| `hcp-terraform-workspace-current-run-get` | `workspace_id` | `run_id` do relacionamento `current-run`, ou null enquanto ausente |
| `hcp-terraform-run-get` | `run_id` | Status, workspace, flags e timestamps do run |
| `hcp-terraform-run-events-get` | `run_id` | Eventos com ID, ação, data e descrição |
| `hcp-terraform-workspace-run-monitor` | `workspace_id`, `max_polls` opcional (360) | Resultado terminal com `success`, `workspace_id`, `run_id`, `status` |
| `hcp-terraform-workspace-resources-get` | `workspace_id` | Todas as páginas de `resources`, `resource_count`, `has_resources` |
| `hcp-terraform-workspace-queue-destroy` | `workspace_id`, `message` opcional | `destroy_queued`, `destroy_run_id`, `workspace_id`, `status` |

O monitor aguarda o `current-run` aparecer e fixa esse ID. Depois consulta
`GET /runs/:run_id` e `GET /runs/:run_id/run-events` a cada 10 segundos.
O limite de consultas inclui a descoberta do current run. O consumidor final
limita o monitoramento a uma hora. Timeout, status desconhecido e erro HTTP
interrompem o acompanhamento e não são classificados como falha terminal do run.

Estados tratados conforme a [API de runs](https://developer.hashicorp.com/terraform/enterprise/api-docs/run#run-states):

- Sucesso: `applied`; ou `planned_and_finished` quando não for plan-only e
  `has-changes` for false.
- Falha terminal: `errored`, `canceled`, `force_canceled`, `discarded`,
  `policy_soft_failed`.
- Espera: `pending`, `fetching`, `fetching_completed`, `pre_plan_running`,
  `pre_plan_completed`, `queuing`, `plan_queued`, `planning`, `planned`,
  `cost_estimating`, `cost_estimated`, `policy_checking`, `policy_override`,
  `policy_checked`, `confirmed`, `post_plan_running`, `post_plan_completed`,
  `planned_and_saved`, `apply_queued`, `applying`.

Eventos são registros de timeline; a conclusão é determinada pelo status do run.
O [SDK oficial](https://github.com/hashicorp/go-tfe/blob/main/run_event.go) expõe
`action` como string, sem enum fechado, e informa que a listagem de eventos não
é paginada. A função aceita novas ações e o monitor elimina duplicatas por ID.
O runner imprime mudanças e novos eventos em stderr, prefixados por
`workflow-progress:`. Stdout contém apenas o JSON final. A metadata
`console-progress: true` habilita essa saída para tarefas que projetam os dados
que devem aparecer no console; não deve ser usada com dados secretos.

A [listagem de recursos](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/workspace-resources)
consulta o inventário da workspace, incluindo todas as páginas. Uma resposta
404 ou malformada é erro, não inventário vazio. A decisão usa os recursos
observados nessa consulta; a API não comprova quais foram criados pelo run nem
recursos que ainda não foram registrados no state/inventário.

O [queue destroy](https://developer.hashicorp.com/terraform/enterprise/api-docs/run#create-a-run)
usa `POST /runs`, `is-destroy: true`, `auto-apply: true` e o relacionamento da
workspace. Ele destrói os recursos gerenciados, sem excluir a workspace.
`destroy_queued: true` confirma apenas o enfileiramento. O workflow retorna a
falha original com esse ID; não acompanha a execução do destroy. Falha/timeout
no POST não deve causar nova tentativa automática, pois o run pode ter sido criado.
A integração real com HCP ainda precisa ser validada; os testes usam HTTP mock local.

Exemplo de resultado de sucesso:

```json
{"success": true, "workspace_id": "ws-example", "run_id": "run-example", "status": "applied"}
```

Em falha, `success` é false e `cleanup.destroy_queued` indica se houve destroy.
Esse é um resultado de negócio: o consumidor deve verificar `success`; um
resultado false tratado pelo workflow não gera automaticamente exit code diferente de zero.

Também existem consumidores isolados `workflows/monitor-workspace-run.yaml` e
`workflows/queue-workspace-destroy.yaml`, ambos recebendo `workspace_id` no JSON
passado ao runner. O monitor isolado nunca dispara destroy.

No SDK Java fixado, as condições de `switch` precisam ser mutuamente exclusivas:
a implementação usa um mapa sem garantir a ordem declarada dos casos.
