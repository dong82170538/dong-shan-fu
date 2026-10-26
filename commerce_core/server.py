"""Loopback-only HTTP UI/API. No outbound requests or production publishing."""
import csv
import io
import json
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .adapters import CAPABILITIES
from .domain import ValidationError, only_keys
from .store import ConflictError
from .store import AuthRequired
from .sessions import SessionGuard
from .money import MoneyLedger
from .scheduling import DailyScheduler
from .extension_rules import preview

ROOT = Path(__file__).resolve().parent.parent

# Inventory of independently written handlers, separate from upstream route text.
INDEPENDENT_ROUTES = [
    *[{"method":"GET","path":p,"status":"implemented_fixture"} for p in (
        "/","/app.js","/client.js","/styles.css","/api/health","/api/capabilities","/api/fixtures/demo","/api/fixtures/policy",
        "/api/tasks","/api/tasks/{id}","/api/tasks/{id}/export.json","/api/tasks/{id}/export.csv",
        "/api/schedules","/api/schedules/daily","/api/money-records","/api/session","/api/reference/erp-routes")],
    *[{"method":"POST","path":p,"status":"implemented_fixture"} for p in (
        "/api/fixtures/import","/api/tasks","/api/schedules","/api/schedules/daily","/api/schedules/daily/{id}/cancel",
        "/api/money-records","/api/extension/preview","/api/session/fake-login","/api/session/response","/api/session/logout",
        "/api/tasks/{id}/pause","/api/tasks/{id}/resume","/api/tasks/{id}/cancel","/api/tasks/{id}/retry")],
    *[{"method":"POST","path":p,"status":"disabled_real_capability"} for p in ("/api/publish","/api/shops/connect")],
    *[{"method":"POST","path":p,"status":"guarded_unavailable"} for p in ("/api/restricted/selection","/api/restricted/erp-edit")],
    *[{"method":"GET","path":p,"status":"guarded_unavailable"} for p in ("/restricted/selection","/restricted/erp-edit")],
    {"method":"GET","path":"/api/erp/CreateGoods","status":"not_implemented"},
]


def reference_inventory():
    # These expression strings are inert JSON evidence. Never eval/import them.
    return json.loads((ROOT/"docs/analysis/erp-route-inventory.json").read_text(encoding="utf-8"))


