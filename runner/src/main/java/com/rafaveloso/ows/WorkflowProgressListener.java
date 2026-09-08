package com.rafaveloso.ows;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.serverlessworkflow.impl.lifecycle.TaskCompletedEvent;
import io.serverlessworkflow.impl.lifecycle.WorkflowExecutionListener;

/** Opt-in progress records on stderr; stdout remains the final workflow JSON. */
final class WorkflowProgressListener implements WorkflowExecutionListener {
  private static final ObjectMapper JSON = new ObjectMapper();

  @Override
  public void onTaskCompleted(TaskCompletedEvent event) {
    var task = event.taskContext();
    var metadata = task.task().getMetadata();
    if (metadata == null
        || !Boolean.TRUE.equals(metadata.getAdditionalProperties().get("console-progress"))) return;
    try {
      System.err.println("workflow-progress: " + JSON.writeValueAsString(task.rawOutput().asJavaObject()));
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Could not serialize workflow progress", exception);
    }
  }
}
