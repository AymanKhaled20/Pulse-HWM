// Worker entry point — thin wrapper: error boundary + router dispatch.
// (route() lives in router.js; every lib + route module is a sibling.)

import { route } from "./router.js";
import { fail } from "./lib/respond.js";

export default {
  async fetch(request, env) {
    try {
      return await route(request, env);
    } catch (err) {
      // log for observability; the client gets a generic 500 — internal
      // messages (D1 schemas, stack traces) must never leave the worker
      console.error("unhandled worker error:", err);
      return fail(500, "internal error");
    }
  },
};