class ConnectionLease:
    """Absolute header/body read deadline; bounded writes and one response only."""
    def __init__(self,request,deadline,service_instance):
        self.request=request
        self.service_instance=service_instance
        self.lock=threading.RLock()
        self.expired=False
        self.responded=False
        self.read_finished=False
        self.timer=threading.Timer(deadline,self.expire)
        self.timer.daemon=True

    def expire(self):
        with self.lock:
            if self.responded or self.read_finished:return
            self.expired=True
            self.responded=True
            body=b'{"error":"REQUEST_READ_TIMEOUT"}'
            try:
                self.request.sendall(b'HTTP/1.1 408 Request Timeout\r\nContent-Type: application/json\r\nConnection: close\r\nX-DSF-Service-Instance: '+self.service_instance.encode()+b'\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:pass

    def read_complete(self):
        with self.lock:
            if self.expired:return False
            self.read_finished=True
            self.timer.cancel()
            return True

    def close(self):
        self.timer.cancel()


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=16

    def __init__(self,address,handler,max_requests=8,read_deadline=5.0,service_instance=None):
        if type(max_requests) is not int or not 1<=max_requests<=32:
            raise ValueError('max_requests must be1..32')
        if not isinstance(read_deadline,(int,float)) or not 0.1<=read_deadline<=30:
            raise ValueError('read_deadline must be0.1..30 seconds')
        self.max_requests=max_requests
        self.read_deadline=float(read_deadline)
        self.service_instance=service_instance or uuid.uuid4().hex
        self.slots=threading.BoundedSemaphore(max_requests)
        self.leases={}
        self.leases_lock=threading.Lock()
        self.peak_active=0
        super().__init__(address,handler)

    @property
    def active_requests(self):
        with self.leases_lock:return len(self.leases)

    def process_request(self,request,client_address):
        request.settimeout(self.read_deadline+1)
        if not self.slots.acquire(blocking=False):
            body=b'{"error":"LOCAL_SERVER_BUSY"}'
            try:
                request.settimeout(0.2)
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\nRetry-After: 1\r\nX-DSF-Service-Instance: '+self.service_instance.encode()+b'\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
            except OSError:pass
            finally:self.shutdown_request(request)
            return
        lease=ConnectionLease(request,self.read_deadline,self.service_instance)
        with self.leases_lock:
            self.leases[request]=lease
            self.peak_active=max(self.peak_active,len(self.leases))
        lease.timer.start()
        try:super().process_request(request,client_address)
        except BaseException:
            self._release(request)
            raise

    def _release(self,request):
        with self.leases_lock:lease=self.leases.pop(request,None)
        if lease:
            lease.close();self.slots.release()

    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self._release(request)

    def handle_error(self, request, client_address):
        # The base server prints raw exception data; never expose request secrets.
        import sys
        sys.stderr.write("LOCAL_REQUEST_FAILED\n")


def capability_manifest():
    return {"source": "test_fixture", "real_connections_enabled": False, "published": 0,
            "adapters": CAPABILITIES,
            "surfaces": {"web_erp": "local_fixture_ui", "desktop_assistant": "ui_wired_native_unverified",
                         "mv3_extension": "source_written_host_unverified"},
            "core_rule_modules":{"extension_sku_preview":True,"daily_timezone_schedule":True,"fixture_session_guard":True},
            "original_erp_routes_implemented": 0,
            "original_erp_count_scope":"Upstream module bodies; never a count of our own routes",
            "reference_erp_route_records":len(reference_inventory()["routes"]),
            "reference_route_provenance":"parent static evidence; inventory only",
            "independent_routes":INDEPENDENT_ROUTES,
            "independent_implemented_route_count":sum(r["status"]=="implemented_fixture" for r in INDEPENDENT_ROUTES),
            "independent_guard_or_unavailable_route_count":sum(r["status"]!="implemented_fixture" for r in INDEPENDENT_ROUTES),
            "missing_modules": ["CreateGoods"],
            "platforms": {"OZON": "fixture_only", "WB": "fixture_only", "Mercado": "planned_unavailable"}}


def csv_export(data):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["source", "platform", "external_id", "sku", "decision", "reasons", "cost", "cost_currency", "sale", "sale_currency", "strike", "strike_currency", "rule", "published"])
    for item in data["task"]["items"]:
        product = item["result"]
        if not product:
            continue
        for sku in product["skus"]:
            price = sku["price"]
            row = ["test_fixture", product["platform"], product["external_id"], sku["sku"], sku["decision"], ",".join(sku["reasons"]), sku["cost"], sku["currency"],
                   price["sale"]["amount"] if price else "", price["sale"]["currency"] if price else "",
                   price["strike"]["amount"] if price else "", price["strike"]["currency"] if price else "",
                   price["rule_version"] if price else "", 0]
            # Prevent spreadsheet formula execution when users open arbitrary fixture text.
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in row])
    return stream.getvalue().encode("utf-8-sig")


