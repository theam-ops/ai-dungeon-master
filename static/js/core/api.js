/* Talking to the server. Part of the front end - see js/main.js. */

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    // a refusal the browser can put in the player's language comes as {code, text}
    const err = new Error(typeof detail === "object" ? detail.text : detail);
    err.status = res.status;
    if (typeof detail === "object") err.code = detail.code;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}
