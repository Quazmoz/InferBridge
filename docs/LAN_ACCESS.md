# LAN and home-lab access

InferBridge is local-only by default. The packaged desktop server listens on `127.0.0.1` unless LAN access is explicitly enabled or an advanced environment override requests another bind host.

LAN mode is intended for trusted devices on the same local network or home lab. It is not a public internet mode, reverse proxy, tunnel, or firewall manager.

## Packaged desktop application

Open **Generation Settings > Local API > Network / API access**.

1. Configure or generate an API key.
2. Enable **Allow access from other devices on my local network**.
3. Leave **Allowed browser origins** blank unless a browser application on another origin needs CORS.
4. Choose **Apply and restart**.
5. After restart, copy one of the displayed LAN endpoints such as `http://192.168.1.20:8123/v1` into the trusted remote client.

The desktop application stores a GUI-managed API key with Windows DPAPI under InferBridge's normal writable configuration root. The persisted key is not returned by status APIs, written to diagnostics, or stored in browser `localStorage`. A newly generated key is shown once in the local settings dialog so it can be copied into remote clients; after it is stored, InferBridge does not reveal that persisted value again. When packaged authentication is active, the local UI receives a random per-process HttpOnly, SameSite cookie. For protected requests from that validated loopback browser session, InferBridge supplies one configured API key internally on the server side. The stored key is not placed in page source. Remote LAN clients never receive this browser-auth bridge and must present their own `Authorization: Bearer <API_KEY>` header.

Installed mode stores mutable state under `%LOCALAPPDATA%\InferBridge`. Portable mode keeps it under the portable `data` directory. The DPAPI-encrypted key is bound to the Windows user context, so moving a portable directory to another machine may require configuring a new key. Nothing mutable is written into the installed program directory.

### Listener and client addresses

`0.0.0.0` is a bind address. It means the server listens on available IPv4 interfaces. It is never a valid client destination.

Use the actual private LAN endpoint shown in the UI, for example:

```text
http://192.168.1.20:8123/v1
```

InferBridge discovers active RFC1918 IPv4 addresses and prefers the address associated with the active route when it can determine one. Multiple usable addresses may be shown. Discovery failure does not stop the server.

The local browser, tray controller, instance verification, health checks, and desktop control plane continue using loopback even when the server listens on `0.0.0.0`.

### Authentication gate

Packaged LAN exposure requires an API key. If a persisted LAN setting or `OV_LLM_HOST` requests network exposure while no API key is available, InferBridge stays bound to `127.0.0.1` and reports the blocked configuration instead of starting an unauthenticated LAN listener.

Source/CLI mode applies the same security intent at the request boundary. If it is bound to a non-loopback address without `OV_LLM_API_KEY`, loopback clients can still use the process but non-loopback HTTP requests are rejected. This also protects a direct Uvicorn wildcard bind from accidentally exposing an unauthenticated InferBridge server.

A GUI-managed key can be generated in Network / API settings. Advanced users can instead provide:

```powershell
$env:OV_LLM_HOST = "0.0.0.0"
$env:OV_LLM_API_KEY = "replace-with-a-strong-secret"
```

`OV_LLM_API_KEY` is never copied into the GUI or returned by a status endpoint.

## CORS is separate from LAN access

CORS controls which browser origins may make cross-origin requests. It is not required for ordinary SDK, Open WebUI, n8n, curl, or other server-to-server clients.

For a browser client at a known origin, prefer an explicit value:

```powershell
$env:OV_LLM_CORS_ORIGINS = "http://192.168.1.50:3000"
```

Multiple origins are comma-separated. A wildcard must be used by itself:

```text
*
```

Wildcard CORS is an advanced configuration. Packaged mode will not activate wildcard CORS without API authentication, and the GUI requires an explicit warning confirmation before saving it. InferBridge does not automatically set `*` when LAN mode is enabled.

## Windows Firewall

InferBridge does not silently create a Windows Firewall rule.

When Windows prompts after LAN access is enabled, allow InferBridge only on trusted **Private** network profiles. Do not enable an unrestricted rule for **Public** profiles. Managed devices may require an administrator or organization policy change.

If another device cannot connect, verify:

1. LAN mode is active in Network / API settings.
2. The displayed endpoint uses a current private address and the actual active port.
3. The client sends `Authorization: Bearer <API_KEY>`.
4. Windows Firewall allows the InferBridge process or active port on the Private profile.
5. Client and server can route to each other without guest Wi-Fi or VLAN isolation.

## Configuration precedence

### Source mode

Source mode keeps the existing precedence:

1. CLI flags such as `--host` and `--port`
2. Windows environment variables
3. repo-root `.env`
4. built-in defaults

The repo-root `.env` is a source-mode convenience. Do not expect an installed packaged application to have or read a repo-root `.env`.

Authenticated LAN example:

```powershell
$env:OV_LLM_API_KEY = "replace-with-a-strong-secret"
.\start_server.bat --host 0.0.0.0
```

or:

```powershell
$env:OV_LLM_HOST = "0.0.0.0"
$env:OV_LLM_API_KEY = "replace-with-a-strong-secret"
.\start_server.bat
```

Binding source mode to a non-loopback address without an API key is not an unauthenticated LAN mode. Remote HTTP clients receive `403` until an API key is configured; localhost access remains available for recovery.

### Packaged desktop mode

Packaged network settings use this precedence:

1. supported Windows environment overrides such as `OV_LLM_HOST`, `OV_LLM_API_KEY`, and `OV_LLM_CORS_ORIGINS`
2. persisted desktop Network / API settings
3. secure defaults, including `127.0.0.1` and empty CORS

The tray still chooses the actual available server port using the existing fallback logic. Network settings do not pin the port to 8000.

## Disable LAN access

Open **Network / API access**, clear **Allow access from other devices on my local network**, and choose **Apply and restart**. After restart, the active listener returns to `127.0.0.1` unless `OV_LLM_HOST` is still overriding the GUI setting.

If `OV_LLM_HOST` is set in Windows environment variables, remove or change that variable before expecting the GUI LAN toggle to control the listener.
