# AGENTS.md — webui_app (Flask WebUI)

Flask app factory at repo root (not under `src/`). `create_app()` in `__init__.py` returns configured app with blueprints, scheduler, and CSRF guard.

## Structure

```
webui_app/
├── __init__.py          # create_app() factory — CSRF guard, scheduler, blueprint registration
├── routes/              # 20 route modules, registered via register_blueprints()
├── services/            # 5 modules: bind_job, browser_login, recheck, seo_viz, url_verify_throttle
├── helpers/             # 8 modules: _request_cache, channel_probes, cli_runner, contexts, history, security, url_meta
├── binding_status.py    # Channel binding badge state
├── medium_liveness.py   # Medium session health check
├── medium_login.py      # Medium login flow
├── scheduler.py         # APScheduler + job restore
└── templates/           # Jinja2 templates
```

## Where to look

| Task | Location |
|---|---|
| Add a new page/route | `routes/` + `register_blueprints()` auto-registers |
| Add a background service | `services/` + wire in `create_app()` startup |
| CSRF / security | `helpers/security.py` — `_global_csrf_guard` in `__init__.py` |
| Store access | `current_app.extensions['webui_stores']` or direct `webui_store.X_store` |
| Template context | `@app.context_processor` in `__init__.py` (`platforms`, `csrf_token`) |

## Conventions

- **CSRF**: Global guard on every POST/PUT/PATCH/DELETE. Tests opt out via `app.config['CSRF_ENABLED'] = False` or `WTF_CSRF_ENABLED = False`. OAuth callbacks excluded.
- **Stores**: Module-level singletons (`webui_store/`) backed by JSON files in config dir. Lazy-resolved on first access. Access via `current_app.extensions['webui_stores']` (Plan 2026-05-22 P7 C1).
- **Platform list**: Reverse-driven from `publishing.registry.registered_platforms()` — no hardcoded platform lists in templates.
- **Scheduler**: Starts automatically unless under pytest (`PYTEST_CURRENT_TEST`).
- **Binding**: Loopback-only (`Blueprint.before_request` rejects non-127.0.0.1/::1 with 403). `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` for off-loopback (unsupported, emits warning).

## Anti-patterns

- Do NOT add hardcoded platform lists in templates — they come from `registered_platforms()`.
- Do NOT add inline per-route CSRF checks — the global guard covers all state-mutating verbs.
- Do NOT add modules under `services/` that duplicate `webui_store` singletons — stores are the persistence layer; services orchestrate.
- Do NOT import `webui_store` stores eagerly at module level — use `_LazyStore` or `current_app.extensions`.
