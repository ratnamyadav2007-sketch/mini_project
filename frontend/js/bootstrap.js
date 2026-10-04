import { ApiError, clearRequestCache, handleGlobalApiError } from "./api.js";
import { setState } from "./store.js";
import { showOfflineBanner, showToast } from "./ui.js";

const authChannel = "BroadcastChannel" in window
  ? new BroadcastChannel("fieldnote-auth")
  : null;

authChannel?.addEventListener("message", event => {
  if (event.data?.type === "logout") {
    clearRequestCache();
    if (!/\/login\.html$/.test(window.location.pathname)) {
      window.location.replace("/login.html?reason=signed-out");
    }
  }
});

window.addEventListener("offline", () => showOfflineBanner(true));
window.addEventListener("online", () => {
  showOfflineBanner(false);
  showToast("You are back online.", "success");
});

window.addEventListener("fieldnote:authenticated", event => {
  setState({ currentUser: event.detail });
});

window.addEventListener("fieldnote:active-member", event => {
  setState({ activeMember: event.detail });
});

window.addEventListener("fieldnote:family", event => {
  setState({ family: event.detail });
});

window.addEventListener("unhandledrejection", event => {
  if (event.reason instanceof ApiError) handleGlobalApiError(event.reason);
});

if (!navigator.onLine) showOfflineBanner(true);
