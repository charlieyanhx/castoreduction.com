/* castor-api.js — one way to call this server, for every page.

   THERE WERE EIGHT. Two of them were both called api() and did different things, and the
   rest were hand-rolled at each call site, so every fix to this class of bug landed on
   exactly one page. The two failures a reader actually hit this week were both here: a
   checkout refusal that reported into an off-screen slot, and a report page that treated a
   404 as an empty result. Neither was a hard problem; both were invisible because nothing
   made surfacing a failure the default.

   THREE THINGS THIS GUARANTEES, and they are the three that kept getting lost:

     the status survives   a 402 is a price, a 404 is not yours, a 409 is a state. A caller
                           that cannot tell them apart writes one message for all of them.
     the reason survives   the server sends `detail` on every refusal and it is written for
                           the reader. Throwing it away and substituting "something went
                           wrong" is discarding the only useful sentence in the exchange.
     a failure throws      including a dropped connection, which fetch reports by rejecting
                           and which every hand-rolled copy forgot. Silence is the one
                           outcome a caller must not be able to produce by accident.
*/
(function (w) {
  "use strict";

  /** A refusal from the server, or the network failing to reach it. */
  function ApiError(message, status, detail, body) {
    var e = new Error(message);
    e.name = "ApiError";
    e.status = status || 0;        // 0 means the request never arrived
    e.detail = detail || "";
    e.body = body || null;
    e.offline = !status;
    return e;
  }

  //: What to say when the server did not answer at all. Deliberately not "an error
  //: occurred": the reader can act on this one.
  var UNREACHABLE = "Could not reach the server. Check your connection and try again.";

  /** GET/POST/PATCH/DELETE. Resolves with the parsed body, or throws ApiError. */
  async function api(method, path, body) {
    var opt = {method: method, headers: {accept: "application/json"}};
    if (body !== undefined && body !== null) {
      opt.headers["content-type"] = "application/json";
      opt.body = JSON.stringify(body);
    }

    var res;
    try {
      res = await fetch(path, opt);
    } catch (e) {
      // fetch rejects only when the request never completed. Every hand-rolled copy of
      // this let that become an unhandled rejection: the button stayed dead, nothing said.
      throw ApiError(UNREACHABLE, 0, "");
    }

    // Parse first, always. The BODY OF A FAILED CALL is where the reason lives, and a
    // non-JSON error body (a proxy 502, a gateway timeout) must not turn a clean refusal
    // into a parse crash.
    var data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }

    if (!res.ok) {
      var detail = (data && typeof data.detail === "string" && data.detail) ? data.detail : "";
      throw ApiError(detail || (res.status + " " + (res.statusText || "refused")),
                     res.status, detail, data);
    }
    return data;
  }

  /** The message to show a person, for any error this module throws. */
  function reason(e) {
    if (!e) return UNREACHABLE;
    if (e.detail) return e.detail;
    if (e.offline) return UNREACHABLE;
    return e.message || UNREACHABLE;
  }

  w.castor = w.castor || {};
  w.castor.api = api;
  w.castor.reason = reason;
  w.castor.ApiError = ApiError;
})(window);
