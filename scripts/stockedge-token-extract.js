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
// If it prints "No token pair found", StockEdge changed its storage keys —
// run  Object.keys(localStorage).concat(Object.keys(sessionStorage))  and
// share the KEY NAMES (not values) so the finder below can be tuned.
// ============================================================================
// Auto-discovers the access/refresh token pair in localStorage/sessionStorage
// and downloads it as stockedge-tokens.json.
(() => {
  const stores = [localStorage, sessionStorage];
  let found = null;

  const tryVal = (raw) => {
    if (!raw || typeof raw !== "string") return null;
    let o;
    try { o = JSON.parse(raw); } catch { return null; }
    // walk one level deep for an object carrying both tokens
    const cands = [o, ...Object.values(o).filter(v => v && typeof v === "object")];
    for (const c of cands) {
      const at = c.access_token || c.accessToken || c.token;
      const rt = c.refresh_token || c.refreshToken;
      if (at && rt) return { at, rt, exp_in: c.expires_in || c.expiresIn };
    }
    return null;
  };

  for (const store of stores) {
    for (let i = 0; i < store.length; i++) {
      const hit = tryVal(store.getItem(store.key(i)));
      if (hit) { found = hit; break; }
    }
    if (found) break;
  }

  if (!found) {
    console.error("❌ No access/refresh token pair found. Make sure you're logged in at web.stockedge.com, then re-run.");
    return;
  }

  // decode JWT exp if present, else fall back to expires_in / 24h
  let expMs = null;
  try {
    const payload = JSON.parse(atob(found.at.split(".")[1]));
    if (payload.exp) expMs = payload.exp * 1000;
  } catch {}
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
