# OhMyClash Python backend

Python backend owns every Mihomo process. The Vue UI only calls this backend and never receives a Mihomo secret.

## Managed profiles

The normal desktop launcher does not require a core configuration file. Put user-managed YAML profiles in `data/profiles/`. Each profile becomes one isolated Mihomo instance automatically. The runtime generates a private home directory, mixed port, controller port, and secret for every profile.

The UI manages this directory through the profile page. Importing a local YAML or adding a subscription downloads/saves the profile, rebuilds the managed instance set, and starts the required Mihomo processes. Subscription URLs are kept locally for the update action and are not returned by the profile API.

The core is discovered from the bundled `runtime/core/mihomo.exe` first, then supported local development locations. A release build should ship the binary at `runtime/core/mihomo.exe`.

## Standalone backend debug

```powershell
python -m backend.main
```

The backend listens on `http://127.0.0.1:17890` by default. It starts instances with `autostart: true` and stops them when the backend exits. Normal desktop use should start `desktop.launcher` instead, so the manager belongs to the OhMyClash process.

The frontend uses these routes:

- `GET /api/instances`
- `GET /api/profiles`
- `POST /api/profiles/import` with `{ "name", "content" }`
- `POST /api/profiles/subscribe` with `{ "name", "url" }`
- `POST /api/profiles/{id}/refresh`
- `POST /api/instances/{id}/start|stop|restart`
- `GET /api/instances/{id}/proxies`
- `GET /api/instances/{id}/rules`
- `PUT /api/instances/{id}/proxies/{group}`
- `GET /api/instances/{id}/group/{group}/delay`
- `GET /api/instances/{id}/proxies/{proxy}/delay`

Other Mihomo API paths are proxied through the same instance route, so connections and logs can use the same manager without exposing the controller directly to the browser.

## WebViewUI launcher

From the project root:

```powershell
python -m desktop.launcher
```

The launcher reuses an already-running Vite dev server on port 5173, creates the backend server in the OhMyClash process, starts all managed Mihomo processes, waits for `/api/health`, and then opens the WebViewUI window. Closing the window stops the backend and all managed Mihomo processes.

For a production-like local test:

```powershell
npm run build
python -m desktop.launcher --dist
```
