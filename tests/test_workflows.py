"""Integration tests: actual Java runner, local HTTP mock, fake credentials only."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "runner/target/ows-runner-0.1.0.jar"
PROJECT = {"id": "prj-mock", "type": "projects", "attributes": {"name": "prj-test"}}
NOTIFICATION = {"id": "nc-mock", "type": "notification-configurations",
                "attributes": {"enabled": True, "url": "https://example.test/events",
                               "triggers": ["run:completed", "run:errored"]},
                "relationships": {"subscribable": {"data": {"id": "prj-mock",
                                                                "type": "projects"}}}}
MODULE = {"data": {"attributes": {"no-code": True}, "relationships": {
    "no-code-modules": {"data": [{"id": "nocode-mock", "type": "no-code-modules"}]}}}}

VARIABLE_SET = {"id": "varset-mock", "type": "varsets",
                "attributes": {"name": "landing-zone"}}


class WorkflowTests(unittest.TestCase):
    def run_workflow(self, responses, *, input_data=None, timeout_ms=None, secret=True,
                     workflow_text=None, poll_ms=None):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                requests.append((self.command, self.path,
                                 {k.lower(): v for k, v in self.headers.items()}, body))
                index = len(requests) - 1
                status, data, delay = responses[min(index, len(responses) - 1)]
                time.sleep(delay)
                payload = json.dumps(data).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/vnd.api+json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                workflow = (workflow_text if workflow_text is not None else
                            self.ensure_project_workflow())
                # Only transport and, for deadline tests, duration change in this copy.
                if timeout_ms is not None:
                    workflow = workflow.replace("seconds: 120", f"milliseconds: {timeout_ms}")
                    workflow = workflow.replace('"seconds": 120', f'"milliseconds": {timeout_ms}')
                path = Path(directory)
                (path / "workflows").mkdir()
                shutil.copytree(ROOT / "catalog", path / "catalog")
                for function in (path / "catalog").rglob("function.yaml"):
                    text = function.read_text().replace(
                        "https://app.terraform.io", f"http://127.0.0.1:{server.server_port}")
                    if poll_ms is not None:
                        text = text.replace("seconds: 10\n", f"milliseconds: {poll_ms}\n")
                    function.write_text(text)
                (path / "workflows/workflow.yaml").write_text(workflow)
                (path / "input.json").write_text(json.dumps(
                    input_data if input_data is not None else
                    {"organization": "mock-org", "project_name": "prj-test",
                     "notification_url": "https://example.test/events",
                     "notification_triggers": ["run:completed", "run:errored"]}))
                env = dict(os.environ)
                env.pop("HCP_TERRAFORM_TOKEN", None)
                if secret:
                    env["HCP_TERRAFORM_TOKEN"] = "mock-token"
                result = subprocess.run(
                    ["java", "-jar", str(JAR), "run", str(path / "workflows/workflow.yaml"),
                     "--input", str(path / "input.json")],
                    env=env, cwd=path, text=True, capture_output=True, timeout=40)
                self.assertNotIn("mock-token", result.stdout + result.stderr)
                return result, requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def ensure_project_workflow(self):
        return json.dumps({
            "document": {"dsl": "1.0.3", "namespace": "test",
                         "name": "ensure-project", "version": "1.0.0"},
            "input": {"schema": {"format": "json", "document": {"type": "object",
                "required": ["organization", "project_name"]}}},
            "use": {"secrets": ["HCP_TERRAFORM_TOKEN"],
                    "catalogs": {
                        "hcp": {"endpoint": "../catalog"},
                        "projects": {"endpoint": "../catalog/hcp-terraform-projects"}}},
            "do": [{"ensure": {"timeout": {"after": {"seconds": 120}},
                "call": "validate:1.0.0@projects",
                "with": {"organization": "${ .organization }",
                         "project_name": "${ .project_name }",
                         "notification_url": "${ .notification_url }",
                         "notification_triggers": "${ .notification_triggers }"},
                "output": {"as": "${ {project_id: .project_id, project_name: .project_name, created: .project_created} }"}}}]
        })

    def assert_success(self, result, created):
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["project_id"], "prj-mock")
        self.assertEqual(output["project_name"], "prj-test")
        self.assertIs(output["created"], created)

    def variable_set_input(self):
        return {"organization": "mock-org", "variable_set_name": "landing-zone",
                "project_id": "prj-mock"}

    def no_code_workflow(self):
        return json.dumps({
            "document": {"dsl": "1.0.3", "namespace": "test",
                         "name": "no-code-consumer", "version": "1.0.0"},
            "use": {"secrets": ["HCP_TERRAFORM_TOKEN"],
                    "catalogs": {
                        "hcp": {"endpoint": "../catalog"},
                        "projects": {"endpoint": "../catalog/hcp-terraform-projects"},
                        "variable-sets": {"endpoint": "../catalog/hcp-terraform-variable-sets"}}},
            "do": [{"create": {
                "call": "hcp-terraform-no-code-workspace-create:1.0.0@hcp",
                "with": {key: "${ .payload." + key + " }" for key in
                         ("no_code_module_id", "workspace_name", "project_id", "attributes", "vars")}}},
                {"continue": {"set": {"workspace": "${ . }", "continued": True}}}]
        })

    def test_create_no_code_workspace(self):
        for optional in ({}, {
            "attributes": {"description": "Example", "execution-mode": "agent",
                           "setting-overwrites": {"execution-mode": True},
                           "agent-pool-id": "apool-mock", "auto_apply": False,
                           "terraform-version": "~> 1.9", "name": "ignored"},
            "vars": [{"key": "region", "value": "us-east-1", "category": "terraform",
                      "hcl": False, "sensitive": False},
                     {"key": "config", "value": '{region="us-east-1"}',
                      "category": "terraform", "hcl": True, "sensitive": True}]
        }):
            with self.subTest(optional=bool(optional)):
                payload = {"no_code_module_id": "nocode-mock", "workspace_name": "ws-test",
                           "project_id": "prj-mock", **optional}
                response = {"data": {"id": "ws-mock", "type": "workspaces",
                            "attributes": {"name": "ws-test"}, "relationships": {
                                "project": {"data": {"id": "prj-mock"}},
                                "current-configuration-version": {"data": {"id": "cv-mock"}}
                            }}}
                result, requests = self.run_workflow(
                    [(200, response, 0)], input_data={"payload": payload},
                    workflow_text=self.no_code_workflow())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"continued": True, "workspace": {
                    "workspace_id": "ws-mock", "workspace_name": "ws-test",
                    "project_id": "prj-mock", "configuration_version_id": "cv-mock"}})
                self.assertEqual(len(requests), 1)
                method, path, headers, body = requests[0]
                self.assertEqual((method, path),
                                 ("POST", "/api/v2/no-code-modules/nocode-mock/workspaces"))
                self.assertEqual(headers.get("authorization"), "Bearer mock-token")
                self.assertEqual(headers.get("content-type"), "application/vnd.api+json")
                self.assertEqual(json.loads(body), {"data": {
                    "type": "workspaces",
                    "attributes": {**{k: v for k, v in optional.get("attributes", {}).items()
                                      if k not in ("execution-mode", "setting-overwrites")},
                                   "name": "ws-test"},
                    "relationships": {
                        "project": {"data": {"id": "prj-mock", "type": "project"}},
                        "vars": {"data": [{"type": "vars", "attributes": var}
                                          for var in optional.get("vars", [])]}}}})

    def test_no_code_workspace_errors_are_not_retried(self):
        for status in (404, 422, 500):
            with self.subTest(status=status):
                result, requests = self.run_workflow(
                    [(status, {"errors": []}, 0)],
                    input_data={"payload": {"no_code_module_id": "nocode-mock",
                                            "workspace_name": "ws-test", "project_id": "prj-mock"}},
                    workflow_text=self.no_code_workflow())
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([r[0] for r in requests], ["POST"])

    def azure_input(self):
        return {"organization": "mock-org", "project_name": "prj-test",
                "notification_url": "https://example.test/events",
                "notification_triggers": ["run:completed", "run:errored"],
                "variable_set_name": "landing-zone", "provider_name": "azurerm", "module_name": "resource-group",
                "workspace_name": "ws-test", "vars": [{"key": "location",
                "value": "brazilsouth", "category": "terraform"}]}

    def azure_workflow(self):
        # Test the provisioning composition independently; full orchestration is covered in test_run_monitor.
        return (ROOT / "workflows/provision-azure.yaml").read_text().split("\n  - monitor:", 1)[0]

    def catalog_function_workflow(self, function, fields):
        return json.dumps({
            "document": {"dsl": "1.0.3", "namespace": "test",
                         "name": "catalog-composition", "version": "1.0.0"},
            "use": {"secrets": ["HCP_TERRAFORM_TOKEN"],
                    "catalogs": {
                        "hcp": {"endpoint": "../catalog"},
                        "projects": {"endpoint": "../catalog/hcp-terraform-projects"},
                        "variable-sets": {"endpoint": "../catalog/hcp-terraform-variable-sets"}}},
            "do": [{"execute": {"call": function,
                                  "with": {field: "${ ." + field + " }"
                                           for field in fields}}}]
        })

    def test_validate_project_reuses_get_and_create(self):
        workflow = self.catalog_function_workflow(
            "validate:1.0.0@projects", ("organization", "project_name",
                                        "notification_url", "notification_triggers"))
        input_data = {"organization": "mock-org", "project_name": "prj-test",
                      "notification_url": "https://example.test/events",
                      "notification_triggers": ["run:completed", "run:errored"]}
        for exists in (True, False):
            with self.subTest(exists=exists):
                responses = [(200, {"data": [PROJECT] if exists else []}, 0)]
                if not exists:
                    responses.append((201, {"data": PROJECT}, 0))
                    responses.append((201, {"data": NOTIFICATION}, 0))
                result, requests = self.run_workflow(
                    responses, input_data=input_data, workflow_text=workflow)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {
                    "project_id": "prj-mock", "project_name": "prj-test",
                    "project_created": not exists})
                self.assertEqual([request[0] for request in requests],
                                 ["GET"] if exists else ["GET", "POST", "POST"])
                if not exists:
                    method, path, headers, body = requests[-1]
                    self.assertEqual((method, path),
                                     ("POST", "/api/v2/projects/prj-mock/notification-configurations"))
                    self.assertEqual(json.loads(body), {"data": {
                        "type": "notification-configuration", "attributes": {
                            "destination-type": "generic", "enabled": True,
                            "name": "Project notifications",
                            "url": "https://example.test/events",
                            "triggers": ["run:completed", "run:errored"]}}})

    def test_validate_variable_set_reuses_find_and_apply(self):
        workflow = self.catalog_function_workflow(
            "validate:1.0.0@variable-sets",
            ("organization", "variable_set_name", "project_id"))
        cases = (("landing-zone", [VARIABLE_SET], True, "applied"),
                 ("missing", [], False, "not_found"),
                 ("", [], False, "omitted"))
        for name, matches, applied, reason in cases:
            with self.subTest(name=name):
                responses = [] if not name else [(200, {"data": matches}, 0)]
                if applied:
                    responses.append((204, {}, 0))
                result, requests = self.run_workflow(
                    responses or [(500, {}, 0)],
                    input_data={"organization": "mock-org", "variable_set_name": name,
                                "project_id": "prj-mock"}, workflow_text=workflow)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertIs(output["applied"], applied)
                self.assertEqual(output["reason"], reason)
                self.assertEqual(len(requests), len(responses))

    def test_azure_provision(self):
        provider = {**VARIABLE_SET, "id": "varset-azure", "attributes": {"name": "azurerm"}}
        for exists in (True, False):
            with self.subTest(exists=exists):
                responses = [(200, MODULE, 0), (200, {"data": [PROJECT] if exists else []}, 0)]
                if not exists:
                    responses.append((201, {"data": PROJECT}, 0))
                    responses.append((201, {"data": NOTIFICATION}, 0))
                responses += [(200, {"data": [VARIABLE_SET]}, 0), (204, {}, 0),
                              (200, {"data": [provider]}, 0), (204, {}, 0),
                              (200, {"data": {"id": "ws-mock", "attributes": {"name": "ws-test"},
                               "relationships": {"project": {"data": {"id": "prj-mock"}}}}}, 0)]
                result, requests = self.run_workflow(responses,
                    input_data=self.azure_input(), workflow_text=self.azure_workflow())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {
                    "workspace_id": "ws-mock", "workspace_name": "ws-test",
                    "project_id": "prj-mock", "configuration_version_id": None,
                    "project_created": not exists,
                    "variable_set_ids": ["varset-mock", "varset-azure"]})
                self.assertEqual(len(requests), 7 if exists else 9)
                tail = requests[-5:]
                self.assertEqual(requests[0][1],
                    "/api/v2/organizations/mock-org/registry-modules/private/mock-org/resource-group/azurerm")
                self.assertEqual(requests[-1][:2],
                    ("POST", "/api/v2/no-code-modules/nocode-mock/workspaces"))
                self.assertIn("q=landing-zone", tail[0][1])
                self.assertIn("q=azurerm", tail[2][1])
                for index, varset in ((1, "varset-mock"), (3, "varset-azure")):
                    self.assertEqual(tail[index][1],
                                     f"/api/v2/varsets/{varset}/relationships/projects")
                    self.assertEqual(json.loads(tail[index][3]),
                                     {"data": [{"type": "projects", "id": "prj-mock"}]})
                body = json.loads(tail[4][3])["data"]
                self.assertNotIn("setting-overwrites", body["attributes"])
                self.assertNotIn("execution-mode", body["attributes"])
                self.assertEqual(body["relationships"]["project"]["data"]["id"], "prj-mock")
                self.assertEqual(body["relationships"]["vars"]["data"][0]["attributes"],
                                 self.azure_input()["vars"][0])

    def test_azure_invalid_variable_sets_stop_provisioning(self):
        provider = {**VARIABLE_SET, "attributes": {"name": "azurerm"}}
        for stage in ("landing-zone", "azurerm"):
            for ambiguous in (True,):
                with self.subTest(stage=stage, ambiguous=ambiguous):
                    responses = [(200, MODULE, 0), (200, {"data": [PROJECT]}, 0)]
                    if stage == "azurerm":
                        responses += [(200, {"data": [VARIABLE_SET]}, 0), (204, {}, 0)]
                    match = VARIABLE_SET if stage == "landing-zone" else provider
                    responses.append((200, {"data": [match, {**match, "id": "duplicate"}]
                                           if ambiguous else []}, 0))
                    result, requests = self.run_workflow(responses,
                        input_data=self.azure_input(), workflow_text=self.azure_workflow())
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(requests), len(responses))
                    self.assertFalse(any("/no-code-modules/" in r[1] for r in requests))


    def test_azure_missing_variable_sets_continue(self):
        provider = {**VARIABLE_SET, "id": "varset-azure", "attributes": {"name": "azurerm"}}
        for landing_found, provider_found in ((False, True), (True, False), (False, False)):
            with self.subTest(landing_found=landing_found, provider_found=provider_found):
                responses = [(200, MODULE, 0), (200, {"data": [PROJECT]}, 0)]
                expected_ids = []
                for found, varset in ((landing_found, VARIABLE_SET), (provider_found, provider)):
                    responses.append((200, {"data": [varset] if found else []}, 0))
                    if found:
                        responses.append((204, {}, 0))
                        expected_ids.append(varset["id"])
                responses.append((200, {"data": {"id": "ws-mock",
                    "attributes": {"name": "ws-test"},
                    "relationships": {"project": {"data": {"id": "prj-mock"}}}}}, 0))
                result, requests = self.run_workflow(responses,
                    input_data=self.azure_input(), workflow_text=self.azure_workflow())
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output["workspace_id"], "ws-mock")
                self.assertEqual(output["variable_set_ids"], expected_ids)
                self.assertEqual(len(requests), len(responses))
                self.assertIn("/no-code-modules/", requests[-1][1])
                associations = [r for r in requests if "/relationships/projects" in r[1]]
                self.assertEqual([r[1] for r in associations],
                    [f"/api/v2/varsets/{identifier}/relationships/projects"
                     for identifier in expected_ids])

    def test_azure_without_landing_zone_uses_current_project(self):
        for exists in (True, False):
            with self.subTest(exists=exists):
                project = {**PROJECT, "id": "prj-current"}
                data = self.azure_input()
                data.pop("variable_set_name")
                data["attributes"] = {"description": "Azure workspace", "auto_apply": True}
                responses = [(200, MODULE, 0), (200, {"data": [project] if exists else []}, 0)]
                if not exists:
                    responses.append((201, {"data": project}, 0))
                    responses.append((201, {"data": NOTIFICATION}, 0))
                responses += [(200, {"data": []}, 0),
                    (200, {"data": {"id": "ws-mock", "attributes": {"name": "ws-test"},
                        "relationships": {"project": {"data": {"id": "prj-current"}}}}}, 0)]
                result, requests = self.run_workflow(responses, input_data=data,
                    workflow_text=self.azure_workflow())
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output["project_id"], "prj-current")
                self.assertEqual(output["variable_set_ids"], [])
                self.assertEqual(output["project_created"], not exists)
                self.assertEqual(len(requests), len(responses))
                self.assertIn("q=azurerm", requests[-2][1])
                body = json.loads(requests[-1][3])["data"]
                self.assertEqual(body["relationships"]["project"]["data"]["id"], "prj-current")
                self.assertTrue(body["attributes"]["auto_apply"])
                self.assertNotIn("execution-mode", body["attributes"])

    def test_azure_optional_variable_set_names(self):
        for names in ({"provider_name": "azurerm"},
                      {"provider_name": "customprovider", "variable_set_name": ""}):
            with self.subTest(names=names):
                data = self.azure_input()
                data.pop("variable_set_name")
                data.pop("provider_name")
                data.update(names)
                responses = [(200, MODULE, 0), (200, {"data": [PROJECT]}, 0)]
                if names.get("provider_name"):
                    responses += [(200, {"data": [{**VARIABLE_SET,
                        "attributes": {"name": names["provider_name"]}}]}, 0), (204, {}, 0)]
                responses.append((200, {"data": {"id": "ws-mock",
                    "attributes": {"name": "ws-test"},
                    "relationships": {"project": {"data": {"id": "prj-mock"}}}}}, 0))
                result, requests = self.run_workflow(responses,
                    input_data=data, workflow_text=self.azure_workflow())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(requests), len(responses))
                self.assertEqual(json.loads(result.stdout)["variable_set_ids"],
                    ["varset-mock"] if names.get("provider_name") else [])
                if names.get("provider_name"):
                    self.assertIn("q=" + names["provider_name"], requests[2][1])
                self.assertTrue(requests[0][1].endswith("/" + names["provider_name"]))
                body = json.loads(requests[-1][3])["data"]
                self.assertEqual(body["relationships"]["project"]["data"]["id"], "prj-mock")

    def test_azure_association_failure_stops_without_retry(self):
        result, requests = self.run_workflow(
            [(200, MODULE, 0), (200, {"data": [PROJECT]}, 0), (200, {"data": [VARIABLE_SET]}, 0),
             (503, {"errors": []}, 0)],
            input_data=self.azure_input(), workflow_text=self.azure_workflow())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([r[0] for r in requests], ["GET", "GET", "GET", "POST"])
        self.assertFalse(any("/no-code-modules/" in r[1] for r in requests))

    def test_azure_resolution_error_detail(self):
        workflow = self.azure_workflow().replace(
            "      timeout:\n        after:\n          seconds: 120\n", "")
        result, requests = self.run_workflow(
            [(200, MODULE, 0), (200, {"data": [PROJECT]}, 0),
             (200, {"data": [VARIABLE_SET, {**VARIABLE_SET, "id": "duplicate"}]}, 0)],
            input_data=self.azure_input(), workflow_text=workflow)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(requests), 3)
        self.assertIn("exact", result.stderr)

    def test_module_resolution(self):
        workflow = self.azure_workflow()
        result, requests = self.run_workflow(
            [(404, {"errors": []}, 0)], input_data=self.azure_input(), workflow_text=workflow)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][:2], ("GET",
            "/api/v2/organizations/mock-org/registry-modules/private/mock-org/resource-group/azurerm"))
        for enabled, ids in ((False, ["nocode-mock"]), (True, []),
                             (True, ["nocode-a", "nocode-b"]), (True, ["mod-wrong"])):
            with self.subTest(enabled=enabled, ids=ids):
                module = {"data": {"attributes": {"no-code": enabled}, "relationships": {
                    "no-code-modules": {"data": [{"id": value} for value in ids]}}}}
                result, requests = self.run_workflow(
                    [(200, module, 0)], input_data=self.azure_input(), workflow_text=workflow)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(requests), 1)

    def test_existing_project(self):
        result, requests = self.run_workflow([(200, {"data": [PROJECT]}, 0)])
        self.assert_success(result, False)
        self.assertEqual([r[0] for r in requests], ["GET"])
        self.assertIn("filter%5Bnames%5D=prj-test", requests[0][1])
        self.assertEqual(requests[0][2].get("authorization"), "Bearer mock-token")

    def test_create_project(self):
        result, requests = self.run_workflow([
            (200, {"data": []}, 0), (201, {"data": PROJECT}, 0),
            (201, {"data": NOTIFICATION}, 0)])
        self.assert_success(result, True)
        self.assertEqual([r[0] for r in requests], ["GET", "POST", "POST"])
        self.assertEqual(requests[1][2].get("content-type"), "application/vnd.api+json")
        self.assertEqual(json.loads(requests[1][3]),
                         {"data": {"type": "projects", "attributes": {"name": "prj-test"}}})

    def test_transient_get_recovers(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                result, requests = self.run_workflow([
                    (status, {"errors": []}, 0), (200, {"data": [PROJECT]}, 0)])
                self.assert_success(result, False)
                self.assertEqual([r[0] for r in requests], ["GET", "GET"])
                self.assertEqual(requests[0][1], requests[1][1])
                self.assertIn('/organizations/mock-org/projects?', requests[1][1])
                self.assertIn('filter%5Bnames%5D=prj-test', requests[1][1])
                self.assertEqual(requests[1][2].get('authorization'), 'Bearer mock-token')

    def test_get_retry_exhaustion_fails(self):
        result, requests = self.run_workflow([(503, {"errors": []}, 0)])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([r[0] for r in requests], ["GET"] * 4)

    def test_permanent_get_errors_fail_without_retry(self):
        for status in (400, 401, 403, 404, 422):
            with self.subTest(status=status):
                result, requests = self.run_workflow([(status, {"errors": []}, 0)])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([r[0] for r in requests], ["GET"])

    def test_post_errors_are_not_retried(self):
        for status in (401, 409, 422, 503):
            with self.subTest(status=status):
                result, requests = self.run_workflow([
                    (200, {"data": []}, 0), (status, {"errors": []}, 0)])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([r[0] for r in requests], ["GET", "POST"])

    def test_invalid_input_fails_before_http(self):
        result, requests = self.run_workflow([(200, {"data": []}, 0)], input_data={})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(requests, [])

    def test_missing_secret_fails_before_http(self):
        result, requests = self.run_workflow([(200, {"data": []}, 0)], secret=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(requests, [])

    def test_total_timeout(self):
        result, requests = self.run_workflow(
            [(200, {"data": [PROJECT]}, 3)], timeout_ms=1000)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual([r[0] for r in requests], ["GET"])

    def test_total_timeout_includes_post(self):
        result, requests = self.run_workflow([
            (200, {"data": []}, 0), (201, {"data": PROJECT}, 3)], timeout_ms=1500)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual([r[0] for r in requests], ["GET", "POST"])

    def test_total_timeout_includes_retry_delay(self):
        result, requests = self.run_workflow([
            (503, {"errors": []}, 0), (200, {"data": [PROJECT]}, 0)], timeout_ms=1000)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual([r[0] for r in requests], ["GET"])

    def test_hello(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text("{}")
            result = subprocess.run(
                ["java", "-jar", str(JAR), "run", str(ROOT / "workflows/hello.yaml"),
                 "--input", str(path)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"message": "Hello World"})

    def test_functions_reusable_with_different_workflow_input(self):
        for operation, response in (("get", {"data": [PROJECT]}),
                                    ("create", {"data": PROJECT})):
            with self.subTest(operation=operation):
                # Function parameters intentionally differ from the caller's root input.
                definition = {
                    "document": {"dsl": "1.0.3", "namespace": "test",
                                 "name": "another-consumer", "version": "1.0.0"},
                    "use": {"secrets": ["HCP_TERRAFORM_TOKEN"],
                            "catalogs": {"hcp": {"endpoint": "../catalog"}}},
                    "do": [{"call-function": {
                        "call": f"hcp-terraform-project-{operation}:1.0.0@hcp",
                        "with": {"organization": "${ .payload.org }",
                                 "project_name": "${ .payload.name }"}}},
                           {"continue-caller": {"set": {
                               "project_id": "${ .project_id }", "continued": True}}}]
                }
                result, requests = self.run_workflow(
                    [(200 if operation == "get" else 201, response, 0)],
                    input_data={"organization": "wrong-org", "project_name": "wrong-name",
                                "payload": {"org": "mock-org", "name": "prj-test"}},
                    workflow_text=json.dumps(definition))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout),
                                 {"project_id": "prj-mock", "continued": True})
                self.assertEqual(len(requests), 1)
                self.assertIn('/organizations/mock-org/projects', requests[0][1])
                self.assertEqual(requests[0][2].get('authorization'), 'Bearer mock-token')
                if operation == 'get':
                    self.assertIn('filter%5Bnames%5D=prj-test', requests[0][1])
                else:
                    self.assertEqual(json.loads(requests[0][3])['data']['attributes']['name'],
                                     'prj-test')

    def test_missing_catalog_version_fails_before_http(self):
        workflow = self.ensure_project_workflow().replace(
            'validate:1.0.0@projects', 'validate:9.9.9@projects')
        result, requests = self.run_workflow(
            [(200, {"data": [PROJECT]}, 0)], workflow_text=workflow)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('9.9.9', result.stderr)
        self.assertEqual(requests, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
