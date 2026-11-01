// Static-demo fetch shim for the frozen sms-toolkit viewer (see scripts/freeze.py).
//
// GitHub Pages serves files, not the Flask API, and project Pages live under
// /sms-toolkit/, so the viewer's absolute /api/... fetches would resolve to
// the domain root and 404. This shim runs before the viewer script (freeze.py
// inserts its <script> tag first) and rewrites API fetches to the static JSON
// files freeze.py generated, all relative so the subpath works.
//
// Mapping:
//   /api/stats                    -> api/stats (static file)
//   /api/conversations?...        -> api/conversations (static file; the
//                                    viewer filters client-side, and the
//                                    demo list fits under the limit)
//   /api/conversation?address=X   -> api/conversation/<encoded X> (one file
//                                    per thread; a static host ignores query
//                                    strings, so one shared file can't work)
//   /api/conversation?...&before= -> synthetic empty page (demo threads fit
//                                    on one page, so "load earlier" never
//                                    appears; this is belt-and-braces)
//   /api/messages?page=1          -> api/messages (static file)
//   /api/messages?page>1          -> synthetic [] (archive paginates until
//                                    an empty page; the demo is one page)
//   /api/messages?...&search=q    -> api/messages, filtered client-side
//   /api/message/<hash>           -> relativized, static file
// Image <img> URLs are not fetches, so the shim cannot touch them;
// freeze.py rewrites /api/image/ to api/image/ in the frozen HTML instead.
(function () {
  const origFetch = window.fetch.bind(window);
  const EMPTY_PAGE = { messages: [], has_more: false, oldest_ms: null };

  function jsonResponse(data) {
    return new Response(JSON.stringify(data), {
      headers: { "Content-Type": "application/json" },
    });
  }

  window.fetch = function (input, init) {
    let url;
    try {
      url = new URL(typeof input === "string" ? input : input.url, window.location.href);
    } catch (e) {
      return origFetch(input, init);
    }
    if (!url.pathname.startsWith("/api/")) {
      return origFetch(input, init);
    }
    const path = url.pathname.replace(/^\//, "");
    if (path === "api/conversation") {
      if (url.searchParams.has("before")) {
        return Promise.resolve(jsonResponse(EMPTY_PAGE));
      }
      const address = url.searchParams.get("address");
      if (!address) return origFetch(input, init);
      return origFetch("api/conversation/" + encodeURIComponent(address), init);
    }
    if (path === "api/messages") {
      const page = Number(url.searchParams.get("page") || "1");
      const q = (url.searchParams.get("search") || "").toLowerCase();
      if (page > 1) return Promise.resolve(jsonResponse([]));
      if (!q) return origFetch("api/messages", init);
      return origFetch("api/messages", init)
        .then((res) => res.json())
        .then((msgs) =>
          jsonResponse(
            msgs.filter(
              (m) =>
                (m.body || "").toLowerCase().includes(q) ||
                (m.contact_name || "").toLowerCase().includes(q) ||
                (m.address || "").toLowerCase().includes(q),
            ),
          ),
        );
    }
    // stats, conversations, message detail: path-addressed static files.
    return origFetch(path, init);
  };
})();
