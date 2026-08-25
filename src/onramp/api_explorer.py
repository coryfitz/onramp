"""OpenAPI generation and the built-in OnRamp API explorer."""

from __future__ import annotations

import re
from typing import Any, Iterable


WRITE_METHODS = {"POST", "PUT", "PATCH"}
PATH_PARAMETER_PATTERN = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def build_openapi_document(
    operations: Iterable[dict[str, Any]],
    *,
    title: str = "OnRamp API",
    version: str = "0.1.0",
) -> dict[str, Any]:
    """Build a compact OpenAPI document from discovered file routes."""
    paths: dict[str, dict[str, Any]] = {}
    tags: list[dict[str, str]] = []
    seen_tags: set[str] = set()

    for operation in operations:
        path = operation["path"]
        method = operation["method"].lower()
        tag = operation["tag"]
        if tag not in seen_tags:
            tags.append({"name": tag})
            seen_tags.add(tag)

        parameters = [
            {
                "name": name,
                "in": "path",
                "required": True,
                "description": f"Value for {name}",
                "schema": {"type": "string"},
            }
            for name in PATH_PARAMETER_PATTERN.findall(path)
        ]

        openapi_operation: dict[str, Any] = {
            "tags": [tag],
            "summary": operation["summary"],
            "description": operation["description"],
            "operationId": operation["operation_id"],
            "parameters": parameters,
            "responses": {
                "200": {
                    "description": "Successful response",
                    "content": {
                        "application/json": {
                            "schema": {},
                        }
                    },
                }
            },
        }

        if operation["method"] in WRITE_METHODS:
            openapi_operation["requestBody"] = {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                        "example": {},
                    }
                },
            }

        paths.setdefault(path, {})[method] = openapi_operation

    return {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": version,
            "description": (
                "Interactive documentation generated from your file-based "
                "OnRamp routes."
            ),
        },
        "tags": tags,
        "paths": paths,
    }


def api_explorer_html() -> str:
    """Return the self-contained HTML for the built-in API explorer."""
    return _API_EXPLORER_HTML


