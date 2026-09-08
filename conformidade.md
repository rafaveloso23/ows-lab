# Conformidade com a Open Workflow Specification

## Resumo executivo

Este projeto utiliza a DSL e o Java SDK oficiais da
[Open Workflow Specification](https://open-workflow-specification.org/). Os workflows principais
empregam construções padronizadas, como `document`, `input`, `use`, `do`, `call`, `set`, `switch`,
`try`, `wait`, `raise`, `timeout`, `output` e expressões `jq`.

Entretanto, o estado atual não deve ser descrito como uma implementação Open Workflow pura,
portável e independente de runtime. O projeto contém adaptações deliberadas para o comportamento
do Java SDK 7.32.0.Final. Essas adaptações não criam uma DSL paralela, mas fazem com que a execução
dependa de convenções e extensões do runner local.

A descrição mais precisa é:

> Workflows baseados na Open Workflow Specification, executados por um adaptador do runtime Java.

## Escopo revisado

A análise abrange:

- `.github/workflows/publish-acr.yml`;
- `runner/src/main/java/com/rafaveloso/ows/OwsRunner.java`;
- `runner/src/main/java/com/rafaveloso/ows/WorkflowHttpModelConverter.java`;
- `runner/src/main/java/com/rafaveloso/ows/WorkflowProgressListener.java`;
- `runner/pom.xml`;
- `Dockerfile`;
- workflows em `workflows/`;
- functions em `catalog/`;
- fixtures e testes em `tests/`.

## O que está alinhado à especificação

### DSL dos workflows

Os workflows `workflows/hello.yaml` e `workflows/provision-azure.yaml` usam elementos definidos
pela Open Workflow Specification. Não existe parser próprio, pré-processador de YAML ou sintaxe
proprietária substituindo a DSL oficial.

As chamadas HTTP, os catálogos, os secrets, os controles de fluxo, os retries, os timeouts e as
transformações de entrada e saída são descritos na própria DSL.

### SDK oficial

O runner é construído sobre os artefatos oficiais `io.serverlessworkflow`, na versão
`7.32.0.Final`. A representação e a execução dos workflows são fornecidas pelo Java SDK de
referência, e não por uma engine criada neste projeto.

### Validação dos documentos principais

Durante a revisão, a validação de schema oferecida pelo Java SDK foi executada sobre:

- `workflows/hello.yaml`;
- `workflows/provision-azure.yaml`;
- `tests/fixtures/container-catalog.yaml`.

Os três documentos foram aceitos.

### Testes funcionais

O build Maven foi concluído com sucesso e a suíte de integração executou 40 testes com sucesso.
Isso confirma a consistência do comportamento esperado no runner atual.

Essa evidência não comprova, isoladamente, portabilidade entre runtimes ou conformidade completa
com todas as funcionalidades da especificação.

## Adaptações específicas do runtime

### Estrutura dos catálogos

O projeto organiza functions da seguinte maneira:

```text
catalog/main/functions/<nome>/<versão>/function.yaml
```

A estrutura documentada pelo catálogo oficial é:

```text
catalog/functions/<nome>/<versão>/function.yaml
```

O diretório adicional `main` acomoda o comportamento do resolvedor do Java SDK, que acrescenta
`main/functions/<nome>/<versão>/function.yaml` ao endpoint configurado.

Essa é a principal limitação de portabilidade. Um runtime que siga diretamente a estrutura
documentada pela especificação pode procurar a function em outro caminho e não conseguir resolver
os catálogos atuais.

Referência: [estrutura oficial de catálogos](https://github.com/open-workflow-specification/catalog#structure).

### Conversor HTTP customizado

O runner registra `WorkflowHttpModelConverter` no lugar do conversor HTTP padrão do Java SDK.
Esse componente:

- resolve expressões declaradas nos headers da tarefa;
- lê o header `Content-Type` sem diferenciar maiúsculas e minúsculas;
- serializa o corpo usando o media type declarado;
- usa JSON como fallback quando não há `Content-Type`.

Essa adaptação é necessária no runtime atual para que chamadas ao HCP Terraform respeitem
`application/vnd.api+json`.

Os campos usados nos workflows (`headers`, `body`, `endpoint` e `authentication`) pertencem à
DSL oficial. A customização está na execução desses campos pelo runner, não no formato dos YAMLs.

Consequência: remover esse conversor sem antes validar ou corrigir o comportamento do SDK pode
alterar as requisições HTTP e interromper a integração.

### Injeção de secrets

Os workflows declaram `HCP_TERRAFORM_TOKEN` em `use.secrets` e o utilizam por meio de
`authentication.bearer.use`.

O runner adota uma convenção própria para fornecer esse secret ao Java SDK:

1. lê uma variável de ambiente com o mesmo nome do secret;
2. exige que o valor exista e não seja vazio;
3. publica o valor como system property `<nome-do-secret>.token`.

A declaração e o uso do secret são padronizados. O transporte do valor da variável de ambiente
para uma system property é uma decisão deste runner.

Consequência: em outro runtime, será necessário configurar o provider de secrets oferecido por
esse runtime. Não se deve assumir que a variável `HCP_TERRAFORM_TOKEN` será interpretada da mesma
forma.

### Progresso por metadata

O monitor usa:

```yaml
metadata:
  console-progress: true
```

O listener `WorkflowProgressListener` interpreta essa propriedade e imprime a saída da tarefa em
`stderr`, prefixada por `workflow-progress:`.

O campo `metadata` é permitido pela especificação, mas `console-progress` não possui semântica
padronizada. Outro runtime pode ignorá-lo completamente.

Essa extensão afeta somente observabilidade. Ela não deve alterar o resultado funcional do
workflow. Dados secretos não devem ser colocados na saída de tarefas marcadas com essa metadata.

### Wrappers `do` em functions com `try`

Algumas functions envolvem uma tarefa `try` em uma tarefa `do`. Essa estrutura foi introduzida
porque o Java SDK fixado apresenta falha interna ao carregar determinadas functions com `try`
diretamente na raiz.

O wrapper usa uma construção oficial da DSL e continua sendo um documento válido. Ainda assim,
ele foi deliberadamente acrescentado por causa do comportamento desta implementação.

Ao atualizar ou substituir o runtime, esses wrappers podem ser reavaliados, mas não devem ser
removidos sem testes de equivalência.

### Resolução de recursos relativos

O runner configura o carregador de recursos do SDK usando como base o diretório do arquivo de
workflow. Isso permite que endpoints como `../catalog` sejam resolvidos independentemente do
diretório em que o processo Java foi iniciado.

Trata-se de uma decisão de integração do runner. Ela não inclui textualmente arquivos YAML nem
modifica a definição carregada, mas define como recursos relativos são encontrados.

### CLI e contrato de entrada/saída

O comando:

```text
java -jar ows-runner.jar run WORKFLOW --input INPUT_JSON
```

e a decisão de emitir somente o JSON final em `stdout` são contratos próprios deste executável.
Eles não fazem parte da Open Workflow Specification.

## Versão da especificação

Os workflows declaram:

```yaml
document:
  dsl: "1.0.3"
```

A versão atual publicada no site oficial é 1.0.3. Contudo, a documentação do Java SDK declara a
linha 7.x como conforme à versão 1.0.0 da especificação.

Os documentos 1.0.3 revisados são aceitos pelo validador do SDK 7.32.0.Final, mas isso não comprova
que toda a semântica adicionada ou esclarecida até a versão 1.0.3 esteja implementada pelo runtime.

Referência: [matriz de conformidade do Java SDK](https://github.com/open-workflow-specification/sdk-java#status).

## Limitações do workflow de CI/CD

O workflow `.github/workflows/publish-acr.yml`:

- compila o runner customizado;
- executa testes contra esse mesmo runner;
- constrói a imagem Docker;
- executa smoke tests da imagem;
- publica exatamente a imagem testada no Azure Container Registry.

O processo garante consistência entre o artefato testado e o publicado, mas não mede conformidade
geral com a Open Workflow Specification.

Em particular:

- não executa o Conformance Test Kit (CTK) oficial;
- não ativa explicitamente a validação de schema do Java SDK;
- o smoke test de catálogo percorre apenas o ramo em que o variable set foi omitido;
- esse smoke test não realiza chamada HTTP nem valida a autenticação real;
- os testes HTTP usam mocks locais e substituem o host do HCP Terraform em cópias temporárias das
  functions;
- a integração real completa com o HCP Terraform não é comprovada pelo CI.

Os mocks e a substituição temporária de endpoints são práticas de teste e não modificam os
workflows ou catálogos distribuídos na imagem.

## Classificação das dependências

| Componente | Padronizado | Específico do runner | Impacto de portabilidade |
|---|---:|---:|---|
| Sintaxe dos workflows | Sim | Não | Baixo |
| Tarefas e expressões `jq` | Sim | Não | Baixo |
| Chamadas a functions catalogadas | Sim | Parcialmente | Alto por causa do layout |
| Layout `main/functions` | Não | Sim | Alto |
| HTTP `call` | Sim | Conversor customizado | Médio/alto |
| Declaração de secrets | Sim | Provider customizado | Médio |
| `metadata.console-progress` | Campo sim, semântica não | Sim | Baixo |
| Base de recursos relativos | Não totalmente prescrita | Sim | Médio |
| CLI e formato de console | Não | Sim | Baixo |
| Lógica HCP Terraform | Lógica de aplicação | Não é parte da OWS | Não aplicável |

## O que pode ser afirmado atualmente

É correto afirmar que:

- os workflows são escritos na Open Workflow DSL;
- o projeto usa o Java SDK oficial;
- os documentos principais passam pela validação de schema do SDK quando ela é acionada;
- a lógica principal está declarada nos YAMLs;
- o runner fornece integração, empacotamento, secrets, HTTP e observabilidade;
- a suíte funcional passa no runtime fixado.

Não é correto afirmar, sem qualificações, que:

- não há customizações;
- o runner é apenas um launcher neutro;
- o catálogo funcionará sem alterações em qualquer runtime;
- o projeto comprova conformidade integral com a versão 1.0.3;
- os testes atuais equivalem ao CTK oficial;
- a integração real com o HCP Terraform está integralmente validada pelo CI.

## Recomendações para portabilidade verificável

Para evoluir o projeto em direção a uma implementação independente de runtime:

1. adicionar validação obrigatória de schema ao build e ao carregamento dos workflows;
2. executar o CTK oficial aplicável ao runtime;
3. separar o catálogo portável em `functions/...` da adaptação de resolução necessária ao Java SDK;
4. testar o mesmo workflow e catálogo em pelo menos um segundo runtime compatível;
5. substituir a ponte de system properties por uma abstração explícita de secret provider;
6. verificar se versões mais recentes do Java SDK eliminam a necessidade do conversor HTTP;
7. manter `console-progress` documentado como extensão opcional e não funcional;
8. adicionar um teste da imagem que faça chamadas HTTP contra um mock acessível ao container;
9. registrar, por versão do SDK, quais workarounds continuam necessários;
10. evitar declarar conformidade completa com 1.0.3 até que schema, semântica e CTK estejam cobertos.

## Conclusão

O projeto representa autenticamente a Open Workflow DSL e mantém a lógica de orquestração nos
documentos declarativos. Não foi encontrada uma implementação paralela da linguagem nem uma
transformação oculta dos workflows.

Ainda assim, a execução atual depende de customizações deliberadas para o Java SDK, especialmente
na resolução de catálogos, no tratamento HTTP e no fornecimento de secrets. O estado atual deve ser
tratado como compatível com o runner Java customizado e não como portabilidade irrestrita entre
runtimes Open Workflow.
