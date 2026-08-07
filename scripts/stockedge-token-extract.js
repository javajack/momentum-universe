// ============================================================================
// StockEdge MCP token refresher
// ----------------------------------------------------------------------------
// The StockEdge MCP token lives at ~/.config/stockedge/tokens.json and expires
// ~every 24h. When MCP calls fail with "No valid StockEdge token", refresh it:
//
//   1. Open https://web.stockedge.com in Chrome, logged in.
//   2. DevTools (F12) -> Console -> paste this whole file -> Enter.
//      It downloads ~/Downloads/stockedge-tokens.json.
//   3. Install it (overwrites the stale token):
//        mv ~/Downloads/stockedge-tokens.json ~/.config/stockedge/tokens.json
//   4. Retry the MCP call.
//
// If the token pair can't be found, this script auto-prints a STRUCTURE dump
// of local/sessionStorage (KEY NAMES and sub-keys only, NO token values) so the
// finder can be tuned. Share that output. Deep-nested / stringified-JSON token
// blobs are handled: the finder recurses and parses nested JSON strings.
// ============================================================================
(() => {
  const MAX_DEPTH = 4;

  // Recursively hunt for an object carrying an access+refresh pair. Parses
  // JSON-string values encountered along the way (tokens are often double-encoded).
  const hunt = (val, depth) => {
    if (depth > MAX_DEPTH || val == null) return null;
    if (typeof val === "string") {
      if (val.length > 5000 || (val[0] !== "{" && val[0] !== "[")) return null;
      try { return hunt(JSON.parse(val), depth + 1); } catch { return null; }
    }
    if (typeof val !== "object") return null;
    const at = val.access_token || val.accessToken || val.token || val.id_token;
    const rt = val.refresh_token || val.refreshToken;
    if (at && rt && typeof at === "string") return { at, rt, exp_in: val.expires_in || val.expiresIn };
    for (const v of Object.values(val)) {
      const hit = hunt(v, depth + 1);
      if (hit) return hit;
    }
    return null;
  };

  const pairFrom = (obj) => {
    const at = obj.access_token || obj.accessToken || obj.token || obj.id_token;
    const rt = obj.refresh_token || obj.refreshToken;
    if (at && rt && typeof at === "string")
      return { at, rt, exp_in: obj.expires_in || obj.expiresIn, expires_at: obj.expires_at };
    return null;
  };

  let found = null;
  // sessionStorage first — StockEdge (oidc-client) keeps them there.
  for (const store of [sessionStorage, localStorage]) {
    // (a) sibling top-level keys: access_token / refresh_token as their own entries.
    const flat = {};
    for (let i = 0; i < store.length; i++) flat[store.key(i)] = store.getItem(store.key(i));
    found = pairFrom(flat);
    // (b) fallback: a single key holding a (possibly nested/stringified) token blob.
    for (let i = 0; i < store.length && !found; i++) found = hunt(store.getItem(store.key(i)), 0);
    if (found) break;
  }

  if (!found) {
    console.error("❌ No access/refresh token pair found. Structure dump below "
      + "(key names & sub-keys only, no values) — share it so the finder can be tuned:");
    const describe = (store, name) => {
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i), raw = store.getItem(k);
        let shape;
        try {
          const o = JSON.parse(raw);
          if (o && typeof o === "object") {
            shape = "{ " + Object.keys(o).map(kk => {
              const v = o[kk];
              if (v && typeof v === "object") return kk + ":{" + Object.keys(v).join(",") + "}";
              if (typeof v === "string") return kk + ":str(" + v.length + (v.split(".").length === 3 ? ",JWT" : "") + ")";
              return kk + ":" + typeof v;
            }).join(", ") + " }";
          } else shape = typeof o;
        } catch {
          shape = "str(len=" + raw.length + (raw.split(".").length === 3 ? ",JWT-like" : "") + ")";
        }
        console.log(`${name}["${k}"] -> ${shape}`);
      }
    };
    describe(localStorage, "local");
    describe(sessionStorage, "session");
    return;
  }

  // prefer the access-token JWT exp; else the sibling expires_at (secs or ms);
  // else expires_in / 24h from now.
  let expMs = null;
  try {
    const payload = JSON.parse(atob(found.at.split(".")[1]));
    if (payload.exp) expMs = payload.exp * 1000;
  } catch {}
  if (!expMs && found.expires_at) {
    const n = Number(found.expires_at);
    if (n) expMs = n < 1e12 ? n * 1000 : n;   // normalize seconds -> ms
  }
  const now = Date.now();
  const expIn = found.exp_in || 86400;
  if (!expMs) expMs = now + expIn * 1000;

  const out = {
    access_token: found.at,
    refresh_token: found.rt,
    expires_at: expMs,
    expires_in: expIn,
    saved_at: Math.floor(now / 1000),
  };

  const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "stockedge-tokens.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  console.log("✅ Downloaded stockedge-tokens.json — token expires:", new Date(expMs).toString());
})();
