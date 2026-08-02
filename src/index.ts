import { serve } from "@hono/node-server";
import { app } from "./api/routes.js";
import { preloadSchemas } from "./verifier/schema-validator.js";
import { ensureContextRoot } from "./context/store.js";

const port = Number(process.env.PORT ?? 3000);

await ensureContextRoot();
await preloadSchemas();

console.log(`trip-agent P0 server listening on http://localhost:${port}`);

serve({ fetch: app.fetch, port });