_API_EXPLORER_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>OnRamp API</title>
  <style>
    :root {
      --canvas: #f4f7fc;
      --surface: #ffffff;
      --surface-soft: #f8faf7;
      --ink: #071c47;
      --muted: #66738a;
      --line: #dce4f0;
      --line-strong: #c9d5e7;
      --brand: #174a96;
      --brand-dark: #071c47;
      --brand-soft: #e7effb;
      --blue: #2769d8;
      --blue-soft: #eaf1fd;
      --amber: #a65c09;
      --amber-soft: #fff3df;
      --red: #b74343;
      --red-soft: #fdecec;
      --purple: #7650b6;
      --purple-soft: #f2ecfb;
      --shadow: 0 18px 55px rgba(7, 28, 71, 0.09);
      --shadow-small: 0 7px 24px rgba(7, 28, 71, 0.07);
      --radius: 18px;
      font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }
    html { min-width: 320px; background: var(--canvas); }
    body { margin: 0; color: var(--ink); background: var(--canvas); }
    button, input, textarea { font: inherit; }
    button { color: inherit; }

    .topbar {
      position: sticky;
      top: 0;
      z-index: 20;
      border-bottom: 1px solid rgba(201, 213, 231, 0.84);
      background: rgba(244, 247, 252, 0.9);
      backdrop-filter: blur(18px);
    }

    .topbar-inner,
    .shell {
      width: min(1120px, calc(100% - 40px));
      margin: 0 auto;
    }

    .topbar-inner {
      height: 70px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }

    .brand { display: flex; align-items: center; gap: 11px; }
    .brand-mark {
      width: 34px;
      height: 34px;
      border-radius: 11px;
      border: 1px solid var(--line);
      background: white;
      box-shadow: 0 8px 20px rgba(7, 28, 71, 0.12);
      object-fit: contain;
    }
    .brand-copy { display: flex; align-items: baseline; gap: 7px; }
    .brand-name { font-weight: 760; letter-spacing: -0.025em; }
    .brand-label { color: var(--muted); font-size: 13px; }

    .spec-link {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-height: 38px;
      padding: 0 13px;
      border: 1px solid var(--line-strong);
      border-radius: 11px;
      color: var(--ink);
      background: rgba(255, 255, 255, 0.72);
      text-decoration: none;
      font-size: 13px;
      font-weight: 650;
      transition: 160ms ease;
    }
    .spec-link:hover { border-color: var(--brand); color: var(--brand-dark); background: white; }
    .spec-link svg { width: 15px; height: 15px; }

    .shell { padding: 68px 0 80px; }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 42px;
      align-items: end;
      margin-bottom: 35px;
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 15px;
      color: var(--brand-dark);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.11em;
      text-transform: uppercase;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #2aa36b;
      box-shadow: 0 0 0 5px rgba(42, 163, 107, 0.12);
    }
    h1 {
      max-width: 700px;
      margin: 0;
      font-size: clamp(42px, 6vw, 68px);
      line-height: 0.98;
      letter-spacing: -0.058em;
      font-weight: 760;
    }
    .hero p {
      max-width: 640px;
      margin: 22px 0 0;
      color: var(--muted);
      font-size: 17px;
      line-height: 1.65;
    }
    .stats { display: flex; gap: 10px; }
    .stat {
      min-width: 112px;
      padding: 17px 18px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.64);
    }
    .stat strong { display: block; font-size: 24px; letter-spacing: -0.04em; }
    .stat span { color: var(--muted); font-size: 12px; font-weight: 650; }

    .toolbar {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 31px;
    }
    .search-wrap { position: relative; flex: 1; }
    .search-wrap svg {
      position: absolute;
      left: 16px;
      top: 50%;
      width: 18px;
      height: 18px;
      color: #74817b;
      transform: translateY(-50%);
      pointer-events: none;
    }
    .search {
      width: 100%;
      height: 50px;
      padding: 0 18px 0 46px;
      border: 1px solid var(--line-strong);
      border-radius: 14px;
      outline: 0;
      color: var(--ink);
      background: var(--surface);
      box-shadow: var(--shadow-small);
      transition: 150ms ease;
    }
    .search:focus { border-color: var(--brand); box-shadow: 0 0 0 4px rgba(23, 74, 150, 0.11); }
    .search::placeholder { color: #87928d; }
    .version-pill {
      min-height: 50px;
      display: flex;
      align-items: center;
      padding: 0 16px;
      border: 1px solid var(--line);
      border-radius: 14px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.58);
      font-size: 13px;
      font-weight: 650;
      white-space: nowrap;
    }

    .group { margin-top: 33px; }
    .group-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin: 0 4px 13px;
    }
    .group-title { margin: 0; font-size: 16px; font-weight: 760; letter-spacing: -0.02em; }
    .group-count { color: var(--muted); font-size: 12px; }
    .operations { display: grid; gap: 11px; }
    .operation {
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      box-shadow: 0 3px 12px rgba(36, 58, 49, 0.035);
      transition: border-color 150ms ease, box-shadow 150ms ease;
    }
    .operation.open { border-color: #b8c9e3; box-shadow: var(--shadow); }
    .operation-button {
      width: 100%;
      min-height: 74px;
      display: grid;
      grid-template-columns: 76px minmax(180px, 1fr) minmax(150px, 0.8fr) 28px;
      align-items: center;
      gap: 18px;
      padding: 12px 20px;
      border: 0;
      text-align: left;
      background: transparent;
      cursor: pointer;
    }
    .operation-button:hover { background: var(--surface-soft); }
    .method {
      width: 68px;
      padding: 8px 0;
      border-radius: 9px;
      text-align: center;
      font-size: 11px;
      font-weight: 850;
      letter-spacing: 0.08em;
    }
    .method.get { color: var(--brand-dark); background: var(--brand-soft); }
    .method.post { color: #1755a8; background: var(--blue-soft); }
    .method.put { color: #8a4a00; background: var(--amber-soft); }
    .method.patch { color: #65419f; background: var(--purple-soft); }
    .method.delete { color: #a63232; background: var(--red-soft); }
    .method.head, .method.options { color: #4f5d57; background: #edf0ee; }
    .path {
      overflow: hidden;
      color: #24342d;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 14px;
      font-weight: 650;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .summary { overflow: hidden; color: var(--muted); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
    .chevron { width: 18px; height: 18px; color: #7f8a85; transition: transform 180ms ease; }
    .operation.open .chevron { transform: rotate(180deg); }

    .operation-panel {
      display: none;
      border-top: 1px solid var(--line);
      background: #fbfcfa;
    }
    .operation.open .operation-panel { display: block; }
    .panel-inner { padding: 28px; }
    .description { margin: 0 0 24px; color: var(--muted); font-size: 14px; line-height: 1.65; }
    .section { margin-top: 25px; }
    .section:first-child { margin-top: 0; }
    .section-title-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 11px; }
    .section-title { margin: 0; font-size: 13px; font-weight: 780; }
    .optional { color: #8a958f; font-size: 11px; font-weight: 600; }
    .field-grid { display: grid; grid-template-columns: minmax(140px, 0.45fr) 1fr; gap: 12px; }
    .field-meta {
      padding: 13px 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: white;
    }
    .field-meta strong { display: block; font-family: "SFMono-Regular", Consolas, monospace; font-size: 12px; }
    .field-meta span { color: var(--muted); font-size: 11px; }
    .input,
    .textarea {
      width: 100%;
      border: 1px solid var(--line-strong);
      border-radius: 12px;
      outline: 0;
      color: var(--ink);
      background: white;
      transition: 150ms ease;
    }
    .input { min-height: 47px; padding: 0 14px; }
    .textarea {
      min-height: 180px;
      padding: 15px;
      resize: vertical;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 13px;
      line-height: 1.55;
    }
    .input:focus, .textarea:focus { border-color: var(--brand); box-shadow: 0 0 0 4px rgba(23, 74, 150, 0.1); }
    .body-tabs { display: flex; gap: 5px; margin-bottom: 10px; }
    .body-tab {
      padding: 7px 10px;
      border: 0;
      border-radius: 8px;
      color: var(--muted);
      background: transparent;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
    }
    .body-tab.active { color: var(--brand-dark); background: var(--brand-soft); }
    .schema-view {
      display: none;
      min-height: 180px;
      margin: 0;
      padding: 15px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 12px;
      color: #355047;
      background: #f3f7f4;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
    }
    .body-wrap.schema-mode .textarea { display: none; }
    .body-wrap.schema-mode .schema-view { display: block; }

    .actions { display: flex; align-items: center; gap: 9px; margin-top: 25px; }
    .primary,
    .secondary {
      min-height: 42px;
      padding: 0 15px;
      border-radius: 11px;
      font-size: 13px;
      font-weight: 750;
      cursor: pointer;
      transition: 150ms ease;
    }
    .primary { border: 1px solid var(--brand); color: white; background: var(--brand); }
    .primary:hover { border-color: var(--brand-dark); background: var(--brand-dark); }
    .primary:disabled { opacity: 0.6; cursor: wait; }
    .secondary { border: 1px solid var(--line-strong); color: var(--ink); background: white; }
    .secondary:hover { border-color: #9fafa6; background: var(--surface-soft); }

    .response {
      display: none;
      margin-top: 27px;
      padding-top: 25px;
      border-top: 1px solid var(--line);
    }
    .response.visible { display: block; }
    .response-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 11px; }
    .response-meta { display: flex; align-items: center; gap: 8px; }
    .status {
      padding: 6px 9px;
      border-radius: 8px;
      color: var(--brand-dark);
      background: var(--brand-soft);
      font-size: 11px;
      font-weight: 800;
    }
    .status.error { color: #a63232; background: var(--red-soft); }
    .duration { color: var(--muted); font-size: 11px; }
    .request-url {
      margin: 0 0 9px;
      color: var(--muted);
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 11px;
      overflow-wrap: anywhere;
    }
    .response-body {
      min-height: 105px;
      max-height: 420px;
      margin: 0;
      padding: 16px;
      overflow: auto;
      border: 1px solid #cbd7d0;
      border-radius: 12px;
      color: #dff8eb;
      background: #07162e;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .empty,
    .load-error {
      padding: 42px 24px;
      border: 1px dashed var(--line-strong);
      border-radius: 18px;
      color: var(--muted);
      text-align: center;
      background: rgba(255, 255, 255, 0.48);
    }
    .load-error strong { display: block; margin-bottom: 6px; color: var(--ink); }
    .footer { margin-top: 56px; color: #89938e; font-size: 12px; text-align: center; }

    @media (max-width: 760px) {
      .topbar-inner, .shell { width: min(100% - 28px, 1120px); }
      .shell { padding-top: 44px; }
      .hero { grid-template-columns: 1fr; gap: 23px; }
      .stats { justify-content: flex-start; }
      .stat { min-width: 100px; }
      .operation-button { grid-template-columns: 68px minmax(0, 1fr) 22px; gap: 12px; padding: 11px 14px; }
      .operation-button .summary { display: none; }
      .panel-inner { padding: 22px 18px; }
      .field-grid { grid-template-columns: 1fr; }
    }

    @media (max-width: 500px) {
      .brand-label, .spec-link span { display: none; }
      .spec-link { width: 40px; justify-content: center; padding: 0; }
      h1 { font-size: 44px; }
      .hero p { font-size: 15px; }
      .stats { width: 100%; }
      .stat { flex: 1; min-width: 0; }
      .toolbar { align-items: stretch; flex-direction: column; }
      .version-pill { min-height: 42px; }
      .operation-button { grid-template-columns: 61px minmax(0, 1fr) 18px; gap: 9px; }
      .method { width: 58px; }
      .path { font-size: 12px; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <img class="brand-mark" src="/api/onramp-logo.png" alt="">
        <div class="brand-copy"><span class="brand-name">OnRamp</span><span class="brand-label">API explorer</span></div>
      </div>
      <a class="spec-link" href="/api/openapi.json" target="_blank" rel="noreferrer">
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M14 3h4a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3h4M9 12h6M12 9v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
        <span>OpenAPI JSON</span>
      </a>
    </div>
  </header>

  <main class="shell">
    <section class="hero">
      <div>
        <div class="eyebrow"><span class="status-dot"></span>Backend is running</div>
        <h1 id="api-title">Explore your API.</h1>
        <p id="api-description">Browse every file-based route, inspect its inputs, and make live requests without leaving your project.</p>
      </div>
      <div class="stats" aria-label="API statistics">
        <div class="stat"><strong id="route-count">—</strong><span>routes</span></div>
        <div class="stat"><strong id="method-count">—</strong><span>operations</span></div>
      </div>
    </section>

    <div class="toolbar">
      <label class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m16.5 16.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
        <input id="search" class="search" type="search" placeholder="Filter by path, method, or description" autocomplete="off">
      </label>
      <div id="api-version" class="version-pill">OpenAPI 3.1</div>
    </div>

    <div id="explorer" aria-live="polite">
      <div class="empty">Reading your file-based routes…</div>
    </div>
    <div class="footer">Generated locally from your OnRamp backend.</div>
  </main>

  <script>
    const METHOD_ORDER = ["get", "post", "put", "patch", "delete", "head", "options"];
    const controllers = new Map();
    let allOperations = [];

    const escapeHtml = (value) => String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

    const titleCase = (value) => String(value || "default")
      .replaceAll("_", " ")
      .replaceAll("-", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());

    const operationKey = (operation) => `${operation.method}:${operation.path}`;

    function flattenOperations(spec) {
      const operations = [];
      Object.entries(spec.paths || {}).forEach(([path, pathItem]) => {
        METHOD_ORDER.forEach((method) => {
          const detail = pathItem[method];
          if (!detail) return;
          operations.push({
            path,
            method: method.toUpperCase(),
            tag: (detail.tags && detail.tags[0]) || "default",
            summary: detail.summary || `${method.toUpperCase()} ${path}`,
            description: detail.description || "",
            parameters: detail.parameters || [],
            requestBody: detail.requestBody || null,
            operationId: detail.operationId || `${method}-${path}`,
          });
        });
      });
      return operations;
    }

    function render(spec) {
      allOperations = flattenOperations(spec);
      const uniquePaths = new Set(allOperations.map((operation) => operation.path));
      document.getElementById("route-count").textContent = uniquePaths.size;
      document.getElementById("method-count").textContent = allOperations.length;
      document.getElementById("api-version").textContent = `${spec.info?.version || "0.1.0"} · OpenAPI ${spec.openapi || "3.1"}`;
      if (spec.info?.description) document.getElementById("api-description").textContent = spec.info.description;
      renderFiltered("");
    }

    function renderFiltered(query) {
      const normalized = query.trim().toLowerCase();
      const filtered = allOperations.filter((operation) =>
        [operation.path, operation.method, operation.tag, operation.summary, operation.description]
          .join(" ")
          .toLowerCase()
          .includes(normalized)
      );
      const explorer = document.getElementById("explorer");
      if (!filtered.length) {
        explorer.innerHTML = '<div class="empty">No routes match that filter.</div>';
        return;
      }

      const groups = new Map();
      filtered.forEach((operation) => {
        if (!groups.has(operation.tag)) groups.set(operation.tag, []);
        groups.get(operation.tag).push(operation);
      });

      explorer.innerHTML = Array.from(groups.entries()).map(([tag, operations]) => `
        <section class="group">
          <div class="group-head">
            <h2 class="group-title">${escapeHtml(titleCase(tag))}</h2>
            <span class="group-count">${operations.length} ${operations.length === 1 ? "operation" : "operations"}</span>
          </div>
          <div class="operations">${operations.map(renderOperation).join("")}</div>
        </section>
      `).join("");

      explorer.querySelectorAll(".operation-button").forEach((button) => {
        button.addEventListener("click", () => toggleOperation(button));
      });
      explorer.querySelectorAll(".execute-button").forEach((button) => {
        button.addEventListener("click", () => executeOperation(button.closest(".operation")));
      });
      explorer.querySelectorAll(".reset-button").forEach((button) => {
        button.addEventListener("click", () => resetOperation(button.closest(".operation")));
      });
      explorer.querySelectorAll(".body-tab").forEach((button) => {
        button.addEventListener("click", () => switchBodyTab(button));
      });
    }

    function renderOperation(operation) {
      const key = operationKey(operation);
      const pathParameters = operation.parameters.filter((parameter) => parameter.in === "path");
      const bodyContent = operation.requestBody?.content?.["application/json"] || null;
      const schema = bodyContent?.schema || {};
      const example = bodyContent?.example ?? {};
      const description = operation.description || `${operation.method} request to ${operation.path}.`;

      return `
        <article class="operation" data-key="${escapeHtml(key)}" data-method="${escapeHtml(operation.method)}" data-path="${escapeHtml(operation.path)}">
          <button class="operation-button" type="button" aria-expanded="false">
            <span class="method ${operation.method.toLowerCase()}">${escapeHtml(operation.method)}</span>
            <span class="path">${escapeHtml(operation.path)}</span>
            <span class="summary">${escapeHtml(operation.summary)}</span>
            <svg class="chevron" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m6.5 9 5.5 5.5L17.5 9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
          <div class="operation-panel">
            <div class="panel-inner">
              <p class="description">${escapeHtml(description)}</p>
              ${pathParameters.length ? renderPathParameters(pathParameters) : ""}
              <section class="section">
                <div class="section-title-row"><h3 class="section-title">Query string</h3><span class="optional">optional</span></div>
                <input class="input query-input" type="text" placeholder="limit=10&amp;active=true" aria-label="Query string">
              </section>
              ${bodyContent ? renderRequestBody(schema, example) : ""}
              <div class="actions">
                <button class="primary execute-button" type="button">Send request</button>
                <button class="secondary reset-button" type="button">Reset</button>
              </div>
              <section class="response" aria-live="polite">
                <div class="response-head">
                  <h3 class="section-title">Response</h3>
                  <div class="response-meta"><span class="status"></span><span class="duration"></span></div>
                </div>
                <p class="request-url"></p>
                <pre class="response-body"></pre>
              </section>
            </div>
          </div>
        </article>
      `;
    }

    function renderPathParameters(parameters) {
      return `
        <section class="section">
          <div class="section-title-row"><h3 class="section-title">Path parameters</h3><span class="optional">required</span></div>
          <div class="field-grid">
            ${parameters.map((parameter) => `
              <div class="field-meta"><strong>${escapeHtml(parameter.name)}</strong><span>${escapeHtml(parameter.schema?.type || "string")} · path</span></div>
              <input class="input path-input" data-name="${escapeHtml(parameter.name)}" type="text" placeholder="Enter ${escapeHtml(parameter.name)}" aria-label="${escapeHtml(parameter.name)}">
            `).join("")}
          </div>
        </section>
      `;
    }

    function renderRequestBody(schema, example) {
      const exampleText = JSON.stringify(example, null, 2);
      const schemaText = JSON.stringify(schema, null, 2);
      return `
        <section class="section body-wrap">
          <div class="section-title-row"><h3 class="section-title">Request body</h3><span class="optional">application/json</span></div>
          <div class="body-tabs">
            <button class="body-tab active" data-mode="edit" type="button">Edit value</button>
            <button class="body-tab" data-mode="schema" type="button">Schema</button>
          </div>
          <textarea class="textarea body-input" spellcheck="false" aria-label="JSON request body">${escapeHtml(exampleText)}</textarea>
          <pre class="schema-view">${escapeHtml(schemaText)}</pre>
        </section>
      `;
    }

    function toggleOperation(button) {
      const operation = button.closest(".operation");
      const open = operation.classList.toggle("open");
      button.setAttribute("aria-expanded", String(open));
    }

    function switchBodyTab(button) {
      const wrap = button.closest(".body-wrap");
      wrap.querySelectorAll(".body-tab").forEach((tab) => tab.classList.toggle("active", tab === button));
      wrap.classList.toggle("schema-mode", button.dataset.mode === "schema");
    }

    function resetOperation(operation) {
      const controller = controllers.get(operation.dataset.key);
      if (controller) controller.abort();
      controllers.delete(operation.dataset.key);
      operation.querySelectorAll(".input").forEach((input) => { input.value = ""; });
      const body = operation.querySelector(".body-input");
      if (body) body.value = "{}";
      const response = operation.querySelector(".response");
      response.classList.remove("visible");
      operation.querySelector(".execute-button").disabled = false;
      operation.querySelector(".execute-button").textContent = "Send request";
    }

    async function executeOperation(operation) {
      const button = operation.querySelector(".execute-button");
      const responseSection = operation.querySelector(".response");
      const status = responseSection.querySelector(".status");
      const duration = responseSection.querySelector(".duration");
      const responseBody = responseSection.querySelector(".response-body");
      const requestUrl = responseSection.querySelector(".request-url");
      let path = operation.dataset.path;

      for (const input of operation.querySelectorAll(".path-input")) {
        if (!input.value.trim()) {
          input.focus();
          input.setCustomValidity("This path value is required.");
          input.reportValidity();
          input.addEventListener("input", () => input.setCustomValidity(""), { once: true });
          return;
        }
        path = path.replace(`{${input.dataset.name}}`, encodeURIComponent(input.value.trim()));
      }

      const query = operation.querySelector(".query-input").value.trim().replace(/^\?/, "");
      const url = `${path}${query ? `?${query}` : ""}`;
      const options = { method: operation.dataset.method, headers: { Accept: "application/json" } };
      const body = operation.querySelector(".body-input");
      if (body) {
        try {
          JSON.parse(body.value);
        } catch (error) {
          body.focus();
          responseSection.classList.add("visible");
          status.textContent = "Invalid JSON";
          status.classList.add("error");
          duration.textContent = "";
          requestUrl.textContent = url;
          responseBody.textContent = error.message;
          return;
        }
        options.headers["Content-Type"] = "application/json";
        options.body = body.value;
      }

      const controller = new AbortController();
      controllers.set(operation.dataset.key, controller);
      options.signal = controller.signal;
      button.disabled = true;
      button.textContent = "Sending…";
      responseSection.classList.add("visible");
      status.classList.remove("error");
      status.textContent = "Waiting";
      duration.textContent = "";
      requestUrl.textContent = `${operation.dataset.method} ${url}`;
      responseBody.textContent = "Request in progress…";
      const started = performance.now();

      try {
        const response = await fetch(url, options);
        const elapsed = Math.round(performance.now() - started);
        const contentType = response.headers.get("content-type") || "";
        const text = await response.text();
        let formatted = text || "(empty response)";
        if (contentType.includes("json") && text) {
          try { formatted = JSON.stringify(JSON.parse(text), null, 2); } catch (_) {}
        }
        status.textContent = `${response.status} ${response.statusText}`;
        status.classList.toggle("error", !response.ok);
        duration.textContent = `${elapsed} ms`;
        responseBody.textContent = formatted;
      } catch (error) {
        status.textContent = error.name === "AbortError" ? "Cancelled" : "Request failed";
        status.classList.add("error");
        duration.textContent = `${Math.round(performance.now() - started)} ms`;
        responseBody.textContent = error.message;
      } finally {
        controllers.delete(operation.dataset.key);
        button.disabled = false;
        button.textContent = "Send request";
      }
    }

    document.getElementById("search").addEventListener("input", (event) => renderFiltered(event.target.value));

    fetch("/api/openapi.json", { headers: { Accept: "application/json" } })
      .then((response) => {
        if (!response.ok) throw new Error(`OpenAPI request failed with ${response.status}`);
        return response.json();
      })
      .then(render)
      .catch((error) => {
        document.getElementById("explorer").innerHTML = `<div class="load-error"><strong>Could not load the API description.</strong>${escapeHtml(error.message)}</div>`;
      });
  </script>
</body>
</html>
"""