def make_server(store, engine, port=8765, fixture_extension_ids=(), max_requests=8, read_deadline=5.0, service_instance=None):
    service_instance=service_instance or uuid.uuid4().hex
    if not isinstance(service_instance,str) or len(service_instance)!=32 or any(c not in "0123456789abcdef" for c in service_instance):
        raise ValueError("Invalid service instance")
    allowed_extension_origins={"chrome-extension://"+ident for ident in fixture_extension_ids if isinstance(ident,str) and len(ident)==32 and set(ident)<=set("abcdefghijklmnop")}
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalFixtureCore/0.1"

        def log_message(self, *_args):
            pass  # Never log request bodies, raw URLs, headers or credentials.

        def respond(self, data, status=200, content_type="application/json; charset=utf-8", attachment=None):
            lease=self.server.leases[self.request]
            with lease.lock:
                if lease.responded:return
                lease.responded=True
                lease.timer.cancel()
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-DSF-Service-Instance",service_instance)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            if attachment:
                self.send_header("Content-Disposition", 'attachment; filename="' + attachment + '"')
            self.end_headers()
            self.wfile.write(body)

        def send_error(self,code,message=None,explain=None):
            self.respond({"error":"INVALID_HTTP_REQUEST"},code)

        def dispatch(self, mutate=False):
            if self.headers.get("X-DSF-Service-Instance") not in (None,service_instance):
                return self.respond({"error":"SERVICE_INSTANCE_MISMATCH"},409)
            expected_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in expected_hosts:
                return self.respond({"error": "LOCAL_HOST_REQUIRED"}, 403)
            if mutate:
                origin = self.headers.get("Origin")
                extension_preview = self.path == "/api/extension/preview" and origin in allowed_extension_origins
                if self.headers.get("X-Local-Client") != "dong-shan-fu" or (origin and origin not in {"http://" + h for h in expected_hosts} and not extension_preview):
                    return self.respond({"error": "LOCAL_CLIENT_REQUIRED"}, 403)
                if self.headers.get_content_type() != "application/json":
                    return self.respond({"error": "JSON_REQUIRED"}, 415)
            try:
                if not self.path.startswith("/") or self.path.startswith("//"):
                    return self.respond({"error": "ORIGIN_FORM_REQUIRED"}, 400)
                path = urlsplit(self.path).path
                if not mutate:
                    if not self.server.leases[self.request].read_complete():return
                    if path in {"/", "/app.js", "/client.js", "/styles.css"}:
                        filename = {"/": "index.html", "/app.js": "app.js", "/client.js": "client.js", "/styles.css": "styles.css"}[path]
                        mime = {"/": "text/html", "/app.js": "text/javascript", "/client.js": "text/javascript", "/styles.css": "text/css"}[path]
                        return self.respond((ROOT / "web" / filename).read_bytes(), content_type=mime + "; charset=utf-8")
                    if path == "/api/health":
                        return self.respond({**engine.status(),"service_instance":service_instance,"http":{"active_requests":self.server.active_requests,"max_requests":self.server.max_requests,"read_deadline_seconds":self.server.read_deadline}})
                    if path == "/api/capabilities":
                        return self.respond(capability_manifest())
                    if path == "/api/reference/erp-routes":
                        return self.respond(reference_inventory())
                    if path == "/api/fixtures/demo":
                        return self.respond((ROOT / "fixtures" / "pipeline.json").read_bytes())
                    if path == "/api/fixtures/policy":
                        return self.respond((ROOT / "fixtures" / "pipeline-policy.json").read_bytes())
                    if path == "/api/tasks":
                        return self.respond({"tasks": store.list_tasks(), "summary": store.summary()})
                    if path == "/api/schedules":
                        return self.respond({"schedules": store.list_schedules()})
                    if path == "/api/schedules/daily":
                        return self.respond({"schedules": DailyScheduler(store).list()})
                    if path == "/api/money-records":
                        return self.respond({"records":MoneyLedger(store).list()})
                    if path == "/api/session":
                        return self.respond(SessionGuard(store).state())
                    if path == "/api/erp/CreateGoods":
                        return self.respond({"error":"NOT_IMPLEMENTED","source":"test_fixture","original_route_implemented":False},501)
                    if path in {"/restricted/selection","/restricted/erp-edit"}:
                        SessionGuard(store).require()
                        return self.respond({"error":"CAPABILITY_UNAVAILABLE","real_authentication":False},501)
                    parts = path.strip("/").split("/")
                    if len(parts) in {3, 4} and parts[:2] == ["api", "tasks"]:
                        if len(parts) == 3:
                            return self.respond(store.task(parts[2]))
                        data = store.export(parts[2])
                        if parts[3] == "export.json":
                            return self.respond(data, attachment="fixture-result.json")
                        if parts[3] == "export.csv":
                            return self.respond(csv_export(data), content_type="text/csv; charset=utf-8", attachment="fixture-result.csv")
                    return self.respond({"error": "NOT_FOUND"}, 404)
                if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length",[]))!=1:
                    raise ValidationError("One Content-Length required; chunked bodies unsupported")
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 2_000_000:
                    return self.respond({"error": "BODY_LIMIT_2MB"}, 413)
                raw=self.rfile.read(length)
                lease=self.server.leases[self.request]
                if lease.expired:return
                if not lease.read_complete():return
                if len(raw)!=length:
                    return self.respond({"error":"INCOMPLETE_BODY"},400)
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValidationError("JSON object required")
                schemas={
                    "/api/tasks":{"fixture_id","policy","idempotency_key","timeout_ms","account_id","kind","retry_policy"},
                    "/api/schedules":{"fixture_id","policy","due","account_id"},
                    "/api/schedules/daily":{"fixture_id","policy","timezone","local_time","account_id"},
                    "/api/session/fake-login":{"source","account_id"},
                    "/api/session/response":{"source","account_id","epoch","code"},
                    "/api/session/logout":{"source","account_id"},
                    "/api/restricted/selection":{"account_id"},"/api/restricted/erp-edit":{"account_id"},
                    "/api/publish":set(),"/api/shops/connect":set(),
                }
                if path in schemas:only_keys(body,schemas[path],"request")
                parts=path.strip("/").split("/")
                if (len(parts)==4 and parts[:2]==["api","tasks"] and parts[3] in {"pause","resume","cancel","retry"}) or (len(parts)==5 and parts[:3]==["api","schedules","daily"] and parts[4]=="cancel"):
                    only_keys(body,set(),"control request")
                if path in {"/api/publish", "/api/shops/connect"}:
                    return self.respond({"error": "CAPABILITY_UNAVAILABLE", "reason": "Authorized official adapters are not implemented", "published": 0}, 403)
                if path == "/api/fixtures/import":
                    return self.respond(store.import_fixture(body), 201)
                if path == "/api/tasks":
                    result = store.submit(body.get("fixture_id"), body.get("policy"), body.get("idempotency_key"), body.get("timeout_ms", 2000), body.get("account_id","fixture-default"),body.get("kind","selection"),body.get("retry_policy"))
                    return self.respond(result, 202)
                if path == "/api/schedules":
                    return self.respond(store.schedule(body.get("fixture_id"), body.get("policy"), body.get("due"),body.get("account_id","fixture-default")), 201)
                if path == "/api/schedules/daily":
                    return self.respond(DailyScheduler(store).create(body.get("fixture_id"),body.get("policy"),body.get("timezone"),body.get("local_time"),body.get("account_id","fixture-default")),201)
                if path.startswith("/api/schedules/daily/") and path.endswith("/cancel"):
                    parts=path.strip("/").split("/")
                    if len(parts)==5:return self.respond(DailyScheduler(store).cancel(parts[3]))
                if path == "/api/money-records":
                    return self.respond(MoneyLedger(store).save(body),201)
                if path == "/api/extension/preview":
                    return self.respond(preview(body))
                if path in {"/api/restricted/selection","/api/restricted/erp-edit"}:
                    SessionGuard(store).require(body.get("account_id","fixture-default"))
                    return self.respond({"error":"CAPABILITY_UNAVAILABLE","reason":"Fixture login grants no real merchant capabilities","published":0},403)
                if path.startswith("/api/session/"):
                    if body.get("source")!="test_fixture":
                        raise ValidationError("Explicit fixture session required")
                    guard=SessionGuard(store);account=body.get("account_id","fixture-default")
                    if path == "/api/session/fake-login":return self.respond(guard.fake_login(account,body.get("source")))
                    if path == "/api/session/response":return self.respond(guard.response(account,body.get("epoch"),body.get("code")))
                    if path == "/api/session/logout":return self.respond(guard.logout(account))
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] in {"pause", "resume", "cancel", "retry"}:
                    return self.respond(store.control(parts[2], parts[3]))
                return self.respond({"error": "NOT_FOUND"}, 404)
            except KeyError:
                self.respond({"error": "NOT_FOUND"}, 404)
            except TimeoutError:
                self.server.leases[self.request].expire()
            except AuthRequired:
                self.respond({"error":"AUTH_REQUIRED","real_authentication":False,"published":0},401)
            except ConflictError as error:
                self.respond({"error": "CONFLICT", "message": str(error)}, 409)
            except (ValidationError, json.JSONDecodeError, ValueError, TypeError):
                # Avoid echoing user-supplied credential-like values in errors/logs.
                self.respond({"error": "INVALID_INPUT", "message": "Check fixture/policy schema, currency tags and idempotency key"}, 400)
            except Exception:
                self.respond({"error": "STORAGE_OR_INTERNAL_ERROR", "success": False}, 503)

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch(mutate=True)

    server = LocalHTTPServer(("127.0.0.1", port), Handler, max_requests, read_deadline, service_instance)
    return server
