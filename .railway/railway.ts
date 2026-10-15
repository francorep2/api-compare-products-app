import { defineRailway, github, preserve, project, service } from "railway/iac";
// This repository manages only its own resources in the environment. Other
// repositories export their own partial name.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
export const partial = "api";
export default defineRailway(() => {
  const api = service("api", {
    builder: "DOCKERFILE",
    dockerfilePath: "Dockerfile",
    source: github("francorep2/api-compare-products-app", { branch: "main" }),
    healthcheck: "/health",
    healthcheckTimeout: 60,
    restartPolicyType: "ON_FAILURE",
    restartPolicyMaxRetries: 5,
    replicas: { "sfo": 1 },
    // Las variables viven en Railway (secretos): preserve() las deja como están.
    env: { APP_NAME: preserve(), DATABASE_URL: preserve(), JWT_SECRET: preserve(), MAIL_FROM: preserve(), SMTP_DRY_RUN: preserve(), USE_SENDGRID_API: preserve(), RESEND_API_KEY: preserve(), SENDGRID_API_KEY: preserve() },
  });

  return project("changuito-beta", {
    resources: [api],
  });
});
