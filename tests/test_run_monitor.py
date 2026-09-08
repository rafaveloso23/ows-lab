"""Exercise catalog functions using the actual Java runtime and local HTTP mocks."""
import json
import unittest
import test_workflows as existing


class RunMonitorTests(unittest.TestCase):
    run_workflow = existing.WorkflowTests.run_workflow

    def workflow(self, function="hcp-terraform-workspace-run-monitor"):
        return json.dumps({
            "document": {"dsl": "1.0.3", "namespace": "test", "name": "monitor", "version": "1.0.0"},
            "use": {"secrets": ["HCP_TERRAFORM_TOKEN"],
                    "catalogs": {"hcp": {"endpoint": "../catalog"}}},
            "do": [{"execute": {"timeout": {"after": {"seconds": 120}},
                               "call": function + ":1.0.0@hcp",
                               "with": {"workspace_id": "${ .workspace_id }", "max_polls": "${ .max_polls // 10 }"}}},
                   {"continue": {"set": {"result": "${ . }", "continued": True}}}]})

    def run_case(self, responses, function="hcp-terraform-workspace-run-monitor", **kwargs):
        return self.run_workflow(
            responses, input_data=kwargs.pop("input_data", {"workspace_id": "ws-mock"}),
            workflow_text=self.workflow(function), poll_ms=1, **kwargs)

    def run_provision_case(self, responses):
        workflow = (existing.ROOT / "workflows/provision-azure.yaml").read_text()
        # Shorten only the waits in the copy used by the local mock.
        workflow = workflow.replace("seconds: 10", "milliseconds: 1")
        created = (201, {"data": {"id": "ws-mock", "attributes": {"name": "ws-test"},
                    "relationships": {"project": {"data": {"id": "prj-mock"}}}}}, 0)
        return self.run_workflow(
            [(200, existing.MODULE, 0), (200, {"data": [existing.PROJECT]}, 0),
             (200, {"data": []}, 0), created] + responses,
            input_data={"organization": "mock-org", "project_name": "prj-test",
                        "workspace_name": "ws-test", "module_name": "resource-group",
                        "provider_name": "azurerm"},
            workflow_text=workflow, poll_ms=1)

    @staticmethod
    def workspace(run_id="run-mock", latest=None, no_code=True):
        return 200, {"data": {"id": "ws-mock", "type": "workspaces", "relationships": {
            "current-run": {"data": {"id": run_id} if run_id else None},
            "latest-run": {"data": {"id": latest or run_id} if run_id else None},
            "no-code-module-version": {"data": {"id": "nocodever-mock"} if no_code else None}
        }}}, 0

    @staticmethod
    def run_response(status, *, started=False, destroy=False, plan_only=False, has_changes=True, workspace="ws-mock"):
        return 200, {"data": {"id": "run-mock", "type": "runs", "attributes": {
            "status": status, "is-destroy": destroy, "plan-only": plan_only, "has-changes": has_changes,
            "status-timestamps": {"applying-at": "2026-09-05T10:00:00Z"} if started else {}
        }, "relationships": {"workspace": {"data": {"id": workspace}}}}}, 0

    @staticmethod
    def events(*ids):
        return 200, {"data": [{"id": id, "type": "run-events", "attributes": {
            "action": "some_future_action", "created-at": "2026-09-05T10:00:00Z", "description": "Event"
        }} for id in ids]}, 0

    @staticmethod
    def resources(*ids, more=False):
        return 200, {"data": [{"id": id, "type": "resources", "attributes": {
            "address": "random_pet." + id}} for id in ids],
            "links": {"next": "https://untrusted.example/ignored" if more else None}}, 0

    @staticmethod
    def queued():
        return 201, {"data": {"id": "run-destroy", "type": "runs", "attributes": {
            "status": "pending", "is-destroy": True},
            "relationships": {"workspace": {"data": {"id": "ws-mock"}}}}}, 0

    def test_poll_pins_run_and_deduplicates_events(self):
        result, requests = self.run_case([
            self.workspace(None), self.workspace(), self.run_response("planning"), self.events("re-1"),
            self.run_response("planning"), self.events("re-1"), self.run_response("applying", started=True),
            self.events("re-1", "re-2"), self.run_response("applied", started=True), self.events("re-1", "re-2", "re-3")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"continued": True, "result": {
            "success": True, "workspace_id": "ws-mock", "run_id": "run-mock", "status": "applied"}})
        self.assertEqual([r[1] for r in requests].count("/api/v2/workspaces/ws-mock"), 2)
        lines = [json.loads(line.split(": ", 1)[1]) for line in result.stderr.splitlines()
                 if line.startswith("workflow-progress: ")]
        self.assertEqual([line["status"] for line in lines], ["planning", "applying", "applied"])
        self.assertEqual([e["id"] for line in lines for e in line["events"]], ["re-1", "re-2", "re-3"])

    def test_failure_with_resources_queues_separate_destroy_function(self):
        result, requests = self.run_provision_case([self.workspace(), self.run_response("errored", started=True),
            self.events("re-1"), self.resources("wsr-1", more=True), self.resources("wsr-2"),
            self.queued()])
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertFalse(output["success"])
        self.assertEqual(output["cleanup"]["destroy_run_id"], "run-destroy")
        self.assertEqual(requests[-1][0:2], ("POST", "/api/v2/runs"))
        body = json.loads(requests[-1][3])["data"]
        self.assertEqual(body["relationships"], {"workspace": {"data": {"type": "workspaces", "id": "ws-mock"}}})
        self.assertTrue(body["attributes"]["is-destroy"])
        self.assertTrue(body["attributes"]["auto-apply"])
        self.assertIn("run-mock", body["attributes"]["message"])
        self.assertIn("page%5Bnumber%5D=2", requests[8][1])

    def test_empty_inventory_skips_destroy(self):
        result, requests = self.run_provision_case([self.workspace(), self.run_response("errored", started=True),
                                         self.events(), self.resources()])
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertFalse(output["success"])
        self.assertFalse(output["cleanup"]["destroy_queued"])
        self.assertFalse(any(r[0:2] == ("POST", "/api/v2/runs") for r in requests))

    def test_terminal_failures_with_resources_queue_destroy(self):
        for status in ("errored", "discarded", "canceled", "force_canceled", "policy_soft_failed"):
            with self.subTest(status=status):
                result, requests = self.run_provision_case([self.workspace(), self.run_response(status),
                    self.events(), self.resources("wsr-existing"), self.queued()])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(json.loads(result.stdout)["success"])
                self.assertTrue(json.loads(result.stdout)["cleanup"]["destroy_queued"])

    def test_no_changes_succeeds_but_plan_only_does_not(self):
        for plan_only in (False, True):
            with self.subTest(plan_only=plan_only):
                result, requests = self.run_case([self.workspace(),
                    self.run_response("planned_and_finished", plan_only=plan_only, has_changes=False), self.events()])
                self.assertEqual(result.returncode == 0, not plan_only, result.stderr)
                self.assertEqual(len(requests), 3)

    def test_waiting_and_unknown_states_never_queue_destroy(self):
        for status in ("policy_override", "planned_and_saved", "policy_checked", "unknown_new_status"):
            with self.subTest(status=status):
                result, requests = self.run_case([self.workspace(), self.run_response(status), self.events()],
                                                input_data={"workspace_id": "ws-mock", "max_polls": 2})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(requests), 3)
                self.assertFalse(any(r[0:2] == ("POST", "/api/v2/runs") for r in requests))

    def test_missing_current_run_is_bounded(self):
        result, requests = self.run_case([self.workspace(None)],
                                        input_data={"workspace_id": "ws-mock", "max_polls": 2})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(requests), 2)

    def test_resource_errors_do_not_become_empty_inventory(self):
        for response in ((404, {"errors": []}, 0), (200, {}, 0)):
            with self.subTest(response=response):
                result, requests = self.run_provision_case([self.workspace(), self.run_response("errored", started=True),
                                                 self.events(), response])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(requests), 8)

    def test_queue_destroy_is_independent_and_not_retried(self):
        for status in (201, 422, 503):
            with self.subTest(status=status):
                response = self.queued() if status == 201 else (status, {"errors": []}, 0)
                result, requests = self.run_case([response], "hcp-terraform-workspace-queue-destroy")
                self.assertEqual(result.returncode == 0, status == 201, result.stderr)
                self.assertEqual(len(requests), 1)

    def test_final_workflow_success(self):
        result, requests = self.run_provision_case([self.workspace(), self.run_response("applied"),
                                                   self.events("re-final")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "success": True, "workspace_id": "ws-mock", "run_id": "run-mock", "status": "applied"})
        self.assertEqual(len(requests), 7)
        self.assertEqual([r[0] for r in requests], ["GET", "GET", "GET", "POST", "GET", "GET", "GET"])

    def test_monitor_failure_only_observes(self):
        result, requests = self.run_case([self.workspace(), self.run_response("errored", started=True),
                                         self.events("re-error")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["result"]["success"])
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(r[0] == "GET" for r in requests))

    def test_resources_function_returns_every_page(self):
        result, requests = self.run_case(
            [self.resources("wsr-1", more=True), self.resources("wsr-2")],
            "hcp-terraform-workspace-resources-get")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"]["resource_count"], 2)
        self.assertTrue(all(r[1].startswith("/api/v2/workspaces/ws-mock/resources?") for r in requests))

    def test_final_workflow_queue_failure_is_not_retried(self):
        result, requests = self.run_provision_case([self.workspace(), self.run_response("errored", started=True),
            self.events(), self.resources("wsr-1"), (503, {"errors": []}, 0)])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len([r for r in requests if r[0:2] == ("POST", "/api/v2/runs")]), 1)

    def test_get_retries_transient_failures(self):
        result, requests = self.run_case([(503, {"errors": []}, 0), self.workspace(),
                                         self.run_response("applied"), self.events()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(requests), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
