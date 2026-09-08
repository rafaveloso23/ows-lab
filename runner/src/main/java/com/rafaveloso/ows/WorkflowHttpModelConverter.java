package com.rafaveloso.ows;

import io.serverlessworkflow.api.types.CallHTTP;
import io.serverlessworkflow.api.types.HTTPArguments;
import io.serverlessworkflow.api.types.Headers;
import io.serverlessworkflow.impl.TaskContext;
import io.serverlessworkflow.impl.WorkflowContext;
import io.serverlessworkflow.impl.WorkflowModel;
import io.serverlessworkflow.impl.WorkflowUtils;
import io.serverlessworkflow.impl.WorkflowValueResolver;
import io.serverlessworkflow.impl.executors.http.HttpModelConverter;
import jakarta.ws.rs.client.Entity;
import jakarta.ws.rs.core.MediaType;
import java.util.Map;

/**
 * Creates HTTP entities using the media type declared by the workflow request.
 *
 * <p>The reference HTTP executor resolves headers before invoking the model converter, but the
 * converter API only receives the body model. This adapter resolves the task's header definition
 * with the same runtime resolver, so header expressions are evaluated against the current
 * workflow state instead of being treated as literal YAML values.
 */
final class WorkflowHttpModelConverter implements HttpModelConverter {
  private final WorkflowContext workflow;
  private final TaskContext task;
  private final WorkflowValueResolver<Map<String, Object>> headers;

  WorkflowHttpModelConverter(WorkflowContext workflow, TaskContext task) {
    this.workflow = workflow;
    this.task = task;
    this.headers = buildHeadersResolver(workflow, task);
  }

  @Override
  public Entity<?> toEntity(WorkflowModel model) {
    Object body = model.as(model.objectClass()).orElseThrow();
    Object declaredContentType = findHeader("Content-Type");
    if (declaredContentType == null) {
      return Entity.json(body);
    }
    return Entity.entity(body, MediaType.valueOf(declaredContentType.toString()));
  }

  @Override
  public Class<?> responseType() {
    return Object.class;
  }

  private Object findHeader(String name) {
    return headers.apply(workflow, task, task.input()).entrySet().stream()
        .filter(entry -> name.equalsIgnoreCase(entry.getKey()))
        .map(Map.Entry::getValue)
        .findFirst()
        .orElse(null);
  }

  private static WorkflowValueResolver<Map<String, Object>> buildHeadersResolver(
      WorkflowContext workflow, TaskContext task) {
    if (!(task.task() instanceof CallHTTP httpTask)) {
      throw new IllegalStateException("HTTP model converter requires a CallHTTP task");
    }

    HTTPArguments httpArguments = httpTask.getWith();
    Headers headerDefinition = httpArguments == null ? null : httpArguments.getHeaders();
    if (headerDefinition == null) {
      return (w, t, model) -> Map.of();
    }

    Map<String, ?> literalHeaders =
        headerDefinition.getHTTPHeaders() == null
            ? null
            : headerDefinition.getHTTPHeaders().getAdditionalProperties();
    return WorkflowUtils.buildMapResolver(
        workflow.definition().application(),
        headerDefinition.getRuntimeExpression(),
        literalHeaders);
  }
}
