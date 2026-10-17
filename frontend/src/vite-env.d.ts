/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_AUTH_MODE?: "dev" | "entra"
  readonly VITE_ENTRA_CLIENT_ID?: string
  readonly VITE_ENTRA_TENANT_ID?: string
  readonly VITE_ENTRA_API_SCOPE?: string
  readonly VITE_EXTERNAL_CLIENT_ID?: string
  readonly VITE_EXTERNAL_TENANT_SUBDOMAIN?: string
  readonly VITE_EXTERNAL_TENANT_ID?: string
  readonly VITE_EXTERNAL_API_SCOPE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
