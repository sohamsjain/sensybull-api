# Web Frontend

> **Status:** The current frontend in `services/web/` is a **placeholder/prototype** used to validate the backend's real-time pipeline during development. It is not the production frontend.

> **Plan:** A separate, standalone web application will be built from scratch to serve as the production frontend, consuming this project purely as a backend API.

---

## Current Placeholder (`services/web/`)

The existing React app was built to prove out:
- Socket.IO connection lifecycle and event replay
- JWT auth flow (login, register, token refresh)
- Watchlist-based event filtering
- Real-time filing card rendering with tier/sentiment badges

It uses React 19, Vite, Tailwind CSS, and socket.io-client. It lives inside the monorepo but is **not intended for production use**.

---

## Production Frontend (TBD)

The production web app will be a **separate repository/project** that treats this monorepo as a backend. It will connect via:

- **REST API** — Auth, CRUD, historical queries (see [[API Routes Reference]])
- **WebSocket** — Real-time filing events via Socket.IO `/feed` namespace (see [[Real-Time System]])

### What the Backend Provides

The API is frontend-agnostic. Any client that can:
1. Authenticate via `POST /auth/login` → JWT tokens
2. Send `Authorization: Bearer <token>` headers
3. Connect to Socket.IO at `/feed` with `auth: {token}`

...can consume the full Sensybull platform.

---

## See Also

- [[API Routes Reference]] — All REST endpoints
- [[Real-Time System]] — WebSocket protocol and events
- [[Authentication System]] — JWT token lifecycle
- [[Architecture Overview]] — System topology
