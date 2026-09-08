package com.rafaveloso.ows;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.serverlessworkflow.api.WorkflowReader;
import io.serverlessworkflow.api.types.Workflow;
import io.serverlessworkflow.impl.TaskContext;
import io.serverlessworkflow.impl.WorkflowApplication;
import io.serverlessworkflow.impl.WorkflowContext;
import io.serverlessworkflow.impl.executors.http.HttpConverterResolver;
import io.serverlessworkflow.impl.resources.DefaultResourceLoaderFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

/** Generic adapter for executing an Open Workflow definition. */
public final class OwsRunner {
  private static final ObjectMapper JSON = new ObjectMapper();

  private OwsRunner() {}

  public static void main(String[] args) {
    try {
      Arguments arguments = Arguments.parse(args);
      Workflow workflow = WorkflowReader.readWorkflow(arguments.workflow());
      exposeDeclaredSecrets(workflow);
      Map<String, Object> input = JSON.readValue(Files.readString(arguments.input()), new TypeReference<>() {});
      try (WorkflowApplication application = WorkflowApplication.builder()
          .withListener(new WorkflowProgressListener())
          .withResourceLoaderFactory((app, path) ->
              DefaultResourceLoaderFactory.get().getResourceLoader(
                  app, arguments.workflow().toAbsolutePath().normalize().getParent()))
          .withAdditionalObject(
              HttpConverterResolver.HTTP_MODEL_CONVERTER,
              (workflowContext, taskContext) ->
                  new WorkflowHttpModelConverter(
                      (WorkflowContext) workflowContext, (TaskContext) taskContext))
          .build()) {
        Object output = application.workflowDefinition(workflow).instance(input).start().join().asJavaObject();
        System.out.println(JSON.writeValueAsString(output));
      }
    } catch (Exception exception) {
      System.err.println("ows-runner: " + exception.getMessage());
      System.exit(1);
    }
  }

  private static void exposeDeclaredSecrets(Workflow workflow) {
    if (workflow.getUse() == null || workflow.getUse().getSecrets() == null) return;
    for (String secretName : workflow.getUse().getSecrets()) {
      String value = System.getenv(secretName);
      if (value == null || value.isBlank()) {
        throw new IllegalStateException("required secret environment variable is missing: " + secretName);
      }
      // The Java reference runtime's secret-based bearer policy reads the token field.
      System.setProperty(secretName + ".token", value);
    }
  }

  private record Arguments(Path workflow, Path input) {
    private static Arguments parse(String[] args) {
      int offset = args.length > 0 && args[0].equals("run") ? 1 : 0;
      if (args.length <= offset) throw new IllegalArgumentException("usage: java -jar ows-runner.jar run WORKFLOW --input INPUT_JSON");
      Path workflow = Path.of(args[offset]);
      Path input = null;
      for (int index = offset + 1; index < args.length; index++) {
        if (args[index].equals("--input") && index + 1 < args.length) input = Path.of(args[++index]);
        else throw new IllegalArgumentException("unknown argument: " + args[index]);
      }
      if (input == null) throw new IllegalArgumentException("--input INPUT_JSON is required");
      return new Arguments(workflow, input);
    }
  }
}
