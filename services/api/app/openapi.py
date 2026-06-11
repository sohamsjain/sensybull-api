"""
OpenAPI 3.0 specification for the Sensybull API.

Served as JSON at /docs/openapi.json and rendered via Swagger UI at /docs.
"""

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Sensybull API",
        "version": "1.0.0",
        "description": (
            "Real-time SEC 8-K filing intelligence platform. "
            "Provides REST endpoints for authentication, filing events, "
            "watchlists, companies, and a Socket.IO WebSocket feed for live updates."
        ),
    },
    "servers": [
        {"url": "/", "description": "Current server"},
    ],

    # ── Security ──────────────────────────────────────────────────────────
    "components": {
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Access token from /auth/login or /auth/register",
            },
            "RefreshAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Refresh token from /auth/login or /auth/register",
            },
        },
        "schemas": {
            # ── Request bodies ────────────────────────────────────────────
            "RegisterRequest": {
                "type": "object",
                "required": ["name", "email", "password"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 100},
                    "email": {"type": "string", "format": "email"},
                    "password": {"type": "string", "minLength": 6},
                },
            },
            "LoginRequest": {
                "type": "object",
                "required": ["email", "password"],
                "properties": {
                    "email": {"type": "string", "format": "email"},
                    "password": {"type": "string"},
                },
            },
            "GoogleLoginRequest": {
                "type": "object",
                "required": ["token"],
                "properties": {
                    "token": {"type": "string", "description": "Google OAuth JWT credential"},
                },
            },
            "EmailOnlyRequest": {
                "type": "object",
                "required": ["email"],
                "properties": {
                    "email": {"type": "string", "format": "email"},
                },
            },
            "TokenRequest": {
                "type": "object",
                "required": ["token"],
                "properties": {
                    "token": {"type": "string", "minLength": 16, "maxLength": 128},
                },
            },
            "ResetPasswordRequest": {
                "type": "object",
                "required": ["token", "new_password"],
                "properties": {
                    "token": {"type": "string", "minLength": 16, "maxLength": 128},
                    "new_password": {"type": "string", "minLength": 6},
                },
            },
            "ChangePasswordRequest": {
                "type": "object",
                "required": ["current_password", "new_password"],
                "properties": {
                    "current_password": {"type": "string"},
                    "new_password": {"type": "string", "minLength": 6},
                },
            },
            "WatchlistCreateRequest": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 100},
                    "description": {"type": "string"},
                },
            },
            "AddCompanyRequest": {
                "type": "object",
                "required": ["company_id"],
                "properties": {
                    "company_id": {"type": "string", "format": "uuid"},
                },
            },

            # ── Response objects ──────────────────────────────────────────
            "User": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "name": {"type": "string"},
                    "email": {"type": "string", "format": "email"},
                    "phone_number": {"type": "string", "nullable": True},
                    "is_admin": {"type": "boolean"},
                    "email_verified": {"type": "boolean"},
                    "email_verified_at": {"type": "string", "format": "date-time", "nullable": True},
                    "created_at": {"type": "string", "format": "date-time"},
                },
            },
            "AuthResponse": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "user": {"$ref": "#/components/schemas/User"},
                    "access_token": {"type": "string"},
                    "refresh_token": {"type": "string"},
                },
            },
            "Company": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "name": {"type": "string"},
                    "ticker": {"type": "string", "nullable": True},
                    "cik": {"type": "string", "nullable": True},
                    "sic": {"type": "string", "nullable": True},
                    "state_of_incorporation": {"type": "string", "nullable": True},
                    "created_at": {"type": "string", "format": "date-time"},
                },
            },
            "Watchlist": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "name": {"type": "string"},
                    "description": {"type": "string", "nullable": True},
                    "user_id": {"type": "string", "format": "uuid"},
                    "created_at": {"type": "string", "format": "date-time"},
                    "companies": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Company"},
                    },
                },
            },
            "BriefingItem": {
                "type": "object",
                "properties": {
                    "number": {"type": "string", "example": "5.02"},
                    "title": {"type": "string"},
                    "tier": {"type": "integer", "enum": [1, 2, 3]},
                    "category": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
            "Exhibit": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "example": "EX-99.1"},
                    "description": {"type": "string"},
                    "url": {"type": "string", "format": "uri"},
                },
            },
            "Briefing": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "primary_event_type": {"type": "string"},
                    "significance": {"type": "string", "enum": ["High", "Medium", "Low"]},
                    "sentiment": {"type": "string", "enum": ["Positive", "Negative", "Neutral", "Mixed"]},
                    "investor_takeaway": {"type": "string"},
                    "catalysts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string", "format": "date", "nullable": True},
                                "event": {"type": "string"},
                            },
                        },
                    },
                    "deal_terms": {"type": "object", "additionalProperties": True},
                },
            },
            "FilingEvent": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "edgar_id": {"type": "string"},
                    "signal_type": {"type": "string", "example": "8-K"},
                    "ticker": {"type": "string", "nullable": True},
                    "company_name": {"type": "string"},
                    "company_id": {"type": "string", "format": "uuid", "nullable": True},
                    "cik": {"type": "string"},
                    "filing_date": {"type": "string", "format": "date-time", "nullable": True},
                    "edgar_url": {"type": "string", "format": "uri", "nullable": True},
                    "accession_number": {"type": "string", "nullable": True},
                    "max_tier": {"type": "integer", "enum": [1, 2, 3], "description": "1=critical, 2=important, 3=routine"},
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/BriefingItem"}},
                    "exhibits": {"type": "array", "items": {"$ref": "#/components/schemas/Exhibit"}},
                    "briefing": {"$ref": "#/components/schemas/Briefing", "nullable": True},
                    "event_types": {"type": "array", "items": {"type": "string"}, "example": ["Acquisition", "Debt / Financing"]},
                    "catalysts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "event": {"type": "string"},
                                "date": {"type": "string", "format": "date", "nullable": True},
                            },
                        },
                    },
                    "received_at": {"type": "string", "format": "date-time"},
                },
            },
            "Catalyst": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "filing_event_id": {"type": "string", "format": "uuid"},
                    "event": {"type": "string"},
                    "date": {"type": "string", "format": "date", "nullable": True},
                    "ticker": {"type": "string"},
                    "company_name": {"type": "string"},
                },
            },
            "HealthResponse": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["ok", "degraded"]},
                    "api": {"type": "string"},
                    "redis": {"type": "string"},
                    "database": {"type": "string"},
                },
            },
            "Error": {
                "type": "object",
                "properties": {
                    "error": {"type": "string"},
                    "details": {"type": "object"},
                },
            },
        },
        "parameters": {
            "Page": {
                "name": "page", "in": "query",
                "schema": {"type": "integer", "default": 1, "minimum": 1},
            },
            "PerPage": {
                "name": "per_page", "in": "query",
                "schema": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
            },
            "MaxTier": {
                "name": "max_tier", "in": "query",
                "description": "Include events at this tier or more critical (1=critical only, 2=important+critical, 3=all)",
                "schema": {"type": "integer", "default": 3, "enum": [1, 2, 3]},
            },
            "SignalType": {
                "name": "signal_type", "in": "query",
                "schema": {"type": "string"},
                "description": "Filter by signal type (e.g. '8-K')",
            },
            "EventType": {
                "name": "event_type", "in": "query",
                "schema": {"type": "string"},
                "description": "Filter by canonical event type label (e.g. 'Acquisition')",
            },
        },
    },

    # ── Paths ─────────────────────────────────────────────────────────────
    "paths": {
        # ── Health ────────────────────────────────────────────────────────
        "/health": {
            "get": {
                "tags": ["Health"],
                "summary": "Health check",
                "description": "Returns API, Redis, and database connectivity status.",
                "responses": {
                    "200": {"description": "All systems operational", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HealthResponse"}}}},
                    "503": {"description": "One or more systems degraded"},
                },
            },
        },

        # ── Auth ──────────────────────────────────────────────────────────
        "/api/v1/auth/register": {
            "post": {
                "tags": ["Authentication"],
                "summary": "Register a new account",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RegisterRequest"}}}},
                "responses": {
                    "201": {"description": "Account created", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthResponse"}}}},
                    "400": {"description": "Validation error"},
                    "409": {"description": "Email already exists"},
                },
            },
        },
        "/api/v1/auth/login": {
            "post": {
                "tags": ["Authentication"],
                "summary": "Login with email and password",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LoginRequest"}}}},
                "responses": {
                    "200": {"description": "Login successful", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthResponse"}}}},
                    "401": {"description": "Invalid credentials"},
                },
            },
        },
        "/api/v1/auth/google": {
            "post": {
                "tags": ["Authentication"],
                "summary": "Login with Google OAuth",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GoogleLoginRequest"}}}},
                "responses": {
                    "200": {"description": "Login successful", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthResponse"}}}},
                    "401": {"description": "Invalid Google token"},
                },
            },
        },
        "/api/v1/auth/refresh": {
            "post": {
                "tags": ["Authentication"],
                "summary": "Refresh access token",
                "security": [{"RefreshAuth": []}],
                "responses": {
                    "200": {"description": "New access token issued", "content": {"application/json": {"schema": {"type": "object", "properties": {"access_token": {"type": "string"}, "user": {"$ref": "#/components/schemas/User"}}}}}},
                    "401": {"description": "Invalid or expired refresh token"},
                },
            },
        },
        "/api/v1/auth/verify-email": {
            "post": {
                "tags": ["Authentication"],
                "summary": "Verify email address",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TokenRequest"}}}},
                "responses": {
                    "200": {"description": "Email verified"},
                    "400": {"description": "Invalid or expired token"},
                },
            },
        },
        "/api/v1/auth/resend-verification": {
            "post": {
                "tags": ["Authentication"],
                "summary": "Resend verification email",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EmailOnlyRequest"}}}},
                "responses": {
                    "200": {"description": "Verification email sent (if account exists)"},
                },
            },
        },
        "/api/v1/auth/forgot-password": {
            "post": {
                "tags": ["Authentication"],
                "summary": "Request password reset email",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EmailOnlyRequest"}}}},
                "responses": {
                    "200": {"description": "Reset email sent (if account exists)"},
                },
            },
        },
        "/api/v1/auth/reset-password": {
            "post": {
                "tags": ["Authentication"],
                "summary": "Reset password with token",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ResetPasswordRequest"}}}},
                "responses": {
                    "200": {"description": "Password reset successful"},
                    "400": {"description": "Invalid or expired token"},
                },
            },
        },
        "/api/v1/auth/change-password": {
            "post": {
                "tags": ["Authentication"],
                "summary": "Change password (authenticated)",
                "security": [{"BearerAuth": []}],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ChangePasswordRequest"}}}},
                "responses": {
                    "200": {"description": "Password changed"},
                    "401": {"description": "Current password incorrect"},
                },
            },
        },
        "/api/v1/auth/me": {
            "get": {
                "tags": ["Authentication"],
                "summary": "Get current user",
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {"description": "Current user", "content": {"application/json": {"schema": {"type": "object", "properties": {"user": {"$ref": "#/components/schemas/User"}}}}}},
                    "401": {"description": "Not authenticated"},
                },
            },
        },

        # ── Events ────────────────────────────────────────────────────────
        "/api/v1/events/": {
            "get": {
                "tags": ["Events"],
                "summary": "Get events for user's watchlist",
                "description": "Returns paginated filing events filtered to companies in the authenticated user's watchlists.",
                "security": [{"BearerAuth": []}],
                "parameters": [
                    {"$ref": "#/components/parameters/Page"},
                    {"$ref": "#/components/parameters/PerPage"},
                    {"$ref": "#/components/parameters/MaxTier"},
                    {"$ref": "#/components/parameters/SignalType"},
                    {"$ref": "#/components/parameters/EventType"},
                ],
                "responses": {
                    "200": {
                        "description": "Paginated events",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "events": {"type": "array", "items": {"$ref": "#/components/schemas/FilingEvent"}},
                                "total": {"type": "integer"},
                                "page": {"type": "integer"},
                                "per_page": {"type": "integer"},
                            },
                        }}},
                    },
                    "401": {"description": "Not authenticated"},
                },
            },
        },
        "/api/v1/events/all": {
            "get": {
                "tags": ["Events"],
                "summary": "Get all events (public)",
                "description": "Returns paginated filing events regardless of watchlist. No authentication required.",
                "parameters": [
                    {"$ref": "#/components/parameters/Page"},
                    {"$ref": "#/components/parameters/PerPage"},
                    {"$ref": "#/components/parameters/MaxTier"},
                    {"$ref": "#/components/parameters/SignalType"},
                    {"$ref": "#/components/parameters/EventType"},
                ],
                "responses": {
                    "200": {
                        "description": "Paginated events",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "events": {"type": "array", "items": {"$ref": "#/components/schemas/FilingEvent"}},
                                "total": {"type": "integer"},
                                "page": {"type": "integer"},
                                "per_page": {"type": "integer"},
                            },
                        }}},
                    },
                },
            },
        },
        "/api/v1/events/types": {
            "get": {
                "tags": ["Events"],
                "summary": "List canonical event types",
                "description": "Returns the 34 canonical event type labels used for classification. Use these values in the event_type filter.",
                "responses": {
                    "200": {
                        "description": "Event type list",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "event_types": {"type": "array", "items": {"type": "string"}},
                            },
                        }}},
                    },
                },
            },
        },
        "/api/v1/events/catalysts": {
            "get": {
                "tags": ["Events"],
                "summary": "Upcoming catalyst dates",
                "description": "Returns future catalyst dates extracted from filing briefings, ordered by date.",
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50, "maximum": 200}},
                ],
                "responses": {
                    "200": {
                        "description": "Catalyst list",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "catalysts": {"type": "array", "items": {"$ref": "#/components/schemas/Catalyst"}},
                            },
                        }}},
                    },
                },
            },
        },
        "/api/v1/events/{event_id}": {
            "get": {
                "tags": ["Events"],
                "summary": "Get single event",
                "security": [{"BearerAuth": []}],
                "parameters": [
                    {"name": "event_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
                ],
                "responses": {
                    "200": {"description": "Event detail", "content": {"application/json": {"schema": {"type": "object", "properties": {"event": {"$ref": "#/components/schemas/FilingEvent"}}}}}},
                    "403": {"description": "Company not in user's watchlist"},
                    "404": {"description": "Event not found"},
                },
            },
        },
        "/api/v1/events/company/{company_id}": {
            "get": {
                "tags": ["Events"],
                "summary": "Get events for a company",
                "security": [{"BearerAuth": []}],
                "parameters": [
                    {"name": "company_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
                    {"$ref": "#/components/parameters/Page"},
                    {"$ref": "#/components/parameters/PerPage"},
                    {"$ref": "#/components/parameters/MaxTier"},
                    {"$ref": "#/components/parameters/SignalType"},
                    {"$ref": "#/components/parameters/EventType"},
                ],
                "responses": {
                    "200": {"description": "Paginated events for company"},
                    "403": {"description": "Company not in user's watchlist"},
                },
            },
        },

        # ── Watchlists ────────────────────────────────────────────────────
        "/api/v1/watchlists/": {
            "get": {
                "tags": ["Watchlists"],
                "summary": "List user's watchlists",
                "security": [{"BearerAuth": []}],
                "parameters": [
                    {"$ref": "#/components/parameters/Page"},
                    {"name": "per_page", "in": "query", "schema": {"type": "integer", "default": 20}},
                ],
                "responses": {
                    "200": {
                        "description": "Paginated watchlists",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "watchlists": {"type": "array", "items": {"$ref": "#/components/schemas/Watchlist"}},
                                "total": {"type": "integer"},
                                "page": {"type": "integer"},
                                "per_page": {"type": "integer"},
                            },
                        }}},
                    },
                },
            },
            "post": {
                "tags": ["Watchlists"],
                "summary": "Create a watchlist",
                "security": [{"BearerAuth": []}],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/WatchlistCreateRequest"}}}},
                "responses": {
                    "201": {"description": "Watchlist created", "content": {"application/json": {"schema": {"type": "object", "properties": {"message": {"type": "string"}, "watchlist": {"$ref": "#/components/schemas/Watchlist"}}}}}},
                    "400": {"description": "Validation error"},
                },
            },
        },
        "/api/v1/watchlists/{watchlist_id}": {
            "get": {
                "tags": ["Watchlists"],
                "summary": "Get a watchlist",
                "security": [{"BearerAuth": []}],
                "parameters": [{"name": "watchlist_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                "responses": {
                    "200": {"description": "Watchlist with companies"},
                    "403": {"description": "Not your watchlist"},
                    "404": {"description": "Not found"},
                },
            },
            "put": {
                "tags": ["Watchlists"],
                "summary": "Update a watchlist",
                "security": [{"BearerAuth": []}],
                "parameters": [{"name": "watchlist_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/WatchlistCreateRequest"}}}},
                "responses": {
                    "200": {"description": "Watchlist updated"},
                    "403": {"description": "Not your watchlist"},
                },
            },
            "delete": {
                "tags": ["Watchlists"],
                "summary": "Delete a watchlist",
                "security": [{"BearerAuth": []}],
                "parameters": [{"name": "watchlist_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                "responses": {
                    "200": {"description": "Watchlist deleted"},
                    "403": {"description": "Not your watchlist"},
                },
            },
        },
        "/api/v1/watchlists/{watchlist_id}/companies": {
            "post": {
                "tags": ["Watchlists"],
                "summary": "Add company to watchlist",
                "security": [{"BearerAuth": []}],
                "parameters": [{"name": "watchlist_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AddCompanyRequest"}}}},
                "responses": {
                    "200": {"description": "Company added"},
                    "409": {"description": "Company already in watchlist"},
                },
            },
        },
        "/api/v1/watchlists/{watchlist_id}/companies/{company_id}": {
            "delete": {
                "tags": ["Watchlists"],
                "summary": "Remove company from watchlist",
                "security": [{"BearerAuth": []}],
                "parameters": [
                    {"name": "watchlist_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
                    {"name": "company_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
                ],
                "responses": {
                    "200": {"description": "Company removed"},
                    "404": {"description": "Company not in watchlist"},
                },
            },
        },

        # ── Companies ─────────────────────────────────────────────────────
        "/api/v1/companies/": {
            "get": {
                "tags": ["Companies"],
                "summary": "List companies",
                "security": [{"BearerAuth": []}],
                "parameters": [
                    {"$ref": "#/components/parameters/Page"},
                    {"name": "per_page", "in": "query", "schema": {"type": "integer", "default": 20}},
                    {"name": "ticker", "in": "query", "schema": {"type": "string"}, "description": "Filter by ticker symbol"},
                ],
                "responses": {
                    "200": {
                        "description": "Paginated companies",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "companies": {"type": "array", "items": {"$ref": "#/components/schemas/Company"}},
                                "total": {"type": "integer"},
                                "page": {"type": "integer"},
                                "per_page": {"type": "integer"},
                            },
                        }}},
                    },
                },
            },
        },
        "/api/v1/companies/{company_id}": {
            "get": {
                "tags": ["Companies"],
                "summary": "Get a company",
                "security": [{"BearerAuth": []}],
                "parameters": [{"name": "company_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                "responses": {
                    "200": {"description": "Company detail", "content": {"application/json": {"schema": {"type": "object", "properties": {"company": {"$ref": "#/components/schemas/Company"}}}}}},
                    "404": {"description": "Not found"},
                },
            },
        },

        # ── Chats ─────────────────────────────────────────────────────────
        "/api/v1/chats/": {
            "get": {
                "tags": ["Chats"],
                "summary": "Chat list — watchlist companies with unread counts",
                "description": (
                    "Returns every company across the user's watchlists as a chat: "
                    "last filing event preview, unread count, mute state. Sorted "
                    "with unread chats first, then by most recent activity."
                ),
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {
                        "description": "Chat list",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "chats": {"type": "array", "items": {
                                    "type": "object",
                                    "properties": {
                                        "company": {"type": "object"},
                                        "last_event": {"type": "object", "nullable": True},
                                        "last_activity_at": {"type": "string", "format": "date-time", "nullable": True},
                                        "unread_count": {"type": "integer"},
                                        "muted": {"type": "boolean"},
                                        "last_read_at": {"type": "string", "format": "date-time", "nullable": True},
                                    },
                                }},
                                "total_unread": {"type": "integer"},
                            },
                        }}},
                    },
                },
            },
        },
        "/api/v1/chats/{company_id}/read": {
            "post": {
                "tags": ["Chats"],
                "summary": "Mark a company's events as read",
                "security": [{"BearerAuth": []}],
                "parameters": [{"name": "company_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                "responses": {
                    "200": {"description": "Read state updated"},
                    "403": {"description": "Company not on user's watchlists"},
                },
            },
        },
        "/api/v1/chats/{company_id}/mute": {
            "put": {
                "tags": ["Chats"],
                "summary": "Mute or unmute alerts for a company",
                "security": [{"BearerAuth": []}],
                "parameters": [{"name": "company_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["muted"],
                        "properties": {"muted": {"type": "boolean"}},
                    }}},
                },
                "responses": {
                    "200": {"description": "Mute state updated"},
                    "400": {"description": "muted (boolean) is required"},
                    "403": {"description": "Company not on user's watchlists"},
                },
            },
        },
    },

    # ── Tags ──────────────────────────────────────────────────────────────
    "tags": [
        {"name": "Health", "description": "System health checks"},
        {"name": "Authentication", "description": "Registration, login, tokens, password management"},
        {"name": "Events", "description": "SEC 8-K filing events with AI briefings"},
        {"name": "Watchlists", "description": "User-defined company watchlists"},
        {"name": "Companies", "description": "SEC-registered companies"},
        {"name": "Chats", "description": "Chat-style watchlist: per-company read state, unread counts, mute"},
    ],
}
