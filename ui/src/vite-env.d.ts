/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "idp" signs in through the mock IdP (`POST /idp/token`); anything else uses `Bearer dev:<persona>`. */
  readonly VITE_AUTH_MODE?: "dev" | "idp";
  /** "1" shows the demo controls drawer (scripted events, reset, tamper). Never in a production build. */
  readonly VITE_DEMO_CONTROLS?: string;
}
